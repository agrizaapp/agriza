import re
import unicodedata
from datetime import date, timedelta


UNITS = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
    "quatorze": 14, "catorze": 14, "quinze": 15, "dezesseis": 16,
    "dezessete": 17, "dezoito": 18, "dezenove": 19,
}
TENS = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50,
    "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
}
HUNDREDS = {
    "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200,
    "trezentos": 300, "trezentas": 300, "quatrocentos": 400,
    "quatrocentas": 400, "quinhentos": 500, "quinhentas": 500,
    "seiscentos": 600, "seiscentas": 600, "setecentos": 700,
    "setecentas": 700, "oitocentos": 800, "oitocentas": 800,
    "novecentos": 900, "novecentas": 900,
}
MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).lower()


def _number(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip().replace(" ", "")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    else:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _words_to_number(fragment: str) -> float | None:
    words = [w for w in _plain(fragment).split() if w != "e"]
    if not words:
        return None
    total = 0
    current = 0
    used = False
    for word in words:
        if word in UNITS:
            current += UNITS[word]
            used = True
        elif word in TENS:
            current += TENS[word]
            used = True
        elif word in HUNDREDS:
            current += HUNDREDS[word]
            used = True
        elif word in ("mil", "milhar", "milhares"):
            current = max(current, 1) * 1000
            total += current
            current = 0
            used = True
        elif word in ("milhao", "milhoes"):
            current = max(current, 1) * 1_000_000
            total += current
            current = 0
            used = True
        else:
            return None
    return float(total + current) if used else None


def _extract_money(plain: str) -> float:
    digit_patterns = [
        r"(?:valor(?: total)?|total|por|custou|custa|de)\s+(?:r\$\s*)?"
        r"(\d[\d\.,]*)\s*(mil|milhao|milhoes)?",
        r"(?:r\$\s*)(\d[\d\.,]*)\s*(mil|milhao|milhoes)?",
        r"(\d[\d\.,]*)\s*(mil|milhao|milhoes)?\s*reais?\b",
    ]
    for pattern in digit_patterns:
        match = re.search(pattern, plain)
        if match:
            value = _number(match.group(1)) or 0.0
            scale = match.group(2) or ""
            if scale == "mil":
                value *= 1000
            elif scale in ("milhao", "milhoes"):
                value *= 1_000_000
            return value

    words_pattern = (
        r"(?:valor(?: total)?|total|por|custou|custa|de)\s+"
        r"((?:(?:zero|um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|"
        r"nove|dez|onze|doze|treze|quatorze|catorze|quinze|dezesseis|"
        r"dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta|"
        r"sessenta|setenta|oitenta|noventa|cem|cento|duzentos|duzentas|"
        r"trezentos|trezentas|quatrocentos|quatrocentas|quinhentos|"
        r"quinhentas|seiscentos|seiscentas|setecentos|setecentas|"
        r"oitocentos|oitocentas|novecentos|novecentas|mil|milhao|"
        r"milhoes|e)\s*)+)\s*reais?"
    )
    match = re.search(words_pattern, plain)
    if match:
        return _words_to_number(match.group(1)) or 0.0
    return 0.0


def _detect_category(plain: str) -> str:
    rules = [
        ("Sementes", ["semente", "sementes", "hibrido", "cultivar"]),
        ("Fertilizantes", ["fertilizante", "adubo", "ureia", "map", "dap",
                           "cloreto", "potassio", "fosfato"]),
        ("Defensivos", ["defensivo", "herbicida", "fungicida", "inseticida",
                        "acaricida", "glifosato"]),
        ("Máquinas", ["maquina", "trator", "colheitadeira", "plantadeira",
                      "pulverizador", "implemento"]),
        ("Arrendamento", ["arrendamento", "aluguel de terra", "renda da terra"]),
        ("Custeio", ["custeio", "financiamento", "juros", "seguro"]),
    ]
    for category, words in rules:
        if any(word in plain for word in words):
            return category
    return "Outro"


def _detect_payment_crop(plain: str) -> str:
    patterns = [
        ("Soja", ["pagar com soja", "pago com soja", "pela soja", "safra de soja"]),
        ("Milho", ["pagar com milho", "pago com milho", "pelo milho", "safra de milho"]),
        ("Trigo", ["pagar com trigo", "pago com trigo", "pelo trigo", "safra de trigo"]),
        ("Canola", ["pagar com canola", "pago com canola", "pela canola", "safra de canola"]),
    ]
    for crop, terms in patterns:
        if any(term in plain for term in terms):
            return crop
    if "mais de uma" in plain or "varias culturas" in plain:
        return "Mais de uma"
    return "Caixa"


def _spoken_date(raw: str, plain: str) -> date:
    today = date.today()
    if "depois de amanha" in plain:
        return today + timedelta(days=2)
    if "amanha" in plain:
        return today + timedelta(days=1)
    if "hoje" in plain:
        return today

    days_match = re.search(
        r"(?:vence|vencimento|pagar|prazo)(?:\s+daqui)?\s+(?:em\s+)?"
        r"(\d+)\s+dias", plain
    )
    if days_match:
        return today + timedelta(days=int(days_match.group(1)))

    numeric = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", raw)
    if numeric:
        day, month, year = numeric.groups()
        year = int(year) if year else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, int(month), int(day))
        except ValueError:
            pass

    named = re.search(
        r"\b(?:dia\s+)?(\d{1,2})\s+de\s+"
        r"(janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|"
        r"setembro|outubro|novembro|dezembro)"
        r"(?:\s+de\s+(\d{2,4}))?\b",
        plain,
    )
    if named:
        day, month_name, year = named.groups()
        year = int(year) if year else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, MONTHS[month_name], int(day))
        except ValueError:
            pass
    return today


