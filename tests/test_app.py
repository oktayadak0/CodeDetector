"""CodeDetector otomatik birim ve API testleri."""

from __future__ import annotations

import io
import json

import pytest

from app import create_app
from codedetector.analysis import extract_json, normalize_result, run_analysis
from codedetector.config import Settings
from codedetector.demo import SAMPLE_NEW, SAMPLE_OLD
from codedetector.diffing import compute_diff, parse_unified_diff
from codedetector.gemma import ChatResult, GemmaClient, GemmaError, looks_like_gemma4
from codedetector.languages import detect_language, is_allowed_upload


# --------------------------------------------------------------------------- #
# 1. Diff Motoru Testleri
# --------------------------------------------------------------------------- #
def test_compute_diff_counts_and_lines():
    old = "a = 1\nb = 2\nc = 3\n"
    new = "a = 1\nb = 20\nc = 3\nd = 4\n"
    d = compute_diff(old, new, "calc.py", "auto")
    assert d["path"] == "calc.py"
    assert d["language"] == "python"
    assert d["line_mapping"] == "exact"
    assert d["added"] == 2
    assert d["removed"] == 1
    del_rows = [r for r in d["rows"] if r["type"] == "del"]
    add_rows = [r for r in d["rows"] if r["type"] == "add"]
    assert del_rows[0]["old"] == 2 and del_rows[0]["new"] is None and del_rows[0]["mod"] is True
    assert add_rows[0]["new"] == 2 and add_rows[0]["old"] is None and add_rows[0]["mod"] is True
    assert add_rows[1]["new"] == 4 and add_rows[1]["text"] == "d = 4"


def test_parse_unified_diff_preserves_lines_and_filenames():
    patch = (
        "diff --git a/auth/login.py b/auth/login.py\n"
        "--- a/auth/login.py\n"
        "+++ b/auth/login.py\n"
        "@@ -10,4 +10,4 @@ def check_user(u):\n"
        "     if u is None:\n"
        "-        return False\n"
        "+        return True\n"
        "     return u.is_active\n"
    )
    files = parse_unified_diff(patch)
    assert len(files) == 1
    f = files[0]
    assert f["path"] == "auth/login.py"
    assert f["line_mapping"] == "exact"
    assert f["added"] == 1 and f["removed"] == 1
    del_r = next(r for r in f["rows"] if r["type"] == "del")
    add_r = next(r for r in f["rows"] if r["type"] == "add")
    assert del_r["old"] == 11 and del_r["new"] is None
    assert add_r["new"] == 11 and add_r["old"] is None


def test_parse_headerless_diff_does_not_fabricate_line_numbers():
    raw = "-old_call()\n+new_call()\n"
    files = parse_unified_diff(raw)
    assert len(files) == 1
    f = files[0]
    assert f["line_mapping"] == "unavailable"
    for r in f["rows"]:
        assert r["old"] is None and r["new"] is None


# --------------------------------------------------------------------------- #
# 2. Dil Algılama ve Dosya Doğrulama Testleri
# --------------------------------------------------------------------------- #
def test_language_detection_and_upload_allowlist():
    assert detect_language("service.ts", "") == "typescript"
    assert detect_language(None, "def hello(x):\n    return x + 1\n") == "python"
    assert is_allowed_upload("main.py") is True
    assert is_allowed_upload("changes.patch") is True
    assert is_allowed_upload("malware.exe") is False
    assert is_allowed_upload("archive.zip") is False


# --------------------------------------------------------------------------- #
# 3. Gemma 4 Doğrulama ve Kanıt Kontrolü Testleri
# --------------------------------------------------------------------------- #
def test_looks_like_gemma4_rejects_other_models():
    assert looks_like_gemma4("gemma4:e2b") is True
    assert looks_like_gemma4("gemma4:e4b") is True
    assert looks_like_gemma4("custom-name", family="gemma4") is True
    assert looks_like_gemma4("llama3:8b", family="llama") is False
    assert looks_like_gemma4("deepseek-coder:6.7b") is False
    assert looks_like_gemma4("gemma2:9b", family="gemma2") is False


