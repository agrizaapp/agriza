"""USDA/FAS PSD — balanço mundial de oferta e demanda, por país.

Enquanto o NASS cobre só os Estados Unidos, a PSD (*Production, Supply and
Distribution*) traz produção, produtividade, estoques e exportação **por país**
— Brasil, Argentina, China, EUA. É o "cenário mundial" da visão do produto.

**Estado atual: diagnóstico.** As rotas foram confirmadas (respondem 403, e não
404, sem chave), mas o formato exato da resposta não é público — a documentação
também fica atrás da chave. Em vez de escrever parsing sobre formato adivinhado,
este módulo primeiro *descobre* a estrutura em produção, onde a chave existe.
Só depois disso o parser definitivo é escrito, em cima do que realmente chega.

Rotas confirmadas::

    /api/psd/commodities
    /api/psd/countries
    /api/psd/regions
    /api/psd/unitsOfMeasure
    /api/psd/commodityAttributes
    /api/psd/commodity/{codigo}/country/{pais}/year/{ano}
"""
from __future__ import annotations

import os

BASE_URL = "https://api.fas.usda.gov/api/psd"
VARIAVEL_DE_AMBIENTE = "FAS_API_KEY"

ROTAS_DE_REFERENCIA = (
    "commodities",
    "countries",
    "unitsOfMeasure",
    "commodityAttributes",
)

LIMITE_DE_AMOSTRA = 2
LIMITE_DE_TEXTO = 120

# Confirmado pelo diagnóstico em produção: a FAS aceita a chave por query
# string (`?api_key=`). A rota /commodities devolve uma lista de objetos com
# `commodityCode` e `commodityName` — nomes em inglês.
FORMA_DE_AUTENTICACAO = "query api_key"


def tem_chave():
    return bool(os.getenv(VARIAVEL_DE_AMBIENTE, "").strip())


def _ocultar_chave(texto):
    """Nunca deixar a chave aparecer em tela ou log."""
    chave = os.getenv(VARIAVEL_DE_AMBIENTE, "").strip()
    return str(texto).replace(chave, "***") if chave else str(texto)


def _encurtar(valor):
    if isinstance(valor, str) and len(valor) > LIMITE_DE_TEXTO:
        return valor[:LIMITE_DE_TEXTO] + "…"
    return valor


def resumir_estrutura(payload):
    """Descreve o formato de uma resposta, sem despejar o conteúdo inteiro.

    Função pura. É o que permite escrever o parser definitivo com base no que a
    API realmente devolve, em vez de suposição.
    """
    if isinstance(payload, list):
        resumo = {"tipo": "lista", "itens": len(payload)}
        if payload and isinstance(payload[0], dict):
            resumo["chaves_do_item"] = sorted(payload[0].keys())
        resumo["amostra"] = [
            {k: _encurtar(v) for k, v in item.items()}
            if isinstance(item, dict) else _encurtar(item)
            for item in payload[:LIMITE_DE_AMOSTRA]
        ]
        return resumo

    if isinstance(payload, dict):
        resumo = {"tipo": "objeto", "chaves": sorted(payload.keys())}
        for chave, valor in payload.items():
            if isinstance(valor, list):
                resumo["lista_em"] = chave
                resumo["itens"] = len(valor)
                if valor and isinstance(valor[0], dict):
                    resumo["chaves_do_item"] = sorted(valor[0].keys())
                    resumo["amostra"] = [
                        {k: _encurtar(v) for k, v in item.items()}
                        for item in valor[:LIMITE_DE_AMOSTRA]
                        if isinstance(item, dict)
                    ]
                break
        else:
            resumo["amostra"] = {k: _encurtar(v) for k, v in payload.items()}
        return resumo

    return {"tipo": type(payload).__name__, "amostra": _encurtar(payload)}


def filtrar_por_termo(itens, termo):
    """Filtra registros procurando o termo em qualquer valor de texto.

    Busca sobre os valores, e não sobre nomes de campo fixos: assim funciona
    independentemente de como a API nomeia as colunas, e não quebra se algum
    nome mudar.
    """
    termo = (termo or "").strip().lower()
    if not termo:
        return list(itens)
    encontrados = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        if any(termo in str(valor).lower() for valor in item.values()):
            encontrados.append(item)
    return encontrados


