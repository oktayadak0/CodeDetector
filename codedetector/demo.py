"""Açıkça etiketlenmiş DEMO senaryosu.

Buradaki bulgular Gemma 4 tarafından ÜRETİLMEMİŞTİR. Yalnızca model
çalışma ortamı kullanılamadığında sunum/geliştirme amacıyla, örnek senaryo
için gösterilir ve arayüzde "DEMO — Gemma 4 analizi değil" olarak işaretlenir.
Satır numaraları sabit yazılmaz; örnek koddaki gerçek satırlardan hesaplanır.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .analysis import normalize_result

SAMPLE_FILENAME = "users/validation.py"

SAMPLE_DESCRIPTION = (
    "Kayıt doğrulama fonksiyonunu sadeleştirdim: kullanıcı adı kontrolünü kısalttım ve yaş kontrolünü "
    "string olarak gelen yaşları da kabul edecek şekilde güncelledim."
)

SAMPLE_OLD = '''MIN_AGE = 18
MAX_USERNAME_LEN = 20


def validate_registration(data: dict) -> list[str]:
    """Yeni kullanıcı kayıt verisini doğrular ve hata listesini döndürür."""
    errors = []

    username = (data.get("username") or "").strip()
    if not username:
        errors.append("username is required")
    elif len(username) > MAX_USERNAME_LEN:
        errors.append("username is too long")

    age = data.get("age")
    if age is None:
        errors.append("age is required")
    elif not isinstance(age, int):
        errors.append("age must be an integer")
    elif age < MIN_AGE:
        errors.append("user must be at least 18")

    email = data.get("email", "")
    if "@" not in email:
        errors.append("email is invalid")

    return errors
'''

SAMPLE_NEW = '''MIN_AGE = 18
MAX_USERNAME_LEN = 20


def validate_registration(data: dict) -> list[str]:
    """Yeni kullanıcı kayıt verisini doğrular ve hata listesini döndürür."""
    errors = []

    username = data.get("username").strip()
    if len(username) > MAX_USERNAME_LEN:
        errors.append("username is too long")

    age = data.get("age")
    if age is None:
        errors.append("age is required")
    elif int(age) <= MIN_AGE:
        errors.append("user must be at least 18")

    email = data.get("email", "")
    if "@" not in email:
        errors.append("email is invalid")

    return errors
'''


def _line_of(code: str, needle: str) -> int:
    for i, line in enumerate(code.splitlines(), 1):
        if needle in line:
            return i
    return 0  # bulunamazsa satır bildirilmez (normalize_result kaldırır)


def sample_input() -> dict:
    return {
        "filename": SAMPLE_FILENAME,
        "language": "python",
        "description": SAMPLE_DESCRIPTION,
        "old_code": SAMPLE_OLD,
        "new_code": SAMPLE_NEW,
    }


def _raw_demo_result() -> dict:
    age_line = _line_of(SAMPLE_NEW, "elif int(age) <= MIN_AGE:")
    user_line = _line_of(SAMPLE_NEW, 'username = data.get("username").strip()')
    len_line = _line_of(SAMPLE_NEW, "if len(username) > MAX_USERNAME_LEN:")
    old_required = _line_of(SAMPLE_OLD, "if not username:")
    old_isint = _line_of(SAMPLE_OLD, "elif not isinstance(age, int):")
    return {
        "summary": (
            "Değişiklik validate_registration fonksiyonunun davranışını üç noktada değiştiriyor: yaş sınırı "
            "karşılaştırması '<' yerine '<=' oldu, kullanıcı adı zorunluluk kontrolü kaldırıldı ve tip kontrolü "
            "yerine int() dönüşümü eklendi. Bu, 18 yaşındaki kullanıcıların reddedilmesine ve eksik/geçersiz "
            "girdilerde doğrulama hatası yerine istisna fırlatılmasına yol açabilir."
        ),
        "no_significant_risk": False,
        "affected_components": [
            {"name": "validate_registration", "kind": "function", "file": SAMPLE_FILENAME,
             "impact": "Kayıt doğrulama kuralları ve hata davranışı değişti.", "evidence_type": "confirmed"},
            {"name": "validate_registration çağıranlar (görünmüyor)", "kind": "other", "file": "",
             "impact": "Fonksiyonun her zaman liste döndüreceğini varsayan çağıranlar istisna ile karşılaşabilir.",
             "evidence_type": "unknown"},
        ],
        "findings": [
            {
                "id": "F1", "title": "Yaş sınırında off-by-one: 18 yaşındaki kullanıcılar reddediliyor",
                "severity": "high", "category": "boundary", "evidence_type": "confirmed",
                "file": SAMPLE_FILENAME, "line_start": age_line, "line_end": age_line + 1, "line_side": "new",
                "changed_code": "elif int(age) <= MIN_AGE:\n    errors.append(\"user must be at least 18\")",
                "explanation": "Eski kod 'age < MIN_AGE' ile 18 yaşını kabul ediyordu. Yeni kod '<=' kullandığı için "
                               "tam 18 yaşındaki kullanıcı da hata alıyor; hata mesajı ise 'en az 18' demeye devam ediyor.",
                "failure_scenario": "data = {\"username\": \"ayse\", \"age\": 18, \"email\": \"test@example.com\"} için eski sürüm "
                                    "[] döndürürken yeni sürüm [\"user must be at least 18\"] döndürür; kayıt reddedilir.",
                "recommendation": "Karşılaştırmayı 'int(age) < MIN_AGE' olarak düzeltin ve 17/18/19 sınır değerleri için test ekleyin.",
                "confidence": "high", "assumptions": ["İş kuralı 18 yaşın kabul edilmesi yönünde (hata mesajı bunu ima ediyor)."],
            },
            {
                "id": "F2", "title": "Eksik kullanıcı adı artık AttributeError fırlatıyor",
                "severity": "high", "category": "null-handling", "evidence_type": "confirmed",
                "file": SAMPLE_FILENAME, "line_start": user_line, "line_end": user_line, "line_side": "new",
                "changed_code": "username = data.get(\"username\").strip()",
                "explanation": "Eski kod '(data.get(\"username\") or \"\")' ile None değerini güvenle ele alıyordu. "
                               "Yeni kodda anahtar yoksa veya None ise .strip() çağrısı AttributeError fırlatır.",
                "failure_scenario": "data = {\"age\": 25, \"email\": \"test@example.com\"} gönderildiğinde fonksiyon hata listesi "
                                    "döndürmek yerine 'NoneType' object has no attribute 'strip' istisnası fırlatır; bir web "
                                    "API'sinde bu 400 yerine 500 yanıtına dönüşebilir.",
                "recommendation": "None durumunu yeniden ele alın: (data.get(\"username\") or \"\").strip().",
                "confidence": "high", "assumptions": ["Çağıran kodun istisnayı yakalayıp yakalamadığı görünmüyor."],
            },
            {
                "id": "F3", "title": "Boş kullanıcı adı zorunluluk kontrolü kaldırıldı",
                "severity": "medium", "category": "validation", "evidence_type": "confirmed",
                "file": SAMPLE_FILENAME, "line_start": old_required, "line_end": old_required + 1, "line_side": "old",
                "changed_code": "if not username:\n    errors.append(\"username is required\")",
                "explanation": "'username is required' kontrolü silindi. Yalnızca boşluk içeren veya boş kullanıcı adları "
                               "artık uzunluk kontrolünden geçiyor ve hata üretmiyor.",
                "failure_scenario": "data = {\"username\": \"   \", \"age\": 25, \"email\": \"test@example.com\"} için yeni sürüm [] "
                                    "döndürür ve boş kullanıcı adıyla kayıt oluşturulabilir.",
                "recommendation": "Boş kullanıcı adı kontrolünü geri ekleyin veya kaldırılmasının bilinçli olduğunu doğrulayın.",
                "confidence": "high", "assumptions": [],
            },
            {
                "id": "F4", "title": "Tip kontrolü yerine int(): sayısal olmayan yaş ValueError fırlatıyor",
                "severity": "medium", "category": "exception-handling", "evidence_type": "confirmed",
                "file": SAMPLE_FILENAME, "line_start": age_line, "line_end": age_line, "line_side": "new",
                "changed_code": "elif int(age) <= MIN_AGE:",
                "explanation": "isinstance kontrolü kaldırıldı ve yerine int(age) geldi. 'abc' gibi değerler ValueError, "
                               "liste/sözlük TypeError fırlatır. Ayrıca 17.9 gibi ondalıklar sessizce 17'ye yuvarlanır ve "
                               "True değeri 1 olarak yorumlanır.",
                "failure_scenario": "data = {\"username\": \"ali\", \"age\": \"on sekiz\", \"email\": \"test@example.com\"} için "
                                    "'age must be an integer' hatası yerine ValueError istisnası oluşur.",
                "recommendation": "int() dönüşümünü try/except ile sarın ve dönüşemeyen değerler için doğrulama hatası ekleyin.",
                "confidence": "high", "assumptions": [],
            },
            {
                "id": "F5", "title": "Çağıranlar eski hata mesajına bağımlı olabilir",
                "severity": "low", "category": "api-contract", "evidence_type": "unknown",
                "file": SAMPLE_FILENAME, "line_start": old_isint, "line_end": old_isint + 1, "line_side": "old",
                "changed_code": "elif not isinstance(age, int):\n    errors.append(\"age must be an integer\")",
                "explanation": "'age must be an integer' hata mesajı artık hiç üretilmiyor. Bu mesajı kontrol eden istemci "
                               "veya testler varsa davranış değişir; çağıran kod diff'te görünmediği için etki belirsiz.",
                "failure_scenario": "Ön yüz 'age must be an integer' mesajına göre alanı işaretliyorsa kullanıcı artık "
                                    "anlamlı bir hata görmez.",
                "recommendation": "Kod tabanında bu hata mesajının kullanımlarını arayın.",
                "confidence": "low", "assumptions": ["Mesajın başka yerde kullanıldığı doğrulanmadı."],
            },
        ],
        "tests": [
            {"id": "T1", "name": "18 yaş sınır değeri kabul edilmeli", "category": "regression", "priority": "high",
             "finding_id": "F1", "behavior": "Tam 18 yaşındaki kullanıcı geçerli sayılır.",
             "steps": ["validate_registration({\"username\": \"ayse\", \"age\": 18, \"email\": \"test@example.com\"}) çağırın.",
                       "Dönen listeyi inceleyin."],
             "expected_result": "[] (boş liste) döner."},
            {"id": "T2", "name": "17 / 19 yaş sınır komşuları", "category": "edge_case", "priority": "high",
             "finding_id": "F1", "behavior": "Sınırın iki yanı doğru sınıflandırılır.",
             "steps": ["age=17 ile çağırın.", "age=19 ile çağırın."],
             "expected_result": "17 için [\"user must be at least 18\"], 19 için [] döner."},
            {"id": "T3", "name": "username anahtarı eksik", "category": "unit", "priority": "high", "finding_id": "F2",
             "behavior": "Eksik kullanıcı adı istisna yerine doğrulama hatası üretir.",
             "steps": ["validate_registration({\"age\": 25, \"email\": \"test@example.com\"}) çağırın.",
                       "İstisna fırlatılıp fırlatılmadığını kontrol edin."],
             "expected_result": "İstisna oluşmaz; liste \"username is required\" içerir."},
            {"id": "T4", "name": "Yalnızca boşluk içeren kullanıcı adı", "category": "edge_case", "priority": "medium",
             "finding_id": "F3", "behavior": "Boş kullanıcı adı reddedilir.",
             "steps": ["username=\"   \", age=25, email=\"test@example.com\" ile çağırın."],
             "expected_result": "Liste \"username is required\" içerir."},
            {"id": "T5", "name": "Sayısal olmayan yaş", "category": "unit", "priority": "medium", "finding_id": "F4",
             "behavior": "Geçersiz yaş istisna yerine doğrulama hatası üretir.",
             "steps": ["age=\"on sekiz\" ile çağırın.", "age=17.9 ile çağırın.", "age=True ile çağırın."],
             "expected_result": "Hiçbiri istisna fırlatmaz; her biri yaşla ilgili bir doğrulama hatası döndürür."},
            {"id": "T6", "name": "Kayıt API'si uçtan uca hata kodu", "category": "integration", "priority": "medium",
             "finding_id": "F2", "behavior": "Eksik alanlı kayıt isteği 4xx döner, 500 dönmez.",
             "steps": ["Kayıt uç noktasına username alanı olmayan bir JSON gönderin.", "HTTP durum kodunu kontrol edin."],
             "expected_result": "400/422 ve anlamlı hata mesajı; 500 değil."},
            {"id": "T7", "name": "Hata mesajı kullanımlarını ara", "category": "manual", "priority": "low",
             "finding_id": "F5", "behavior": "Kaldırılan mesaja bağımlı kod olup olmadığı belirlenir.",
             "steps": ["Depoda \"age must be an integer\" metnini arayın.", "Bulunan yerlerin yeni davranışla uyumunu inceleyin."],
             "expected_result": "Bağımlılık yoksa not edin; varsa ilgili istemci/testi güncelleyin."},
        ],
        "unknowns": [
            "validate_registration fonksiyonunu çağıran kod diff'te yer almıyor; istisnaların nasıl ele alındığı bilinmiyor.",
            "İş kuralının 18 yaşı kabul edip etmediği yalnızca hata mesajından çıkarılıyor.",
        ],
    }


def demo_analysis(files: list[dict]) -> dict:
    result, warnings = normalize_result(_raw_demo_result(), files)
    return {
        "analysis_id": "demo-" + uuid.uuid4().hex[:8],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": {
            "name": "DEMO (Gemma 4 değil)",
            "model": None,
            "runtime": None,
            "is_demo": True,
            "notice": "Bu sonuçlar örnek senaryo için önceden hazırlanmış sabit DEMO verisidir; Gemma 4 tarafından "
                      "üretilmemiştir.",
        },
        "result": result,
        "meta": {"duration_ms": 0, "attempts": 0, "prompt_chars": 0, "warnings": warnings, "static_analysis_only": True},
    }