def test_normalize_result_strips_unverified_line_numbers():
    files = [compute_diff("x = 1\ny = 2\n", "x = 1\ny = 3\n", "app.py", "python")]
    raw = {
        "summary": "y değişti.",
        "no_significant_risk": False,
        "affected_components": [{"name": "y", "kind": "variable", "file": "app.py", "impact": "değer değişti", "evidence_type": "confirmed"}],
        "findings": [
            {
                "id": "F1",
                "title": "Geçerli satır",
                "severity": "medium",
                "category": "logic",
                "evidence_type": "confirmed",
                "file": "app.py",
                "line_start": 2,
                "line_end": 2,
                "line_side": "new",
                "changed_code": "y = 3",
                "explanation": "y 2 yerine 3 oldu.",
                "failure_scenario": "y=2 bekleyen yerler 3 alır.",
                "recommendation": "y değerini kontrol edin.",
                "confidence": "high",
                "assumptions": [],
            },
            {
                "id": "F2",
                "title": "Uydurulmuş satır numarası",
                "severity": "low",
                "category": "logic",
                "evidence_type": "plausible",
                "file": "app.py",
                "line_start": 999,
                "line_end": 999,
                "line_side": "new",
                "changed_code": "olmayan_kod()",
                "explanation": "Deneme",
                "failure_scenario": "Deneme",
                "recommendation": "Deneme",
                "confidence": "low",
                "assumptions": [],
            },
        ],
        "tests": [
            {
                "id": "T1",
                "name": "y değerini doğrula",
                "category": "unit",
                "priority": "medium",
                "finding_id": "F1",
                "behavior": "y kontrolü",
                "steps": ["Çalıştır ve kontrol et"],
                "expected_result": "Beklenen değer",
            }
        ],
        "unknowns": [],
    }
    res, warnings = normalize_result(raw, files)
    f1 = next(f for f in res["findings"] if f["id"] == "F1")
    f2 = next(f for f in res["findings"] if f["id"] == "F2")
    assert f1["line_verified"] is True and f1["line_start"] == 2
    assert f1["evidence_check"] == "verified"
    assert f2["line_verified"] is False and f2["line_start"] is None
    assert f2["evidence_check"] == "not_found"
    assert any("F2" in w for w in warnings)


# --------------------------------------------------------------------------- #
# 4. Kontrollü Yeniden Deneme ve Sahte İstemci ile Analiz Testleri
# --------------------------------------------------------------------------- #
class FakeGemmaClient(GemmaClient):
    def __init__(self, settings: Settings, responses: list[str], ready: bool = True):
        super().__init__(settings)
        self._responses = list(responses)
        self._ready = ready
        self.calls = 0

    def status(self) -> dict:
        if not self._ready:
            return {
                "engine": "Gemma 4",
                "runtime": "ollama",
                "configured_model": self.s.gemma_model,
                "runtime_reachable": False,
                "runtime_version": None,
                "model_installed": False,
                "is_gemma4": False,
                "ready": False,
                "model_details": None,
                "error": {"code": "runtime_unreachable", "message": "Ollama kapalı", "hint": "ollama serve"},
            }
        return {
            "engine": "Gemma 4",
            "runtime": "ollama",
            "configured_model": self.s.gemma_model,
            "runtime_reachable": True,
            "runtime_version": "0.6.0",
            "model_installed": True,
            "is_gemma4": True,
            "ready": True,
            "model_details": {"family": "gemma4", "parameter_size": "5.1B"},
            "error": None,
        }

    def chat(self, messages, json_schema=None, runtime_version=None) -> ChatResult:
        self.calls += 1
        content = self._responses.pop(0)
        return ChatResult(content=content, done_reason="stop", eval_count=120, prompt_eval_count=80, duration_ms=42)


