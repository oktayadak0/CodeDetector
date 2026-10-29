"""Dosya uzantısı ve basit içerik ipuçlarıyla programlama dili tespiti."""

from __future__ import annotations

import os
import re

EXTENSIONS: dict[str, str] = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".dart": "dart", ".lua": "lua", ".r": "r",
    ".sql": "sql", ".sh": "shell", ".bash": "shell", ".ps1": "powershell",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "css", ".vue": "vue", ".svelte": "svelte",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml",
    ".ini": "ini", ".cfg": "ini", ".env": "ini", ".md": "markdown", ".txt": "text",
    ".gradle": "groovy", ".tf": "terraform",
}

# Yükleme için izin verilen uzantılar (metin tabanlı kaynak kod + diff/patch)
ALLOWED_UPLOAD_EXTENSIONS = set(EXTENSIONS) | {".diff", ".patch"}
ALLOWED_UPLOAD_FILENAMES = {"dockerfile", "makefile", ".gitignore"}

SUPPORTED_LANGUAGES = sorted(set(EXTENSIONS.values()))

_HEURISTICS: list[tuple[str, re.Pattern]] = [
    ("php", re.compile(r"<\?php")),
    ("python", re.compile(r"^\s*(def|class)\s+\w+.*:\s*$|^\s*(from\s+\w[\w.]*\s+)?import\s+\w+|^\s*elif\b|self\.", re.M)),
    ("go", re.compile(r"^\s*package\s+\w+\s*$|^\s*func\s+(\(\w+\s+\*?\w+\)\s*)?\w+\(", re.M)),
    ("rust", re.compile(r"^\s*(pub\s+)?fn\s+\w+|let\s+mut\s+|impl\s+\w+", re.M)),
    ("csharp", re.compile(r"^\s*using\s+System|namespace\s+\w+|public\s+(async\s+)?Task", re.M)),
    ("java", re.compile(r"^\s*(public|private|protected)\s+(static\s+)?(final\s+)?(class|void|int|String)\b|System\.out\.", re.M)),
    ("typescript", re.compile(r":\s*(string|number|boolean|any)\b|interface\s+\w+\s*\{|\bas\s+const\b")),
    ("javascript", re.compile(r"\b(const|let|var)\s+\w+\s*=|function\s*\w*\s*\(|=>|module\.exports|require\(")),
    ("cpp", re.compile(r"#include\s*<\w+>|std::|cout\s*<<")),
    ("c", re.compile(r"#include\s*[<\"]\w+\.h[>\"]|printf\s*\(")),
    ("sql", re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE)\b", re.M | re.I)),
    ("ruby", re.compile(r"^\s*def\s+\w+[^:]*$|^\s*end\s*$|\bputs\b", re.M)),
]


def language_from_path(path: str | None) -> str | None:
    if not path:
        return None
    base = os.path.basename(path).lower()
    if base == "dockerfile":
        return "dockerfile"
    if base == "makefile":
        return "makefile"
    _, ext = os.path.splitext(base)
    return EXTENSIONS.get(ext)


def detect_language(path: str | None, content: str | None = "") -> str:
    """Önce dosya uzantısına, sonra içerikteki tipik kalıplara bakar."""
    lang = language_from_path(path)
    if lang:
        return lang
    sample = (content or "")[:20000]
    for name, pattern in _HEURISTICS:
        if pattern.search(sample):
            return name
    return "text"


def is_allowed_upload(filename: str) -> bool:
    base = os.path.basename(filename or "").lower()
    if base in ALLOWED_UPLOAD_FILENAMES:
        return True
    _, ext = os.path.splitext(base)
    return ext in ALLOWED_UPLOAD_EXTENSIONS
