"""Analiz servisi: istem (prompt) hazırlama, Gemma 4 çağrısı ve çıktı doğrulama.

Önemli ilkeler
--------------
* Model çıktısı bir şema ile doğrulanır; eksik bulgular ASLA uydurulmaz.
* Modelin bildirdiği dosya, satır ve kod alıntıları gerçek diff'e karşı
  kontrol edilir. Doğrulanamayan satır numaraları kaldırılır ve işaretlenir.
* Genel risk seviyesi, modelin iddiasına değil bulguların en yüksek
  önem derecesine göre hesaplanır.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .diffing import render_for_model
from .gemma import GemmaClient, GemmaError

SEVERITIES = ["critical", "high", "medium", "low", "info"]
EVIDENCE_TYPES = ["confirmed", "plausible", "unknown"]
CONFIDENCE = ["high", "medium", "low"]
PRIORITIES = ["high", "medium", "low"]
TEST_CATEGORIES = ["regression", "unit", "integration", "edge_case", "security", "manual"]
COMPONENT_KINDS = ["function", "class", "method", "module", "interface", "api", "database", "config", "variable", "other"]

MAX_FINDINGS = 10
MAX_TESTS = 16

# --------------------------------------------------------------------------- #
# JSON şeması (Ollama structured outputs için)
# --------------------------------------------------------------------------- #
ANALYSIS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "no_significant_risk": {"type": "boolean"},
        "affected_components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": COMPONENT_KINDS},
                    "file": {"type": "string"},
                    "impact": {"type": "string"},
                    "evidence_type": {"type": "string", "enum": EVIDENCE_TYPES},
                },
                "required": ["name", "kind", "file", "impact", "evidence_type"],
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "category": {"type": "string"},
                    "evidence_type": {"type": "string", "enum": EVIDENCE_TYPES},
                    "file": {"type": "string"},
                    "line_start": {"type": "integer"},
                    "line_end": {"type": "integer"},
                    "line_side": {"type": "string", "enum": ["new", "old", "none"]},
                    "changed_code": {"type": "string"},
                    "explanation": {"type": "string"},
                    "failure_scenario": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "confidence": {"type": "string", "enum": CONFIDENCE},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "title", "severity", "category", "evidence_type", "file", "line_start", "line_end",
                             "line_side", "changed_code", "explanation", "failure_scenario", "recommendation",
                             "confidence", "assumptions"],
            },
        },
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": TEST_CATEGORIES},
                    "priority": {"type": "string", "enum": PRIORITIES},
                    "finding_id": {"type": "string"},
                    "behavior": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "expected_result": {"type": "string"},
                },
                "required": ["id", "name", "category", "priority", "finding_id", "behavior", "steps", "expected_result"],
            },
        },
        "unknowns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "no_significant_risk", "affected_components", "findings", "tests", "unknowns"],
}

# --------------------------------------------------------------------------- #
# İstemler
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """You are the analysis engine of "CodeDetector", a static code-change risk analyzer.
Your ONLY job: given a code diff, explain what the change could break and how to test it.
You do NOT write new features and you do NOT execute code.

STRICT RULES
1. Analyze ONLY the code shown. Never invent files, functions, callers, dependencies, configs, or test results.
   If something depends on code you cannot see, say so and mark it as "unknown" or "plausible".
2. evidence_type for every finding:
   - "confirmed": directly visible in the diff (e.g. a check was removed, a comparison changed from >= to >).
   - "plausible": a likely consequence that needs verification (e.g. callers may rely on the old behavior).
   - "unknown": impact cannot be established from the given code.
3. severity (do NOT inflate):
   - critical: severe security vulnerability, data loss, or major system failure.
   - high: likely significant regression or breaking behavior.
   - medium: plausible functional bug or compatibility problem.
   - low: minor issue or edge case worth checking.
   - info: noteworthy change without an identified concrete defect.
4. Investigate: changed behavior and boundary conditions (off-by-one, < vs <=), removed or weakened validation,
   null/None/undefined handling, exception handling, changed signatures/return values/data structures,
   authentication/authorization/security-sensitive logic, database queries, API contracts, async/concurrency
   (only if relevant), backward compatibility, and missing test coverage.
