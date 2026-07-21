"""Persistência dos fundamentos de oferta.

Tabela própria, separada de ``quotes``: fundamento é observação anual por
commodity e região, não ponto de preço. Misturar as duas coisas na mesma tabela
sujaria a série que alimenta os indicadores.
"""
from __future__ import annotations

from core.database import ex, insert_id, q


def salvar_fundamento(registro, *, fonte="USDA"):
    """Grava ou atualiza uma observação anual.

    Faz verificar-e-então-gravar em Python em vez de ``ON CONFLICT``/``UPSERT``,
    que têm sintaxe diferente em SQLite e PostgreSQL. Mesma lição do INTERVAL.
    """
    existente = q(
        """SELECT id FROM fundamentals
           WHERE source=:fonte AND commodity=:commodity AND statistic=:statistic
             AND COALESCE(region,'')=COALESCE(:region,'') AND year=:ano
           LIMIT 1""",
        {
            "fonte": fonte,
            "commodity": registro["commodity"],
            "statistic": registro["statistic"],
            "region": registro.get("regiao"),
            "ano": registro["ano"],
        },
    )
    if existente:
        ex(
            """UPDATE fundamentals
               SET value=:valor, unit=:unidade, collected_at=CURRENT_TIMESTAMP
               WHERE id=:id""",
            {
                "valor": registro["valor"],
                "unidade": registro.get("unidade"),
                "id": existente[0]["id"],
            },
        )
        return existente[0]["id"]

    return insert_id(
        """INSERT INTO fundamentals(source,commodity,statistic,region,year,value,unit)
           VALUES(:fonte,:commodity,:statistic,:region,:ano,:valor,:unidade)""",
        {
            "fonte": fonte,
            "commodity": registro["commodity"],
            "statistic": registro["statistic"],
            "region": registro.get("regiao"),
            "ano": registro["ano"],
            "valor": registro["valor"],
            "unidade": registro.get("unidade"),
        },
    )


def serie_anual(commodity, statistic, *, fonte="USDA"):
    """Série anual de um fundamento, do ano mais antigo para o mais recente."""
    linhas = q(
        """SELECT year, value, unit FROM fundamentals
           WHERE source=:fonte AND commodity=:commodity AND statistic=:statistic
           ORDER BY year ASC""",
        {"fonte": fonte, "commodity": commodity, "statistic": statistic},
    )
    return [
        {"ano": int(l["year"]), "valor": float(l["value"]), "unidade": l["unit"]}
        for l in linhas
        if l["value"] is not None
    ]


def leitura_de_oferta(commodity, statistic="YIELD", *, fonte="USDA"):
    """Compara o último ano com a média dos anteriores.

    Devolve ``None`` quando não há histórico suficiente para uma leitura honesta
    — dois pontos não são tendência.
    """
    serie = serie_anual(commodity, statistic, fonte=fonte)
    if len(serie) < 3:
        return None

    atual = serie[-1]
    anteriores = [item["valor"] for item in serie[:-1]]
    media = sum(anteriores) / len(anteriores)
    if media == 0:
        return None

    variacao = (atual["valor"] - media) / media * 100
    if variacao >= 3:
        leitura, efeito = "acima da média", "tende a pressionar o preço para baixo"
    elif variacao <= -3:
        leitura, efeito = "abaixo da média", "tende a sustentar o preço"
    else:
        leitura, efeito = "em linha com a média", "efeito neutro sobre o preço"

    return {
        "ano": atual["ano"],
        "valor": atual["valor"],
        "unidade": atual["unidade"],
        "media_anterior": round(media, 2),
        "variacao_pct": round(variacao, 1),
        "leitura": leitura,
        "efeito": efeito,
        "anos_considerados": len(serie),
    }
