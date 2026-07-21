"""Importação de histórico de preços a partir de planilha CSV.

O motor de mercado só é útil com série histórica, e cadastrar preço a preço não
é caminho. Aqui o produtor traz de uma vez o que já tem — exportado do Excel,
de uma cooperativa ou de um controle proprio.

O parser é deliberadamente tolerante com o que sai do Excel em português:
separador `;`, vírgula decimal, `R$` no valor, datas `DD/MM/AAAA`, acentos e
maiúsculas nos cabeçalhos. O que ele **não** faz é adivinhar: linha que não dá
para interpretar com segurança vira erro com o número da linha, para a pessoa
corrigir na origem.
"""
from __future__ import annotations

import csv
import io
import unicodedata
from datetime import date, datetime, timedelta

# Cabeçalhos aceitos para cada campo, já normalizados (sem acento, minúsculos).
ALIASES = {
    "data": {"data", "date", "dia", "datacotacao", "data cotacao", "data_cotacao"},
    "cultura": {"cultura", "produto", "crop", "commodity", "grao", "cultivo"},
    "preco": {"preco", "price", "valor", "precosc", "valorsc", "preco_sc",
              "valor_sc", "preco por saca", "rs", "r$"},
    "fonte": {"fonte", "source", "origem", "comprador"},
    "regiao": {"regiao", "praca", "region", "local", "cidade", "municipio"},
}

OBRIGATORIOS = ("data", "cultura", "preco")

# Limites de sanidade: protegem contra coluna trocada (ex.: quantidade no lugar
# do preço), que passaria silenciosamente e envenenaria os indicadores.
PRECO_MINIMO = 0.01
PRECO_MAXIMO = 100_000.0


def _sem_acento(texto):
    normalizado = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def _normalizar_cabecalho(nome):
    return _sem_acento(nome).strip().lower().replace("-", "").replace(".", "")


def _mapear_colunas(cabecalhos):
    """Descobre qual coluna do arquivo corresponde a cada campo conhecido."""
    mapa = {}
    for indice, bruto in enumerate(cabecalhos):
        chave = _normalizar_cabecalho(bruto)
        compacto = chave.replace(" ", "").replace("_", "")
        for campo, nomes in ALIASES.items():
            if campo in mapa:
                continue
            if chave in nomes or compacto in {n.replace(" ", "").replace("_", "") for n in nomes}:
                mapa[campo] = indice
                break
    return mapa


def parse_numero(valor):
    """Converte número em formato brasileiro ou internacional para float."""
    texto = _sem_acento(valor).replace("R$", "").replace("r$", "").strip()
    texto = texto.replace(" ", "").replace("\xa0", "")
    if not texto:
        return None
    if "," in texto and "." in texto:
        # o separador decimal é o que aparece por último
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


FORMATOS_DATA = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%Y/%m/%d")


