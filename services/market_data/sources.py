"""Conectores de fontes externas de preço — interface plugável.

Cada fonte implementa ``fetch()`` e devolve uma lista de pontos de preço no
formato comum. O registro permite que a interface liste o que está disponível e
dispare a coleta sem conhecer os detalhes de cada fonte.

Fontes acordadas com o proprietário:

- **Regional (Grupo Uggeri)**: cotação de balcão pública — já implementada.
- **CEPEA/ESALQ**: indicador de referência brasileiro, uso com atribuição.
- **CONAB**: dados públicos de preços e safra (Brasil).
- **USDA**: dados públicos dos EUA (produtividade/oferta mundial), via API oficial.

Fontes **proibidas** sem licença, nunca raspar: B3, CME, CBOT. O dado de bolsa é
propriedade licenciada. Conectores para elas só devem existir consumindo um
provedor licenciado, com credencial configurada — não por scraping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(frozen=True)
class PricePoint:
    crop: str
    price: float
    source: str
    region: Optional[str] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class FetchResult:
    source_key: str
    points: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def ok(self):
        return bool(self.points) and not self.errors


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    kind: str            # 'regional' | 'indicador' | 'publico' | 'licenciado'
    country: str
    fetch: Callable[[], FetchResult]
    available: bool
    note: str = ""


def _uggeri_fetch():
    """Adapta o coletor regional existente ao formato comum."""
    try:
        from market_prices import UGGERI_URL, fetch_uggeri_quotes
    except Exception as error:  # módulo ausente em algum ambiente
        return FetchResult("uggeri", errors=[f"Coletor regional indisponível: {error}"])

    try:
        cotacoes = fetch_uggeri_quotes()
    except Exception as error:
        return FetchResult("uggeri", errors=[f"Grupo Uggeri: {error}"])

    pontos = [
        PricePoint(crop=crop, price=price, source="Grupo Uggeri",
                   region="Santo Ângelo/RS", source_url=UGGERI_URL)
        for crop, price in cotacoes.items()
    ]
    erros = [] if pontos else ["Grupo Uggeri respondeu, mas nenhum preço foi identificado."]
    return FetchResult("uggeri", points=pontos, errors=erros)


def _not_configured(key, label):
    def _fetch():
        return FetchResult(
            key,
            errors=[
                f"{label} ainda não está conectada. É uma fonte planejada; "
                "a integração será ativada com o endpoint/credencial apropriados."
            ],
        )

    return _fetch


# Registro. As fontes públicas ficam declaradas mas inativas até a integração
# real ser configurada — assim a interface já as apresenta como roadmap sem
# prometer dado que ainda não existe, e sem raspar nada indevido.
SOURCES = {
    "uggeri": Source(
        key="uggeri",
        label="Grupo Uggeri (regional)",
        kind="regional",
        country="BR",
        fetch=_uggeri_fetch,
        available=True,
        note="Cotação de balcão pública de Santo Ângelo/RS.",
    ),
    "cepea": Source(
        key="cepea",
        label="Indicador CEPEA/ESALQ",
        kind="indicador",
        country="BR",
        fetch=_not_configured("cepea", "CEPEA/ESALQ"),
        available=False,
        note="Referência de preço no Brasil; uso com atribuição.",
    ),
    "conab": Source(
        key="conab",
        label="CONAB (preços e safra)",
        kind="publico",
        country="BR",
        fetch=_not_configured("conab", "CONAB"),
        available=False,
        note="Dados públicos brasileiros de oferta, demanda e preços.",
    ),
    "usda": Source(
        key="usda",
        label="USDA (oferta mundial)",
        kind="publico",
        country="US",
        fetch=_not_configured("usda", "USDA"),
        available=False,
        note="Dados públicos dos EUA via API oficial; produtividade e estoques mundiais.",
    ),
}


def available_sources():
    """Fontes prontas para coletar agora."""
    return [s for s in SOURCES.values() if s.available]


def planned_sources():
    """Fontes acordadas mas ainda não conectadas — exibidas como roadmap."""
    return [s for s in SOURCES.values() if not s.available]


def collect(source_key, *, user_id=None):
    """Executa a coleta de uma fonte e grava os pontos na série histórica."""
    from services.market_data.history import record_quote

    source = SOURCES.get(source_key)
    if source is None:
        return {"updated": [], "errors": [f"Fonte desconhecida: {source_key}"]}
    if not source.available:
        return {"updated": [], "errors": [source.fetch().errors[0]]}

    result = source.fetch()
    updated = []
    for point in result.points:
        quote_id = record_quote(
            point.crop, point.price,
            source=point.source, region=point.region,
            quote_type="externa", source_url=point.source_url,
            user_id=user_id,
        )
        updated.append({"id": quote_id, "crop": point.crop, "price": point.price})
    return {"updated": updated, "errors": list(result.errors)}
