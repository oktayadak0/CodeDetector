"""Ortam değişkenlerinden okunan uygulama ayarları.

Tüm model ayarları yalnızca backend tarafında tutulur; frontend'e asla
özel yapılandırma gönderilmez.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # python-dotenv opsiyoneldir; yoksa ortam değişkenleri yine okunur.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _optional_bool(name: str, default: bool | None) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip().lower()
    if raw in ("", "auto", "default"):
        return None
    return raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Gemma 4 / Ollama  (OLLAMA_HOST, Ollama'nın kendi dinleme adresi değişkeni
    # olduğu için çakışmayı önlemek adına OLLAMA_URL kullanılır.)
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/"))
    gemma_model: str = field(default_factory=lambda: os.getenv("GEMMA_MODEL", "gemma4:e2b").strip())
    gemma_timeout: float = field(default_factory=lambda: _float("GEMMA_TIMEOUT", 300.0))
    gemma_connect_timeout: float = field(default_factory=lambda: _float("GEMMA_CONNECT_TIMEOUT", 5.0))
    gemma_num_ctx: int = field(default_factory=lambda: _int("GEMMA_NUM_CTX", 16384))
    gemma_num_predict: int = field(default_factory=lambda: _int("GEMMA_NUM_PREDICT", 4096))
    gemma_temperature: float = field(default_factory=lambda: _float("GEMMA_TEMPERATURE", 0.1))
    gemma_think: bool | None = field(default_factory=lambda: _optional_bool("GEMMA_THINK", False))
    gemma_max_retries: int = field(default_factory=lambda: max(0, min(3, _int("GEMMA_MAX_RETRIES", 1))))

    # Analiz
    analysis_language: str = field(default_factory=lambda: os.getenv("ANALYSIS_LANGUAGE", "tr").strip().lower() or "tr")
    max_model_input_chars: int = field(default_factory=lambda: _int("MAX_MODEL_INPUT_CHARS", 24000))
    max_code_chars: int = field(default_factory=lambda: _int("MAX_CODE_CHARS", 200_000))
    max_upload_bytes: int = field(default_factory=lambda: _int("MAX_UPLOAD_BYTES", 512 * 1024))
    max_request_bytes: int = field(default_factory=lambda: _int("MAX_REQUEST_BYTES", 2 * 1024 * 1024))

    # Demo modu: auto = yalnızca Gemma 4 erişilemezken, on = her zaman, off = asla
    demo_mode: str = field(default_factory=lambda: os.getenv("DEMO_MODE", "auto").strip().lower() or "auto")

    # Sunucu
    host: str = field(default_factory=lambda: os.getenv("APP_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("APP_PORT", 5000))


def get_settings() -> Settings:
    return Settings()
