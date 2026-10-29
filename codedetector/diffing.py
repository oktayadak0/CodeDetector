"""Diff hesaplama ve unified/Git diff ayrıştırma.

Tüm çıktı aynı yapıyı kullanır (FileDiff sözlüğü):

    {
      "path": str, "old_path": str|None, "new_path": str|None,
      "status": "modified"|"added"|"deleted"|"renamed"|"unknown",
      "language": str,
      "line_mapping": "exact"|"unavailable",
      "added": int, "removed": int,
      "binary": bool,
      "rows": [ {"type": "context"|"add"|"del"|"hunk"|"meta",
                 "old": int|None, "new": int|None, "text": str, "mod": bool} ]
    }

Satır numaraları yalnızca gerçekten hesaplanabildiğinde doldurulur; kaynak
eşlemesi yoksa ``None`` kalır ve ``line_mapping`` = ``"unavailable"`` olur.
"""

from __future__ import annotations

import difflib
import re
from typing import Iterable

from .languages import detect_language

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
DIFF_GIT_RE = re.compile(r'^diff --git "?a/(.+?)"? "?b/(.+?)"?$')


def _row(kind: str, old: int | None, new: int | None, text: str, mod: bool = False) -> dict:
    return {"type": kind, "old": old, "new": new, "text": text, "mod": mod}


