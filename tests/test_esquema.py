"""Esquema do banco: colunas que o aplicativo consulta e migrações aditivas."""
import pathlib
import subprocess
import sys
import textwrap

from core.database import init_db, insert_id, q, table_columns


def test_init_db_e_idempotente():
    init_db()
    init_db()


def test_colunas_de_catalogo_que_o_app_consulta():
    assert "unit_id" in table_columns("products")
    assert {"document", "city", "state"} <= table_columns("companies")


def test_products_nao_volta_a_exigir_unit_code():
    """O DDL duplicado exigia unit_code NOT NULL e quebraria o cadastro."""
    assert "unit_code" not in table_columns("products")


def test_inserts_reais_do_cadastro(banco_limpo):
    unidade = q("SELECT id FROM units WHERE code='KG'")[0]["id"]
    insert_id("INSERT INTO products(name,unit_id) VALUES('Adubo',:u)", {"u": unidade})
    insert_id(
        "INSERT INTO companies(name,document,city,state) VALUES('Agro','1','Santo Angelo','RS')",
        {},
    )


def test_banco_legado_recebe_unit_id_sem_perder_coluna(tmp_path):
    """Banco nascido do esquema antigo deve ser reparado sem nada ser removido.

    Roda em subprocesso porque `core.config` fixa a URL do banco no import.
    """
    banco = (tmp_path / "legado.db").as_posix()
    raiz = pathlib.Path(__file__).resolve().parent.parent.as_posix()
    script = textwrap.dedent("""
        import os, sqlite3, sys
        banco, raiz = sys.argv[1], sys.argv[2]

        con = sqlite3.connect(banco)
        con.execute("CREATE TABLE products(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " name VARCHAR(180), unit_code VARCHAR(12), active BOOLEAN)")
        con.execute("CREATE TABLE companies(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " name VARCHAR(180))")
        con.commit(); con.close()

        os.environ["DATABASE_URL"] = "sqlite:///" + banco
        sys.path.insert(0, raiz)
        from core.database import init_db, table_columns
        init_db()

        produtos, empresas = table_columns("products"), table_columns("companies")
        assert "unit_id" in produtos, "migracao nao adicionou unit_id"
        assert "unit_code" in produtos, "coluna antiga foi removida"
        assert {"document", "city", "state"} <= empresas, "colunas de empresa ausentes"
    """)
    r = subprocess.run([sys.executable, "-c", script, banco, raiz],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
