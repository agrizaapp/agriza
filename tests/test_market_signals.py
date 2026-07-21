"""Leitura de mercado (signals) e série histórica (history)."""
from datetime import datetime, timedelta

from core.database import ex
from services.market_data.history import distinct_crops, prices_only, record_quote
from services.market_data.indicators import summarize_series
from services.market_data.signals import build_market_view, evaluate_market


def _resumo(precos):
    return summarize_series(precos, short_window=3, long_window=6)


class TestLeituraDeMercado:
    def test_sem_dados(self):
        s = evaluate_market(_resumo([]), required_price=100)
        assert s["level"] == "sem_dados"
        assert s["suggested_sell_pct"] is None

    def test_preco_alto_e_cobre_margem_e_favoravel(self):
        # série subindo até 120; necessário 100 → cobre a margem, percentil alto
        precos = [90, 95, 100, 105, 110, 115, 120]
        s = evaluate_market(_resumo(precos), required_price=100)
        assert s["level"] == "favoravel"
        assert s["suggested_sell_pct"] >= 10
        assert any("necessário" in f for f in s["factors"])

    def test_preco_baixo_e_desfavoravel(self):
        # preço atual no fundo da série e abaixo do necessário
        precos = [120, 115, 110, 105, 100, 95, 90]
        s = evaluate_market(_resumo(precos), required_price=110)
        assert s["level"] == "desfavoravel"
        assert s["suggested_sell_pct"] == 0

    def test_cobre_margem_mesmo_sem_topo_historico(self):
        # preço no meio do histórico, mas acima do necessário
        precos = [80, 90, 100, 110, 120, 105, 100]
        s = evaluate_market(_resumo(precos), required_price=95)
        assert s["level"] == "favoravel"

    def test_zona_intermediaria_pede_cautela(self):
        precos = [95, 100, 105, 100, 98, 101, 100]
        s = evaluate_market(_resumo(precos), required_price=130)
        assert s["level"] in ("cautela", "desfavoravel")

    def test_sempre_traz_justificativa(self):
        precos = [90, 95, 100, 105, 110, 115, 120]
        s = evaluate_market(_resumo(precos), required_price=100)
        assert s["factors"], "a leitura deve sempre explicar o porquê"


class TestSerieHistorica:
    def test_grava_e_le_em_ordem_cronologica(self, banco_limpo):
        for preco in [100, 105, 110]:
            record_quote("Soja", preco, source="teste", region="RS")
        precos = prices_only("Soja", days=365)
        assert precos == [100.0, 105.0, 110.0]

    def test_distinct_crops(self, banco_limpo):
        record_quote("Soja", 100, source="t")
        record_quote("Milho", 60, source="t")
        record_quote("Soja", 101, source="t")
        assert set(distinct_crops()) == {"Soja", "Milho"}

    def test_filtro_por_janela_de_dias(self, banco_limpo):
        record_quote("Trigo", 70, source="t")
        # empurra a data para fora da janela de 30 dias
        ex("UPDATE quotes SET quoted_at = :d WHERE crop='Trigo'",
           {"d": datetime.utcnow() - timedelta(days=90)})
        assert prices_only("Trigo", days=30) == []
        assert prices_only("Trigo", days=180) == [70.0]

    def test_build_market_view_ponta_a_ponta(self, banco_limpo):
        for preco in [90, 95, 100, 105, 110, 115, 120]:
            record_quote("Soja", preco, source="teste")
        view = build_market_view("Soja", required_price=100,
                                 short_window=3, long_window=6)
        assert view["crop"] == "Soja"
        assert view["summary"]["count"] == 7
        assert view["signal"]["level"] == "favoravel"