5. Line numbers: use ONLY numbers that appear in the listing. For added or unchanged lines use the NEW column
   and set line_side="new"; for removed lines use the OLD column and set line_side="old".
   If the line is unknown or the listing says line numbers are unavailable, use line_start=0, line_end=0,
   line_side="none". Use file="" if a finding is not tied to one file. Use finding_id="" for general tests.
6. changed_code: copy the relevant line(s) EXACTLY from the listing, without the line numbers and without the
   leading "+"/"-"/" " marker.
7. Every finding needs a concrete failure_scenario with specific example input/state and the wrong outcome.
8. tests: derive them from the findings. Each test must be specific to THIS diff (concrete inputs and expected
   values), reference its finding via finding_id (e.g. "F1"), and have 2-5 short steps. Use categories:
   regression, unit, integration, edge_case, security, manual. Never claim a test was run.
9. If you find no meaningful risk, return an empty findings array, set no_significant_risk=true and explain why
   in the summary. You may still list a few sanity tests.
10. Use ids F1, F2, ... for findings and T1, T2, ... for tests. At most {max_findings} findings and {max_tests} tests.
11. Respond with a single JSON object that matches the requested schema. No markdown, no prose outside JSON.
{language_rule}"""

LANGUAGE_RULES = {
    "tr": "12. Write ALL human-readable text values (summary, titles, explanations, scenarios, steps, etc.) in Turkish. "
          "Keep JSON keys, enum values, ids and code snippets exactly as specified (English / original code).",
    "en": "12. Write all human-readable text values in English.",
}

USER_TEMPLATE = """Analyze the following code change.

Developer's description of the intended change:
{description}

Programming language hint: {language}

CHANGE LISTING
{listing}