def _split_lines(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


# --------------------------------------------------------------------------- #
# Eski / yeni kod karşılaştırması
# --------------------------------------------------------------------------- #
def compute_diff(old_text: str, new_text: str, path: str | None = None, language: str = "auto") -> dict:
    """İki metin arasındaki satır bazlı farkı difflib ile hesaplar."""
    old_lines = _split_lines(old_text)
    new_lines = _split_lines(new_text)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    rows: list[dict] = []
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(_row("context", i1 + k + 1, j1 + k + 1, old_lines[i1 + k]))
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(_row("del", k + 1, None, old_lines[k]))
                removed += 1
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append(_row("add", None, k + 1, new_lines[k]))
                added += 1
        else:  # replace -> değiştirilmiş bölüm
            for k in range(i1, i2):
                rows.append(_row("del", k + 1, None, old_lines[k], mod=True))
                removed += 1
            for k in range(j1, j2):
                rows.append(_row("add", None, k + 1, new_lines[k], mod=True))
                added += 1

    name = (path or "").strip() or "kod"
    lang = language if language and language != "auto" else detect_language(name if path else None, new_text or old_text)
    if not old_lines and new_lines:
        status = "added"
    elif old_lines and not new_lines:
        status = "deleted"
    else:
        status = "modified"
    return {
        "path": name,
        "old_path": name,
        "new_path": name,
        "status": status,
        "language": lang,
        "line_mapping": "exact",
        "added": added,
        "removed": removed,
        "binary": False,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# Unified / Git diff ayrıştırma
# --------------------------------------------------------------------------- #
def _clean_path(raw: str) -> str | None:
    raw = raw.split("\t", 1)[0].strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    if raw == "/dev/null":
        return None
    if raw[:2] in ("a/", "b/"):
        raw = raw[2:]
    return raw or None


def _new_file(old_path: str | None = None, new_path: str | None = None) -> dict:
    return {
        "path": new_path or old_path or "bilinmeyen-dosya",
        "old_path": old_path,
        "new_path": new_path,
        "status": "modified",
        "language": "text",
        "line_mapping": "exact",
        "added": 0,
        "removed": 0,
        "binary": False,
        "rows": [],
    }


def _mark_modified(rows: list[dict]) -> None:
    """Hemen ardından eklenen satırlar gelen silinen satır bloklarını 'değiştirilmiş' olarak işaretler."""
    i = 0
    n = len(rows)
    while i < n:
        if rows[i]["type"] == "del":
            j = i
            while j < n and rows[j]["type"] == "del":
                j += 1
            k = j
            while k < n and rows[k]["type"] == "add":
                k += 1
            if k > j:
                for r in rows[i:k]:
                    r["mod"] = True
            i = max(k, i + 1)
        else:
            i += 1


def _finalize(f: dict) -> dict:
    if f["old_path"] is None and f["new_path"] is not None:
        f["status"] = "added"
    elif f["new_path"] is None and f["old_path"] is not None:
        f["status"] = "deleted"
    elif f["old_path"] and f["new_path"] and f["old_path"] != f["new_path"]:
        f["status"] = "renamed"
    f["path"] = f["new_path"] or f["old_path"] or "bilinmeyen-dosya"
    sample = "\n".join(r["text"] for r in f["rows"] if r["type"] in ("add", "context"))
    f["language"] = detect_language(f["path"], sample)
    _mark_modified(f["rows"])
    return f


def parse_unified_diff(text: str) -> list[dict]:
    """Git diff / unified diff metnini dosya ve hunk bazında ayrıştırır.

    Hunk başlıklarındaki satır sayıları takip edilir; böylece "-- yorum" gibi
    satırlar yanlışlıkla dosya başlığı sanılmaz. Hunk başlığı olmayan
    (satır eşlemesi bilinmeyen) içerik ``line_mapping="unavailable"`` olarak
    işaretlenir ve satır numarası uydurulmaz.
    """
    lines = _split_lines(text)
    files: list[dict] = []
    cur: dict | None = None
    old_no = new_no = 0
    old_left = new_left = 0
    in_hunk = False
    loose: list[dict] = []  # başlıksız +/- satırları

    def close_current() -> None:
        nonlocal cur
        if cur is not None:
            files.append(_finalize(cur))
        cur = None

    i = 0
    while i < len(lines):
        line = lines[i]

        if in_hunk and (old_left > 0 or new_left > 0):
            if line.startswith("\\"):
                i += 1
                continue
            tag = line[:1]
            body = line[1:]
            if tag == " " or line == "":
                cur["rows"].append(_row("context", old_no, new_no, body))
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
            elif tag == "-":
                cur["rows"].append(_row("del", old_no, None, body))
                cur["removed"] += 1
                old_no += 1
                old_left -= 1
            elif tag == "+":
                cur["rows"].append(_row("add", None, new_no, body))
                cur["added"] += 1
                new_no += 1
                new_left -= 1
            else:
                # Beklenmeyen satır: hunk erken bitmiş kabul edilir.
                in_hunk = False
                continue
            if old_left <= 0 and new_left <= 0:
                in_hunk = False
            i += 1
            continue

        in_hunk = False
        m_git = DIFF_GIT_RE.match(line)
        if m_git:
            close_current()
            cur = _new_file(m_git.group(1), m_git.group(2))
            i += 1
            continue

        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old_p = _clean_path(line[4:])
            new_p = _clean_path(lines[i + 1][4:])
            if cur is None or cur["rows"]:
                close_current()
                cur = _new_file(old_p, new_p)
            else:
                cur["old_path"], cur["new_path"] = old_p, new_p
            i += 2
            continue

        m_h = HUNK_RE.match(line)
        if m_h:
            if cur is None:
                cur = _new_file(None, None)
                cur["old_path"] = cur["new_path"] = "bilinmeyen-dosya"
            old_no = int(m_h.group(1))
            old_left = int(m_h.group(2)) if m_h.group(2) is not None else 1
            new_no = int(m_h.group(3))
            new_left = int(m_h.group(4)) if m_h.group(4) is not None else 1
            cur["rows"].append(_row("hunk", None, None, line))
            in_hunk = old_left > 0 or new_left > 0
            i += 1
            continue

        if cur is not None:
            if line.startswith("new file mode"):
                cur["old_path"] = None
            elif line.startswith("deleted file mode"):
                cur["new_path"] = None
            elif line.startswith("rename from "):
                cur["old_path"] = line[len("rename from "):].strip()
            elif line.startswith("rename to "):
                cur["new_path"] = line[len("rename to "):].strip()
            elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
                cur["binary"] = True
                cur["rows"].append(_row("meta", None, None, "İkili (binary) dosya — içerik analiz edilmedi."))
            i += 1
            continue

        # Hiçbir dosya başlığı olmadan gelen +/- satırları
        if line[:1] in ("+", "-", " ") and not line.startswith(("+++", "---")):
            tag = line[:1]
            kind = {"+": "add", "-": "del", " ": "context"}[tag]
            loose.append(_row(kind, None, None, line[1:]))
        i += 1

    close_current()

    if not files and loose:
        f = _new_file("bilinmeyen-dosya", "bilinmeyen-dosya")
        f["rows"] = loose
        f["line_mapping"] = "unavailable"
        f["added"] = sum(1 for r in loose if r["type"] == "add")
        f["removed"] = sum(1 for r in loose if r["type"] == "del")
        files.append(_finalize(f))

    for f in files:
        if not any(r["type"] == "hunk" for r in f["rows"]):
            if f["line_mapping"] == "exact" and any(r["type"] in ("add", "del") for r in f["rows"]):
                f["line_mapping"] = "unavailable"
    return files


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def has_changes(files: Iterable[dict]) -> bool:
    return any(f["added"] or f["removed"] for f in files)


def condense_rows(rows: list[dict], context: int = 6) -> list[dict | None]:
    """Değişikliklerden uzak bağlam satırlarını atar; atılan bloklar ``None`` ile temsil edilir."""
    changed_idx = [i for i, r in enumerate(rows) if r["type"] in ("add", "del", "hunk", "meta")]
    if not changed_idx:
        return []
    keep = set()
    for i in changed_idx:
        for k in range(max(0, i - context), min(len(rows), i + context + 1)):
            keep.add(k)
    out: list[dict | None] = []
    skipped = False
    for i, r in enumerate(rows):
        if i in keep:
            out.append(r)
            skipped = False
        elif not skipped:
            out.append(None)
            skipped = True
    return out


def render_for_model(files: list[dict], context: int = 6) -> str:
    """Diff'i, satır numaralarını açıkça gösteren ve modele gönderilecek metne dönüştürür."""
    parts: list[str] = []
    for idx, f in enumerate(files, 1):
        parts.append(
            f"=== FILE {idx}: {f['path']} (language: {f['language']}, status: {f['status']}, "
            f"+{f['added']} / -{f['removed']}) ==="
        )
        if f["old_path"] and f["new_path"] and f["old_path"] != f["new_path"]:
            parts.append(f"(renamed from {f['old_path']})")
        if f.get("binary"):
            parts.append("(binary file — content not available)")
            continue
        exact = f["line_mapping"] == "exact"
        if exact:
            parts.append('Columns: OLD line | NEW line | marker ("-" removed, "+" added, " " unchanged) | code')
        else:
            parts.append("LINE NUMBERS UNAVAILABLE for this file. Do NOT report line numbers for it (use null).")
        for r in condense_rows(f["rows"], context):
            if r is None:
                parts.append("      ...  (unchanged lines omitted)")
                continue
            if r["type"] == "hunk":
                parts.append(r["text"])
                continue
            if r["type"] == "meta":
                parts.append(f"# {r['text']}")
                continue
            marker = {"add": "+", "del": "-", "context": " "}[r["type"]]
            if exact:
                o = str(r["old"]) if r["old"] is not None else ""
                n = str(r["new"]) if r["new"] is not None else ""
                parts.append(f"{o:>5} | {n:>5} | {marker} {r['text']}")
            else:
                parts.append(f"{marker} {r['text']}")
        parts.append("")
    return "\n".join(parts)