def parse_data(valor):
    """Interpreta data em formatos comuns; prioriza o brasileiro DD/MM/AAAA."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    texto = texto.split(" ")[0]  # descarta hora, se vier junto
    for formato in FORMATOS_DATA:
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _decodificar(conteudo):
    if isinstance(conteudo, str):
        return conteudo
    for codificacao in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1", errors="replace")


def _detectar_separador(texto):
    primeira = texto.splitlines()[0] if texto.splitlines() else ""
    candidatos = {sep: primeira.count(sep) for sep in (";", ",", "\t")}
    melhor = max(candidatos, key=candidatos.get)
    return melhor if candidatos[melhor] > 0 else ","


def parse_price_csv(conteudo, *, culturas_validas=None):
    """Lê o CSV e devolve ``(linhas, erros)``.

    Cada linha válida é ``{"data": date, "cultura": str, "preco": float,
    "fonte": str|None, "regiao": str|None}``. Cada erro é uma frase em
    português já citando o número da linha do arquivo.
    """
    texto = _decodificar(conteudo)
    if not texto.strip():
        return [], ["O arquivo está vazio."]

    leitor = csv.reader(io.StringIO(texto), delimiter=_detectar_separador(texto))
    try:
        cabecalhos = next(leitor)
    except StopIteration:
        return [], ["O arquivo está vazio."]

    mapa = _mapear_colunas(cabecalhos)
    faltando = [c for c in OBRIGATORIOS if c not in mapa]
    if faltando:
        return [], [
            "Não encontrei as colunas obrigatórias: "
            + ", ".join(faltando)
            + f". Cabeçalhos lidos: {', '.join(h for h in cabecalhos if h)}."
        ]

    linhas, erros = [], []
    hoje = date.today()
    for numero, bruto in enumerate(leitor, start=2):  # 1 é o cabeçalho
        if not any(str(c).strip() for c in bruto):
            continue  # linha em branco

        def coluna(campo):
            indice = mapa.get(campo)
            if indice is None or indice >= len(bruto):
                return ""
            return str(bruto[indice]).strip()

        data = parse_data(coluna("data"))
        cultura = coluna("cultura").strip()
        preco = parse_numero(coluna("preco"))

        if data is None:
            erros.append(f"Linha {numero}: data inválida ({coluna('data')!r}).")
            continue
        if data > hoje:
            erros.append(f"Linha {numero}: data no futuro ({data.strftime('%d/%m/%Y')}).")
            continue
        if not cultura:
            erros.append(f"Linha {numero}: cultura em branco.")
            continue
        if culturas_validas and _sem_acento(cultura).lower() not in {
            _sem_acento(c).lower() for c in culturas_validas
        }:
            erros.append(
                f"Linha {numero}: cultura {cultura!r} não reconhecida "
                f"(esperado: {', '.join(culturas_validas)})."
            )
            continue
        if preco is None:
            erros.append(f"Linha {numero}: preço inválido ({coluna('preco')!r}).")
            continue
        if not (PRECO_MINIMO <= preco <= PRECO_MAXIMO):
            erros.append(
                f"Linha {numero}: preço fora do intervalo aceitável ({preco})."
            )
            continue

        # normaliza a cultura para a grafia oficial, quando houver lista
        if culturas_validas:
            for oficial in culturas_validas:
                if _sem_acento(oficial).lower() == _sem_acento(cultura).lower():
                    cultura = oficial
                    break

        linhas.append({
            "data": data,
            "cultura": cultura,
            "preco": preco,
            "fonte": coluna("fonte") or None,
            "regiao": coluna("regiao") or None,
        })

    if not linhas and not erros:
        erros.append("O arquivo não tem nenhuma linha de dados.")
    return linhas, erros


def importar_linhas(linhas, *, user_id=None, fonte_padrao="Importação de histórico"):
    """Grava as linhas já validadas, pulando o que a série tem igual.

    Duplicata é mesma cultura, mesma data e mesma fonte — assim reimportar o
    mesmo arquivo não infla a série, e o produtor pode complementar sem medo.
    """
    from core.database import q

    from services.market_data.history import record_quote

    gravadas, ignoradas = 0, 0
    for linha in linhas:
        fonte = linha.get("fonte") or fonte_padrao
        inicio_do_dia = datetime.combine(linha["data"], datetime.min.time())
        fim_do_dia = inicio_do_dia + timedelta(days=1)
        # Comparação por intervalo em vez de CAST(... AS DATE): funciona igual
        # em SQLite e PostgreSQL e ainda aproveita índice por data.
        existente = q(
            """SELECT id FROM quotes
               WHERE lower(crop) = lower(:crop)
                 AND quoted_at >= :inicio AND quoted_at < :fim
                 AND COALESCE(lower(source),'') = COALESCE(lower(:source),'')
               LIMIT 1""",
            {
                "crop": linha["cultura"],
                "inicio": inicio_do_dia,
                "fim": fim_do_dia,
                "source": fonte,
            },
        )
        if existente:
            ignoradas += 1
            continue

        record_quote(
            linha["cultura"], linha["preco"],
            source=fonte,
            region=linha.get("regiao"),
            quote_type="importada",
            user_id=user_id,
            quoted_at=inicio_do_dia,
        )
        gravadas += 1

    return {"gravadas": gravadas, "ignoradas": ignoradas}
