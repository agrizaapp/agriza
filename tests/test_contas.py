"""Checklist de contas a pagar, exercitando o app.py real."""
import pathlib

import pytest
from streamlit.testing.v1 import AppTest

from core.database import insert_id, q

APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")


def _abrir(filtro="A pagar", papel="admin"):
    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state["user"] = {
        "id": 1, "name": "T", "email": "t@t.com", "role": papel, "active": True,
    }
    at.session_state["current_page"] = "🧾 Contas e pagamentos"
    at.session_state["cookie_read_attempted"] = True
    at.session_state["account_payment_filter"] = filtro
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.fixture
def conta(banco_limpo):
    safra = insert_id(
        """INSERT INTO seasons(name,crop,area_ha,cost_ha,yield_sc_ha,margin_pct,active)
           VALUES('Soja 2026/27','Soja',100,5000,60,20,TRUE)""", {})
    return insert_id(
        """INSERT INTO commitments(season_id,category,description,supplier,
                                   total_value,due_date,status)
           VALUES(:s,'Insumos','Adubo NPK','Agro',50000,'2026-12-01','aberto')""",
        {"s": safra},
    )


class TestChecklist:
    def test_conta_aberta_aparece_desmarcada(self, conta):
        at = _abrir()
        assert len(at.checkbox) == 1
        assert at.checkbox[0].value is False
        assert "Adubo NPK" in at.checkbox[0].label

    def test_rotulo_enxuto_cabe_no_celular(self, conta):
        """Descrição · data · valor. Vencimento e saldo ficam na tabela acima."""
        at = _abrir()
        rotulo = at.checkbox[0].label
        assert rotulo == "Adubo NPK · 01/12/2026 · R$ 50.000,00"
        assert "vence" not in rotulo and "falta" not in rotulo

    def test_marcar_nao_grava_sem_confirmar(self, conta):
        """Regra do projeto: nada é gravado antes da confirmação."""
        at = _abrir()
        at.checkbox[0].set_value(True).run()
        assert q("SELECT id FROM payments") == []
        assert q("SELECT status FROM commitments")[0]["status"] == "aberto"

    def test_marcar_e_confirmar_baixa_a_conta(self, conta):
        at = _abrir()
        at.checkbox[0].set_value(True).run()
        at.button(key="confirmar_checklist_contas").click().run()

        pagamentos = q("SELECT amount,notes FROM payments")
        assert len(pagamentos) == 1
        assert float(pagamentos[0]["amount"]) == 50000.0
        assert q("SELECT status FROM commitments")[0]["status"] == "encerrado"

    def test_descartar_nao_grava_nada(self, conta):
        at = _abrir()
        at.checkbox[0].set_value(True).run()
        at.button(key="descartar_checklist_contas").click().run()
        assert q("SELECT id FROM payments") == []
        assert q("SELECT status FROM commitments")[0]["status"] == "aberto"

    def test_desmarcar_reabre_a_conta(self, conta):
        at = _abrir()
        at.checkbox[0].set_value(True).run()
        at.button(key="confirmar_checklist_contas").click().run()
        assert q("SELECT status FROM commitments")[0]["status"] == "encerrado"

        at = _abrir(filtro="Todas")
        at.checkbox[0].set_value(False).run()
        at.button(key="confirmar_checklist_contas").click().run()

        assert q("SELECT id FROM payments") == []
        assert q("SELECT status FROM commitments")[0]["status"] == "aberto"

    def test_reabrir_preserva_pagamento_lancado_em_outra_tela(self, conta):
        """Só a baixa desta tela é removida; o resto é dado do produtor."""
        insert_id(
            """INSERT INTO payments(commitment_id,payment_date,amount,notes)
               VALUES(:c,'2026-06-01',20000,'Pagamento parcial no banco')""",
            {"c": conta},
        )
        at = _abrir()
        at.checkbox[0].set_value(True).run()
        at.button(key="confirmar_checklist_contas").click().run()
        assert len(q("SELECT id FROM payments")) == 2

        at = _abrir(filtro="Todas")
        at.checkbox[0].set_value(False).run()
        at.button(key="confirmar_checklist_contas").click().run()

        restantes = q("SELECT notes FROM payments")
        assert len(restantes) == 1
        assert restantes[0]["notes"] == "Pagamento parcial no banco"

    def test_sem_alteracao_nao_aparece_confirmacao(self, conta):
        at = _abrir()
        rotulos = [b.label for b in at.button]
        assert not any("Confirmar alterações" in str(r) for r in rotulos)

    def test_perfil_consulta_nao_ve_o_checklist(self, conta):
        at = _abrir(papel="consulta")
        assert len(at.checkbox) == 0
