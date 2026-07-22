"""Conector da FAS: tradução dos IDs e leitura da rota de dados.

A rota de dados devolve só `attributeId` e `unitId` — sem rótulo. Sem a
tradução correta, os números não significam nada, e um número sem significado
alimentando a recomendação é pior do que dado nenhum.
"""
from services.market_data.fas import indice_por_id, parse_dados

# Formato real da rota de dados, conforme diagnóstico em produção:
# valor numérico, sem attributeName nem unitDescription.
DADOS_REAIS = [
    {"commodityCode": "2222000", "countryCode": "BR", "marketYear": "2024",
     "calendarYear": "2026", "month": "04", "attributeId": 4, "unitId": 4,
     "value": 47400},
    {"commodityCode": "2222000", "countryCode": "BR", "marketYear": "2024",
     "calendarYear": "2026", "month": "04", "attributeId": 20, "unitId": 8,
     "value": 29761},
]

ATRIBUTOS = {4: "Area Harvested", 20: "Production", 28: "Yield"}
UNIDADES = {4: "(1000 HA)", 8: "(1000 MT)"}


class TestIndicePorId:
    def test_monta_mapa_de_id_para_rotulo(self):
        itens = [
            {"attributeId": 20, "attributeName": "Production"},
            {"attributeId": 28, "attributeName": "Yield"},
        ]
        assert indice_por_id(itens) == {20: "Production", 28: "Yield"}

    def test_funciona_com_outros_nomes_de_coluna(self):
        """Os nomes dessas rotas não são documentados; o índice não depende deles."""
        itens = [{"unitId": 8, "description": "(1000 MT)"}]
        assert indice_por_id(itens) == {8: "(1000 MT)"}

    def test_escolhe_o_texto_mais_descritivo(self):
        itens = [{"attributeId": 20, "code": "PR", "attributeName": "Production"}]
        assert indice_por_id(itens)[20] == "Production"

    def test_id_como_texto_tambem_serve(self):
        assert indice_por_id([{"unitId": "8", "name": "MT"}]) == {8: "MT"}

    def test_registro_sem_id_e_ignorado(self):
        assert indice_por_id([{"nome": "sem id"}]) == {}

    def test_lista_vazia(self):
        assert indice_por_id([]) == {}


class TestParseDados:
    def test_traduz_e_filtra_atributos(self):
        registros, ignorados = parse_dados(DADOS_REAIS, ATRIBUTOS, UNIDADES)
        # Area Harvested não está na lista de interesse; Production está
        assert len(registros) == 1
        assert ignorados == 1
        assert registros[0] == {
            "commodity": "2222000",
            "statistic": "Production",
            "unidade": "(1000 MT)",
            "regiao": "BR",
            "ano": 2024,
            "valor": 29761.0,
        }

    def test_atributo_sem_traducao_e_descartado(self):
        """Número sem rótulo não entra na base."""
        registros, ignorados = parse_dados(DADOS_REAIS, {}, UNIDADES)
        assert registros == []
        assert ignorados == 2

    def test_usa_marketYear_e_nao_calendarYear(self):
        """marketYear é a safra; calendarYear é quando o dado foi publicado."""
        registros, _ = parse_dados(DADOS_REAIS, ATRIBUTOS, UNIDADES)
        assert registros[0]["ano"] == 2024  # e não 2026

    def test_valor_invalido_e_descartado(self):
        ruim = [dict(DADOS_REAIS[1], value=None)]
        registros, ignorados = parse_dados(ruim, ATRIBUTOS, UNIDADES)
        assert registros == [] and ignorados == 1

    def test_unidade_desconhecida_nao_impede_a_gravacao(self):
        registros, _ = parse_dados(DADOS_REAIS, ATRIBUTOS, {})
        assert registros[0]["unidade"] == ""

    def test_payload_inesperado_nao_quebra(self):
        assert parse_dados({"erro": "x"}, ATRIBUTOS, UNIDADES) == ([], 0)