def test_run_analysis_retries_on_malformed_json():
    settings = Settings(gemma_model="gemma4:e2b", gemma_max_retries=1)
    valid_payload = json.dumps({
        "summary": "Sınır koşulu değişti.",
        "no_significant_risk": False,
        "affected_components": [],
        "findings": [{
            "id": "F1",
            "title": "Off-by-one riski",
            "severity": "high",
            "category": "boundary",
            "evidence_type": "confirmed",
            "file": "a.py",
            "line_start": 1,
            "line_end": 1,
            "line_side": "new",
            "changed_code": "if x <= 18:",
            "explanation": "< yerine <= kullanıldı.",
            "failure_scenario": "x=18 reddedilir.",
            "recommendation": "x=18 testini ekleyin.",
            "confidence": "high",
            "assumptions": [],
        }],
        "tests": [{
            "id": "T1",
            "name": "x=18 sınır testi",
            "category": "regression",
            "priority": "high",
            "finding_id": "F1",
            "behavior": "18 kabul edilmeli",
            "steps": ["x=18 gönder"],
            "expected_result": "Geçerli",
        }],
        "unknowns": [],
    })
    fake = FakeGemmaClient(settings, ["bu gecerli bir json degil {{{", valid_payload], ready=True)
    files = [compute_diff("if x < 18:\n    pass\n", "if x <= 18:\n    pass\n", "a.py", "python")]
    out = run_analysis(fake, files, "Yaş kontrolü", "python", settings)
    assert fake.calls == 2
    assert out["engine"]["name"] == "Gemma 4"
    assert out["engine"]["is_demo"] is False
    assert out["result"]["overall_risk"] == "high"
    assert len(out["result"]["findings"]) == 1


# --------------------------------------------------------------------------- #
# 5. Flask API ve Güvenlik Testleri
# --------------------------------------------------------------------------- #
def test_api_endpoints_and_demo_mode_honesty():
    settings = Settings(gemma_model="gemma4:e2b", demo_mode="auto")
    offline_client = FakeGemmaClient(settings, [], ready=False)
    app = create_app(settings, offline_client)
    client = app.test_client()

    # Ana sayfa ve CSP başlığı
    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "CodeDetector" in r_index.get_data(as_text=True)
    assert "Content-Security-Policy" in r_index.headers

    # Model kapalıyken /api/model/status dürüstçe ready=False döndürmeli
    r_st = client.get("/api/model/status")
    assert r_st.status_code == 200
    assert r_st.get_json()["ready"] is False

    # Model kapalıyken /api/analyze 503 döndürmeli (sahte analiz yapmamalı)
    r_an = client.post("/api/analyze", json={"mode": "code", "old_code": "a=1", "new_code": "a=2"})
    assert r_an.status_code == 503
    assert r_an.get_json()["error"]["code"] == "runtime_unreachable"

    # Demo uç noktası rastgele kodu reddetmeli, yalnızca örnek senaryoyu kabul etmeli
    r_demo_custom = client.post("/api/demo/analyze", json={"old_code": "a=1", "new_code": "a=2"})
    assert r_demo_custom.status_code == 422
    assert r_demo_custom.get_json()["error"]["code"] == "demo_only_sample"

    r_demo_ok = client.post("/api/demo/analyze", json={"old_code": SAMPLE_OLD, "new_code": SAMPLE_NEW})
    assert r_demo_ok.status_code == 200
    demo_data = r_demo_ok.get_json()
    assert demo_data["engine"]["is_demo"] is True
    assert all(f["line_verified"] for f in demo_data["result"]["findings"])


def test_upload_rejects_disallowed_file_types():
    settings = Settings()
    app = create_app(settings, FakeGemmaClient(settings, [], ready=True))
    client = app.test_client()
    data = {
        "old_file": (io.BytesIO(b"print(1)"), "bad.exe"),
        "new_file": (io.BytesIO(b"print(2)"), "good.py"),
    }
    resp = client.post("/api/analyze/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 415
    assert resp.get_json()["error"]["code"] == "unsupported_file"
