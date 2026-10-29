"""CodeDetector — Flask uygulaması ve API rotaları.

Çalıştırma:  python app.py   (veya Windows'ta run.bat)
"""

from __future__ import annotations

import logging
import os
import time

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from codedetector import __version__
from codedetector.analysis import run_analysis
from codedetector.config import Settings, get_settings
from codedetector.demo import SAMPLE_FILENAME, SAMPLE_NEW, SAMPLE_OLD, demo_analysis, sample_input
from codedetector.diffing import compute_diff, has_changes, parse_unified_diff
from codedetector.gemma import GemmaClient, GemmaError
from codedetector.languages import SUPPORTED_LANGUAGES, is_allowed_upload

log = logging.getLogger("codedetector")


class InputError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "invalid_input"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# --------------------------------------------------------------------------- #
# Girdi hazırlama ve doğrulama
# --------------------------------------------------------------------------- #
def _clean_text(value, name: str, settings: Settings) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InputError(f"'{name}' alanı metin olmalıdır.")
    if "\x00" in value:
        raise InputError(f"'{name}' alanı ikili (binary) veri içeriyor; yalnızca metin tabanlı kod desteklenir.")
    if len(value) > settings.max_code_chars:
        raise InputError(f"'{name}' alanı çok büyük ({len(value)} karakter; sınır {settings.max_code_chars}).", 413, "input_too_large")
    return value


def _language(value) -> str:
    v = str(value or "auto").strip().lower()
    return v if v == "auto" or v in SUPPORTED_LANGUAGES else "auto"


def prepare_files(payload: dict, settings: Settings) -> tuple[list[dict], str, str, str]:
    """İstek gövdesinden FileDiff listesini üretir. (files, mode, language, description)"""
    if not isinstance(payload, dict):
        raise InputError("Geçersiz istek gövdesi; JSON nesnesi bekleniyor.")
    mode = str(payload.get("mode") or "").strip().lower()
    language = _language(payload.get("language"))
    description = _clean_text(payload.get("description"), "description", settings)[:2000]

    if mode == "code":
        old = _clean_text(payload.get("old_code"), "old_code", settings)
        new = _clean_text(payload.get("new_code"), "new_code", settings)
        if not old.strip() and not new.strip():
            raise InputError("Eski ve yeni kod alanları boş. Karşılaştırmak için iki sürümü de yapıştırın.")
        filename = str(payload.get("filename") or "").strip()[:200] or None
        files = [compute_diff(old, new, filename, language)]
    elif mode == "diff":
        diff = _clean_text(payload.get("diff"), "diff", settings)
        if not diff.strip():
            raise InputError("Diff alanı boş. `git diff` çıktısını veya unified diff metnini yapıştırın.")
        files = parse_unified_diff(diff)
        if not files:
            raise InputError("Metin geçerli bir unified/Git diff olarak tanınmadı. '---', '+++' ve '@@' satırlarını içerdiğinden emin olun.")
        if language != "auto":
            for f in files:
                if f["language"] == "text":
                    f["language"] = language
    else:
        raise InputError("Geçersiz mod. 'code' veya 'diff' olmalıdır.")

    if not has_changes(files):
        raise InputError("İki sürüm arasında değişiklik bulunamadı; analiz edilecek bir şey yok.", 422, "no_changes")
    if all(f.get("binary") for f in files):
        raise InputError("Diff yalnızca ikili (binary) dosyalar içeriyor; metin kodu analiz edilebilir.", 422, "no_changes")
    return files, mode, language, description


def _read_upload(storage, settings: Settings) -> tuple[str, str]:
    name = os.path.basename(storage.filename or "").strip()
    if not name:
        raise InputError("Dosya adı okunamadı.")
    if not is_allowed_upload(name):
        raise InputError(f"'{name}' desteklenmeyen bir dosya türü. Kaynak kod veya .diff/.patch dosyası yükleyin.", 415, "unsupported_file")
    data = storage.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise InputError(f"'{name}' çok büyük (sınır {settings.max_upload_bytes // 1024} KB).", 413, "file_too_large")
    if b"\x00" in data:
        raise InputError(f"'{name}' ikili (binary) bir dosya gibi görünüyor.", 415, "unsupported_file")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1254")  # Türkçe Windows kodlaması
        except UnicodeDecodeError:
            raise InputError(f"'{name}' UTF-8 metin olarak okunamadı.", 415, "unsupported_file")
    return name, text


def prepare_upload(form, files_in, settings: Settings) -> tuple[list[dict], str, str, str]:
    language = _language(form.get("language"))
    description = _clean_text(form.get("description"), "description", settings)[:2000]
    if "patch_file" in files_in and files_in["patch_file"].filename:
        _, text = _read_upload(files_in["patch_file"], settings)
        files, _, lang, _ = prepare_files({"mode": "diff", "diff": text, "language": language}, settings)
        return files, "upload_patch", lang, description
    if "old_file" in files_in and "new_file" in files_in and files_in["old_file"].filename and files_in["new_file"].filename:
        _, old = _read_upload(files_in["old_file"], settings)
        new_name, new = _read_upload(files_in["new_file"], settings)
        files, _, lang, _ = prepare_files(
            {"mode": "code", "old_code": old, "new_code": new, "filename": new_name, "language": language}, settings
        )
        return files, "upload_pair", lang, description
    raise InputError("Eski + yeni sürüm dosyalarını ya da tek bir .diff/.patch dosyasını yükleyin.")


