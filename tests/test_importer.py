"""Importação de histórico de preços por planilha."""
from datetime import date, timedelta

from services.market_data.history import prices_only
from services.market_data.importer import (
    importar_linhas,
    parse_data,
    parse_numero,
    parse_price_csv,
)

CULTURAS = ["Soja", "Milho", "Trigo", "Canola"]


class TestParseNumero:
    def test_formato_brasileiro(self):
        assert parse_numero("1.234,56") == 1234.56
        assert parse_numero("142,00") == 142.0
        assert parse_numero("R$ 138,50") == 138.5

    def test_formato_internacional(self):
        assert parse_numero("1234.56") == 1234.56
        assert parse_numero("1,234.56") == 1234.56

    def test_inteiro_e_espacos(self):
        assert parse_numero(" 120 ") == 120.0

    def test_invalido(self):
        assert parse_numero("abc") is None
        assert parse_numero("") is None
        assert parse_numero(None) is None


class TestParseData:
    def test_formato_brasileiro(self):
        assert parse_data("21/07/2026") == date(2026, 7, 21)

    def test_iso(self):
        assert parse_data("2026-07-21") == date(2026, 7, 21)

    def test_com_hora_junto(self):
        assert parse_data("21/07/2026 14:30") == date(2026, 7, 21)

    def test_invalida(self):
        assert parse_data("31/02/2026") is None
        assert parse_data("qualquer coisa") is None
        assert parse_data("") is None


class TestParseArquivo:
    def test_csv_padrao_do_excel_brasileiro(self):
        """Separador ponto e vírgula, vírgula decimal, acento no cabeçalho."""
        conteudo = (
            "Data;Cultura;Preço;Fonte;Praça\n"
            "01/03/2026;Soja;138,50;Cooperativa;Santo Ângelo/RS\n"
            "08/03/2026;Soja;140,00;Cooperativa;Santo Ângelo/RS\n"
        ).encode("utf-8")
        linhas, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert erros == []
        assert len(linhas) == 2
        assert linhas[0]["data"] == date(2026, 3, 1)
        assert linhas[0]["preco"] == 138.5
        assert linhas[0]["regiao"] == "Santo Ângelo/RS"

    def test_csv_com_virgula_e_cabecalho_em_ingles(self):
        conteudo = "date,crop,price\n2026-03-01,Soja,138.50\n"
        linhas, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert erros == []
        assert linhas[0]["preco"] == 138.5

    def test_latin1(self):
        conteudo = "Data;Cultura;Preço\n01/03/2026;Soja;100\n".encode("latin-1")
        linhas, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert erros == []
        assert len(linhas) == 1

    def test_colunas_opcionais_ausentes(self):
        conteudo = "Data;Cultura;Preco\n01/03/2026;Milho;60\n"
        linhas, _ = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert linhas[0]["fonte"] is None and linhas[0]["regiao"] is None

    def test_linhas_em_branco_sao_ignoradas(self):
        conteudo = "Data;Cultura;Preco\n01/03/2026;Soja;100\n\n;;\n"
        linhas, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert len(linhas) == 1 and erros == []


class TestParseRejeitaLixo:
    """O parser não pode adivinhar: o que é duvidoso vira erro com a linha."""

    def test_sem_colunas_obrigatorias(self):
        linhas, erros = parse_price_csv("foo;bar\n1;2\n")
        assert linhas == []
        assert "obrigat" in erros[0]

    def test_arquivo_vazio(self):
        assert parse_price_csv(b"")[1] == ["O arquivo está vazio."]

    def test_data_invalida_cita_a_linha(self):
        conteudo = "Data;Cultura;Preco\n99/99/2026;Soja;100\n"
        linhas, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert linhas == []
        assert "Linha 2" in erros[0] and "data inválida" in erros[0]

    def test_preco_invalido_cita_a_linha(self):
        conteudo = "Data;Cultura;Preco\n01/03/2026;Soja;abc\n"
        _, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert "Linha 2" in erros[0] and "preço inválido" in erros[0]

    def test_cultura_desconhecida(self):
        conteudo = "Data;Cultura;Preco\n01/03/2026;Feijao;100\n"
        _, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert "não reconhecida" in erros[0]

    def test_data_no_futuro(self):
        amanha = (date.today() + timedelta(days=5)).strftime("%d/%m/%Y")
        conteudo = f"Data;Cultura;Preco\n{amanha};Soja;100\n"
        _, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert "futuro" in erros[0]

    def test_preco_absurdo_e_barrado(self):
        """Coluna trocada (quantidade no lugar do preço) envenenaria o histórico."""
        conteudo = "Data;Cultura;Preco\n01/03/2026;Soja;9999999\n"
        _, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert "intervalo aceitável" in erros[0]

    def test_linha_boa_sobrevive_a_linha_ruim(self):
        conteudo = (
            "Data;Cultura;Preco\n"
            "01/03/2026;Soja;100\n"
            "xx;Soja;110\n"
            "03/03/2026;Soja;120\n"
        )
        linhas, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert len(linhas) == 2 and len(erros) == 1


class TestGravacao:
    def test_importa_e_alimenta_a_serie(self, banco_limpo):
        conteudo = (
            "Data;Cultura;Preco;Fonte\n"
            "01/03/2026;Soja;100;Cooperativa\n"
            "02/03/2026;Soja;110;Cooperativa\n"
            "03/03/2026;Soja;120;Cooperativa\n"
        )
        linhas, erros = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        assert erros == []
        resumo = importar_linhas(linhas, user_id=1)
        assert resumo == {"gravadas": 3, "ignoradas": 0}
        assert prices_only("Soja", days=3650) == [100.0, 110.0, 120.0]

    def test_reimportar_o_mesmo_arquivo_nao_duplica(self, banco_limpo):
        conteudo = "Data;Cultura;Preco;Fonte\n01/03/2026;Soja;100;Coop\n"
        linhas, _ = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        importar_linhas(linhas, user_id=1)
        segundo = importar_linhas(linhas, user_id=1)
        assert segundo == {"gravadas": 0, "ignoradas": 1}
        assert len(prices_only("Soja", days=3650)) == 1

    def test_datas_passadas_sao_preservadas(self, banco_limpo):
        """Sem isto o histórico inteiro entraria com a data de hoje."""
        from core.database import q

        conteudo = "Data;Cultura;Preco\n15/01/2026;Soja;100\n"
        linhas, _ = parse_price_csv(conteudo, culturas_validas=CULTURAS)
        importar_linhas(linhas, user_id=1)
        gravado = q("SELECT quoted_at FROM quotes")[0]["quoted_at"]
        assert "2026-01-15" in str(gravado)

    def test_serie_importada_alimenta_os_indicadores(self, banco_limpo):
        from services.market_data import build_market_view

        precos = [90, 95, 100, 105, 110, 115, 120]
        linhas_csv = "\n".join(
            f"{i + 1:02d}/03/2026;Soja;{p}" for i, p in enumerate(precos)
        )
        linhas, erros = parse_price_csv(
            "Data;Cultura;Preco\n" + linhas_csv, culturas_validas=CULTURAS
        )
        assert erros == []
        importar_linhas(linhas, user_id=1)
        view = build_market_view("Soja", required_price=100, days=3650,
                                 short_window=3, long_window=6)
        assert view["summary"]["count"] == 7
        assert view["summary"]["trend"] == "alta"
        assert view["signal"]["level"] == "favoravel"
