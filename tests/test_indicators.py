"""Indicadores de preço — funções puras, casos determinísticos."""
import pytest

from services.market_data.indicators import (
    change_pct,
    exponential_moving_average,
    percentile_position,
    simple_moving_average,
    summarize_series,
    trend,
    volatility_pct,
)


class TestMediasMoveis:
    def test_sma_das_ultimas_observacoes(self):
        assert simple_moving_average([10, 20, 30, 40], 2) == 35
        assert simple_moving_average([10, 20, 30], 3) == 20

    def test_sma_sem_dados_suficientes(self):
        assert simple_moving_average([10, 20], 5) is None
        assert simple_moving_average([], 3) is None

    def test_ema_pesa_mais_o_recente(self):
        precos = [10, 10, 10, 20]
        ema = exponential_moving_average(precos, 3)
        sma = simple_moving_average(precos, 3)
        # com um salto no fim, a EMA reage mais que a SMA
        assert ema > sma

    def test_ema_serie_constante_e_o_proprio_valor(self):
        assert exponential_moving_average([50, 50, 50, 50], 3) == pytest.approx(50)


class TestMediaMovelPontoAPonto:
    """Usada pelo gráfico: precisa ter o mesmo tamanho da série."""

    def test_mesmo_tamanho_da_serie(self):
        from services.market_data.indicators import rolling_average

        assert len(rolling_average([1, 2, 3, 4, 5], 3)) == 5

    def test_pontos_iniciais_ficam_vazios(self):
        from services.market_data.indicators import rolling_average

        r = rolling_average([10, 20, 30, 40], 3)
        assert r[0] is None and r[1] is None
        assert r[2] == 20  # (10+20+30)/3
        assert r[3] == 30  # (20+30+40)/3

    def test_janela_maior_que_a_serie_fica_toda_vazia(self):
        from services.market_data.indicators import rolling_average

        assert rolling_average([10, 20], 30) == [None, None]

    def test_janela_um_devolve_a_propria_serie(self):
        from services.market_data.indicators import rolling_average

        assert rolling_average([10, 20, 30], 1) == [10, 20, 30]


class TestPosicaoNoHistorico:
    def test_maior_preco_fica_no_topo(self):
        assert percentile_position([10, 20, 30, 40], 40) == pytest.approx(87.5)

    def test_menor_preco_fica_no_fundo(self):
        assert percentile_position([10, 20, 30, 40], 10) == pytest.approx(12.5)

    def test_usa_ultimo_preco_por_padrao(self):
        assert percentile_position([10, 20, 30]) == percentile_position([10, 20, 30], 30)

    def test_serie_de_um_ponto_e_neutra(self):
        assert percentile_position([42]) == 50.0

    def test_serie_vazia(self):
        assert percentile_position([]) is None


class TestTendencia:
    def test_alta_quando_recente_supera_a_media(self):
        assert trend([10, 10, 10, 12, 13, 14], short=2, long=6) == "alta"

    def test_baixa_quando_recente_cai(self):
        assert trend([14, 13, 12, 10, 10, 9], short=2, long=6) == "baixa"

    def test_estavel_quando_oscila_pouco(self):
        assert trend([100, 100, 100, 100], short=2, long=4) == "estável"


class TestVolatilidadeEVariacao:
    def test_serie_constante_tem_volatilidade_zero(self):
        assert volatility_pct([50, 50, 50]) == 0.0

    def test_variacao_percentual_do_ultimo_ponto(self):
        assert change_pct([100, 110]) == 10.0
        assert change_pct([100, 90]) == -10.0

    def test_variacao_sem_ponto_anterior(self):
        assert change_pct([100]) is None


class TestResumo:
    def test_serie_vazia_nao_quebra(self):
        r = summarize_series([])
        assert r["count"] == 0
        assert r["current"] is None
        assert r["trend"] == "indefinida"

    def test_resumo_completo(self):
        precos = [80, 85, 90, 95, 100, 105, 110, 115]
        r = summarize_series(precos, short_window=3, long_window=6)
        assert r["count"] == 8
        assert r["current"] == 115
        assert r["min"] == 80
        assert r["max"] == 115
        assert r["percentile"] == pytest.approx(93.8)
        assert r["trend"] == "alta"
        assert r["sma_short"] is not None and r["sma_long"] is not None
