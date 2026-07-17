import re
import unicodedata
from datetime import date, datetime, timedelta


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


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


def parse_spoken_sale(text: str, seasons: list[dict]) -> dict:
    """Interpreta uma frase curta em português sem usar serviço externo."""
    raw = (text or "").strip()
    plain = _plain(raw)

    quantity_match = re.search(
        r"(\d[\d\.,]*)\s*(?:sacas?|sc)\b",
        plain,
    )
    quantity = _number(quantity_match.group(1)) if quantity_match else None

    price_match = re.search(
        r"(?:a|por|preco(?:\s+de)?|valor(?:\s+de)?)\s*"
        r"(?:r\$\s*)?(\d[\d\.,]*)\s*(?:reais?)?",
        plain,
    )
    price = _number(price_match.group(1)) if price_match else None

    sale_date = date.today()
    if "ontem" in plain:
        sale_date = date.today() - timedelta(days=1)
    else:
        date_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", plain)
        if date_match:
            day, month, year = date_match.groups()
            year = int(year) if year else date.today().year
            if year < 100:
                year += 2000
            try:
                sale_date = date(year, int(month), int(day))
            except ValueError:
                pass

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
        r"(?:para|comprador|cooperativa)\s+(.+?)"
        r"(?=\s+(?:hoje|ontem|dia\s+\d|obs|observacao)\b|$)",
        raw,
        flags=re.IGNORECASE,
    )
    if buyer_match:
        buyer = buyer_match.group(1).strip(" .,-")

    return {
        "season_label": selected_label,
        "quantity": quantity or 0.0,
        "price": price or 0.0,
        "buyer": buyer,
        "sale_date": sale_date,
        "notes": raw,
    }
