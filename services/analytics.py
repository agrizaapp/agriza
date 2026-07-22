from datetime import date
from core.database import q, scalar
from core.utils import money, num

def season_summary(season):
    estimated_production = float(season["area_ha"]) * float(season["yield_sc_ha"])
    actual_value = season.get("actual_production_sc")
    actual_production = float(actual_value) if actual_value not in (None, "") else None
    production = actual_production if actual_production is not None else estimated_production
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

    variance_sc = (
        actual_production - estimated_production
        if actual_production is not None
        else None
    )
    variance_pct = (
        variance_sc / estimated_production * 100
        if variance_sc is not None and estimated_production
        else None
    )
    actual_yield_sc_ha = (
        actual_production / float(season["area_ha"])
        if actual_production is not None and float(season["area_ha"])
        else None
    )

    return {
        "production": production,
        "estimated_production": estimated_production,
        "actual_production": actual_production,
        "actual_yield_sc_ha": actual_yield_sc_ha,
        "variance_sc": variance_sc,
        "variance_pct": variance_pct,
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


def commitment_statuses():
    """Carrega a situação de todos os compromissos em uma única consulta."""
    rows = q(
        """SELECT c.id,c.total_value,
                  COALESCE((SELECT SUM(s.quantity_sc*s.price_sc)
                            FROM sales s WHERE s.commitment_id=c.id),0) AS protected,
                  COALESCE((SELECT SUM(p.amount)
                            FROM payments p WHERE p.commitment_id=c.id),0) AS paid
           FROM commitments c"""
    )
    statuses = {}
    for row in rows:
        value = float(row.get("total_value") or 0)
        protected = float(row.get("protected") or 0)
        paid = float(row.get("paid") or 0)
        covered = min(value, protected + paid)
        statuses[row["id"]] = {
            "value": value,
            "protected": protected,
            "paid": paid,
            "covered": covered,
            "pct": covered / value * 100 if value else 0,
            "remaining": max(value - covered, 0),
        }
    return statuses


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
    status_map = commitment_statuses()
    uncovered = sum(
        status_map.get(item["id"], {"remaining": 0})["remaining"]
        for item in commitments
    )

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

    # Cenário externo: onde o preço está no histórico e para onde aponta.
    # Enriquece a leitura sem substituir a lógica de pressão de caixa acima.
    market = _market_context(season["crop"], required)
    if market:
        details.append(f"Cenário de mercado: {market['headline'].lower()}.")
        # A leitura de mercado repete cotação e preço necessário, porque no
        # painel de Mercado ela aparece sozinha. Aqui esses dois já constam
        # acima, e com os motivos visíveis no banner a repetição incomoda.
        details.extend(
            fator for fator in market["factors"][1:]
            if "necessário" not in fator.lower()
        )

    # Fundamento de oferta: entra como razão explícita, não como gatilho de
    # decisão. O nível continua ancorado em preço e pressão de caixa, que são
    # os dados do próprio produtor — o fundamento explica o pano de fundo.
    fundamento = _fundamento_de_oferta(season["crop"])
    if fundamento:
        details.append(
            f"Safra americana {fundamento['ano']}: produtividade "
            f"{fundamento['leitura']} ({fundamento['variacao_pct']:+.1f}% "
            f"frente à média de {fundamento['anos_considerados'] - 1} anos), "
            f"o que {fundamento['efeito']}."
        )

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


def _market_context(crop, required_price):
    """Leitura de mercado da cultura, tolerante a ausência de série ou do módulo.

    Nunca deixa a recomendação quebrar: se não houver histórico suficiente ou o
    módulo não estiver disponível, devolve ``None`` e a recomendação segue só
    com o cenário interno.
    """
    try:
        from services.market_data import build_market_view
    except Exception:
        return None
    try:
        view = build_market_view(crop, required_price)
    except Exception:
        return None
    signal = view["signal"]
    if signal["level"] == "sem_dados" or view["summary"]["count"] < 3:
        return None
    return signal


def _fundamento_de_oferta(crop):
    """Leitura de oferta da cultura (safra americana), ou ``None``.

    Assim como o contexto de mercado, nunca derruba a recomendação: sem dados,
    sem módulo ou com erro, devolve ``None`` e o resto segue.
    """
    try:
        from services.market_data.fundamentals import COMMODITY_POR_CULTURA
        from services.market_data.fundamentals_store import leitura_de_oferta
    except Exception:
        return None
    commodity = COMMODITY_POR_CULTURA.get(crop)
    if not commodity:
        return None
    try:
        return leitura_de_oferta(commodity, "YIELD")
    except Exception:
        return None

