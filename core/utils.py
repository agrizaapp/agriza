import calendar
from datetime import date, datetime


def money(value):
    value = float(value or 0)
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(value, decimals=1):
    value = float(value or 0)
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def add_months(base_date, months):
    """Avança meses mantendo o dia sempre que ele existir no mês de destino."""
    month_index = base_date.month - 1 + int(months)
    target_year = base_date.year + month_index // 12
    target_month = month_index % 12 + 1
    target_day = min(base_date.day, calendar.monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


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