# --------------------------------------------------------------------------- #
# Uygulama fabrikası
# --------------------------------------------------------------------------- #
def create_app(settings: Settings | None = None, client: GemmaClient | None = None) -> Flask:
    settings = settings or get_settings()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_request_bytes
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    gemma = client or GemmaClient(settings)

    def _respond(files, mode, language, description, analysis: dict):
        analysis["input"] = {"mode": mode, "language": language, "description": description}
        analysis["files"] = files
        return jsonify(analysis)

    def _analyze(files, mode, language, description):
        started = time.monotonic()
        try:
            analysis = run_analysis(gemma, files, description, language, settings)
        finally:
            # Kod içeriği LOGLANMAZ; yalnızca boyut ve süre bilgisi.
            log.info("analyze mode=%s files=%d +%d/-%d took=%.1fs", mode, len(files),
                     sum(f["added"] for f in files), sum(f["removed"] for f in files), time.monotonic() - started)
        return _respond(files, mode, language, description, analysis)

    # ------------------------------ Sayfalar ------------------------------ #
    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    # -------------------------------- API --------------------------------- #
    @app.get("/api/health")
    def health():
        st = gemma.status()
        return jsonify({
            "app": "ok",
            "version": __version__,
            "engine": "Gemma 4",
            "model_ready": st["ready"],
            "model": st["configured_model"],
            "demo_available": _demo_allowed(st),
            "error": st["error"],
        })

    @app.get("/api/model/status")
    def model_status():
        st = gemma.status()
        st["demo_mode"] = settings.demo_mode
        st["demo_available"] = _demo_allowed(st)
        return jsonify(st)

    @app.get("/api/sample")
    def sample():
        data = sample_input()
        data["label"] = "ÖRNEK VERİ — demo senaryosu"
        return jsonify(data)

    @app.post("/api/diff")
    def diff_preview():
        files, mode, language, _ = prepare_files(request.get_json(silent=True), settings)
        return jsonify({"mode": mode, "language": language, "files": files})

    @app.post("/api/analyze")
    def analyze():
        files, mode, language, description = prepare_files(request.get_json(silent=True), settings)
        return _analyze(files, mode, language, description)

    @app.post("/api/analyze/upload")
    def analyze_upload():
        files, mode, language, description = prepare_upload(request.form, request.files, settings)
        if request.form.get("preview") == "1":
            return jsonify({"mode": mode, "language": language, "files": files})
        return _analyze(files, mode, language, description)

    @app.post("/api/demo/analyze")
    def demo():
        payload = request.get_json(silent=True) or {}
        st = gemma.status()
        if not _demo_allowed(st):
            if settings.demo_mode == "off":
                raise InputError("Demo modu bu sunucuda kapalı (DEMO_MODE=off).", 403, "demo_disabled")
            raise InputError("Gemma 4 kullanılabilir durumda; sabit demo sonuçları yerine gerçek analiz yapın.", 409, "demo_not_needed")
        norm = lambda s: (s or "").replace("\r\n", "\n").strip()  # noqa: E731
        if norm(payload.get("old_code")) != norm(SAMPLE_OLD) or norm(payload.get("new_code")) != norm(SAMPLE_NEW):
            raise InputError("Demo sonuçları yalnızca değiştirilmemiş örnek senaryo için mevcuttur. "
                             "Kendi kodunuzu analiz etmek için Gemma 4 gereklidir.", 422, "demo_only_sample")
        demo_payload = {**payload, "mode": "code", "filename": payload.get("filename") or SAMPLE_FILENAME}
        files, mode, language, description = prepare_files(demo_payload, settings)
        return _respond(files, "demo", language, description, demo_analysis(files))

    def _demo_allowed(st: dict) -> bool:
        if settings.demo_mode == "on":
            return True
        if settings.demo_mode == "off":
            return False
        return not st.get("ready")

    # ------------------------------ Hatalar ------------------------------- #
    @app.errorhandler(InputError)
    def on_input_error(e: InputError):
        return jsonify({"error": {"code": e.code, "message": e.message, "hint": ""}}), e.status

    @app.errorhandler(GemmaError)
    def on_gemma_error(e: GemmaError):
        log.warning("gemma error code=%s", e.code)
        return jsonify({"error": e.to_dict()}), e.status

    @app.errorhandler(RequestEntityTooLarge)
    def on_too_large(_e):
        return jsonify({"error": {"code": "request_too_large",
                                  "message": f"İstek çok büyük (sınır {settings.max_request_bytes // 1024} KB).",
                                  "hint": "Daha küçük bir diff veya dosya gönderin."}}), 413

    @app.errorhandler(HTTPException)
    def on_http(e: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": {"code": "http_error", "message": e.description or e.name, "hint": ""}}), e.code
        return e

    @app.errorhandler(Exception)
    def on_unexpected(e: Exception):
        log.exception("unexpected error")
        return jsonify({"error": {"code": "internal_error", "message": "Beklenmeyen bir sunucu hatası oluştu.",
                                  "hint": "Sunucu konsolundaki hata kaydını kontrol edin."}}), 500

    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = get_settings()
    application = create_app(cfg)
    print("\n  CodeDetector çalışıyor")
    print(f"  Tarayıcıda açın:  http://{cfg.host}:{cfg.port}")
    print(f"  Analiz motoru:    Gemma 4  (model: {cfg.gemma_model}, Ollama: {cfg.ollama_url})\n")
    application.run(host=cfg.host, port=cfg.port, debug=False, threaded=True)
