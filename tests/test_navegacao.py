"""Navegação e permissões de menu, exercitando o app.py real via AppTest."""
import pathlib

import pytest
from streamlit.testing.v1 import AppTest

APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


def _abrir(papel, pagina, **estado):
    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state["user"] = {
        "id": 1, "name": "Teste", "email": "t@t.com", "role": papel, "active": True,
    }
    at.session_state["current_page"] = pagina
    at.session_state["cookie_read_attempted"] = True
    for chave, valor in estado.items():
        at.session_state[chave] = valor
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _menu(at):
    return [b.label for b in at.button if b.key and b.key.startswith("nav_")]


class TestMenu:
    def test_admin_alcanca_usuarios_e_backup(self):
        """Regressão: a página de Usuários ficou órfã, sem nenhum caminho até ela."""
        menu = _menu(_abrir("admin", "🏠 Início"))
        assert "👥 Usuários" in menu
        assert "📦 BACKUP" in menu

    @pytest.mark.parametrize("papel", ["operador", "consulta"])
    def test_papel_sem_privilegio_nao_ve_area_de_admin(self, papel):
        menu = _menu(_abrir(papel, "🏠 Início"))
        assert "👥 Usuários" not in menu
        assert "📦 BACKUP" not in menu

    def test_pagina_de_usuarios_renderiza(self):
        at = _abrir("admin", "👥 Usuários")
        assert "Usuários da família" in [s.value for s in at.subheader]


class TestCamposDeSafraNosLancamentos:
    """Regressão: os formulários ativos não pediam a safra e gravavam nulo."""

    @pytest.fixture(autouse=True)
    def catalogo(self, banco_limpo):
        from core.database import insert_id, q

        unidade = q("SELECT id FROM units WHERE code='KG'")[0]["id"]
        insert_id("INSERT INTO companies(name) VALUES('Agro Insumos')", {})
        insert_id("INSERT INTO products(name,unit_id) VALUES('Adubo',:u)", {"u": unidade})
        insert_id(
            """INSERT INTO seasons(name,crop,area_ha,cost_ha,yield_sc_ha,margin_pct,active)
               VALUES('Soja 2026/27','Soja',100,5000,60,20,TRUE)""",
            {},
        )

    def test_compra_de_insumo_pede_a_safra(self):
        at = _abrir("admin", "🛒 Compras", purchase_show_history=False)
        assert "Safra" in [s.label for s in at.selectbox]

    def test_compra_de_maquina_pede_a_safra(self):
        at = _abrir("admin", "🚜 Máquinas e financiamentos",
                    machine_screen_v31="➕ Nova máquina")
        assert "Safra que responde pelas parcelas" in [s.label for s in at.selectbox]

    def test_compra_de_insumo_grava_a_safra_no_banco(self):
        """O que importa não é o campo existir, e sim o valor chegar ao banco.

        Sem esta verificação, remover season_id do INSERT passa despercebido.
        """
        from core.database import q

        at = _abrir("admin", "🛒 Compras", purchase_show_history=False)
        at.number_input(key="insumo_quantity_v31").set_value(100.0)
        at.number_input(key="insumo_unit_price_v31").set_value(50.0)
        at.run()

        at.button(key="review_insumo_purchase_v31").click().run()
        assert not at.exception, [e.value for e in at.exception]
        at.button(key="save_insumo_purchase_v31").click().run()
        assert not at.exception, [e.value for e in at.exception]

        linhas = q("SELECT season_id,total_value,category FROM commitments")
        assert len(linhas) == 1, f"esperava 1 compromisso, veio {len(linhas)}"
        assert linhas[0]["season_id"] is not None, "compra gravada sem vinculo de safra"
        assert float(linhas[0]["total_value"]) == 5000.0
        assert linhas[0]["category"] == "Insumos"


class TestPaginaMercado:
    def test_renderiza_sem_dados(self, banco_limpo):
        at = _abrir("admin", "📈 Mercado regional")
        assert any("Inteligência de mercado" in str(m.value) for m in at.markdown)

    def test_painel_com_serie_mostra_indicadores(self, banco_limpo):
        from services.market_data.history import record_quote

        for preco in [90, 95, 100, 105, 110, 115, 120]:
            record_quote("Soja", preco, source="teste")
        at = _abrir("admin", "📈 Mercado regional", market_analysis_crop="Soja")
        assert not at.exception, [e.value for e in at.exception]
        rotulos = [m.label for m in at.metric]
        assert "Posição no histórico" in rotulos
        assert "Tendência" in rotulos
