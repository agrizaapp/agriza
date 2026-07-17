import re
import unicodedata
from datetime import date, timedelta

from services.voice_purchases import _number, _words_to_number, MONTHS


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _extract_amount(plain: str, unit_pattern: str) -> float:
    digit = re.search(r"(\d[\d\.,]*)\s*(?:mil\s+)?(?:" + unit_pattern + r")\b", plain)
    if digit:
        value = _number(digit.group(1)) or 0.0
        segment = digit.group(0)
        if "mil" in segment:
            value *= 1000
        return value

    word = re.search(
        r"((?:(?:um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|"
        r"dez|onze|doze|treze|quatorze|catorze|quinze|dezesseis|"
        r"dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta|"
        r"sessenta|setenta|oitenta|noventa|cem|cento|duzentos|duzentas|"
        r"trezentos|trezentas|quatrocentos|quatrocentas|quinhentos|"
        r"quinhentas|seiscentos|seiscentas|setecentos|setecentas|"
        r"oitocentos|oitocentas|novecentos|novecentas|mil|e)\s*)+)"
        r"(?:" + unit_pattern + r")\b", plain
    )
    return _words_to_number(word.group(1)) or 0.0 if word else 0.0


def _sale_date(raw: str, plain: str) -> date:
    today = date.today()
    if "anteontem" in plain:
        return today - timedelta(days=2)
    if "ontem" in plain:
        return today - timedelta(days=1)
    if "hoje" in plain:
        return today

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
        r"(?:\s+de\s+(\d{2,4}))?\b", plain
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


def _extract_price(plain: str) -> float:
    patterns = [
        r"(?:preco|valor|a|por)\s+(?:r\$\s*)?(\d[\d\.,]*)"
        r"\s*(?:reais?)?(?:\s+por\s+saca)?",
        r"r\$\s*(\d[\d\.,]*)\s*(?:por\s+saca)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, plain)
        if match:
            value = _number(match.group(1))
            if value is not None:
                return value
    return 0.0


def parse_spoken_sale(text: str, seasons: list[dict]) -> dict:
    raw = (text or "").strip()
    plain = _plain(raw)

    quantity = _extract_amount(plain, r"sacas?|sc")
    price = _extract_price(plain)

    selected_label = None
    best_score = -1
    for season in seasons:
        label = f"{season['name']} · {season['crop']}"
        candidates = [_plain(season["name"]), _plain(season["crop"]), _plain(label)]
        score = max((len(c) for c in candidates if c and c in plain), default=-1)
        if score > best_score:
            best_score = score
            selected_label = label
    if best_score < 0 and seasons:
        selected_label = f"{seasons[0]['name']} · {seasons[0]['crop']}"

    buyer = ""
    buyer_match = re.search(
        r"(?:para|comprador|cooperativa|empresa)\s+(.+?)"
        r"(?=\s+(?:hoje|ontem|anteontem|dia\s+\d|em\s+\d|obs|"
        r"observacao|a\s+r?\$?\s*\d|por\s+r?\$?\s*\d)\b|[,.;]|$)",
        raw, flags=re.IGNORECASE
    )
    if buyer_match:
        buyer = buyer_match.group(1).strip(" .,-")

    result = {
        "season_label": selected_label,
        "quantity": quantity,
        "price": price,
        "buyer": buyer,
        "sale_date": _sale_date(raw, plain),
        "notes": raw,
    }
    missing = []
    if quantity <= 0:
        missing.append("quantidade")
    if price <= 0:
        missing.append("preço")
    if not buyer:
        missing.append("comprador")
    result["missing"] = missing
    return result
