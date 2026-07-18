from datetime import date, datetime


def money(value):
    value = float(value or 0)
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(value, decimals=1):
    value = float(value or 0)
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def br_date(value, empty="Não informada"):
    """Exibe datas do banco sempre no padrão brasileiro DD/MM/AAAA."""
    if value in (None, ""):
        return empty
    if isinstance(value, (datetime, date)):
        return value.strftime("%d/%m/%Y")
    try:
        return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except ValueError:
        return str(value)

