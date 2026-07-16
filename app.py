import os
import io
import csv
import json
import hmac
import hashlib
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, inspect, text


# =========================================================
# CONFIGURAÇÃO
# =========================================================
st.set_page_config(
    page_title="AGRIZA • Piloto 7 dias",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///agriza_local.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
IS_POSTGRES = engine.dialect.name == "postgresql"

st.markdown(
    """
    <style>
    #MainMenu, footer {visibility:hidden;}
    .block-container{max-width:1120px;padding-top:1rem;padding-bottom:5rem}
    .brand{font-size:2rem;font-weight:850;letter-spacing:-.04em}
    .subbrand{opacity:.7;margin-top:-.25rem;margin-bottom:.8rem}
    .card{border:1px solid rgba(60,90,45,.16);border-radius:18px;padding:1rem;margin:.55rem 0;background:rgba(255,255,255,.72)}
    .positive{border-left:6px solid #4F7D32}
    .warning{border-left:6px solid #D39B2A}
    .danger{border-left:6px solid #B74B45}
    div[data-testid="stMetric"]{border:1px solid rgba(60,90,45,.14);border-radius:16px;padding:.75rem;background:rgba(255,255,255,.65)}
    .stButton button,.stFormSubmitButton button{min-height:3rem;border-radius:13px;font-weight:700}
    @media(max-width:700px){
        .block-container{padding-left:.7rem;padding-right:.7rem}
        .brand{font-size:1.65rem}
        div[data-testid="column"]{min-width:100%!important}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# BANCO E SEGURANÇA
# =========================================================
def q(sql, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


def scalar(sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def ex(sql, params=None):
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})


def insert_id(sql, params):
    with engine.begin() as conn:
        if IS_POSTGRES:
            result = conn.execute(text(sql + " RETURNING id"), params)
            return int(result.scalar())
        result = conn.execute(text(sql), params)
        return int(result.lastrowid)


def hpw(password):
    salt = os.urandom(16)
    rounds = 210_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def vpw(password, encoded):
    try:
        _, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            bytes.fromhex(salt_hex),
            int(rounds),
        ).hex()
        return hmac.compare_digest(digest, digest_hex)
    except Exception:
        return False


def table_columns(table_name):
    try:
        return {col["name"] for col in inspect(engine).get_columns(table_name)}
    except Exception:
        return set()


def add_missing_column(table_name, column_name, definition):
    if column_name not in table_columns(table_name):
        ex(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def init_db():
    id_def = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ddl = [
        f"""CREATE TABLE IF NOT EXISTS users(
            id {id_def},
            name VARCHAR(120) NOT NULL,
            email VARCHAR(180) NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role VARCHAR(30) NOT NULL DEFAULT 'operador',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS app_settings(
            setting_key VARCHAR(120) PRIMARY KEY,
            setting_value TEXT NOT NULL
        )""",
        f"""CREATE TABLE IF NOT EXISTS seasons(
            id {id_def},
            name VARCHAR(160) NOT NULL,
            crop VARCHAR(50) NOT NULL,
            area_ha NUMERIC(14,2) NOT NULL,
            cost_ha NUMERIC(14,2) NOT NULL,
            yield_sc_ha NUMERIC(14,2) NOT NULL,
            margin_pct NUMERIC(8,2) NOT NULL DEFAULT 20,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS commitments(
            id {id_def},
            season_id INTEGER,
            category VARCHAR(80) NOT NULL,
            description VARCHAR(240) NOT NULL,
            supplier VARCHAR(180),
            total_value NUMERIC(16,2) NOT NULL,
            due_date DATE NOT NULL,
            payment_crop VARCHAR(80),
            notes TEXT,
            status VARCHAR(30) NOT NULL DEFAULT 'aberto',
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS sales(
            id {id_def},
            season_id INTEGER NOT NULL,
            sale_date DATE NOT NULL,
            quantity_sc NUMERIC(16,2) NOT NULL,
            price_sc NUMERIC(16,2) NOT NULL,
            buyer VARCHAR(180),
            commitment_id INTEGER,
            notes TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS quotes(
            id {id_def},
            crop VARCHAR(50) NOT NULL,
            price_sc NUMERIC(16,2) NOT NULL,
            source VARCHAR(180),
            quoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER
        )""",
        f"""CREATE TABLE IF NOT EXISTS payments(
            id {id_def},
            commitment_id INTEGER NOT NULL,
            payment_date DATE NOT NULL,
            amount NUMERIC(16,2) NOT NULL,
            notes TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS activity_log(
            id {id_def},
            user_id INTEGER,
            action VARCHAR(100) NOT NULL,
            entity VARCHAR(80) NOT NULL,
            entity_id INTEGER,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        f"""CREATE TABLE IF NOT EXISTS pilot_feedback(
            id {id_def},
            user_id INTEGER,
            module VARCHAR(80) NOT NULL,
            feedback_type VARCHAR(50) NOT NULL,
            priority VARCHAR(30) NOT NULL,
            description TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]

    with engine.begin() as conn:
        for statement in ddl:
            conn.execute(text(statement))

    # Migrações para quem já estava usando a versão anterior
    timestamp_column = "TIMESTAMP DEFAULT CURRENT_TIMESTAMP" if IS_POSTGRES else "TIMESTAMP"
    add_missing_column("users", "created_at", timestamp_column)
    add_missing_column("seasons", "created_at", timestamp_column)
    add_missing_column("commitments", "status", "VARCHAR(30) DEFAULT 'aberto'")
    add_missing_column("commitments", "created_at", timestamp_column)
    add_missing_column("sales", "created_at", timestamp_column)


def log_action(user_id, action, entity, entity_id=None, details=""):
    ex(
        """INSERT INTO activity_log(user_id,action,entity,entity_id,details)
           VALUES(:u,:a,:e,:i,:d)""",
        {"u": user_id, "a": action, "e": entity, "i": entity_id, "d": details},
    )


init_db()


# =========================================================
# FORMATAÇÃO E CÁLCULOS
# =========================================================
def money(value):
    value = float(value or 0)
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(value, decimals=1):
    value = float(value or 0)
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def season_summary(season):
    production = float(season["area_ha"]) * float(season["yield_sc_ha"])
    total_cost = float(season["area_ha"]) * float(season["cost_ha"])

    sales = q(
        "SELECT quantity_sc,price_sc FROM sales WHERE season_id=:id",
        {"id": season["id"]},
    )
    sold = sum(float(item["quantity_sc"]) for item in sales)
    revenue = sum(float(item["quantity_sc"]) * float(item["price_sc"]) for item in sales)
    balance = max(production - sold, 0)
    average = revenue / sold if sold else 0
    cost_per_sc = total_cost / production if production else 0
    target_revenue = total_cost * (1 + float(season["margin_pct"]) / 100)
    required_price = max(target_revenue - revenue, 0) / balance if balance else 0

    return {
        "production": production,
        "total_cost": total_cost,
        "sold": sold,
        "sold_pct": sold / production * 100 if production else 0,
        "revenue": revenue,
        "balance": balance,
        "average": average,
        "cost_per_sc": cost_per_sc,
        "required_price": required_price,
    }


def commitment_status(commitment_id):
    row = q("SELECT total_value FROM commitments WHERE id=:id", {"id": commitment_id})
    if not row:
        return {"value": 0, "protected": 0, "paid": 0, "covered": 0, "pct": 0, "remaining": 0}

    value = float(row[0]["total_value"])
    protected = float(
        scalar(
            "SELECT COALESCE(SUM(quantity_sc*price_sc),0) FROM sales WHERE commitment_id=:id",
            {"id": commitment_id},
        ) or 0
    )
    paid = float(
        scalar(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE commitment_id=:id",
            {"id": commitment_id},
        ) or 0
    )
    covered = min(value, protected + paid)
    return {
        "value": value,
        "protected": protected,
        "paid": paid,
        "covered": covered,
        "pct": covered / value * 100 if value else 0,
        "remaining": max(value - covered, 0),
    }


def agroia_recommendation(season):
    summary = season_summary(season)
    quote_rows = q(
        """SELECT price_sc,source,quoted_at FROM quotes
           WHERE crop=:crop ORDER BY quoted_at DESC LIMIT 1""",
        {"crop": season["crop"]},
    )

    commitments = q(
        """SELECT id FROM commitments
           WHERE season_id=:id AND COALESCE(status,'aberto')='aberto'""",
        {"id": season["id"]},
    )
    uncovered = sum(commitment_status(item["id"])["remaining"] for item in commitments)

    if not quote_rows:
        return {
            "level": "warning",
            "title": "Atualize a cotação antes de decidir",
            "message": "O AgroIA precisa da cotação atual para comparar com seu preço necessário.",
            "details": ["Registre a cotação na tela Mercado."],
        }

    quote = float(quote_rows[0]["price_sc"])
    required = summary["required_price"]
    sold_pct = summary["sold_pct"]
    details = [
        f"Cotação atual: {money(quote)}/sc.",
        f"Preço necessário no saldo: {money(required)}/sc.",
        f"Produção já vendida: {num(sold_pct)}%.",
        f"Compromissos ainda descobertos: {money(uncovered)}.",
    ]

    if quote >= required and sold_pct < 40:
        pct = 10 if uncovered > 0 else 5
        return {
            "level": "positive",
            "title": f"Consideraria vender aproximadamente {pct}% da produção",
            "message": "A cotação cobre a margem cadastrada e a venda parcial reduz risco.",
            "details": details,
        }

    if quote < required and uncovered == 0:
        return {
            "level": "warning",
            "title": "Eu aguardaria novas condições de mercado",
            "message": "Não há pressão financeira cadastrada que justifique vender abaixo da meta.",
            "details": details,
        }

    if quote < required and uncovered > 0:
        return {
            "level": "danger",
            "title": "Evite venda ampla; avalie somente o necessário para o caixa",
            "message": "Existem compromissos descobertos, mas o preço atual ainda não alcança sua meta.",
            "details": details,
        }

    return {
        "level": "warning",
        "title": "Mantenha uma estratégia gradual",
        "message": "Sua posição atual pede equilíbrio entre proteção e oportunidade.",
        "details": details,
    }


# =========================================================
# PRIMEIRA CONFIGURAÇÃO E LOGIN
# =========================================================
def setup_complete():
    value = scalar(
        "SELECT setting_value FROM app_settings WHERE setting_key='setup_complete'"
    )
    return value == "1"


def save_setting(key, value):
    if IS_POSTGRES:
        ex(
            """INSERT INTO app_settings(setting_key,setting_value)
               VALUES(:k,:v)
               ON CONFLICT(setting_key) DO UPDATE SET setting_value=EXCLUDED.setting_value""",
            {"k": key, "v": value},
        )
    else:
        ex(
            "INSERT OR REPLACE INTO app_settings(setting_key,setting_value) VALUES(:k,:v)",
            {"k": key, "v": value},
        )


def create_initial_admin(name, email, password):
    email = email.strip().lower()
    existing = q("SELECT id FROM users WHERE lower(email)=:e", {"e": email})
    if existing:
        admin_id = existing[0]["id"]
        ex(
            """UPDATE users SET name=:n,password_hash=:p,role='admin',active=TRUE
               WHERE id=:id""",
            {"n": name.strip(), "p": hpw(password), "id": admin_id},
        )
    else:
        admin_id = insert_id(
            """INSERT INTO users(name,email,password_hash,role,active)
               VALUES(:n,:e,:p,'admin',TRUE)""",
            {"n": name.strip(), "e": email, "p": hpw(password)},
        )
    save_setting("setup_complete", "1")
    return admin_id


st.markdown('<div class="brand">🌱 AGRIZA</div>', unsafe_allow_html=True)
st.markdown('<div class="subbrand">AgroIA • Transformando informação em decisão.</div>', unsafe_allow_html=True)

if not setup_complete():
    st.subheader("Primeira configuração")
    with st.form("setup"):
        name = st.text_input("Seu nome", value="Fabio")
        email = st.text_input("Seu e-mail")
        password = st.text_input("Crie uma senha", type="password")
        confirm = st.text_input("Confirme a senha", type="password")
        submit = st.form_submit_button("Criar administrador", use_container_width=True)

    if submit:
        if not name.strip():
            st.error("Informe seu nome.")
        elif "@" not in email or "." not in email.split("@")[-1]:
            st.error("Informe um e-mail válido.")
        elif len(password) < 8:
            st.error("A senha precisa ter pelo menos 8 caracteres.")
        elif password != confirm:
            st.error("As senhas não são iguais.")
        else:
            admin_id = create_initial_admin(name, email, password)
            st.session_state.user = {
                "id": admin_id,
                "name": name.strip(),
                "email": email.strip().lower(),
                "role": "admin",
                "active": True,
            }
            st.rerun()
    st.stop()

if "user" not in st.session_state:
    with st.form("login"):
        email = st.text_input("E-mail").strip().lower()
        password = st.text_input("Senha", type="password")
        submit = st.form_submit_button("Entrar", use_container_width=True)

    if submit:
        rows = q(
            "SELECT * FROM users WHERE lower(email)=:e AND active=TRUE",
            {"e": email},
        )
        if rows and vpw(password, rows[0]["password_hash"]):
            st.session_state.user = {
                key: value for key, value in rows[0].items() if key != "password_hash"
            }
            st.rerun()
        else:
            st.error("E-mail ou senha incorretos.")
    st.stop()

user = st.session_state.user
CAN_EDIT = user["role"] in ("admin", "operador")

top_left, top_right = st.columns([4, 1])
top_left.caption(f"Olá, **{user['name']}** · {user['role'].capitalize()}")
if top_right.button("Sair", use_container_width=True):
    st.session_state.pop("user", None)
    st.rerun()

pages = [
    "🏠 Painel",
    "🌾 Safras",
    "🛒 Compras",
    "💰 Vendas",
    "📈 Mercado",
    "🧪 Teste 7 dias",
]
if user["role"] == "admin":
    pages.extend(["👥 Usuários", "📦 Backup"])

page = st.selectbox("Menu", pages, label_visibility="collapsed")


# =========================================================
# PÁGINAS
# =========================================================
if page == "🏠 Painel":
    st.subheader("Painel de decisão")
    seasons = q("SELECT * FROM seasons WHERE active=TRUE ORDER BY id DESC")

    if not IS_POSTGRES:
        st.error(
            "Atenção: o sistema está usando banco local temporário. "
            "Antes de inserir dados reais, configure DATABASE_URL no Render."
        )

    if not seasons:
        st.info("Cadastre a primeira safra em **🌾 Safras**.")
    else:
        labels = {f"{item['name']} · {item['crop']}": item for item in seasons}
        season = labels[st.selectbox("Safra ativa", list(labels))]
        summary = season_summary(season)

        c1, c2, c3 = st.columns(3)
        c1.metric("Produção estimada", f"{num(summary['production'], 0)} sc")
        c2.metric("Já vendido", f"{num(summary['sold_pct'])}%")
        c3.metric("Saldo livre", f"{num(summary['balance'], 0)} sc")

        c4, c5, c6 = st.columns(3)
        c4.metric("Custo por saca", money(summary["cost_per_sc"]))
        c5.metric("Preço médio vendido", money(summary["average"]))
        c6.metric("Preço necessário", money(summary["required_price"]))

        rec = agroia_recommendation(season)
        st.markdown(
            f"""<div class="card {rec['level']}">
            <small>RECOMENDAÇÃO AGROIA</small>
            <h3>{rec['title']}</h3>
            <div>{rec['message']}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        with st.expander("Ver os motivos"):
            for detail in rec["details"]:
                st.write("•", detail)
            st.caption(
                "A recomendação é apoio gerencial baseado nos dados cadastrados. "
                "Não garante resultado e não substitui assessoria especializada."
            )

        commitments = q(
            """SELECT * FROM commitments
               WHERE season_id=:id AND COALESCE(status,'aberto')='aberto'
               ORDER BY due_date""",
            {"id": season["id"]},
        )
        st.markdown("### Proteção dos compromissos")
        if not commitments:
            st.caption("Nenhum compromisso aberto vinculado a esta safra.")
        else:
            total = 0
            covered = 0
            for item in commitments:
                status = commitment_status(item["id"])
                total += status["value"]
                covered += status["covered"]
                icon = "🟢" if status["pct"] >= 99 else "🟡" if status["pct"] >= 50 else "🔴"
                st.markdown(
                    f"""<div class="card">
                    <b>{icon} {item['description']}</b><br>
                    Vence em {item['due_date']} · {money(item['total_value'])}<br>
                    Proteção: {num(status['pct'])}% · Falta {money(status['remaining'])}
                    </div>""",
                    unsafe_allow_html=True,
                )
            pct = covered / total * 100 if total else 0
            st.progress(min(pct / 100, 1.0))
            st.write(f"**Índice de proteção: {num(pct, 0)} de 100**")

        st.markdown("### Próximos vencimentos")
        upcoming = q(
            """SELECT description,due_date,total_value FROM commitments
               WHERE COALESCE(status,'aberto')='aberto'
               AND due_date BETWEEN CURRENT_DATE AND :limit
               ORDER BY due_date""",
            {"limit": date.today() + timedelta(days=45)},
        )
        if not upcoming:
            st.caption("Nenhum vencimento nos próximos 45 dias.")
        else:
            for item in upcoming:
                st.write(
                    f"• **{item['due_date']}** — {item['description']} — {money(item['total_value'])}"
                )


elif page == "🌾 Safras":
    st.subheader("Safras")

    if CAN_EDIT:
        with st.expander("➕ Nova safra", expanded=not bool(q("SELECT id FROM seasons LIMIT 1"))):
            with st.form("new_season", clear_on_submit=True):
                name = st.text_input("Nome", placeholder="Ex.: Soja 2026/27")
                crop = st.selectbox("Cultura", ["Soja", "Milho", "Trigo", "Canola"])
                c1, c2 = st.columns(2)
                area = c1.number_input("Área (ha)", min_value=0.0)
                cost = c2.number_input("Custo estimado por hectare (R$)", min_value=0.0)
                c3, c4 = st.columns(2)
                yield_sc = c3.number_input("Produtividade esperada (sc/ha)", min_value=0.0)
                margin = c4.number_input("Margem-alvo (%)", min_value=0.0, value=20.0)
                submit = st.form_submit_button("Salvar safra", use_container_width=True)

            if submit:
                if not name.strip() or area <= 0 or cost <= 0 or yield_sc <= 0:
                    st.error("Preencha nome, área, custo e produtividade.")
                else:
                    season_id = insert_id(
                        """INSERT INTO seasons
                           (name,crop,area_ha,cost_ha,yield_sc_ha,margin_pct,active,created_by)
                           VALUES(:n,:c,:a,:co,:y,:m,TRUE,:u)""",
                        {
                            "n": name.strip(),
                            "c": crop,
                            "a": area,
                            "co": cost,
                            "y": yield_sc,
                            "m": margin,
                            "u": user["id"],
                        },
                    )
                    log_action(user["id"], "criou", "safra", season_id, name.strip())
                    st.success("Safra salva.")
                    st.rerun()

    seasons = q("SELECT * FROM seasons ORDER BY id DESC")
    if not seasons:
        st.caption("Nenhuma safra cadastrada.")

    for item in seasons:
        summary = season_summary(item)
        with st.expander(f"🌾 {item['name']} · {item['crop']}"):
            st.write(
                f"**{num(item['area_ha'], 0)} ha** · "
                f"Produção estimada **{num(summary['production'], 0)} sc** · "
                f"Vendido **{num(summary['sold_pct'])}%**"
            )
            if CAN_EDIT:
                new_active = st.checkbox(
                    "Safra ativa",
                    value=bool(item["active"]),
                    key=f"season_active_{item['id']}",
                )
                if st.button("Salvar situação", key=f"save_season_{item['id']}"):
                    ex(
                        "UPDATE seasons SET active=:a WHERE id=:id",
                        {"a": new_active, "id": item["id"]},
                    )
                    log_action(user["id"], "alterou", "safra", item["id"], f"active={new_active}")
                    st.rerun()


elif page == "🛒 Compras":
    st.subheader("Compras e compromissos")
    seasons = q("SELECT id,name,crop FROM seasons WHERE active=TRUE ORDER BY id DESC")
    season_map = {"Nenhuma": None}
    season_map.update({f"{s['name']} · {s['crop']}": s["id"] for s in seasons})

    if CAN_EDIT:
        with st.expander("➕ Registrar compra"):
            with st.form("new_commitment", clear_on_submit=True):
                description = st.text_input("O que foi comprado?")
                category = st.selectbox(
                    "Categoria",
                    ["Sementes", "Fertilizantes", "Defensivos", "Máquinas", "Custeio", "Arrendamento", "Outro"],
                )
                supplier = st.text_input("Fornecedor")
                c1, c2 = st.columns(2)
                total_value = c1.number_input("Valor total (R$)", min_value=0.0)
                due_date = c2.date_input("Vencimento")
                payment_crop = st.selectbox(
                    "Pretende pagar com",
                    ["Soja", "Milho", "Trigo", "Canola", "Caixa", "Mais de uma"],
                )
                selected_season = st.selectbox("Safra relacionada", list(season_map))
                notes = st.text_area("Observação")
                submit = st.form_submit_button("Salvar compra", use_container_width=True)

            if submit:
                if not description.strip() or total_value <= 0:
                    st.error("Informe a descrição e o valor.")
                else:
                    commitment_id = insert_id(
                        """INSERT INTO commitments
                           (season_id,category,description,supplier,total_value,due_date,
                            payment_crop,notes,status,created_by)
                           VALUES(:s,:c,:d,:f,:v,:dt,:p,:n,'aberto',:u)""",
                        {
                            "s": season_map[selected_season],
                            "c": category,
                            "d": description.strip(),
                            "f": supplier.strip(),
                            "v": total_value,
                            "dt": due_date,
                            "p": payment_crop,
                            "n": notes.strip(),
                            "u": user["id"],
                        },
                    )
                    log_action(user["id"], "criou", "compromisso", commitment_id, description.strip())
                    st.success("Compra salva.")
                    st.rerun()

    commitments = q(
        """SELECT * FROM commitments
           WHERE COALESCE(status,'aberto')!='cancelado'
           ORDER BY due_date,id DESC"""
    )
    if not commitments:
        st.caption("Nenhum compromisso registrado.")

    for item in commitments:
        status = commitment_status(item["id"])
        icon = "🟢" if status["pct"] >= 99 else "🟡" if status["pct"] >= 50 else "🔴"
        with st.expander(f"{icon} {item['description']} · {item['due_date']}"):
            st.write(f"**Valor:** {money(item['total_value'])}")
            st.write(f"**Fornecedor:** {item['supplier'] or 'Não informado'}")
            st.write(f"**Protegido por vendas:** {money(status['protected'])}")
            st.write(f"**Pago:** {money(status['paid'])}")
            st.write(f"**Ainda falta:** {money(status['remaining'])}")

            if CAN_EDIT and item.get("status", "aberto") == "aberto":
                with st.form(f"payment_{item['id']}", clear_on_submit=True):
                    amount = st.number_input(
                        "Registrar pagamento (R$)",
                        min_value=0.0,
                        key=f"payment_amount_{item['id']}",
                    )
                    payment_date = st.date_input(
                        "Data do pagamento",
                        value=date.today(),
                        key=f"payment_date_{item['id']}",
                    )
                    note = st.text_input(
                        "Observação do pagamento",
                        key=f"payment_note_{item['id']}",
                    )
                    submit_payment = st.form_submit_button("Salvar pagamento")

                if submit_payment and amount > 0:
                    payment_id = insert_id(
                        """INSERT INTO payments
                           (commitment_id,payment_date,amount,notes,created_by)
                           VALUES(:c,:d,:a,:n,:u)""",
                        {
                            "c": item["id"],
                            "d": payment_date,
                            "a": amount,
                            "n": note.strip(),
                            "u": user["id"],
                        },
                    )
                    log_action(user["id"], "pagou", "compromisso", item["id"], money(amount))
                    st.success("Pagamento registrado.")
                    st.rerun()

                if st.button("Marcar como encerrado", key=f"close_commitment_{item['id']}"):
                    ex("UPDATE commitments SET status='encerrado' WHERE id=:id", {"id": item["id"]})
                    log_action(user["id"], "encerrou", "compromisso", item["id"], item["description"])
                    st.rerun()


elif page == "💰 Vendas":
    st.subheader("Comercialização")
    seasons = q("SELECT id,name,crop FROM seasons WHERE active=TRUE ORDER BY id DESC")

    if not seasons:
        st.info("Cadastre uma safra antes de registrar vendas.")
    else:
        season_map = {f"{s['name']} · {s['crop']}": s["id"] for s in seasons}
        commitments = q(
            """SELECT id,description,due_date FROM commitments
               WHERE COALESCE(status,'aberto')='aberto'
               ORDER BY due_date"""
        )
        commitment_map = {"Venda livre": None}
        commitment_map.update(
            {f"{c['description']} · {c['due_date']}": c["id"] for c in commitments}
        )

        if CAN_EDIT:
            with st.form("new_sale", clear_on_submit=True):
                season_label = st.selectbox("Safra", list(season_map))
                c1, c2 = st.columns(2)
                quantity = c1.number_input("Quantidade (sc)", min_value=0.0)
                price = c2.number_input("Preço (R$/sc)", min_value=0.0)
                buyer = st.text_input("Comprador/cooperativa")
                objective = st.selectbox("Esta venda protege", list(commitment_map))
                sale_date = st.date_input("Data da venda", value=date.today())
                notes = st.text_area("Observação")
                submit = st.form_submit_button("Salvar venda", use_container_width=True)

            if submit:
                if quantity <= 0 or price <= 0:
                    st.error("Informe quantidade e preço.")
                else:
                    season_id = season_map[season_label]
                    summary = season_summary(
                        q("SELECT * FROM seasons WHERE id=:id", {"id": season_id})[0]
                    )
                    if quantity > summary["balance"]:
                        st.error(
                            f"A venda supera o saldo livre de {num(summary['balance'], 0)} sc."
                        )
                    else:
                        sale_id = insert_id(
                            """INSERT INTO sales
                               (season_id,sale_date,quantity_sc,price_sc,buyer,
                                commitment_id,notes,created_by)
                               VALUES(:s,:d,:q,:p,:b,:c,:n,:u)""",
                            {
                                "s": season_id,
                                "d": sale_date,
                                "q": quantity,
                                "p": price,
                                "b": buyer.strip(),
                                "c": commitment_map[objective],
                                "n": notes.strip(),
                                "u": user["id"],
                            },
                        )
                        log_action(user["id"], "criou", "venda", sale_id, f"{quantity} sc")
                        st.success("Venda salva.")
                        st.rerun()

    sales = q(
        """SELECT sales.*,seasons.name AS season_name,seasons.crop
           FROM sales JOIN seasons ON seasons.id=sales.season_id
           ORDER BY sale_date DESC,sales.id DESC"""
    )
    st.markdown("### Histórico")
    if not sales:
        st.caption("Nenhuma venda registrada.")
    for item in sales:
        st.markdown(
            f"""<div class="card">
            <b>{num(item['quantity_sc'], 0)} sc · {money(item['price_sc'])}/sc</b><br>
            {item['season_name']} · {item['crop']}<br>
            {item['buyer'] or 'Comprador não informado'} · {item['sale_date']}
            </div>""",
            unsafe_allow_html=True,
        )


elif page == "📈 Mercado":
    st.subheader("Mercado")

    if CAN_EDIT:
        with st.form("quote_form", clear_on_submit=True):
            crop = st.selectbox("Cultura", ["Soja", "Milho", "Trigo", "Canola"])
            price = st.number_input("Preço regional (R$/sc)", min_value=0.0)
            source = st.text_input("Fonte", placeholder="Cooperativa, corretora, comprador...")
            submit = st.form_submit_button("Salvar cotação", use_container_width=True)

        if submit:
            if price <= 0:
                st.error("Informe um preço maior que zero.")
            else:
                quote_id = insert_id(
                    """INSERT INTO quotes(crop,price_sc,source,created_by)
                       VALUES(:c,:p,:s,:u)""",
                    {
                        "c": crop,
                        "p": price,
                        "s": source.strip(),
                        "u": user["id"],
                    },
                )
                log_action(user["id"], "criou", "cotação", quote_id, f"{crop} {price}")
                st.success("Cotação salva.")
                st.rerun()

    latest = q(
        """SELECT q1.* FROM quotes q1
           JOIN (
             SELECT crop,MAX(quoted_at) AS max_date FROM quotes GROUP BY crop
           ) q2 ON q1.crop=q2.crop AND q1.quoted_at=q2.max_date
           ORDER BY q1.crop"""
    )
    if latest:
        cols = st.columns(min(len(latest), 4))
        for index, item in enumerate(latest):
            cols[index % len(cols)].metric(
                item["crop"],
                money(item["price_sc"]) + "/sc",
                help=f"Fonte: {item['source'] or 'não informada'}",
            )

    history = q(
        """SELECT crop,price_sc,source,quoted_at FROM quotes
           ORDER BY quoted_at DESC LIMIT 30"""
    )
    if history:
        st.markdown("### Últimas cotações")
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)


elif page == "🧪 Teste 7 dias":
    st.subheader("Roteiro do piloto")
    st.info(
        "Durante uma semana, use dados reais em pequenas quantidades. "
        "Não cadastre tudo de uma vez; primeiro valide o fluxo."
    )

    checklist = [
        "Dia 1 — cadastrar uma safra real e conferir produção estimada.",
        "Dia 2 — cadastrar duas compras ou compromissos.",
        "Dia 3 — registrar uma cotação e verificar a recomendação AgroIA.",
        "Dia 4 — registrar uma venda e vinculá-la a um compromisso.",
        "Dia 5 — registrar um pagamento e conferir a proteção.",
        "Dia 6 — testar no celular com outro usuário da família.",
        "Dia 7 — exportar o backup e registrar a avaliação final.",
    ]
    for item in checklist:
        st.write("☐", item)

    st.markdown("### Registrar observação")
    with st.form("feedback", clear_on_submit=True):
        module = st.selectbox(
            "Módulo",
            ["Painel", "Safras", "Compras", "Vendas", "Mercado", "Usuários", "Celular", "Outro"],
        )
        feedback_type = st.selectbox(
            "Tipo",
            ["Ideia", "Dificuldade", "Erro", "Informação faltando", "Elogio"],
        )
        priority = st.selectbox("Prioridade", ["Baixa", "Média", "Alta"])
        description = st.text_area("Descreva o que aconteceu")
        submit = st.form_submit_button("Salvar observação", use_container_width=True)

    if submit:
        if not description.strip():
            st.error("Descreva a observação.")
        else:
            feedback_id = insert_id(
                """INSERT INTO pilot_feedback
                   (user_id,module,feedback_type,priority,description)
                   VALUES(:u,:m,:t,:p,:d)""",
                {
                    "u": user["id"],
                    "m": module,
                    "t": feedback_type,
                    "p": priority,
                    "d": description.strip(),
                },
            )
            log_action(user["id"], "registrou", "feedback", feedback_id, module)
            st.success("Observação salva.")
            st.rerun()

    feedback = q(
        """SELECT pilot_feedback.created_at,users.name,pilot_feedback.module,
                  pilot_feedback.feedback_type,pilot_feedback.priority,
                  pilot_feedback.description
           FROM pilot_feedback
           LEFT JOIN users ON users.id=pilot_feedback.user_id
           ORDER BY pilot_feedback.id DESC"""
    )
    if feedback:
        st.markdown("### Observações registradas")
        st.dataframe(pd.DataFrame(feedback), use_container_width=True, hide_index=True)


elif page == "👥 Usuários":
    st.subheader("Usuários da família")

    with st.form("new_user", clear_on_submit=True):
        name = st.text_input("Nome")
        email = st.text_input("E-mail").strip().lower()
        role = st.selectbox("Permissão", ["operador", "consulta", "admin"])
        password = st.text_input("Senha provisória", type="password")
        submit = st.form_submit_button("Criar usuário", use_container_width=True)

    if submit:
        if not name.strip() or "@" not in email or len(password) < 8:
            st.error("Informe nome, e-mail válido e senha com pelo menos 8 caracteres.")
        else:
            try:
                new_id = insert_id(
                    """INSERT INTO users(name,email,password_hash,role,active)
                       VALUES(:n,:e,:p,:r,TRUE)""",
                    {
                        "n": name.strip(),
                        "e": email,
                        "p": hpw(password),
                        "r": role,
                    },
                )
                log_action(user["id"], "criou", "usuário", new_id, email)
                st.success("Usuário criado.")
                st.rerun()
            except Exception:
                st.error("Não foi possível criar. Verifique se o e-mail já existe.")

    users = q(
        "SELECT id,name,email,role,active,created_at FROM users ORDER BY id"
    )
    for item in users:
        with st.expander(f"{item['name']} · {item['role']}"):
            st.write(item["email"])
            active = st.checkbox(
                "Ativo",
                value=bool(item["active"]),
                key=f"user_active_{item['id']}",
                disabled=item["id"] == user["id"],
            )
            new_password = st.text_input(
                "Nova senha (opcional)",
                type="password",
                key=f"new_password_{item['id']}",
            )
            if st.button("Salvar usuário", key=f"save_user_{item['id']}"):
                if item["id"] != user["id"]:
                    ex(
                        "UPDATE users SET active=:a WHERE id=:id",
                        {"a": active, "id": item["id"]},
                    )
                if new_password:
                    if len(new_password) < 8:
                        st.error("A senha precisa ter pelo menos 8 caracteres.")
                        st.stop()
                    ex(
                        "UPDATE users SET password_hash=:p WHERE id=:id",
                        {"p": hpw(new_password), "id": item["id"]},
                    )
                log_action(user["id"], "alterou", "usuário", item["id"], item["email"])
                st.success("Usuário atualizado.")
                st.rerun()


elif page == "📦 Backup":
    st.subheader("Backup e conferência")
    st.caption(
        "Baixe este arquivo ao final de cada dia do piloto. "
        "Ele contém cópias em CSV das principais tabelas."
    )

    tables = [
        "users",
        "seasons",
        "commitments",
        "sales",
        "quotes",
        "payments",
        "activity_log",
        "pilot_feedback",
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for table in tables:
            rows = q(f"SELECT * FROM {table} ORDER BY id")
            frame = pd.DataFrame(rows)
            archive.writestr(
                f"{table}.csv",
                frame.to_csv(index=False).encode("utf-8-sig"),
            )
        metadata = {
            "generated_at": datetime.now().isoformat(),
            "database": engine.dialect.name,
            "version": "piloto-7-dias",
        }
        archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    st.download_button(
        "Baixar backup do piloto",
        data=buffer.getvalue(),
        file_name=f"agriza_backup_{date.today().isoformat()}.zip",
        mime="application/zip",
        use_container_width=True,
    )

    st.markdown("### Histórico recente")
    logs = q(
        """SELECT activity_log.created_at,users.name,activity_log.action,
                  activity_log.entity,activity_log.details
           FROM activity_log
           LEFT JOIN users ON users.id=activity_log.user_id
           ORDER BY activity_log.id DESC LIMIT 50"""
    )
    if logs:
        st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
