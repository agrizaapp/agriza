"""Diagnóstico da FAS PSD.

A chamada de rede depende da chave, que vive no Render. O que é testado aqui é
o resumo de estrutura — a peça que vai me dizer, com precisão, qual parser
escrever depois — e a garantia de que a chave nunca vaza.
"""
from services.market_data import fas
from services.market_data.fas import resumir_estrutura


class TestResumoDeEstrutura:
    def test_lista_de_objetos(self):
        payload = [
            {"commodityCode": "0813100", "commodityName": "Oilseed, Soybean"},
            {"commodityCode": "0440000", "commodityName": "Corn"},
        ]
        r = resumir_estrutura(payload)
        assert r["tipo"] == "lista"
        assert r["itens"] == 2
        assert r["chaves_do_item"] == ["commodityCode", "commodityName"]
        assert len(r["amostra"]) == 2

    def test_objeto_com_lista_dentro(self):
        """Formato comum: {"data": [...]} — o resumo precisa achar a lista."""
        payload = {"data": [{"a": 1, "b": 2}], "meta": {"total": 1}}
        r = resumir_estrutura(payload)
        assert r["tipo"] == "objeto"
        assert r["lista_em"] == "data"
        assert r["chaves_do_item"] == ["a", "b"]

    def test_objeto_simples(self):
        r = resumir_estrutura({"erro": "sem permissao"})
        assert r["tipo"] == "objeto"
        assert r["amostra"] == {"erro": "sem permissao"}

    def test_lista_vazia_nao_quebra(self):
        r = resumir_estrutura([])
        assert r["tipo"] == "lista" and r["itens"] == 0

    def test_valor_primitivo(self):
        assert resumir_estrutura(42)["tipo"] == "int"

    def test_texto_longo_e_truncado(self):
        payload = [{"descricao": "x" * 500}]
        assert len(payload[0]["descricao"]) == 500
        assert resumir_estrutura(payload)["amostra"][0]["descricao"].endswith("…")

    def test_amostra_limitada(self):
        payload = [{"i": n} for n in range(50)]
        r = resumir_estrutura(payload)
        assert r["itens"] == 50
        assert len(r["amostra"]) == fas.LIMITE_DE_AMOSTRA


class TestSeguranca:
    def test_sem_chave_nao_tenta_rede(self, monkeypatch):
        monkeypatch.delenv(fas.VARIAVEL_DE_AMBIENTE, raising=False)
        relatorio = fas.diagnosticar()
        assert relatorio["estrutura"] is None
        assert "FAS_API_KEY" in relatorio["erros"][0]
        assert relatorio["tentativas"] == []

    def test_chave_e_mascarada(self, monkeypatch):
        monkeypatch.setenv(fas.VARIAVEL_DE_AMBIENTE, "MINHACHAVE999")
        texto = fas._ocultar_chave("erro em ?api_key=MINHACHAVE999&x=1")
        assert "MINHACHAVE999" not in texto
        assert "***" in texto

    def test_ambas_as_convencoes_de_auth_sao_tentadas(self, monkeypatch):
        """A FAS não documenta se a chave vai por query ou header."""
        monkeypatch.setenv(fas.VARIAVEL_DE_AMBIENTE, "K")
        formas = [t[0] for t in fas._tentativas("commodities")]
        assert "query api_key" in formas
        assert "header API_KEY" in formas
