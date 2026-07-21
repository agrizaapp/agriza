"""Configuração comum dos testes.

`core.config` lê `DATABASE_URL` no momento do import, então a variável precisa
existir antes de qualquer import do projeto. Por isso isto fica no topo do
conftest, que o pytest carrega antes dos módulos de teste.
"""
import os
import pathlib
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_banco = pathlib.Path(tempfile.mkdtemp(prefix="agriza_teste_")) / "teste.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_banco}"

import pytest  # noqa: E402

from core.database import ex, init_db  # noqa: E402

TABELAS = [
    "payments", "sales", "commitments", "purchase_contracts", "machinery",
    "seasons", "quotes", "products", "companies", "activity_log",
    "fundamentals",
]


@pytest.fixture(scope="session", autouse=True)
def esquema():
    init_db()
    # Sem isto o app.py para na tela de primeira configuração e nada é renderizado.
    from services.auth import save_setting

    save_setting("setup_complete", "1")


@pytest.fixture
def banco_limpo(esquema):
    """Cada teste começa sem lançamentos, mas com o esquema já criado."""
    for tabela in TABELAS:
        ex(f"DELETE FROM {tabela}")
    yield


@pytest.fixture
def safra(banco_limpo):
    """Safra de números redondos: 6.000 sc previstas, custo total R$ 500.000."""
    from core.database import insert_id, q

    safra_id = insert_id(
        """INSERT INTO seasons(name,crop,area_ha,cost_ha,yield_sc_ha,margin_pct,active)
           VALUES('Soja 2026/27','Soja',100,5000,60,20,TRUE)""",
        {},
    )
    return q("SELECT * FROM seasons WHERE id=:i", {"i": safra_id})[0]
