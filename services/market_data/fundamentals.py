"""Fundamentos de oferta a partir do USDA/NASS Quick Stats.

Preço é uma coisa; **fundamento** é outra. Os indicadores de `indicators.py`
dizem onde o preço está no histórico. Aqui entra o porquê: se a safra americana
vem grande, pressiona o preço mundial; se vem pequena, sustenta.

Escopo honesto: o NASS publica dados **dos Estados Unidos** — não do mundo. É um
fundamento forte, porque os EUA são um dos maiores produtores de soja e milho,
mas não substitui um balanço mundial (isso seria a FAS PSD, outra API).

Formato da resposta, conforme a documentação oficial::

    {"data": [{"commodity_desc": "CORN", "statisticcat_desc": "AREA PLANTED",
               "unit_desc": "ACRES", "Value": "510,000", "year": "2012",
               "country_name": "UNITED STATES", "agg_level_desc": "STATE", ...}]}

Duas armadilhas tratadas aqui: ``Value`` vem como **texto com separador de
milhar**, e valores suprimidos aparecem como códigos entre parênteses — ``(D)``
para dado confidencial, ``(NA)``, ``(Z)``. Tratar isso como número quebraria em
silêncio.
"""
from __future__ import annotations

import os

BASE_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
VARIAVEL_DE_AMBIENTE = "USDA_API_KEY"

# Culturas do AGRIZA -> nome da commodity no NASS
COMMODITY_POR_CULTURA = {
    "Soja": "SOYBEANS",
    "Milho": "CORN",
    "Trigo": "WHEAT",
}

# Estatísticas que interessam para leitura de oferta
ESTATISTICAS = ("YIELD", "PRODUCTION")

# Códigos de supressão do NASS: não são números, são ausência de dado.
CODIGOS_SUPRIMIDOS = {"(D)", "(NA)", "(Z)", "(X)", "(S)", "(H)", "(L)"}


def tem_chave():
    """A coleta só é possível com a chave configurada no ambiente."""
    return bool(os.getenv(VARIAVEL_DE_AMBIENTE, "").strip())


def parse_valor(bruto):
    """Converte o ``Value`` do NASS em float, ou ``None`` quando suprimido.

    O campo vem como texto: ``"510,000"``, ``"49.1"`` ou um código como ``(D)``.
    """
    texto = str(bruto or "").strip()
    if not texto or texto in CODIGOS_SUPRIMIDOS:
        return None
    if texto.startswith("(") and texto.endswith(")"):
        return None  # qualquer outro código entre parênteses
    texto = texto.replace(",", "")  # separador de milhar no padrão americano
    try:
        return float(texto)
    except ValueError:
        return None


def parse_resposta(payload):
    """Normaliza a resposta do NASS. Função pura, sem rede.

    Devolve ``(registros, erros)``. Registro descartado por valor suprimido não
    vira erro — é ausência esperada de dado, não falha.
    """
    if not isinstance(payload, dict):
        return [], ["Resposta do USDA em formato inesperado."]
    if "error" in payload:
        erro = payload["error"]
        if isinstance(erro, list):
            erro = "; ".join(str(e) for e in erro)
        return [], [f"USDA: {erro}"]

    dados = payload.get("data")
    if dados is None:
        return [], ["Resposta do USDA sem o campo 'data'."]
    if not isinstance(dados, list):
        return [], ["Campo 'data' do USDA em formato inesperado."]

    registros = []
    for item in dados:
        if not isinstance(item, dict):
            continue
        valor = parse_valor(item.get("Value"))
        if valor is None:
            continue  # dado suprimido pelo próprio USDA
        try:
            ano = int(str(item.get("year", "")).strip())
        except ValueError:
            continue
        registros.append({
            "commodity": (item.get("commodity_desc") or "").strip(),
            "statistic": (item.get("statisticcat_desc") or "").strip(),
            "unidade": (item.get("unit_desc") or "").strip(),
            "regiao": (item.get("country_name") or "").strip() or "UNITED STATES",
            "ano": ano,
            "valor": valor,
        })
    return registros, []


def _url_de_consulta(commodity, statistic, ano_minimo):
    """Monta a consulta. A chave nunca é registrada em log nem devolvida."""
    from urllib.parse import urlencode

    parametros = {
        "key": os.getenv(VARIAVEL_DE_AMBIENTE, "").strip(),
        "commodity_desc": commodity,
        "statisticcat_desc": statistic,
        "agg_level_desc": "NATIONAL",
        "year__GE": str(ano_minimo),
        "format": "JSON",
    }
    return BASE_URL + "?" + urlencode(parametros)


def _ocultar_chave(texto):
    """Impede que a chave apareça em mensagem de erro exibida na tela."""
    chave = os.getenv(VARIAVEL_DE_AMBIENTE, "").strip()
    if chave:
        return str(texto).replace(chave, "***")
    return str(texto)


def buscar(commodity, statistic, ano_minimo, *, timeout=20):
    """Consulta o NASS. Devolve ``(registros, erros)``; nunca lança."""
    if not tem_chave():
        return [], [
            f"A variável {VARIAVEL_DE_AMBIENTE} não está configurada neste ambiente."
        ]
    import requests

    try:
        resposta = requests.get(
            _url_de_consulta(commodity, statistic, ano_minimo),
            timeout=timeout,
            headers={"User-Agent": "AGRIZA/3.1"},
        )
    except Exception as erro:
        return [], [f"Falha ao consultar o USDA: {_ocultar_chave(erro)}"]

    if resposta.status_code == 401:
        return [], ["USDA recusou a chave (401). Confira a variável USDA_API_KEY."]
    if resposta.status_code != 200:
        return [], [f"USDA respondeu HTTP {resposta.status_code}."]

    try:
        payload = resposta.json()
    except Exception:
        return [], ["USDA respondeu algo que não é JSON."]
    return parse_resposta(payload)


def coletar(culturas=None, *, ano_minimo=2015, user_id=None):
    """Coleta e grava os fundamentos das culturas informadas."""
    from services.market_data.fundamentals_store import salvar_fundamento

    culturas = culturas or list(COMMODITY_POR_CULTURA)
    gravados, erros = 0, []
    for cultura in culturas:
        commodity = COMMODITY_POR_CULTURA.get(cultura)
        if not commodity:
            continue
        for statistic in ESTATISTICAS:
            registros, falhas = buscar(commodity, statistic, ano_minimo)
            erros.extend(falhas)
            for registro in registros:
                salvar_fundamento(registro, fonte="USDA")
                gravados += 1
    return {"gravados": gravados, "erros": erros}