def _extract_supplier(raw: str) -> str:
    match = re.search(
        r"(?:fornecedor\s+|(?:da|do)\s+(?=cooperativa|empresa|agro|comercial)|"
        r"comprei (?:da|do)\s+|comprado (?:da|do)\s+|adquiri (?:da|do)\s+)"
        r"(.+?)(?=\s+(?:por|valor|total|vence|vencimento|para pagar|"
        r"pagar com|safra|dia|data|observacao|obs)\b|[,.;]|$)",
        raw,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip(" .,-") if match else ""


def _extract_description(raw: str) -> str:
    cleaned = re.sub(
        r"^(?:eu\s+)?(?:comprei|compramos|compra de|adquiri|adquirimos)\s+",
        "", raw, flags=re.IGNORECASE
    )
    cleaned = re.split(
        r"\s+(?:do fornecedor|da empresa|da cooperativa|fornecedor|por|"
        r"valor|total|vence|vencimento|para pagar|pagar com)\b",
        cleaned, maxsplit=1, flags=re.IGNORECASE
    )[0]
    return cleaned.strip(" .,-") or raw.strip()


def parse_spoken_purchase(text: str, seasons: list[dict]) -> dict:
    raw = (text or "").strip()
    plain = _plain(raw)

    selected_label = "Nenhuma"
    best_score = -1
    for season in seasons:
        label = f"{season['name']} · {season['crop']}"
        candidates = [_plain(season["name"]), _plain(season["crop"]), _plain(label)]
        score = max((len(c) for c in candidates if c and c in plain), default=-1)
        if score > best_score:
            best_score = score
            selected_label = label

    result = {
        "description": _extract_description(raw),
        "category": _detect_category(plain),
        "supplier": _extract_supplier(raw),
        "total_value": _extract_money(plain),
        "due_date": _spoken_date(raw, plain),
        "payment_crop": _detect_payment_crop(plain),
        "season_label": selected_label,
        "notes": raw,
    }
    missing = []
    if not result["description"]:
        missing.append("produto")
    if result["total_value"] <= 0:
        missing.append("valor")
    if not result["supplier"]:
        missing.append("fornecedor")
    result["missing"] = missing
    return result
