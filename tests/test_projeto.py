"""Regras estruturais do projeto, documentadas em CODEX_CONTEXT.md.

São critérios de aceite que antes só existiam como texto. Aqui viram
verificação automática, para não dependerem de alguém lembrar.
"""
import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent.parent

MODULOS_LEGADOS = [
    "app_contratos_login",
    "auth_contratos_login",
    "database_contratos_login",
]

CODIGO_ATIVO = [
    RAIZ / "app.py",
    RAIZ / "market_prices.py",
    *(RAIZ / "core").glob("*.py"),
    *(RAIZ / "services").rglob("*.py"),
    *(RAIZ / "modules").glob("*.py"),
]


def test_nenhum_import_ativo_de_arquivo_legado():
    """Os arquivos legados servem só para consulta; não podem ser importados."""
    padrao = re.compile(
        r"^\s*(?:from|import)\s+(" + "|".join(MODULOS_LEGADOS) + r")\b",
        re.MULTILINE,
    )
    infratores = []
    for arquivo in CODIGO_ATIVO:
        if not arquivo.is_file():
            continue
        texto = arquivo.read_text(encoding="utf-8", errors="ignore")
        if padrao.search(texto):
            infratores.append(arquivo.relative_to(RAIZ).as_posix())
    assert not infratores, f"import de módulo legado em: {infratores}"


MARCA_SQL_DINAMICO = "sql-dinamico-ok"


def test_sql_nao_interpola_dado_do_usuario():
    """Consultas devem usar parâmetros; f-string em SQL é vetor de injeção.

    Onde o SQL dinâmico for realmente necessário (DDL a partir de nomes
    internos, por exemplo), a linha precisa declarar-se com o comentário
    ``# sql-dinamico-ok: <motivo>``. Assim a exceção é consciente e revisável,
    em vez de silenciosa — e qualquer f-string nova falha até ser justificada.
    """
    padrao = re.compile(r"\b(?:q|ex|scalar|insert_id)\(\s*f[\"']")
    infratores = []
    for arquivo in CODIGO_ATIVO:
        if not arquivo.is_file():
            continue
        linhas = arquivo.read_text(encoding="utf-8", errors="ignore").splitlines()
        for indice, linha in enumerate(linhas):
            if not padrao.search(linha):
                continue
            # A justificativa pode estar na própria linha ou num comentário
            # logo acima, para não obrigar tudo a caber numa linha só.
            contexto = linhas[max(0, indice - 4):indice + 1]
            if any(MARCA_SQL_DINAMICO in c for c in contexto):
                continue
            infratores.append(f"{arquivo.relative_to(RAIZ).as_posix()}:{indice + 1}")
    assert not infratores, (
        "SQL montado por f-string sem justificativa em: "
        f"{infratores}. Use parâmetros ou declare '# {MARCA_SQL_DINAMICO}: <motivo>'."
    )


def test_fontes_de_bolsa_licenciada_nao_sao_coletadas():
    """B3/CME/CBOT são dado licenciado: não pode haver coletor ativo para eles.

    Menção em comentário/documentação é permitida; o que não pode é uma fonte
    marcada como disponível apontando para essas bolsas.
    """
    from services.market_data.sources import available_sources

    proibidas = {"b3", "cme", "cbot"}
    ativas = {s.key.lower() for s in available_sources()}
    assert not (ativas & proibidas), (
        f"fonte de bolsa licenciada marcada como disponível: {ativas & proibidas}"
    )
