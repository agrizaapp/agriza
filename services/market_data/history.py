"""Leitura e gravação da série histórica de preços, sobre a tabela ``quotes``.

A tabela ``quotes`` já guarda ``crop``, ``price_sc``, ``quoted_at``, ``region``,
``source`` e ``quote_type`` — ou seja, já é uma série temporal. Aqui ficam as
consultas que enxergam a série inteira, e não apenas a última cotação como fazia
o resto do aplicativo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.database import insert_id, q


# Uma única consulta serve SQLite e PostgreSQL: a data de corte é calculada em
# Python e vai como parâmetro, evitando aritmética de data específica de cada
# dialeto (INTERVAL vs datetime()). A região opcional entra como predicado
# parametrizado, em vez de fragmento concatenado.
_SERIE = """
    SELECT price_sc, quoted_at, source, region, quote_type
    FROM quotes
    WHERE lower(crop) = lower(:crop)
      AND quoted_at >= :desde
      AND (:region IS NULL OR region = :region)
    ORDER BY quoted_at ASC, id ASC
"""


def price_series(crop, days=180, region=None):
    """Série de preços de uma cultura, em ordem cronológica crescente.

    Cada item é ``{"date": ..., "price": float, "source": ..., "region": ...}``.
    Filtra por janela de dias e, opcionalmente, por praça/região.
    """
    desde = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=int(days))
    rows = q(_SERIE, {"crop": crop, "desde": desde, "region": region})
    return [
        {
            "date": row["quoted_at"],
            "price": float(row["price_sc"]),
            "source": row.get("source"),
            "region": row.get("region"),
            "quote_type": row.get("quote_type"),
        }
        for row in rows
    ]


def prices_only(crop, days=180, region=None):
    """Apenas os valores da série, prontos para os indicadores."""
    return [item["price"] for item in price_series(crop, days=days, region=region)]


def distinct_crops():
    """Culturas que possuem ao menos uma cotação registrada."""
    rows = q("SELECT DISTINCT crop FROM quotes ORDER BY crop")
    return [row["crop"] for row in rows]


def record_quote(crop, price, *, source, region=None, quote_type="externa",
                 source_url=None, user_id=None, quoted_at=None):
    """Grava um ponto na série. Reaproveitado por conectores e pela importação.

    ``quoted_at`` permite gravar um preço com data passada — indispensável para
    importar histórico. Quando omitido, vale o instante atual.
    """
    return insert_id(
        """INSERT INTO quotes
           (crop, price_sc, source, quoted_at, created_by, region, quote_type, source_url)
           VALUES(:crop, :price, :source, COALESCE(:quoted_at, CURRENT_TIMESTAMP),
                  :user_id, :region, :quote_type, :source_url)""",
        {
            "crop": crop,
            "price": float(price),
            "source": source,
            "user_id": user_id,
            "region": region,
            "quote_type": quote_type,
            "source_url": source_url,
            "quoted_at": quoted_at,
        },
    )
