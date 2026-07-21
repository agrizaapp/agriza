"""Indicadores de preço — funções puras, sem banco e sem rede.

Todas recebem uma lista de preços (float) em ordem cronológica crescente, do
mais antigo para o mais recente, e devolvem números ou ``None`` quando não há
dados suficientes. Manter isto puro é o que torna a inteligência de mercado
testável de forma determinística.
"""
from __future__ import annotations

from statistics import mean, pstdev


def simple_moving_average(prices, window):
    """Média móvel simples das últimas ``window`` observações."""
    if window <= 0 or len(prices) < window:
        return None
    return mean(prices[-window:])


def exponential_moving_average(prices, window):
    """Média móvel exponencial, que dá mais peso aos preços recentes."""
    if window <= 0 or len(prices) < window:
        return None
    k = 2 / (window + 1)
    ema = mean(prices[:window])  # semente: média simples da primeira janela
    for price in prices[window:]:
        ema = price * k + ema * (1 - k)
    return ema


def percentile_position(prices, current=None):
    """Onde ``current`` se posiciona na distribuição histórica, de 0 a 100.

    100 significa que é o maior preço já visto na série; 0, o menor. É a
    resposta para "o preço de hoje está caro ou barato frente ao histórico?".
    """
    if not prices:
        return None
    if current is None:
        current = prices[-1]
    if len(prices) == 1:
        return 50.0
    abaixo = sum(1 for p in prices if p < current)
    iguais = sum(1 for p in prices if p == current)
    # posição média dos empates, para não penalizar o preço atual repetido
    return round((abaixo + iguais / 2) / len(prices) * 100, 1)


def volatility_pct(prices):
    """Coeficiente de variação (%): desvio-padrão sobre a média."""
    if len(prices) < 2:
        return None
    media = mean(prices)
    if media == 0:
        return None
    return round(pstdev(prices) / media * 100, 1)


def trend(prices, short=7, long=30):
    """Classifica a tendência comparando uma média curta com uma longa.

    Devolve ``'alta'``, ``'baixa'`` ou ``'estável'``. Usa o que houver quando a
    série é curta demais para a janela longa, para dar sinal desde cedo.
    """
    if len(prices) < 2:
        return "indefinida"
    janela_longa = min(long, len(prices))
    janela_curta = min(short, janela_longa)
    media_curta = mean(prices[-janela_curta:])
    media_longa = mean(prices[-janela_longa:])
    if media_longa == 0:
        return "indefinida"
    variacao = (media_curta - media_longa) / media_longa * 100
    if variacao >= 1.5:
        return "alta"
    if variacao <= -1.5:
        return "baixa"
    return "estável"


def change_pct(prices, periods=1):
    """Variação percentual entre o preço atual e o de ``periods`` observações atrás."""
    if len(prices) <= periods:
        return None
    anterior = prices[-1 - periods]
    if anterior == 0:
        return None
    return round((prices[-1] - anterior) / anterior * 100, 1)


def summarize_series(prices, *, short_window=7, long_window=30):
    """Consolida os indicadores de uma série num único dicionário.

    Campos numéricos vêm como ``None`` quando faltam dados, para a interface
    decidir o que exibir. Nunca lança exceção por série curta ou vazia.
    """
    prices = [float(p) for p in prices if p is not None]
    n = len(prices)
    if n == 0:
        return {
            "count": 0,
            "current": None,
            "min": None,
            "max": None,
            "average": None,
            "sma_short": None,
            "sma_long": None,
            "ema_short": None,
            "percentile": None,
            "volatility_pct": None,
            "trend": "indefinida",
            "change_pct": None,
        }
    return {
        "count": n,
        "current": prices[-1],
        "min": min(prices),
        "max": max(prices),
        "average": round(mean(prices), 2),
        "sma_short": simple_moving_average(prices, short_window),
        "sma_long": simple_moving_average(prices, long_window),
        "ema_short": exponential_moving_average(prices, short_window),
        "percentile": percentile_position(prices),
        "volatility_pct": volatility_pct(prices),
        "trend": trend(prices, short_window, long_window),
        "change_pct": change_pct(prices, 1),
    }
