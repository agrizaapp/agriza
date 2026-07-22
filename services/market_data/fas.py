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
