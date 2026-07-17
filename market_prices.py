import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from core.database import q, insert_id

UGGERI_URL = "https://grupouggeri.com.br/"
CROPS = ["Soja", "Milho", "Trigo", "Canola"]


def _to_float(value):
    value = str(value).replace("R$", "").strip()
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    else:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def fetch_uggeri_quotes(timeout=12):
    response = requests.get(
        UGGERI_URL,
        timeout=timeout,
        headers={"User-Agent": "AGRIZA/2.0"},
    )
    response.raise_for_status()
    text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    found = {}
    for crop in CROPS:
        match = re.search(
            rf"{crop}\s*(?:R\$)?\s*([0-9]+(?:[.,][0-9]{{1,2}})?)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            price = _to_float(match.group(1))
            if price and price > 0:
                found[crop] = price
    return found


def update_regional_quotes(user_id=None):
    result = {"updated": [], "errors": [], "checked_at": datetime.now()}
    try:
        quotes = fetch_uggeri_quotes()
        if not quotes:
            result["errors"].append(
                "Grupo Uggeri respondeu, mas não foi possível identificar os preços."
            )
        for crop, price in quotes.items():
            quote_id = insert_id(
                """INSERT INTO quotes
                   (crop,price_sc,source,quoted_at,created_by,region,quote_type,source_url)
                   VALUES(:c,:p,'Grupo Uggeri',CURRENT_TIMESTAMP,:u,
                          'Santo Ângelo/RS','automática',:url)""",
                {"c": crop, "p": price, "u": user_id, "url": UGGERI_URL},
            )
            result["updated"].append(
                {"id": quote_id, "crop": crop, "price": price}
            )
    except Exception as error:
        result["errors"].append(f"Grupo Uggeri: {error}")
    return result


def latest_quote_for_crop(crop):
    rows = q(
        """SELECT * FROM quotes
           WHERE lower(crop)=lower(:crop)
           ORDER BY quoted_at DESC,id DESC
           LIMIT 1""",
        {"crop": crop},
    )
    return rows[0] if rows else None