def _tentativas(rota):
    """A mensagem de erro da FAS não diz como enviar a chave; testamos as duas
    convenções usuais e relatamos qual funcionou."""
    chave = os.getenv(VARIAVEL_DE_AMBIENTE, "").strip()
    url = f"{BASE_URL}/{rota}"
    return [
        ("query api_key", url, {"api_key": chave}, {}),
        ("header API_KEY", url, {}, {"API_KEY": chave}),
    ]


def diagnosticar(rota="commodities", *, timeout=25):
    """Consulta a FAS e devolve um relatório do formato — nunca lança.

    Devolve ``{"rota", "tentativas": [...], "estrutura": ... , "erros": [...]}``.
    """
    if not tem_chave():
        return {
            "rota": rota,
            "tentativas": [],
            "estrutura": None,
            "erros": [
                f"A variável {VARIAVEL_DE_AMBIENTE} não está configurada neste ambiente."
            ],
        }

    import requests

    relatorio = {"rota": rota, "tentativas": [], "estrutura": None, "erros": []}
    for nome, url, params, headers in _tentativas(rota):
        try:
            resposta = requests.get(
                url, params=params,
                headers={"User-Agent": "AGRIZA/3.1", **headers},
                timeout=timeout,
            )
        except Exception as erro:
            relatorio["tentativas"].append(
                {"forma": nome, "status": "falha", "detalhe": _ocultar_chave(erro)}
            )
            continue

        registro = {"forma": nome, "status": resposta.status_code}
        if resposta.status_code == 200:
            try:
                payload = resposta.json()
            except Exception:
                registro["detalhe"] = "resposta não é JSON"
                relatorio["tentativas"].append(registro)
                continue
            registro["detalhe"] = "OK"
            relatorio["tentativas"].append(registro)
            relatorio["estrutura"] = resumir_estrutura(payload)
            relatorio["forma_que_funcionou"] = nome
            return relatorio

        registro["detalhe"] = _ocultar_chave(resposta.text)[:200]
        relatorio["tentativas"].append(registro)

    relatorio["erros"].append(
        "Nenhuma das formas de autenticação funcionou. Veja os status acima."
    )
    return relatorio


def _consultar(caminho, *, timeout=25):
    """GET autenticado na forma já confirmada em produção. Nunca lança.

    Devolve ``(payload, erros)``.
    """
    if not tem_chave():
        return None, [
            f"A variável {VARIAVEL_DE_AMBIENTE} não está configurada neste ambiente."
        ]
    import requests

    try:
        resposta = requests.get(
            f"{BASE_URL}/{caminho}",
            params={"api_key": os.getenv(VARIAVEL_DE_AMBIENTE, "").strip()},
            headers={"User-Agent": "AGRIZA/3.1"},
            timeout=timeout,
        )
    except Exception as erro:
        return None, [f"Falha ao consultar a FAS: {_ocultar_chave(erro)}"]

    if resposta.status_code != 200:
        return None, [
            f"FAS respondeu HTTP {resposta.status_code}: "
            f"{_ocultar_chave(resposta.text)[:160]}"
        ]
    try:
        return resposta.json(), []
    except Exception:
        return None, ["FAS respondeu algo que não é JSON."]


def buscar_commodities(termo=""):
    """Catálogo de commodities, opcionalmente filtrado.

    Serve para descobrir o código real de soja, milho e trigo — em vez de
    chutar, que foi o erro que quase cometi antes.
    """
    payload, erros = _consultar("commodities")
    if erros:
        return [], erros
    itens = payload if isinstance(payload, list) else payload.get("data", [])
    return filtrar_por_termo(itens, termo), []


def diagnosticar_dados(commodity_code, country_code="BR", year=2024):
    """Inspeciona a rota de dados — é dela que sai o balanço de oferta."""
    caminho = f"commodity/{commodity_code}/country/{country_code}/year/{year}"
    payload, erros = _consultar(caminho)
    if erros:
        return {"caminho": caminho, "estrutura": None, "erros": erros}
    return {"caminho": caminho, "estrutura": resumir_estrutura(payload), "erros": []}


# --- Tradução dos IDs -------------------------------------------------------
# A rota de dados devolve apenas attributeId e unitId; os rótulos vivem em
# /commodityAttributes e /unitsOfMeasure. Como os nomes das colunas dessas
# rotas não são documentados, o índice é montado por forma: o campo numérico
# terminado em "id" é a chave, e o texto mais descritivo é o rótulo. O painel
# exibe o mapeamento resolvido, para conferência.

