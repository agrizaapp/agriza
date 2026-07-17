def money(value):
    value = float(value or 0)
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(value, decimals=1):
    value = float(value or 0)
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