Return the JSON object now."""

RETRY_MESSAGE = (
    "Your previous answer was not a valid JSON object matching the required schema ({reason}). "
    "Return ONLY the corrected JSON object with the keys: summary, no_significant_risk, affected_components, "
    "findings, tests, unknowns. Do not add any new findings that you did not already identify."
)


class ModelOutputError(Exception):
    def __init__(self, reason: str, excerpt: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.excerpt = excerpt


# --------------------------------------------------------------------------- #
# Ayrıştırma
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> dict:
    """Model metninden JSON nesnesini çıkarır (kod bloğu çitlerini tolere eder)."""
    if not text or not text.strip():
        raise ModelOutputError("boş yanıt")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise ModelOutputError("JSON nesnesi bulunamadı", text[:400])
        try:
            data = json.loads(t[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ModelOutputError(f"geçersiz JSON: {exc.msg}", text[:400])
    if not isinstance(data, dict):
        raise ModelOutputError("kök değer bir JSON nesnesi değil", text[:400])
    if "findings" not in data or not isinstance(data.get("findings"), list):
        raise ModelOutputError("'findings' dizisi eksik", text[:400])
    return data


# --------------------------------------------------------------------------- #
# Normalizasyon ve doğrulama
# --------------------------------------------------------------------------- #
_SEVERITY_ALIASES = {
    "informational": "info", "information": "info", "none": "info", "kritik": "critical", "yüksek": "high",
    "orta": "medium", "düşük": "low", "bilgi": "info", "moderate": "medium", "minor": "low", "major": "high",
}
_CATEGORY_ALIASES = {"edge case": "edge_case", "edge-case": "edge_case", "edgecase": "edge_case",
                     "edge": "edge_case", "boundary": "edge_case", "manual verification": "manual"}


def _enum(value: Any, allowed: list[str], default: str, aliases: dict | None = None) -> str:
    v = str(value or "").strip().lower()
    if aliases and v in aliases:
        v = aliases[v]
    return v if v in allowed else default


def _text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(x) for x in value)
    return str(value).strip()[:limit]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


def _norm_code_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _strip_marker(line: str) -> str:
    # Model alıntıya "+", "-" veya satır numarası sütunları eklediyse temizle
    line = re.sub(r"^\s*\d*\s*\|\s*\d*\s*\|\s?", "", line)
    if line[:1] in ("+", "-") and not line.startswith(("++", "--")):
        line = line[1:]
    return line


def _match_file(name: str | None, files: list[dict]) -> dict | None:
    if not files:
        return None
    if not name:
        return files[0] if len(files) == 1 else None
    n = name.strip().replace("\\", "/").lstrip("./")
    if n[:2] in ("a/", "b/"):
        n = n[2:]
    for f in files:
        for p in (f["path"], f.get("new_path"), f.get("old_path")):
            if p and p.replace("\\", "/") == n:
                return f
    base = n.rsplit("/", 1)[-1]
    matches = [f for f in files if f["path"].replace("\\", "/").rsplit("/", 1)[-1] == base]
    return matches[0] if len(matches) == 1 else None


def _verify_snippet(snippet: str, f: dict | None, files: list[dict]) -> str:
    """Bulgudaki kod alıntısının gerçekten girdide olup olmadığını kontrol eder."""
    candidates = []
    for raw_line in snippet.splitlines():
        a = _norm_code_line(raw_line)
        b = _norm_code_line(_strip_marker(raw_line))
        if b and b != "...":
            candidates.append((a, b))
    if not candidates:
        return "missing"
    pool_files = [f] if f else files
    pool = {_norm_code_line(r["text"]) for pf in pool_files for r in pf["rows"] if r["type"] in ("add", "del", "context")}
    pool.discard("")

    def hit(x: str) -> bool:
        return x in pool or (len(x) >= 8 and any(x in p for p in pool))

    found = sum(1 for a, b in candidates if hit(a) or hit(b))
    if found == len(candidates):
        return "verified"
    if found:
        return "partial"
    return "not_found"


def _verify_lines(fd: dict, f: dict | None) -> None:
    start, end, side = fd["line_start"], fd["line_end"], fd["line_side"]
    fd["line_verified"] = False
    if start is None:
        fd["line_start"] = fd["line_end"] = fd["line_side"] = None
        return
    if f is None or f["line_mapping"] != "exact":
        fd["line_note"] = "Satır numarası kaynak eşlemesiyle doğrulanamadığı için kaldırıldı."
        fd["line_start"] = fd["line_end"] = fd["line_side"] = None
        return
    new_lines = {r["new"] for r in f["rows"] if r["new"] is not None}
    old_lines = {r["old"] for r in f["rows"] if r["old"] is not None}
    if end is None or end < start:
        end = start
    if side not in ("new", "old"):
        side = "new" if start in new_lines else ("old" if start in old_lines else None)
    valid = new_lines if side == "new" else old_lines if side == "old" else set()
    if start in valid and end in valid:
        fd.update(line_start=start, line_end=end, line_side=side, line_verified=True)
    elif start in valid:
        fd.update(line_start=start, line_end=start, line_side=side, line_verified=True)
    else:
        fd["line_note"] = f"Model {start}. satırı bildirdi ancak bu satır diff'te bulunamadı; kaldırıldı."
        fd["line_start"] = fd["line_end"] = fd["line_side"] = None


def normalize_result(raw: dict, files: list[dict]) -> tuple[dict, list[str]]:
    """Model çıktısını güvenli, tutarlı bir yapıya dönüştürür. Yeni içerik uydurmaz."""
    warnings: list[str] = []

    findings: list[dict] = []
    used_ids: set[str] = set()
    id_map: dict[str, str] = {}
    for i, item in enumerate(raw.get("findings") or [], 1):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), 200)
        explanation = _text(item.get("explanation"))
        if not title and not explanation:
            warnings.append(f"Başlığı ve açıklaması olmayan {i}. bulgu atlandı.")
            continue
        orig_id = _text(item.get("id"), 20) or f"F{i}"
        fid = orig_id if re.fullmatch(r"F\d+", orig_id) and orig_id not in used_ids else f"F{len(findings) + 1}"
        while fid in used_ids:
            fid = f"F{len(findings) + len(used_ids) + 1}"
        used_ids.add(fid)
        id_map[orig_id] = fid

        f_match = _match_file(_text(item.get("file"), 500) or None, files)
        reported_file = _text(item.get("file"), 500) or None
        fd = {
            "id": fid,
            "title": title or "(başlıksız bulgu)",
            "severity": _enum(item.get("severity"), SEVERITIES, "medium", _SEVERITY_ALIASES),
            "category": _text(item.get("category"), 60) or "genel",
            "evidence_type": _enum(item.get("evidence_type"), EVIDENCE_TYPES, "plausible"),
            "file": f_match["path"] if f_match else reported_file,
            "file_verified": f_match is not None,
            "line_start": _int_or_none(item.get("line_start")),
            "line_end": _int_or_none(item.get("line_end")),
            "line_side": item.get("line_side") if item.get("line_side") in ("new", "old") else None,
            "changed_code": _text(item.get("changed_code"), 3000),
            "explanation": explanation,
            "failure_scenario": _text(item.get("failure_scenario")),
            "recommendation": _text(item.get("recommendation")),
            "confidence": _enum(item.get("confidence"), CONFIDENCE, "low"),
            "assumptions": [_text(a, 500) for a in (item.get("assumptions") or []) if _text(a, 500)][:8]
            if isinstance(item.get("assumptions"), list) else ([_text(item.get("assumptions"), 500)] if item.get("assumptions") else []),
        }
        if reported_file and not f_match:
            warnings.append(f"{fid}: model girdide olmayan bir dosyaya ('{reported_file}') atıf yaptı.")
        _verify_lines(fd, f_match)
        fd["evidence_check"] = _verify_snippet(fd["changed_code"], f_match, files)
        if fd["evidence_check"] == "not_found":
            warnings.append(f"{fid}: alıntılanan kod girdide bulunamadı; bulguyu dikkatle değerlendirin.")
        findings.append(fd)
        if len(findings) >= MAX_FINDINGS:
            break

    findings.sort(key=lambda x: SEVERITIES.index(x["severity"]))
    valid_ids = {f["id"] for f in findings}

    tests: list[dict] = []
    used_t: set[str] = set()
    for i, item in enumerate(raw.get("tests") or [], 1):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 200)
        if not name:
            continue
        tid = _text(item.get("id"), 20)
        if not re.fullmatch(r"T\d+", tid or "") or tid in used_t:
            tid = f"T{len(tests) + 1}"
            while tid in used_t:
                tid = f"T{int(tid[1:]) + 1}"
        used_t.add(tid)
        ref = _text(item.get("finding_id"), 20) or None
        ref = id_map.get(ref, ref) if ref else None
        if ref and ref not in valid_ids:
            ref = None
        steps = item.get("steps")
        if isinstance(steps, str):
            steps = [s.strip(" -•\t") for s in steps.splitlines() if s.strip()]
        steps = [_text(s, 600) for s in (steps or []) if _text(s, 600)][:10]
        tests.append({
            "id": tid,
            "name": name,
            "category": _enum(item.get("category"), TEST_CATEGORIES, "manual", _CATEGORY_ALIASES),
            "priority": _enum(item.get("priority"), PRIORITIES, "medium"),
            "finding_id": ref,
            "behavior": _text(item.get("behavior"), 1000),
            "steps": steps,
            "expected_result": _text(item.get("expected_result"), 1000),
            "status": "pending",
        })
        if len(tests) >= MAX_TESTS:
            break

    components = []
    for item in raw.get("affected_components") or []:
        if not isinstance(item, dict) or not _text(item.get("name")):
            continue
        f_match = _match_file(_text(item.get("file"), 500) or None, files)
        components.append({
            "name": _text(item.get("name"), 200),
            "kind": _enum(item.get("kind"), COMPONENT_KINDS, "other"),
            "file": f_match["path"] if f_match else (_text(item.get("file"), 500) or None),
            "impact": _text(item.get("impact"), 1000),
            "evidence_type": _enum(item.get("evidence_type"), EVIDENCE_TYPES, "plausible"),
        })
    components = components[:20]

    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    overall = next((s for s in SEVERITIES if counts[s]), "none")
    if overall == "info":
        overall = "none"

    result = {
        "summary": _text(raw.get("summary"), 3000),
        "no_significant_risk": not any(f["severity"] in ("critical", "high", "medium", "low") for f in findings),
        "overall_risk": overall,
        "severity_counts": counts,
        "affected_components": components,
        "findings": findings,
        "tests": tests,
        "unknowns": [_text(u, 600) for u in (raw.get("unknowns") or []) if _text(u, 600)][:10]
        if isinstance(raw.get("unknowns"), list) else [],
    }
    if raw.get("no_significant_risk") is True and result["findings"] and not result["no_significant_risk"]:
        warnings.append("Model 'önemli risk yok' dedi ancak risk içeren bulgular döndürdü; bulgular esas alındı.")
    return result, warnings


# --------------------------------------------------------------------------- #
# Analizi çalıştırma
# --------------------------------------------------------------------------- #
def build_messages(files: list[dict], description: str, language: str, settings) -> tuple[list[dict], str]:
    listing = render_for_model(files)
    if len(listing) > settings.max_model_input_chars:
        # Bağlamı daraltarak tekrar dene
        listing = render_for_model(files, context=2)
    if len(listing) > settings.max_model_input_chars:
        raise GemmaError(
            "input_too_large",
            f"Değişiklik, modele tek seferde gönderilemeyecek kadar büyük ({len(listing)} karakter; sınır "
            f"{settings.max_model_input_chars}).",
            "Diff'i daha küçük parçalara bölün (örn. dosya dosya) veya MAX_MODEL_INPUT_CHARS ve GEMMA_NUM_CTX değerlerini "
            "donanımınız elveriyorsa artırın.",
            413,
        )
    system = SYSTEM_PROMPT.format(
        max_findings=MAX_FINDINGS,
        max_tests=MAX_TESTS,
        language_rule=LANGUAGE_RULES.get(settings.analysis_language, LANGUAGE_RULES["en"]),
    )
    user = USER_TEMPLATE.format(
        description=(description or "").strip()[:2000] or "(not provided)",
        language=language or "auto",
        listing=listing,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], listing


def run_analysis(client: GemmaClient, files: list[dict], description: str, language: str, settings) -> dict:
    """Gemma 4 ile analizi çalıştırır. Hatalı JSON için kontrollü sayıda tekrar dener."""
    status = client.ensure_ready()
    messages, listing = build_messages(files, description, language, settings)

    attempts = 0
    total_ms = 0
    last_err: ModelOutputError | None = None
    raw: dict | None = None
    eval_tokens = None
    while attempts <= settings.gemma_max_retries:
        attempts += 1
        res = client.chat(messages, ANALYSIS_SCHEMA, status.get("runtime_version"))
        total_ms += res.duration_ms
        eval_tokens = res.eval_count
        if res.done_reason == "length":
            last_err = ModelOutputError("yanıt token sınırında kesildi", res.content[:400])
        else:
            try:
                raw = extract_json(res.content)
                break
            except ModelOutputError as exc:
                last_err = exc
        messages = messages[:2] + [
            {"role": "assistant", "content": res.content[:6000]},
            {"role": "user", "content": RETRY_MESSAGE.format(reason=last_err.reason)},
        ]

    if raw is None:
        assert last_err is not None
        hint = "Analizi tekrar çalıştırmayı deneyin."
        if last_err.reason.startswith("yanıt token"):
            hint = "GEMMA_NUM_PREDICT değerini artırın veya daha küçük bir diff gönderin."
        raise GemmaError(
            "model_output_invalid",
            f"Gemma 4 {attempts} denemede geçerli yapılandırılmış çıktı üretemedi ({last_err.reason}).",
            hint,
            502,
            {"attempts": attempts, "excerpt": last_err.excerpt[:300]},
        )

    result, warnings = normalize_result(raw, files)
    return {
        "analysis_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": {
            "name": "Gemma 4",
            "model": settings.gemma_model,
            "runtime": "ollama",
            "runtime_version": status.get("runtime_version"),
            "model_details": status.get("model_details"),
            "is_demo": False,
        },
        "result": result,
        "meta": {
            "duration_ms": total_ms,
            "attempts": attempts,
            "prompt_chars": len(listing),
            "output_tokens": eval_tokens,
            "warnings": warnings,
            "static_analysis_only": True,
        },
    }
