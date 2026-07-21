"""Leitura acionável de mercado: cruza posição de preço com o preço necessário.

O diferencial do AGRIZA é juntar o cenário externo (onde o preço está no
histórico, para onde aponta) com o cenário interno do produtor (o preço que ele
precisa obter para bater a margem). Este módulo produz essa leitura combinada.

Importante: é apoio à decisão sobre **venda de grão físico**, apresentado como
cenário e raciocínio explícito — não é recomendação personalizada de operação
financeira. A decisão é sempre do produtor.
"""
from __future__ import annotations

from services.market_data.history import prices_only
from services.market_data.indicators import summarize_series

PERCENTIL_ATRATIVO = 70  # preço no topo do histórico recente
PERCENTIL_BARATO = 30    # preço no fundo do histórico recente


def evaluate_market(summary, required_price=None):
    """Transforma indicadores + preço necessário numa leitura de mercado.

    Função pura: recebe o dicionário de ``summarize_series`` e, opcionalmente, o
    preço necessário da safra. Devolve nível, título, mensagem e os fatores que
    sustentam a leitura, para a interface sempre poder mostrar o porquê.
    """
    if not summary or summary.get("count", 0) == 0 or summary.get("current") is None:
        return {
            "level": "sem_dados",
            "headline": "Sem histórico de preço suficiente",
            "message": "Registre cotações desta cultura para o AGRIZA acompanhar a evolução.",
            "factors": [],
            "suggested_sell_pct": None,
        }

    current = summary["current"]
    percentile = summary.get("percentile")
    trend = summary.get("trend", "indefinida")
    factors = _describe(summary, required_price)

    cobre_margem = required_price is not None and required_price > 0 and current >= required_price
    preco_alto = percentile is not None and percentile >= PERCENTIL_ATRATIVO
    preco_baixo = percentile is not None and percentile <= PERCENTIL_BARATO

    # Preço atrativo no histórico e que cobre a margem: janela favorável.
    if preco_alto and (cobre_margem or required_price is None):
        pct = 15 if trend != "alta" else 10
        return {
            "level": "favoravel",
            "headline": "Momento favorável para proteger parte da produção",
            "message": (
                "O preço está na faixa alta do histórico recente"
                + (" e cobre sua margem." if cobre_margem else ".")
                + " Vender parcialmente reduz risco sem abrir mão de todo o potencial."
            ),
            "factors": factors,
            "suggested_sell_pct": pct,
        }

    # Cobre a margem, mas o preço não está no topo: favorável com moderação.
    if cobre_margem:
        return {
            "level": "favoravel",
            "headline": "Preço já cobre sua margem",
            "message": (
                "A cotação atual atinge o preço necessário da safra. "
                + ("Como a tendência é de alta, dá para escalonar as vendas."
                   if trend == "alta"
                   else "Uma venda parcial trava resultado com segurança.")
            ),
            "factors": factors,
            "suggested_sell_pct": 10,
        }

    # Preço barato no histórico: cautela, a não ser sob pressão de caixa (tratada
    # fora daqui, na recomendação que conhece os compromissos descobertos).
    if preco_baixo:
        return {
            "level": "desfavoravel",
            "headline": "Preço na faixa baixa do histórico",
            "message": (
                "O valor atual está entre os mais baixos do período e "
                + ("ainda não atinge seu preço necessário. "
                   if required_price else "")
                + "Sem urgência de caixa, esperar tende a ser melhor."
            ),
            "factors": factors,
            "suggested_sell_pct": 0,
        }

    # Zona intermediária.
    return {
        "level": "cautela",
        "headline": "Cenário intermediário — avance gradualmente",
        "message": (
            "O preço está numa faixa neutra do histórico. "
            + ("A tendência de baixa pede atenção para não perder patamar."
               if trend == "baixa"
               else "Escalonar vendas equilibra proteção e oportunidade.")
        ),
        "factors": factors,
        "suggested_sell_pct": 5,
    }


def _describe(summary, required_price):
    """Monta as frases de justificativa a partir dos indicadores disponíveis."""
    from core.utils import money, num

    factors = [f"Preço atual: {money(summary['current'])}/sc."]
    if summary.get("percentile") is not None:
        factors.append(
            f"Posição no histórico: percentil {num(summary['percentile'], 0)} "
            f"(mín {money(summary['min'])} · máx {money(summary['max'])})."
        )
    if summary.get("sma_short") is not None and summary.get("sma_long") is not None:
        factors.append(
            f"Médias móveis: {money(summary['sma_short'])} (curta) vs "
            f"{money(summary['sma_long'])} (longa)."
        )
    if summary.get("trend") and summary["trend"] != "indefinida":
        factors.append(f"Tendência recente: {summary['trend']}.")
    if summary.get("volatility_pct") is not None:
        factors.append(f"Volatilidade: {num(summary['volatility_pct'], 0)}%.")
    if required_price:
        factors.append(f"Seu preço necessário: {money(required_price)}/sc.")
    return factors


def build_market_view(crop, required_price=None, *, days=180,
                      short_window=7, long_window=30):
    """Visão completa de uma cultura: série resumida + leitura de mercado.

    É o ponto de entrada que a interface usa. Junta ``history`` +
    ``indicators`` + ``evaluate_market`` numa chamada.
    """
    prices = prices_only(crop, days=days)
    summary = summarize_series(prices, short_window=short_window, long_window=long_window)
    signal = evaluate_market(summary, required_price)
    return {"crop": crop, "summary": summary, "signal": signal}
