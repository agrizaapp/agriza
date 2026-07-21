"""Camada de inteligência de mercado do AGRIZA.

Separa três responsabilidades:

- ``indicators``: cálculo puro (médias móveis, posição no histórico, tendência,
  volatilidade). Não toca no banco nem na rede — é onde moram as regras.
- ``history``: leitura da série de preços já armazenada em ``quotes``.
- ``sources``: conectores plugáveis de fontes externas. As fontes são as
  acordadas com o proprietário: dados públicos (USDA, CONAB), indicador CEPEA
  com atribuição e cotação regional de balcão. **Nunca** raspar B3/CME/CBOT,
  cujo dado é licenciado.
- ``signals``: combina a posição de mercado com o preço necessário do produtor
  para produzir uma leitura acionável.
"""

from services.market_data.history import (
    distinct_crops,
    price_series,
    record_quote,
)
from services.market_data.indicators import summarize_series
from services.market_data.signals import build_market_view, evaluate_market

__all__ = [
    "distinct_crops",
    "price_series",
    "record_quote",
    "summarize_series",
    "build_market_view",
    "evaluate_market",
]
