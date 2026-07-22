"""Regras de safra e de proteção de compromissos."""
from core.database import ex, insert_id
from services.analytics import agroia_recommendation, commitment_statuses, season_summary


def _venda(safra_id, quantidade, preco, commitment_id=None):
    return insert_id(
        """INSERT INTO sales(season_id,sale_date,quantity_sc,price_sc,commitment_id)
           VALUES(:s,'2026-03-01',:q,:p,:c)""",
        {"s": safra_id, "q": quantidade, "p": preco, "c": commitment_id},
    )


def _compromisso(safra_id, valor=300000, status="aberto"):
    return insert_id(
        """INSERT INTO commitments(season_id,category,description,total_value,due_date,status)
           VALUES(:s,'Insumos','Adubo',:v,'2026-12-01',:st)""",
        {"s": safra_id, "v": valor, "st": status},
    )


class TestResumoDaSafra:
    def test_sem_vendas(self, safra):
        r = season_summary(safra)
        assert r["estimated_production"] == 6000
        assert r["total_cost"] == 500000
        assert r["balance"] == 6000
        assert r["cost_per_sc"] == pytest_aprox(83.3333)
        # margem de 20% sobre R$ 500.000 = R$ 600.000 a obter em 6.000 sc
        assert r["required_price"] == 100.0

    def test_venda_reduz_saldo_e_preco_necessario(self, safra):
        _venda(safra["id"], 1000, 120)
        r = season_summary(safra)
        assert r["sold"] == 1000
        assert r["revenue"] == 120000
        assert r["balance"] == 5000
        assert r["average"] == 120
        # faltam R$ 480.000 em 5.000 sc
        assert r["required_price"] == 96.0

    def test_producao_colhida_substitui_a_estimativa(self, safra):
        ex("UPDATE seasons SET actual_production_sc=7200 WHERE id=:i", {"i": safra["id"]})
        safra = dict(safra, actual_production_sc=7200)
        r = season_summary(safra)
        assert r["production"] == 7200
        assert r["variance_sc"] == 1200
        assert r["variance_pct"] == 20.0
        assert r["actual_yield_sc_ha"] == 72.0

    def test_nao_vende_mais_que_a_producao(self, safra):
        _venda(safra["id"], 9999, 100)
        assert season_summary(safra)["balance"] == 0


class TestProtecaoDeCompromissos:
    def test_venda_vinculada_protege_o_compromisso(self, safra):
        cid = _compromisso(safra["id"], valor=100000)
        _venda(safra["id"], 500, 100, commitment_id=cid)
        s = commitment_statuses()[cid]
        assert s["protected"] == 50000
        assert s["remaining"] == 50000
        assert s["pct"] == 50.0

    def test_cobertura_nao_passa_de_cem_por_cento(self, safra):
        cid = _compromisso(safra["id"], valor=100000)
        _venda(safra["id"], 2000, 100, commitment_id=cid)
        s = commitment_statuses()[cid]
        assert s["covered"] == 100000
        assert s["remaining"] == 0


class TestVinculoDeSafra:
    """Regressão: compromisso sem season_id sumia do cálculo de saldo descoberto.

    Era o efeito do bug em que os fluxos ativos de compra gravavam season_id
    nulo — o AgroIA passava a enxergar zero de pressão de caixa para todo mundo.
    """

    def _descoberto(self, safra):
        insert_id("INSERT INTO quotes(crop,price_sc,source) VALUES('Soja',100,'teste')", {})
        detalhe = [d for d in agroia_recommendation(safra)["details"] if "descobert" in d][0]
        return detalhe

    def test_compromisso_sem_safra_fica_invisivel(self, safra):
        _compromisso(None, valor=300000)
        assert "R$ 0,00" in self._descoberto(safra)

    def test_compromisso_com_safra_entra_na_conta(self, safra):
        _compromisso(safra["id"], valor=300000)
        assert "R$ 300.000,00" in self._descoberto(safra)

    def test_compromisso_encerrado_nao_pesa(self, safra):
        _compromisso(safra["id"], valor=300000, status="encerrado")
        assert "R$ 0,00" in self._descoberto(safra)


class TestFundamentoNaRecomendacao:
    """O fundamento de oferta entra como razão explícita da recomendação."""

    def _cotacao(self):
        insert_id("INSERT INTO quotes(crop,price_sc,source) VALUES('Soja',100,'teste')", {})

    def _safra_americana(self, valores):
        from services.market_data.fundamentals_store import salvar_fundamento

        for indice, valor in enumerate(valores):
            salvar_fundamento({
                "commodity": "SOYBEANS", "statistic": "YIELD", "unidade": "BU / ACRE",
                "regiao": "UNITED STATES", "ano": 2021 + indice, "valor": valor,
            })

    def test_safra_grande_aparece_como_motivo(self, safra):
        self._cotacao()
        self._safra_americana([50.0, 50.0, 50.0, 60.0])
        detalhes = " ".join(agroia_recommendation(safra)["details"])
        assert "Safra americana" in detalhes
        assert "acima da média" in detalhes
        assert "pressionar o preço para baixo" in detalhes

    def test_safra_pequena_aparece_como_motivo(self, safra):
        self._cotacao()
        self._safra_americana([50.0, 50.0, 50.0, 40.0])
        detalhes = " ".join(agroia_recommendation(safra)["details"])
        assert "abaixo da média" in detalhes
        assert "sustentar o preço" in detalhes

    def test_sem_fundamento_a_recomendacao_segue_igual(self, safra):
        """Ausência de dado externo não pode alterar o comportamento."""
        self._cotacao()
        recomendacao = agroia_recommendation(safra)
        assert not any("Safra americana" in d for d in recomendacao["details"])
        assert recomendacao["title"]  # continua produzindo recomendação normal

    def test_fundamento_nao_altera_o_nivel(self, safra):
        """O nível segue ancorado em preço e pressão de caixa, por rastreabilidade."""
        self._cotacao()
        sem_fundamento = agroia_recommendation(safra)["level"]
        self._safra_americana([50.0, 50.0, 50.0, 60.0])
        com_fundamento = agroia_recommendation(safra)["level"]
        assert sem_fundamento == com_fundamento


def pytest_aprox(valor):
    import pytest

    return pytest.approx(valor, rel=1e-4)
