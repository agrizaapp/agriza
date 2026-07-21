"""Fundamentos de oferta do USDA/NASS.

A chamada de rede não é testável aqui (a chave vive no Render). O que é testado
é tudo o que pode dar errado *depois* da resposta chegar — que é onde um engano
passaria despercebido e envenenaria a leitura.
"""
import pytest

from services.market_data.fundamentals import parse_resposta, parse_valor
from services.market_data.fundamentals_store import (
    leitura_de_oferta,
    salvar_fundamento,
    serie_anual,
)

# Registro copiado da documentação oficial do NASS, com os campos como eles
# realmente chegam: Value é texto com separador de milhar.
REGISTRO_OFICIAL = {
    "state_alpha": "VA",
    "short_desc": "CORN - ACRES PLANTED",
    "country_name": "UNITED STATES",
    "Value": "510,000",
    "commodity_desc": "CORN",
    "unit_desc": "ACRES",
    "statisticcat_desc": "AREA PLANTED",
    "agg_level_desc": "STATE",
    "year": "2012",
    "reference_period_desc": "YEAR",
}


class TestParseValor:
    def test_separador_de_milhar_americano(self):
        assert parse_valor("510,000") == 510000.0
        assert parse_valor("1,234,567") == 1234567.0

    def test_decimal(self):
        assert parse_valor("49.1") == 49.1

    @pytest.mark.parametrize("codigo", ["(D)", "(NA)", "(Z)", "(X)", "(S)"])
    def test_codigos_suprimidos_viram_none(self, codigo):
        """(D) é dado confidencial — tratar como número seria inventar."""
        assert parse_valor(codigo) is None

    def test_vazio_e_lixo(self):
        assert parse_valor("") is None
        assert parse_valor(None) is None
        assert parse_valor("abc") is None


class TestParseResposta:
    def test_registro_da_documentacao_oficial(self):
        registros, erros = parse_resposta({"data": [REGISTRO_OFICIAL]})
        assert erros == []
        assert registros == [{
            "commodity": "CORN",
            "statistic": "AREA PLANTED",
            "unidade": "ACRES",
            "regiao": "UNITED STATES",
            "ano": 2012,
            "valor": 510000.0,
        }]

    def test_registro_suprimido_e_descartado_sem_virar_erro(self):
        suprimido = dict(REGISTRO_OFICIAL, Value="(D)")
        registros, erros = parse_resposta({"data": [suprimido, REGISTRO_OFICIAL]})
        assert len(registros) == 1
        assert erros == []

    def test_ano_invalido_e_descartado(self):
        ruim = dict(REGISTRO_OFICIAL, year="")
        registros, _ = parse_resposta({"data": [ruim]})
        assert registros == []

    def test_erro_da_api(self):
        _, erros = parse_resposta({"error": ["exceeds limit = 50000"]})
        assert "exceeds limit" in erros[0]

    def test_resposta_sem_data(self):
        _, erros = parse_resposta({"foo": "bar"})
        assert "sem o campo 'data'" in erros[0]

    def test_resposta_nao_e_dicionario(self):
        _, erros = parse_resposta("<html>erro</html>")
        assert erros and "inesperado" in erros[0]

    def test_data_vazia_nao_quebra(self):
        registros, erros = parse_resposta({"data": []})
        assert registros == [] and erros == []


class TestPersistencia:
    def _gravar(self, ano, valor, statistic="YIELD"):
        salvar_fundamento({
            "commodity": "SOYBEANS", "statistic": statistic, "unidade": "BU / ACRE",
            "regiao": "UNITED STATES", "ano": ano, "valor": valor,
        })

    def test_serie_em_ordem_cronologica(self, banco_limpo):
        for ano, valor in [(2022, 51.0), (2020, 50.2), (2021, 51.4)]:
            self._gravar(ano, valor)
        serie = serie_anual("SOYBEANS", "YIELD")
        assert [i["ano"] for i in serie] == [2020, 2021, 2022]

    def test_recoletar_atualiza_em_vez_de_duplicar(self, banco_limpo):
        self._gravar(2024, 50.0)
        self._gravar(2024, 53.0)  # USDA revisou o numero
        serie = serie_anual("SOYBEANS", "YIELD")
        assert len(serie) == 1
        assert serie[0]["valor"] == 53.0


class TestLeituraDeOferta:
    def _serie(self, valores):
        for indice, valor in enumerate(valores):
            salvar_fundamento({
                "commodity": "SOYBEANS", "statistic": "YIELD", "unidade": "BU / ACRE",
                "regiao": "UNITED STATES", "ano": 2018 + indice, "valor": valor,
            })

    def test_historico_curto_nao_gera_leitura(self, banco_limpo):
        """Dois pontos não são tendência; melhor não dizer nada."""
        self._serie([50.0, 51.0])
        assert leitura_de_oferta("SOYBEANS") is None

    def test_safra_grande_pressiona_o_preco(self, banco_limpo):
        self._serie([50.0, 50.0, 50.0, 60.0])
        leitura = leitura_de_oferta("SOYBEANS")
        assert leitura["leitura"] == "acima da média"
        assert "para baixo" in leitura["efeito"]
        assert leitura["variacao_pct"] == 20.0

    def test_safra_pequena_sustenta_o_preco(self, banco_limpo):
        self._serie([50.0, 50.0, 50.0, 40.0])
        leitura = leitura_de_oferta("SOYBEANS")
        assert leitura["leitura"] == "abaixo da média"
        assert "sustentar" in leitura["efeito"]

    def test_safra_normal_e_neutra(self, banco_limpo):
        self._serie([50.0, 50.0, 50.0, 50.5])
        assert leitura_de_oferta("SOYBEANS")["leitura"] == "em linha com a média"


class TestSeguranca:
    def test_sem_chave_a_busca_nao_tenta_rede(self, monkeypatch):
        from services.market_data import fundamentals

        monkeypatch.delenv(fundamentals.VARIAVEL_DE_AMBIENTE, raising=False)
        registros, erros = fundamentals.buscar("SOYBEANS", "YIELD", 2020)
        assert registros == []
        assert "USDA_API_KEY" in erros[0]

    def test_chave_nunca_aparece_em_mensagem_de_erro(self, monkeypatch):
        from services.market_data import fundamentals

        monkeypatch.setenv(fundamentals.VARIAVEL_DE_AMBIENTE, "SEGREDO123")
        texto = fundamentals._ocultar_chave("falhou em https://x?key=SEGREDO123")
        assert "SEGREDO123" not in texto
        assert "***" in texto