def indice_por_id(itens):
    """Monta ``{id: rótulo}`` a partir de uma lista de registros.

    Função pura e tolerante a nomes de coluna: procura o campo que parece um
    identificador numérico e o texto mais informativo do registro.
    """
    indice = {}
    for item in itens:
        if not isinstance(item, dict):
            continue
        identificador = None
        for chave, valor in item.items():
            if chave.lower().endswith("id") and isinstance(valor, (int, str)):
                try:
                    identificador = int(valor)
                    break
                except (TypeError, ValueError):
                    continue
        if identificador is None:
            continue
        textos = [
            str(valor).strip() for chave, valor in item.items()
            if isinstance(valor, str) and not chave.lower().endswith("id")
            and str(valor).strip()
        ]
        if textos:
            indice[identificador] = max(textos, key=len)
    return indice


def carregar_indices():
    """Busca e traduz os catálogos de atributos e unidades."""
    erros = []
    atributos_brutos, e1 = _consultar("commodityAttributes")
    erros.extend(e1)
    unidades_brutas, e2 = _consultar("unitsOfMeasure")
    erros.extend(e2)

    def _lista(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for valor in payload.values():
                if isinstance(valor, list):
                    return valor
        return []

    return (
        indice_por_id(_lista(atributos_brutos)),
        indice_por_id(_lista(unidades_brutas)),
        erros,
    )


# Códigos confirmados um a um pelo diagnóstico em produção, com dado real.
COMMODITY_POR_CULTURA = {
    "Soja": "2222000",
    "Milho": "0440000",
    "Trigo": "0410000",
}

PAISES_DE_INTERESSE = {
    "BR": "Brasil",
    "US": "Estados Unidos",
    "AR": "Argentina",
    "CN": "China",
}

# Atributos que interessam, identificados pelo texto do rótulo (a API entrega
# os rótulos em inglês). Guardamos só estes para não inflar a base.
ATRIBUTOS_DE_INTERESSE = ("production", "yield", "ending stocks", "exports")


def _interessa(rotulo):
    texto = (rotulo or "").lower()
    return any(chave in texto for chave in ATRIBUTOS_DE_INTERESSE)


def parse_dados(payload, indice_atributos, indice_unidades):
    """Traduz a resposta da rota de dados em registros com rótulo. Pura.

    Descarta o que não tem rótulo conhecido: número sem significado não entra
    na base, porque alimentaria a recomendação com dado que ninguém consegue
    interpretar.
    """
    itens = payload if isinstance(payload, list) else []
    registros, ignorados = [], 0
    for item in itens:
        if not isinstance(item, dict):
            continue
        rotulo = indice_atributos.get(item.get("attributeId"))
        if not rotulo or not _interessa(rotulo):
            ignorados += 1
            continue
        try:
            valor = float(item["value"])
            ano = int(item["marketYear"])
        except (KeyError, TypeError, ValueError):
            ignorados += 1
            continue
        registros.append({
            "commodity": str(item.get("commodityCode", "")),
            "statistic": rotulo,
            "unidade": indice_unidades.get(item.get("unitId")) or "",
            "regiao": str(item.get("countryCode", "")),
            "ano": ano,
            "valor": valor,
        })
    return registros, ignorados


def coletar(culturas=None, paises=None, anos=None, *, user_id=None):
    """Coleta o balanço mundial e grava na base de fundamentos."""
    from services.market_data.fundamentals_store import salvar_fundamento

    indice_atributos, indice_unidades, erros = carregar_indices()
    if not indice_atributos:
        erros.append("Não foi possível traduzir os atributos da FAS.")
        return {"gravados": 0, "erros": erros, "atributos": {}, "unidades": {}}

    culturas = culturas or list(COMMODITY_POR_CULTURA)
    paises = paises or list(PAISES_DE_INTERESSE)
    anos = anos or list(range(2019, 2025))

    gravados = 0
    for cultura in culturas:
        codigo = COMMODITY_POR_CULTURA.get(cultura)
        if not codigo:
            continue
        for pais in paises:
            for ano in anos:
                payload, falhas = _consultar(
                    f"commodity/{codigo}/country/{pais}/year/{ano}"
                )
                if falhas:
                    erros.extend(falhas)
                    continue
                registros, _ = parse_dados(payload, indice_atributos, indice_unidades)
                for registro in registros:
                    salvar_fundamento(registro, fonte="FAS")
                    gravados += 1
    return {
        "gravados": gravados,
        "erros": erros,
        "atributos": indice_atributos,
        "unidades": indice_unidades,
    }
