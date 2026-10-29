"""Gemma 4 model istemcisi (Ollama HTTP API üzerinden).

Bu modül uygulamanın modelle konuşan TEK yeridir. Gemma 4 dışında bir model
yapılandırılmışsa analiz reddedilir; başka bir modele sessizce geçiş yapılmaz
ve harici/ücretli bir API'ye geri dönüş (fallback) yoktur.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import Settings

GEMMA4_NAME_RE = re.compile(r"gemma[\s_\-.]?4", re.I)


class GemmaError(Exception):
    """Kullanıcıya gösterilebilir, kodlu model hatası."""

    def __init__(self, code: str, message: str, hint: str = "", status: int = 503, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.status = status
        self.details = details or {}

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message, "hint": self.hint}
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class ChatResult:
    content: str
    done_reason: str | None
    eval_count: int | None
    prompt_eval_count: int | None
    duration_ms: int


def looks_like_gemma4(name: str | None, family: str | None = None, families: list | None = None) -> bool:
    candidates = [name or "", family or ""] + [str(x) for x in (families or [])]
    return any(GEMMA4_NAME_RE.search(c) for c in candidates)


def _version_tuple(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:3]) if nums else (0,)


class GemmaClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.http = session or requests.Session()

    # ------------------------------------------------------------------ #
    # Durum / doğrulama
    # ------------------------------------------------------------------ #
    def _get(self, path: str, timeout: float | None = None) -> requests.Response:
        return self.http.get(f"{self.s.ollama_url}{path}", timeout=timeout or self.s.gemma_connect_timeout)

    def _unreachable(self, exc: Exception | None = None) -> GemmaError:
        return GemmaError(
            "runtime_unreachable",
            f"Gemma 4 çalışma ortamına (Ollama) ulaşılamadı: {self.s.ollama_url}",
            "Ollama'nın kurulu ve çalışır olduğundan emin olun (`ollama serve` veya Ollama uygulamasını başlatın). "
            "Adres farklıysa .env dosyasındaki OLLAMA_URL değerini düzeltin.",
            503,
            {"error": type(exc).__name__} if exc else None,
        )

    def status(self) -> dict:
        """Yapılandırılmış modeli GERÇEK çalışma ortamına sorarak doğrular."""
        model = self.s.gemma_model
        out: dict[str, Any] = {
            "engine": "Gemma 4",
            "runtime": "ollama",
            "configured_model": model,
            "runtime_reachable": False,
            "runtime_version": None,
            "model_installed": False,
            "is_gemma4": False,
            "ready": False,
            "model_details": None,
            "error": None,
        }
        try:
            r = self._get("/api/version")
            r.raise_for_status()
            out["runtime_version"] = r.json().get("version")
            out["runtime_reachable"] = True
        except requests.RequestException as exc:
            out["error"] = self._unreachable(exc).to_dict()
            return out
        except ValueError:
            out["error"] = GemmaError("runtime_invalid", "Çalışma ortamı beklenmeyen bir yanıt döndürdü.",
                                      "OLLAMA_URL'nin bir Ollama sunucusunu gösterdiğini kontrol edin.").to_dict()
            return out

        try:
            r = self._get("/api/tags")
            r.raise_for_status()
            installed = [m.get("name") or m.get("model") for m in r.json().get("models", [])]
        except (requests.RequestException, ValueError) as exc:
            out["error"] = self._unreachable(exc).to_dict()
            return out

        out["installed_gemma4_models"] = [m for m in installed if m and looks_like_gemma4(m)]
        wanted = model if ":" in model else f"{model}:latest"
        if model not in installed and wanted not in installed:
            out["error"] = GemmaError(
                "model_not_installed",
                f"Yapılandırılan model '{model}' Ollama'da kurulu değil.",
                f"Modeli indirmek için: `ollama pull {model}` (indirme boyutu birkaç GB'tır). "
                "Kurulu Gemma 4 modelleriniz varsa GEMMA_MODEL değerini onlardan biriyle değiştirin.",
            ).to_dict()
            return out
        out["model_installed"] = True

        family = families = None
        try:
            r = self.http.post(f"{self.s.ollama_url}/api/show", json={"model": model}, timeout=self.s.gemma_connect_timeout * 2)
            if r.ok:
                info = r.json()
                det = info.get("details") or {}
                family, families = det.get("family"), det.get("families")
                out["model_details"] = {
                    "family": family,
                    "families": families,
                    "parameter_size": det.get("parameter_size"),
                    "quantization": det.get("quantization_level"),
                    "format": det.get("format"),
                    "capabilities": info.get("capabilities"),
                }
        except (requests.RequestException, ValueError):
            pass

        out["is_gemma4"] = looks_like_gemma4(model, family, families)
        if not out["is_gemma4"]:
            out["error"] = GemmaError(
                "model_not_gemma4",
                f"Yapılandırılan model '{model}' bir Gemma 4 modeli olarak doğrulanamadı.",
                "CodeDetector yalnızca Gemma 4 ile çalışır. GEMMA_MODEL değerini örn. `gemma4:e2b` olarak ayarlayın.",
                409,
            ).to_dict()
            return out

        out["ready"] = True
        return out

    def ensure_ready(self) -> dict:
        st = self.status()
        if not st["ready"]:
            err = st["error"] or {}
            raise GemmaError(err.get("code", "model_unavailable"), err.get("message", "Gemma 4 hazır değil."),
                             err.get("hint", ""), 409 if err.get("code") == "model_not_gemma4" else 503)
        return st

    # ------------------------------------------------------------------ #
    # Sohbet çağrısı
    # ------------------------------------------------------------------ #
    def chat(self, messages: list[dict], json_schema: dict | None = None, runtime_version: str | None = None) -> ChatResult:
        fmt: Any = "json"
        if json_schema is not None and _version_tuple(runtime_version or "99") >= (0, 5, 0):
            fmt = json_schema  # Ollama yapılandırılmış çıktı (structured outputs)

        payload: dict[str, Any] = {
            "model": self.s.gemma_model,
            "messages": messages,
            "stream": False,
            "format": fmt,
            "keep_alive": "10m",
            "options": {
                "temperature": self.s.gemma_temperature,
                "num_ctx": self.s.gemma_num_ctx,
                "num_predict": self.s.gemma_num_predict,
            },
        }
        if self.s.gemma_think is not None:
            payload["think"] = self.s.gemma_think

        started = time.monotonic()
        resp = self._post_chat(payload)
        if resp.status_code == 400 and "think" in payload and "think" in resp.text.lower():
            # Bazı Ollama sürümleri 'think' parametresini desteklemez: parametresiz tekrar dene.
            payload.pop("think", None)
            resp = self._post_chat(payload)

        if resp.status_code == 404:
            raise GemmaError("model_not_installed", f"Model '{self.s.gemma_model}' bulunamadı.",
                             f"`ollama pull {self.s.gemma_model}` komutuyla indirin.", 503)
        if resp.status_code >= 400:
            msg = _extract_error(resp)
            hint = "Ollama günlüklerini kontrol edin."
            if "memory" in msg.lower() or "out of memory" in msg.lower():
                hint = ("Model belleğe sığmıyor olabilir. Daha küçük bir Gemma 4 varyantı (örn. gemma4:e2b) deneyin "
                        "veya GEMMA_NUM_CTX değerini düşürün.")
            elif "newer version" in msg.lower() or "requires" in msg.lower():
                hint = "Bu Gemma 4 modeli daha yeni bir Ollama sürümü gerektiriyor olabilir. Ollama'yı güncelleyin."
            raise GemmaError("model_error", f"Gemma 4 isteği başarısız oldu: {msg}", hint, 502)

        try:
            data = resp.json()
        except ValueError:
            raise GemmaError("model_error", "Gemma 4 çalışma ortamı JSON olmayan bir yanıt döndürdü.", "", 502)

        content = ((data.get("message") or {}).get("content") or "").strip()
        return ChatResult(
            content=content,
            done_reason=data.get("done_reason"),
            eval_count=data.get("eval_count"),
            prompt_eval_count=data.get("prompt_eval_count"),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _post_chat(self, payload: dict) -> requests.Response:
        try:
            return self.http.post(
                f"{self.s.ollama_url}/api/chat",
                json=payload,
                timeout=(self.s.gemma_connect_timeout, self.s.gemma_timeout),
            )
        except requests.Timeout:
            raise GemmaError(
                "model_timeout",
                f"Gemma 4 {int(self.s.gemma_timeout)} saniye içinde yanıt vermedi.",
                "İlk çalıştırmada model belleğe yüklenirken gecikme olabilir. Tekrar deneyin, daha küçük bir diff gönderin "
                "veya .env içindeki GEMMA_TIMEOUT değerini artırın.",
                504,
            )
        except requests.ConnectionError as exc:
            raise self._unreachable(exc)
        except requests.RequestException as exc:
            raise GemmaError("model_error", f"Gemma 4 isteği gönderilemedi ({type(exc).__name__}).", "", 502)


def _extract_error(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("error") or resp.text)[:300]
    except ValueError:
        return resp.text[:300] or f"HTTP {resp.status_code}"
