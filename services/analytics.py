from datetime import date
from core.database import q, scalar
from core.utils import money, num

def season_summary(season):
    production = float(season["area_ha"]) * float(season["yield_sc_ha"])
    total_cost = float(season["area_ha"]) * float(season["cost_ha"])

    sales = q(
        "SELECT quantity_sc,price_sc FROM sales WHERE season_id=:id",
        {"id": season["id"]},
    )
    sold = sum(float(item["quantity_sc"]) for item in sales)
    revenue = sum(float(item["quantity_sc"]) * float(item["price_sc"]) for item in sales)
    balance = max(production - sold, 0)
    average = revenue / sold if sold else 0
    cost_per_sc = total_cost / production if production else 0
    target_revenue = total_cost * (1 + float(season["margin_pct"]) / 100)
    required_price = max(target_revenue - revenue, 0) / balance if balance else 0

    return {
        "production": production,
        "total_cost": total_cost,
        "sold": sold,
        "sold_pct": sold / production * 100 if production else 0,
        "revenue": revenue,
        "balance": balance,
        "average": average,
        "cost_per_sc": cost_per_sc,
        "required_price": required_price,
    }


def commitment_status(commitment_id):
    row = q("SELECT total_value FROM commitments WHERE id=:id", {"id": commitment_id})
    if not row:
        return {"value": 0, "protected": 0, "paid": 0, "covered": 0, "pct": 0, "remaining": 0}

    value = float(row[0]["total_value"])
    protected = float(
        scalar(
            "SELECT COALESCE(SUM(quantity_sc*price_sc),0) FROM sales WHERE commitment_id=:id",
            {"id": commitment_id},
        ) or 0
    )
    paid = float(
        scalar(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE commitment_id=:id",
            {"id": commitment_id},
        ) or 0
    )
    covered = min(value, protected + paid)
    return {
        "value": value,
        "protected": protected,
        "paid": paid,
        "covered": covered,
        "pct": covered / value * 100 if value else 0,
        "remaining": max(value - covered, 0),
    }


def agroia_recommendation(season):
    summary = season_summary(season)
    quote_rows = q(
        """SELECT price_sc,source,quoted_at FROM quotes
           WHERE crop=:crop ORDER BY quoted_at DESC LIMIT 1""",
        {"crop": season["crop"]},
    )

    commitments = q(
        """SELECT id FROM commitments
           WHERE season_id=:id AND COALESCE(status,'aberto')='aberto'""",
        {"id": season["id"]},
    )
    uncovered = sum(commitment_status(item["id"])["remaining"] for item in commitments)

    if not quote_rows:
        return {
            "level": "warning",
            "title": "Atualize a cotação antes de decidir",
            "message": "O AgroIA precisa da cotação atual para comparar com seu preço necessário.",
            "details": ["Registre a cotação na tela Mercado."],
        }

    quote = float(quote_rows[0]["price_sc"])
    required = summary["required_price"]
    sold_pct = summary["sold_pct"]
    details = [
        f"Cotação atual: {money(quote)}/sc.",
        f"Preço necessário no saldo: {money(required)}/sc.",
        f"Produção já vendida: {num(sold_pct)}%.",
        f"Compromissos ainda descobertos: {money(uncovered)}.",
    ]

    if quote >= required and sold_pct < 40:
        pct = 10 if uncovered > 0 else 5
        return {
            "level": "positive",
            "title": f"Consideraria vender aproximadamente {pct}% da produção",
            "message": "A cotação cobre a margem cadastrada e a venda parcial reduz risco.",
            "details": details,
        }

    if quote < required and uncovered == 0:
        return {
            "level": "warning",
            "title": "Eu aguardaria novas condições de mercado",
            "message": "Não há pressão financeira cadastrada que justifique vender abaixo da meta.",
            "details": details,
        }

    if quote < required and uncovered > 0:
        return {
            "level": "danger",
            "title": "Evite venda ampla; avalie somente o necessário para o caixa",
            "message": "Existem compromissos descobertos, mas o preço atual ainda não alcança sua meta.",
            "details": details,
        }

    return {
        "level": "warning",
        "title": "Mantenha uma estratégia gradual",
        "message": "Sua posição atual pede equilíbrio entre proteção e oportunidade.",
        "details": details,
    }


