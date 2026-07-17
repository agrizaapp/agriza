import re
import unicodedata
from datetime import date, timedelta


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    ).lower()


def _number(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip().replace(" ", "")
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    else:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _detect_category(plain: str) -> str:
    rules = [
        ("Sementes", ["semente", "sementes", "hibrido", "cultivar"]),
        (
            "Fertilizantes",
            [
                "fertilizante",
                "fertilizantes",
                "adubo",
                "ureia",
                "map",
                "dap",
                "cloreto",
                "potassio",
            ],
        ),
        (
            "Defensivos",
            [
                "defensivo",
                "herbicida",
                "fungicida",
                "inseticida",
                "acaricida",
                "glifosato",
            ],
        ),
        ("Máquinas", ["maquina", "maquinas", "trator", "colheitadeira", "implemento"]),
        ("Arrendamento", ["arrendamento", "aluguel de terra", "renda da terra"]),
        ("Custeio", ["custeio", "financiamento", "juros", "seguro"]),
    ]
    for category, words in rules:
        if any(word in plain for word in words):
            return category
    return "Outro"


def _detect_payment_crop(plain: str) -> str:
    if "mais de uma" in plain or "varias culturas" in plain:
        return "Mais de uma"
    for crop in ["Soja", "Milho", "Trigo", "Canola"]:
        if crop.lower() in plain:
            return crop
    return "Caixa"


def _detect_due_date(raw: str, plain: str) -> date:
    today = date.today()
    if "hoje" in plain:
        return today
    if "amanha" in plain:
        return today + timedelta(days=1)

    days_match = re.search(
        r"(?:vence|vencimento|pagar|prazo)\s+(?:em\s+)?(\d+)\s+dias",
        plain,
    )
    if days_match:
        return today + timedelta(days=int(days_match.group(1)))

    date_match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b",
        raw,
    )
    if date_match:
        day, month, year = date_match.groups()
        year = int(year) if year else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, int(month), int(day))
        except ValueError:
            pass

    return today


def parse_spoken_purchase(text: str, seasons: list[dict]) -> dict:
    """Interpreta uma compra curta em português sem serviço externo."""
    raw = (text or "").strip()
    plain = _plain(raw)

    value_match = re.search(
        r"(?:r\$\s*)?(\d[\d\.,]*)\s*(?:reais|real)\b",
        plain,
    )
    if not value_match:
        value_match = re.search(
            r"(?:valor|total|por)\s+(?:de\s+)?(?:r\$\s*)?"
            r"(\d[\d\.,]*)",
            plain,
        )
    total_value = _number(value_match.group(1)) if value_match else 0.0

    supplier = ""
    supplier_match = re.search(
        r"(?:de|fornecedor|na|no)\s+([A-Za-zÀ-ÿ0-9 .&'-]+?)"
        r"(?=\s+(?:por|valor|total|vence|vencimento|para pagar|pagar com|"
        r"safra|observacao|obs)\b|$)",
        raw,
        flags=re.IGNORECASE,
    )
    if supplier_match:
        supplier = supplier_match.group(1).strip(" .,-")

    selected_label = "Nenhuma"
    best_score = -1
    for season in seasons:
        label = f"{season['name']} · {season['crop']}"
        candidates = [
            _plain(season["name"]),
            _plain(season["crop"]),
            _plain(label),
        ]
        score = max(
            (len(candidate) for candidate in candidates
             if candidate and candidate in plain),
            default=-1,
        )
        if score > best_score:
            best_score = score
            selected_label = label

    description = raw
    description = re.sub(
        r"^(?:comprei|compramos|compra de|adquiri)\s+",
        "",
        description,
        flags=re.IGNORECASE,
    ).strip()

    return {
        "description": description,
        "category": _detect_category(plain),
        "supplier": supplier,
        "total_value": total_value,
        "due_date": _detect_due_date(raw, plain),
        "payment_crop": _detect_payment_crop(plain),
        "season_label": selected_label,
        "notes": raw,
    }
