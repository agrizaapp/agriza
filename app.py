import io
import json
import zipfile
import time
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
import extra_streamlit_components as stx

from core.config import engine, IS_POSTGRES, apply_page_config, apply_global_style
from core.database import init_db, q, scalar, ex, insert_id, log_action
from core.security import hpw, vpw
from core.utils import money, num, br_date
from services.analytics import season_summary, commitment_status, agroia_recommendation
from services.auth import (
    setup_complete,
    save_setting,
    create_initial_admin,
    create_persistent_session,
    get_user_from_session_token,
    revoke_persistent_session,
    cleanup_expired_sessions,
)
from services.voice_sales import parse_spoken_sale
from services.voice_purchases import parse_spoken_purchase
try:
    from market_prices import update_regional_quotes, latest_quote_for_crop
except ModuleNotFoundError:
    def update_regional_quotes(user_id=None):
        return {
            "updated": [],
            "errors": [
                "O módulo de mercado regional não foi encontrado. "
                "Envie o arquivo services/market_prices.py para ativar as cotações."
            ],
        }

    def latest_quote_for_crop(crop):
        return None


def confirmation_card(title, rows, total_label=None, total_value=None, warnings=None):
    st.markdown(f"### {title}")
    for label, value in rows:
        st.markdown(f"**{label}:** {value}")
    if total_label is not None:
        st.markdown(f"### {total_label}: {total_value}")
    if warnings:
        for warning in warnings:
            st.warning(warning)




apply_page_config()
apply_global_style()


@st.cache_resource
def initialize_database():
    """Executa esquema e limpeza uma vez por processo, não a cada navegação."""
    init_db()
    try:
        cleanup_expired_sessions()
    except Exception:
        pass


initialize_database()

COOKIE_NAME = "agriza_remember_session"
cookie_manager = stx.CookieManager(key="agriza_cookie_manager")

st.markdown('<div class="brand">🌱 AGRIZA</div>', unsafe_allow_html=True)
st.markdown('<div class="subbrand">AgroIA • Transformando informação em decisão.</div>', unsafe_allow_html=True)
st.caption("Versão ativa: AGRIZA Enterprise 3.1")

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
    # O componente de cookies é carregado no navegador e pode precisar de um
    # segundo ciclo do Streamlit antes de disponibilizar os valores.
    cookies = cookie_manager.get_all() or {}
    remembered_token = cookies.get(COOKIE_NAME)

    if remembered_token:
        remembered_user = get_user_from_session_token(remembered_token)
        if remembered_user:
            st.session_state.user = remembered_user
            st.session_state.persistent_token = remembered_token
        else:
            try:
                cookie_manager.delete(COOKIE_NAME, key="delete_invalid_cookie")
            except Exception:
                pass
    elif not st.session_state.get("cookie_read_attempted"):
        st.session_state.cookie_read_attempted = True
        time.sleep(0.8)
        st.rerun()

if "user" not in st.session_state:
    st.caption(
        "Você pode manter este dispositivo conectado por 1 ano."
    )
    with st.form("login"):
        email = st.text_input("E-mail").strip().lower()
        password = st.text_input("Senha", type="password")
        remember_login = st.checkbox(
            "Manter conectado neste dispositivo",
            value=True,
        )
        submit = st.form_submit_button("Entrar", use_container_width=True)

    if submit:
        rows = q(
            "SELECT * FROM users WHERE lower(email)=:e AND active=TRUE",
            {"e": email},
        )
        if rows and vpw(password, rows[0]["password_hash"]):
            st.session_state.user = {
                key: value
                for key, value in rows[0].items()
                if key != "password_hash"
            }

            if remember_login:
                try:
                    token, expires_at = create_persistent_session(
                        rows[0]["id"],
                        days=365,
                    )
                    cookie_manager.set(
                        COOKIE_NAME,
                        token,
                        expires_at=expires_at,
                        key=f"set_agriza_remember_cookie_{token[:8]}",
                    )
                    st.session_state.persistent_token = token
                    st.session_state.cookie_read_attempted = True
                    time.sleep(1.2)
                except Exception as error:
                    st.warning(
                        "O acesso foi realizado, mas não foi possível "
                        "lembrar este dispositivo."
                    )
                    st.caption("Confira os dados e tente novamente.")

            st.rerun()
        else:
            st.error("E-mail ou senha incorretos.")
    st.stop()

user = st.session_state.user
CAN_EDIT = user["role"] in ("admin", "operador")

identity_column, logout_column = st.columns([4, 1])
identity_column.caption(f"Olá, **{user['name']}** · {user['role'].capitalize()}")
if logout_column.button("Sair da conta", key="logout_top", use_container_width=True):
    active_token = (
        st.session_state.get("persistent_token")
        or cookie_manager.get(COOKIE_NAME)
    )
    try:
        revoke_persistent_session(active_token)
    except Exception:
        pass
    try:
        cookie_manager.delete(COOKIE_NAME)
    except Exception:
        pass
    st.session_state.pop("persistent_token", None)
    st.session_state.pop("user", None)
    st.rerun()

PAYMENT_OPTIONS = [
    "Soja",
    "Milho",
    "Trigo",
    "Canola",
    "Caixa",
    "Mais de uma",
]

menu_pages = [
    "🏠 Início",
    "📝 Lançar / Visualizar",
    "🤖 AgroIA",
]
view_pages = [
    "🌾 Safras",
    "🛒 Compras",
    "🧾 Contas e pagamentos",
    "🚜 Máquinas e financiamentos",
    "💰 Vendas",
    "📈 Mercado regional",
    "⚙️ Cadastro",
]
pages = menu_pages + view_pages
if user["role"] == "admin":
    menu_pages.append("📦 BACKUP")
    pages.extend(["👥 Usuários", "📦 BACKUP"])

if "current_page" not in st.session_state or st.session_state.current_page not in pages:
    st.session_state.current_page = pages[0]
if "menu_should_expand" not in st.session_state:
    st.session_state.menu_should_expand = False
if st.session_state.current_page != "🏠 Início":
    st.session_state.menu_should_expand = False

if "page_history" not in st.session_state:
    st.session_state.page_history = []
last_page = st.session_state.get("last_rendered_page")
if last_page and last_page != st.session_state.current_page:
    if not st.session_state.pop("skip_next_page_history", False):
        st.session_state.page_history.append(last_page)
st.session_state.last_rendered_page = st.session_state.current_page

if st.session_state.current_page != "🏠 Início":
    if st.button("← Voltar", key="global_back_button"):
        st.session_state.current_page = (
            st.session_state.page_history.pop()
            if st.session_state.page_history else "🏠 Início"
        )
        st.session_state.skip_next_page_history = True
        st.rerun()

with st.expander(
    f"☰ Menu principal — {st.session_state.current_page}",
    expanded=st.session_state.menu_should_expand,
):
    st.caption("Toque em uma área para abrir.")
    with st.container(key="main_menu_grid"):
        for start in range(0, len(menu_pages), 2):
            cols = st.columns(2, gap="small")
            for offset, label in enumerate(menu_pages[start:start + 2]):
                with cols[offset]:
                    button_type = "primary" if label == st.session_state.current_page else "secondary"
                    if st.button(
                        label,
                        key=f"nav_{start + offset}",
                        use_container_width=True,
                        type=button_type,
                    ):
                        st.session_state.current_page = label
                        st.session_state.menu_should_expand = label == "🏠 Início"
                        st.rerun()

page = st.session_state.current_page


# =========================================================
# PÁGINAS
# =========================================================
if page == "🏠 Início":
    st.subheader("Visão geral")
    st.markdown("### Resumo da gestão")
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
        if summary["actual_production"] is None:
            c1.metric(
                "Produção estimada",
                f"{num(summary['estimated_production'], 0)} sc",
            )
        else:
            c1.metric(
                "Produção colhida",
                f"{num(summary['actual_production'], 0)} sc",
                delta=(
                    f"{summary['variance_pct']:+.1f}% da estimativa"
                    if summary["variance_pct"] is not None
                    else None
                ),
            )
        c2.metric("Já vendido", f"{num(summary['sold_pct'])}%")
        c3.metric("Saldo livre", f"{num(summary['balance'], 0)} sc")

        i1, i2, i3 = st.columns(3)
        if i1.button("ℹ️", key="info_production", help="Como a produção é calculada"):
            st.info(
                "Produção estimada = área da safra × produtividade prevista. "
                "Quando a colheita é informada, o indicador passa a usar a produção colhida."
            )
        if i2.button("ℹ️", key="info_sold", help="Ver vendas contabilizadas"):
            st.session_state.current_page = "💰 Vendas"
            st.rerun()
        if i3.button("ℹ️", key="info_balance", help="Como o saldo é calculado"):
            st.info(
                "Saldo livre = produção da safra − quantidade das vendas de grãos registradas."
            )

        c4, c5, c6 = st.columns(3)
        c4.metric("Custo por saca", money(summary["cost_per_sc"]))
        c5.metric("Preço médio vendido", money(summary["average"]))
        c6.metric("Preço necessário", money(summary["required_price"]))

        i4, i5, i6 = st.columns(3)
        if i4.button("ℹ️", key="info_cost", help="Como o custo é calculado"):
            st.info("Custo por saca = custo total da safra ÷ produção considerada.")
        if i5.button("ℹ️", key="info_average", help="Ver vendas usadas no preço médio"):
            st.session_state.current_page = "💰 Vendas"
            st.rerun()
        if i6.button("ℹ️", key="info_required", help="Como o preço necessário é calculado"):
            st.info(
                "Preço necessário = valor que falta para atingir a margem cadastrada ÷ saldo livre."
            )

        recommendation_key = f"agroia_recommendation_{season['id']}"
        if recommendation_key not in st.session_state:
            st.session_state[recommendation_key] = agroia_recommendation(season)
        if st.button("✨ Gerar recomendação", key=f"generate_{season['id']}"):
            st.session_state[recommendation_key] = agroia_recommendation(season)
            st.session_state[f"recommendation_generated_{season['id']}"] = datetime.now()
        rec = st.session_state[recommendation_key]
        generated_at = st.session_state.get(f"recommendation_generated_{season['id']}")
        if generated_at:
            st.caption(f"✓ Análise atualizada em {generated_at.strftime('%d/%m/%Y %H:%M')}")
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
                    Vence em {br_date(item['due_date'])} · {money(item['total_value'])}<br>
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
                    f"• **{br_date(item['due_date'])}** — {item['description']} — {money(item['total_value'])}"
                )


elif page == "👁️ Visualizar":
    st.subheader("O que você quer visualizar?")
    st.caption("Escolha uma área para consultar lançamentos, indicadores e histórico.")
    view_options = [
        ("🌾 Safras", "🌾 Safras"),
        ("🛒 Compras", "🛒 Compras"),
        ("💰 Vendas", "💰 Vendas"),
        ("🚜 Máquinas", "🚜 Máquinas e financiamentos"),
        ("📈 Mercado", "📈 Mercado regional"),
    ]
    if user["role"] == "admin":
        view_options.append(("👥 Usuários", "👥 Usuários"))
    for start in range(0, len(view_options), 2):
        cols = st.columns(2)
        for offset, (label, destination) in enumerate(view_options[start:start + 2]):
            with cols[offset]:
                if st.button(label, key=f"view_{destination}", use_container_width=True):
                    st.session_state.current_page = destination
                    st.rerun()


elif page == "📝 Lançar / Visualizar":
    st.subheader("Lançar ou visualizar")
    st.caption("Escolha uma área. Em cada tela você pode registrar e consultar seus lançamentos.")

    c1, c2 = st.columns(2)
    if c1.button("🛒 Compra", use_container_width=True, type="primary"):
        st.session_state.purchase_show_history = False
        st.session_state.current_page = "🛒 Compras"
        st.rerun()
    if c2.button("💰 Venda", use_container_width=True, type="primary"):
        st.session_state.sale_show_history = False
        st.session_state.current_page = "💰 Vendas"
        st.rerun()

    c3, c4 = st.columns(2)
    if c3.button("🌾 Nova safra", use_container_width=True):
        st.session_state.current_page = "🌾 Safras"
        st.rerun()
    if c4.button("📈 Cotação", use_container_width=True):
        st.session_state.current_page = "📈 Mercado regional"
        st.rerun()

    c5, c6 = st.columns(2)
    if c5.button("🧾 Ver Contas", use_container_width=True):
        st.session_state.account_payment_filter = "A pagar"
        st.session_state.current_page = "🧾 Contas e pagamentos"
        st.rerun()
    if c6.button("💳 Pagamentos", use_container_width=True):
        st.session_state.account_payment_filter = "Pagas"
        st.session_state.current_page = "🧾 Contas e pagamentos"
        st.rerun()

    if st.button("⚙️ Cadastro", use_container_width=True):
        st.session_state.current_page = "⚙️ Cadastro"
        st.rerun()

    st.info(
        "Escolha o tipo de registro. Em compras e vendas, revise o resumo antes de confirmar."
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
                f"Estimativa **{num(summary['estimated_production'], 0)} sc** · "
                f"Vendido **{num(summary['sold_pct'])}%**"
            )

            a1, a2, a3 = st.columns(3)
            if a1.button("👁️ Visualizar", key=f"view_season_{item['id']}"):
                st.info(
                    f"Custo total estimado: {money(summary['total_cost'])}. "
                    f"Saldo livre: {num(summary['balance'], 0)} sacas."
                )
            if CAN_EDIT and a2.button("✏️ Editar", key=f"open_edit_season_{item['id']}"):
                st.session_state[f"edit_season_open_{item['id']}"] = True
            if CAN_EDIT and a3.button("🗑️ Excluir", key=f"delete_season_{item['id']}"):
                st.session_state[f"confirm_delete_season_{item['id']}"] = True

            if CAN_EDIT:
                with st.expander(
                    "✏️ Editar custo da safra",
                    expanded=st.session_state.get(f"edit_season_open_{item['id']}", False),
                ):
                    with st.form(f"edit_cost_{item['id']}"):
                        current_cost = float(item["cost_ha"] or 0)
                        new_cost = st.number_input(
                            "Custo por hectare (R$)",
                            min_value=0.0,
                            value=current_cost,
                            step=50.0,
                            help=(
                                "Altere este valor sempre que a estimativa de custo "
                                "da safra precisar ser corrigida."
                            ),
                        )
                        total_preview = new_cost * float(item["area_ha"])
                        st.caption(
                            f"Custo total estimado após a alteração: "
                            f"{money(total_preview)}"
                        )
                        save_cost = st.form_submit_button(
                            "Salvar novo custo",
                            use_container_width=True,
                        )

                    if save_cost:
                        if new_cost <= 0:
                            st.error("Informe um custo por hectare maior que zero.")
                        else:
                            old_cost = current_cost
                            ex(
                                """UPDATE seasons
                                   SET cost_ha=:cost
                                   WHERE id=:id""",
                                {"cost": new_cost, "id": item["id"]},
                            )
                            log_action(
                                user["id"],
                                "editou",
                                "custo_safra",
                                item["id"],
                                f"de {old_cost:.2f} para {new_cost:.2f} por ha",
                            )
                            st.session_state.current_page = "🌾 Safras"
                            st.success(
                                f"Custo atualizado para {money(new_cost)} por hectare."
                            )
                            st.session_state.pop(f"edit_season_open_{item['id']}", None)

            if st.session_state.get(f"confirm_delete_season_{item['id']}"):
                st.warning("A safra será desativada. As vendas e o histórico permanecerão preservados.")
                d1, d2 = st.columns(2)
                if d1.button("Confirmar exclusão", key=f"confirm_season_delete_{item['id']}"):
                    ex("UPDATE seasons SET active=FALSE WHERE id=:id", {"id": item["id"]})
                    log_action(user["id"], "desativou", "safra", item["id"], item["name"])
                    st.success("Safra desativada.")
                    st.rerun()
                if d2.button("Cancelar", key=f"cancel_season_delete_{item['id']}"):
                    st.session_state.pop(f"confirm_delete_season_{item['id']}", None)
                    st.rerun()

            if summary["actual_production"] is not None:
                variation_text = (
                    f"{summary['variance_pct']:+.1f}%"
                    if summary["variance_pct"] is not None
                    else "—"
                )
                c_real1, c_real2, c_real3 = st.columns(3)
                c_real1.metric(
                    "Produção colhida",
                    f"{num(summary['actual_production'], 0)} sc",
                )
                c_real2.metric(
                    "Produtividade real",
                    f"{num(summary['actual_yield_sc_ha'], 1)} sc/ha",
                )
                c_real3.metric("Diferença da estimativa", variation_text)

                reason = item.get("production_reason") or "Motivo não informado"
                result_label = {
                    "abaixo": "Abaixo do estimado",
                    "acima": "Acima do estimado",
                    "dentro": "Dentro do estimado",
                }.get(item.get("production_result"), "Resultado registrado")
                st.caption(f"**{result_label}:** {reason}")
                if item.get("production_notes"):
                    st.caption(item["production_notes"])

            if CAN_EDIT:
                with st.expander(
                    "🚜 Registrar ou corrigir colheita",
                    expanded=summary["actual_production"] is None,
                ):
                    estimated = float(summary["estimated_production"])
                    current_actual = float(summary["actual_production"] or 0)
                    with st.form(f"harvest_{item['id']}"):
                        h1, h2 = st.columns(2)
                        harvest_date = h1.date_input(
                            "Data do encerramento da colheita",
                            value=item.get("harvest_date") or date.today(),
                            format="DD/MM/YYYY",
                        )
                        actual_sc = h2.number_input(
                            "Total colhido (sacas)",
                            min_value=0.0,
                            value=current_actual,
                            step=10.0,
                        )

                        difference = actual_sc - estimated
                        tolerance = estimated * 0.02
                        if actual_sc <= 0:
                            result = ""
                            st.info(
                                f"Estimativa cadastrada: {num(estimated, 0)} sacas."
                            )
                        elif difference < -tolerance:
                            result = "abaixo"
                            st.warning(
                                f"A produção ficou {abs(difference):,.0f} sacas "
                                "abaixo da estimativa."
                            )
                        elif difference > tolerance:
                            result = "acima"
                            st.success(
                                f"A produção ficou {difference:,.0f} sacas "
                                "acima da estimativa."
                            )
                        else:
                            result = "dentro"
                            st.info("A produção ficou próxima da estimativa.")

                        below_reasons = [
                            "Ano seco / falta de chuva",
                            "Excesso de chuva",
                            "Geada",
                            "Granizo ou vento",
                            "Pragas",
                            "Doenças",
                            "Falha de implantação",
                            "Problema de solo ou fertilidade",
                            "Perdas na colheita",
                            "Área efetivamente colhida menor",
                            "Outro",
                        ]
                        above_reasons = [
                            "Clima favorável",
                            "Boa distribuição das chuvas",
                            "Manejo acima do esperado",
                            "Cultivar com melhor desempenho",
                            "Solo ou fertilidade favorável",
                            "Baixa pressão de pragas e doenças",
                            "Estimativa inicial conservadora",
                            "Outro",
                        ]
                        neutral_reasons = [
                            "Dentro do esperado",
                            "Variação normal da lavoura",
                            "Outro",
                        ]
                        options = (
                            below_reasons
                            if result == "abaixo"
                            else above_reasons
                            if result == "acima"
                            else neutral_reasons
                        )
                        previous_reason = item.get("production_reason")
                        default_index = (
                            options.index(previous_reason)
                            if previous_reason in options
                            else 0
                        )
                        reason = st.selectbox(
                            "Motivo principal",
                            options,
                            index=default_index,
                            disabled=actual_sc <= 0,
                        )
                        notes = st.text_area(
                            "Observação curta (opcional)",
                            value=item.get("production_notes") or "",
                            placeholder=(
                                "Ex.: 20 dias sem chuva no enchimento de grãos."
                            ),
                        )
                        save_harvest = st.form_submit_button(
                            "Salvar resultado da colheita",
                            use_container_width=True,
                        )

                    if save_harvest:
                        if actual_sc <= 0:
                            st.error("Informe o total efetivamente colhido.")
                        else:
                            ex(
                                """UPDATE seasons
                                   SET actual_production_sc=:p,
                                       harvest_date=:d,
                                       production_result=:r,
                                       production_reason=:m,
                                       production_notes=:o
                                   WHERE id=:id""",
                                {
                                    "p": actual_sc,
                                    "d": harvest_date,
                                    "r": result,
                                    "m": reason,
                                    "o": notes.strip(),
                                    "id": item["id"],
                                },
                            )
                            log_action(
                                user["id"],
                                "registrou",
                                "colheita",
                                item["id"],
                                (
                                    f"{actual_sc:.0f} sc; resultado={result}; "
                                    f"motivo={reason}"
                                ),
                            )
                            st.success("Resultado da colheita registrado.")
                            st.rerun()

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


elif page == "🧾 Contas e pagamentos":
    st.subheader("Contas a pagar e pagas")
    st.caption("Acompanhe somente os compromissos financeiros, sem o formulário de novas compras.")
    account_filter = st.radio(
        "Exibir",
        ["A pagar", "Pagas", "Todas"],
        horizontal=True,
        key="account_payment_filter",
    )
    accounts = q(
        """SELECT id,description,supplier,due_date,total_value,status,installment_no
           FROM commitments
           WHERE COALESCE(status,'aberto') != 'cancelado'
           ORDER BY due_date,id DESC"""
    )
    account_rows = []
    account_statuses = {
        account["id"]: commitment_status(account["id"]) for account in accounts
    }
    open_count = 0
    paid_count = 0
    for account in accounts:
        payment = account_statuses[account["id"]]
        is_paid = payment["remaining"] <= 0.01 or account.get("status") == "encerrado"
        if is_paid:
            paid_count += 1
        else:
            open_count += 1
        if account_filter == "A pagar" and is_paid:
            continue
        if account_filter == "Pagas" and not is_paid:
            continue
        account_rows.append(
            {
                "Descrição": account["description"],
                "Fornecedor": account.get("supplier") or "—",
                "Parcela": account.get("installment_no") or "—",
                "Vencimento": br_date(account.get("due_date")),
                "Valor": money(account["total_value"]),
                "Pago": money(payment["covered"]),
                "Em aberto": money(payment["remaining"]),
                "Situação": "Paga" if is_paid else "A pagar",
            }
        )

    total_open = sum(
        account_statuses[account["id"]]["remaining"]
        for account in accounts
        if account_statuses[account["id"]]["remaining"] > 0.01
    )
    a1, a2, a3 = st.columns(3)
    a1.metric("Contas a pagar", open_count)
    a2.metric("Contas pagas", paid_count)
    a3.metric("Valor em aberto", money(total_open))
    if account_rows:
        st.dataframe(pd.DataFrame(account_rows), use_container_width=True, hide_index=True)
    else:
        st.info("Não há contas nesta situação.")
    if CAN_EDIT:
        payable_accounts = [
            account for account in accounts
            if account_statuses[account["id"]]["remaining"] > 0.01
            and account_filter in ("A pagar", "Todas")
        ]
        if payable_accounts:
            st.markdown("### Baixar parcela paga")
            st.caption("Use este botão somente quando a parcela tiver sido quitada integralmente.")
            for account in payable_accounts:
                payment = account_statuses[account["id"]]
                p1, p2 = st.columns([3, 1])
                p1.write(
                    f"**{account['description']}** · vence em {br_date(account.get('due_date'))} "
                    f"· falta {money(payment['remaining'])}"
                )
                if p2.button("✅ Marcar como paga", key=f"pay_full_account_{account['id']}"):
                    try:
                        insert_id(
                            """INSERT INTO payments
                               (commitment_id,payment_date,amount,notes,created_by)
                               VALUES(:c,:d,:a,:n,:u)""",
                            {
                                "c": account["id"],
                                "d": date.today(),
                                "a": float(payment["remaining"]),
                                "n": "Baixa integral pela tela de contas",
                                "u": user["id"],
                            },
                        )
                        ex("UPDATE commitments SET status='encerrado' WHERE id=:id", {"id": account["id"]})
                        log_action(user["id"], "pagou", "compromisso", account["id"], money(payment["remaining"]))
                        st.success("Parcela marcada como paga.")
                        st.rerun()
                    except Exception:
                        st.error("Não foi possível registrar o pagamento. Tente novamente.")
    if st.button("← Voltar para lançamentos", key="back_to_launch_from_accounts"):
        st.session_state.current_page = "📝 Lançar / Visualizar"
        st.rerun()

elif page == "🛒 Compras":
    st.subheader("Compras e compromissos")
    st.success(
        "Novo: cadastre máquinas e outras compras em várias parcelas, "
        "com vencimento e cultura de pagamento diferentes."
    )
    seasons = q("SELECT id,name,crop FROM seasons WHERE active=TRUE ORDER BY id DESC")
    season_map = {"Nenhuma": None}
    season_map.update({f"{s['name']} · {s['crop']}": s["id"] for s in seasons})

    categories = [
        "Sementes",
        "Fertilizantes",
        "Defensivos",
        "Máquinas",
        "Custeio",
        "Arrendamento",
        "Outro",
    ]
    payment_options = PAYMENT_OPTIONS
    show_purchase_history = st.session_state.get("purchase_show_history", False)
    purchase_nav_1, purchase_nav_2 = st.columns(2)
    if purchase_nav_1.button("➕ Nova compra", key="open_new_purchase", use_container_width=True, type="primary" if not show_purchase_history else "secondary"):
        st.session_state.purchase_show_history = False
        st.rerun()
    if purchase_nav_2.button("📚 Histórico de compras", key="open_purchase_history", use_container_width=True, type="primary" if show_purchase_history else "secondary"):
        st.session_state.purchase_show_history = True
        st.rerun()

    purchase_history_season = "Todas"
    purchase_history_year = "Todos"
    purchase_history_crop = "Todas"
    if show_purchase_history:
        st.markdown("### Histórico de compras")
        hf1, hf2, hf3 = st.columns(3)
        purchase_history_season = hf1.selectbox(
            "Safra", ["Todas"] + list(season_map), key="purchase_history_season"
        )
        purchase_history_year = hf2.selectbox(
            "Ano", ["Todos"] + [str(year) for year in range(date.today().year, date.today().year - 11, -1)],
            key="purchase_history_year",
        )
        purchase_history_crop = hf3.selectbox(
            "Cultura", ["Todas", "Soja", "Milho", "Trigo", "Canola", "Caixa"],
            key="purchase_history_crop",
        )

    if st.session_state.pop("reset_purchase_route_v31", False):
        st.session_state.pop("purchase_route_v31", None)

    if CAN_EDIT and not show_purchase_history:
        st.markdown("### Nova compra")
        purchase_route = st.radio(
            "Tipo de compra",
            ["Insumos", "Máquinas", "Bancos"],
            horizontal=True,
            key="purchase_route_v31",
        )
        if purchase_route in ("Máquinas", "Bancos"):
            st.session_state.reset_purchase_route_v31 = True
            st.session_state.current_page = "🚜 Máquinas e financiamentos"
            st.rerun()

        companies = q("SELECT id,name FROM companies WHERE active=TRUE ORDER BY name")
        products = q(
            """SELECT products.id,products.name,products.unit_id
               FROM products WHERE products.active=TRUE ORDER BY products.name"""
        )
        units = q("SELECT id,code,description FROM units WHERE active=TRUE ORDER BY code")
        if not companies or not products or not units:
            st.warning("Cadastre ao menos uma empresa, um produto e uma unidade antes de lançar insumos.")
            if st.button("⚙️ Abrir Cadastro", key="open_catalog_from_purchase", use_container_width=True):
                st.session_state.current_page = "⚙️ Cadastro"
                st.rerun()
            st.stop()

        company_map = {company["name"]: company for company in companies}
        product_map = {product["name"]: product for product in products}
        unit_map = {
            f"{unit['code']} · {unit.get('description') or unit['code']}": unit
            for unit in units
        }

        draft = st.session_state.get("insumo_purchase_review_v31")
        if not draft:
            c1, c2 = st.columns(2)
            selected_company_name = c1.selectbox("Empresa", list(company_map), key="insumo_company_v31")
            selected_product_name = c2.selectbox("Produto", list(product_map), key="insumo_product_v31")
            selected_product = product_map[selected_product_name]
            default_unit_index = next(
                (
                    index for index, unit in enumerate(unit_map.values())
                    if unit["id"] == selected_product.get("unit_id")
                ),
                0,
            )

            c3, c4 = st.columns(2)
            purchase_date = c3.date_input(
                "Data da compra", value=date.today(), format="DD/MM/YYYY", key="insumo_purchase_date_v31"
            )
            payment_date = c4.date_input(
                "Data do pagamento", value=date.today(), format="DD/MM/YYYY", key="insumo_payment_date_v31"
            )
            c5, c6 = st.columns(2)
            quantity = c5.number_input("Quantidade", min_value=0.0, step=1.0, key="insumo_quantity_v31")
            selected_unit_label = c6.selectbox(
                "Unidade", list(unit_map), index=default_unit_index, key="insumo_unit_v31"
            )
            unit_price = st.number_input(
                "Valor unitário (R$)", min_value=0.0, step=0.01, format="%.2f", key="insumo_unit_price_v31"
            )
            total_value = round(float(quantity) * float(unit_price), 2)
            st.number_input("Valor total (R$)", value=total_value, format="%.2f", disabled=True)

            if st.button("🔎 Revisar compra", key="review_insumo_purchase_v31", use_container_width=True, type="primary"):
                if quantity <= 0 or unit_price <= 0:
                    st.error("Informe uma quantidade e um valor unitário maiores que zero.")
                else:
                    st.session_state.insumo_purchase_review_v31 = {
                        "company": company_map[selected_company_name],
                        "product": selected_product,
                        "unit": unit_map[selected_unit_label],
                        "purchase_date": purchase_date,
                        "payment_date": payment_date,
                        "quantity": float(quantity),
                        "unit_price": float(unit_price),
                        "total_value": total_value,
                    }
                    st.rerun()
        else:
            confirmation_card(
                "🧾 Resumo da compra de insumos",
                [
                    ("Empresa", draft["company"]["name"]),
                    ("Produto", draft["product"]["name"]),
                    ("Data da compra", br_date(draft["purchase_date"])),
                    ("Data do pagamento", br_date(draft["payment_date"])),
                    ("Quantidade", f"{num(draft['quantity'])} {draft['unit']['code']}"),
                    ("Valor unitário", money(draft["unit_price"])),
                ],
                "Valor total",
                money(draft["total_value"]),
            )
            r1, r2, r3 = st.columns(3)
            save_insumo = r1.button("✅ Salvar", key="save_insumo_purchase_v31", use_container_width=True, type="primary")
            cancel_insumo = r2.button("↩️ Cancelar e voltar", key="cancel_insumo_purchase_v31", use_container_width=True)
            edit_insumo = r3.button("✏️ Editar compra", key="edit_insumo_purchase_v31", use_container_width=True)

            if edit_insumo:
                st.session_state.pop("insumo_purchase_review_v31", None)
                st.rerun()
            if cancel_insumo:
                for key in [
                    "insumo_purchase_review_v31", "insumo_company_v31", "insumo_product_v31",
                    "insumo_purchase_date_v31", "insumo_payment_date_v31", "insumo_quantity_v31",
                    "insumo_unit_v31", "insumo_unit_price_v31",
                ]:
                    st.session_state.pop(key, None)
                st.session_state.current_page = "📝 Lançar / Visualizar"
                st.rerun()
            if save_insumo:
                duplicate = q(
                    """SELECT id FROM commitments
                       WHERE COALESCE(status,'aberto') != 'cancelado'
                         AND company_id=:company_id AND product_id=:product_id
                         AND purchase_date=:purchase_date AND due_date=:payment_date
                         AND quantity=:quantity AND unit_price=:unit_price
                       LIMIT 1""",
                    {
                        "company_id": draft["company"]["id"],
                        "product_id": draft["product"]["id"],
                        "purchase_date": draft["purchase_date"],
                        "payment_date": draft["payment_date"],
                        "quantity": draft["quantity"],
                        "unit_price": draft["unit_price"],
                    },
                )
                if duplicate:
                    st.warning("Esta compra de insumo já está registrada. Nada foi salvo.")
                else:
                    commitment_id = insert_id(
                        """INSERT INTO commitments
                           (company_id,product_id,unit_id,quantity,unit_price,category,description,
                            supplier,total_value,purchase_date,due_date,payment_crop,notes,status,created_by)
                           VALUES(:company_id,:product_id,:unit_id,:quantity,:unit_price,'Insumos',:description,
                                  :supplier,:total_value,:purchase_date,:payment_date,'Caixa',:notes,'aberto',:created_by)""",
                        {
                            "company_id": draft["company"]["id"],
                            "product_id": draft["product"]["id"],
                            "unit_id": draft["unit"]["id"],
                            "quantity": draft["quantity"],
                            "unit_price": draft["unit_price"],
                            "description": draft["product"]["name"],
                            "supplier": draft["company"]["name"],
                            "total_value": draft["total_value"],
                            "purchase_date": draft["purchase_date"],
                            "payment_date": draft["payment_date"],
                            "notes": f"{num(draft['quantity'])} {draft['unit']['code']} × {money(draft['unit_price'])}",
                            "created_by": user["id"],
                        },
                    )
                    log_action(user["id"], "criou", "compra_insumo", commitment_id, draft["product"]["name"])
                    for key in ["insumo_purchase_review_v31", "insumo_quantity_v31", "insumo_unit_price_v31"]:
                        st.session_state.pop(key, None)
                    st.success("Compra de insumo salva com sucesso.")
                    st.rerun()
        st.stop()

    def save_purchase_record(
        description,
        category,
        supplier,
        total_value,
        purchase_date,
        due_date,
        payment_crop,
        selected_season,
        notes,
    ):
        if not description.strip() or total_value <= 0:
            st.error("Informe a descrição e o valor.")
            return False

        duplicate = q(
            """SELECT id FROM commitments
               WHERE COALESCE(status,'aberto') != 'cancelado'
                 AND lower(trim(description)) = lower(trim(:description))
                 AND COALESCE(lower(trim(supplier)), '') = COALESCE(lower(trim(:supplier)), '')
                 AND total_value = :value
                 AND purchase_date = :purchase_date
                 AND due_date = :due_date
                 AND season_id = :season_id
               LIMIT 1""",
            {
                "description": description.strip(),
                "supplier": supplier.strip(),
                "value": total_value,
                "purchase_date": purchase_date,
                "due_date": due_date,
                "season_id": season_map[selected_season],
            },
        )
        if duplicate:
            st.warning(
                "Esta compra já está registrada. Nenhum lançamento duplicado foi salvo."
            )
            return False

        try:
            commitment_id = insert_id(
                """INSERT INTO commitments
                   (season_id,category,description,supplier,total_value,
                    purchase_date,due_date,payment_crop,notes,status,created_by)
                   VALUES(:s,:c,:d,:f,:v,:pd,:dt,:p,:n,'aberto',:u)""",
                {
                    "s": season_map[selected_season],
                    "c": category,
                    "d": description.strip(),
                    "f": supplier.strip(),
                    "v": total_value,
                    "pd": purchase_date,
                    "dt": due_date,
                    "p": payment_crop,
                    "n": notes.strip(),
                    "u": user["id"],
                },
            )
            log_action(
                user["id"],
                "criou",
                "compromisso",
                commitment_id,
                description.strip(),
            )
            st.session_state.current_page = "🛒 Compras"
            st.success("Compra salva com sucesso.")
            return True
        except Exception as error:
            st.error(
                "Não foi possível salvar a compra. "
                "A sessão continuará aberta para você conferir os dados."
            )
            st.caption(f"Detalhe técnico: {error}")
            return False

    if st.session_state.pop("reset_purchase_type_after_redirect", False):
        st.session_state.pop("purchase_type_v22", None)

    if CAN_EDIT and not show_purchase_history:
        st.markdown("### Nova compra")
        st.caption(
            "Escolha o tipo de lançamento. O AGRIZA mostrará apenas os campos necessários."
        )

        purchase_type = st.radio(
            "Como foi a compra?",
            [
                "🛒 À vista",
                "📅 Com vencimento",
                "📑 Parcelada / contrato",
                "🚜 Máquina ou financiamento",
            ],
            horizontal=True,
            key="purchase_type_v22",
        )

        if purchase_type in ("📑 Parcelada / contrato", "🚜 Máquina ou financiamento"):
            st.session_state.reset_purchase_type_after_redirect = True
            st.session_state.current_page = "🚜 Máquinas e financiamentos"
            st.rerun()
        else:
            is_cash = purchase_type == "🛒 À vista"

            with st.form("guided_purchase_v22", clear_on_submit=False):
                p1, p2 = st.columns(2)
                description = p1.text_input(
                    "O que foi comprado?",
                    key="purchase_description_v22",
                    placeholder="Ex.: Fertilizante 05-20-20",
                )
                supplier = p2.text_input(
                    "Fornecedor",
                    key="purchase_supplier_v22",
                    placeholder="Ex.: Cooperativa Alfa",
                )

                p3, p4 = st.columns(2)
                category = p3.selectbox(
                    "Categoria",
                    categories,
                    key="purchase_category_v22",
                )
                total_value = p4.number_input(
                    "Valor total (R$)",
                    min_value=0.0,
                    step=100.0,
                    key="purchase_value_v22",
                )

                p5, p6 = st.columns(2)
                purchase_date = p5.date_input(
                    "Data da compra",
                    value=date.today(),
                    format="DD/MM/YYYY",
                    key="purchase_date_v22",
                )
                if is_cash:
                    due_date = purchase_date
                    p6.info("Pagamento à vista")
                else:
                    due_date = p6.date_input(
                        "Vencimento",
                        value=date.today(),
                        format="DD/MM/YYYY",
                        key="purchase_due_v22",
                    )

                p7, p8 = st.columns(2)
                payment_crop = p7.selectbox(
                    "Fonte prevista de pagamento",
                    ["Caixa"] if is_cash else payment_options,
                    key="purchase_crop_v22",
                )
                selected_season = p8.selectbox(
                    "Safra relacionada",
                    list(season_map),
                    key="purchase_season_v22",
                )

                notes = st.text_area(
                    "Observação (opcional)",
                    key="purchase_notes_v22",
                )

                review_purchase = st.form_submit_button(
                    "Revisar compra",
                    use_container_width=True,
                    type="primary",
                )

            if review_purchase:
                if not description.strip():
                    st.error("Informe o que foi comprado.")
                elif total_value <= 0:
                    st.error("Informe um valor maior que zero.")
                elif due_date < purchase_date:
                    st.error("O vencimento não pode ser anterior à data da compra.")
                else:
                    st.session_state.purchase_review_v22 = {
                        "description": description.strip(),
                        "supplier": supplier.strip(),
                        "category": category,
                        "total_value": float(total_value),
                        "purchase_date": purchase_date,
                        "due_date": due_date,
                        "payment_crop": payment_crop,
                        "selected_season": selected_season,
                        "notes": notes.strip(),
                        "purchase_type": purchase_type,
                    }

            purchase_review = st.session_state.get("purchase_review_v22")
            if purchase_review:
                d = purchase_review
                st.markdown("---")
                confirmation_card(
                    "✅ Confirmar compra",
                    [
                        ("Tipo", d["purchase_type"]),
                        ("Produto/serviço", d["description"]),
                        ("Fornecedor", d["supplier"] or "Não informado"),
                        ("Categoria", d["category"]),
                        ("Data da compra", d["purchase_date"].strftime("%d/%m/%Y")),
                        ("Vencimento", d["due_date"].strftime("%d/%m/%Y")),
                        ("Pagamento previsto", d["payment_crop"]),
                        ("Safra relacionada", d["selected_season"]),
                    ],
                    "Valor total",
                    money(d["total_value"]),
                    warnings=(
                        ["Fornecedor não informado. Confirme se deseja continuar."]
                        if not d["supplier"] else None
                    ),
                )

                b1, b2, b3 = st.columns(3)
                confirm_purchase = b1.button(
                    "✅ Confirmar e salvar",
                    use_container_width=True,
                    type="primary",
                    key="confirm_purchase_v22",
                )
                correct_purchase = b2.button(
                    "✏️ Corrigir",
                    use_container_width=True,
                    key="correct_purchase_v22",
                )
                discard_purchase = b3.button(
                    "🗑️ Descartar",
                    use_container_width=True,
                    key="discard_purchase_v22",
                )

                if correct_purchase:
                    st.session_state.pop("purchase_review_v22", None)
                    st.info("Corrija os campos acima e clique novamente em Revisar compra.")
                    st.rerun()

                if discard_purchase:
                    st.session_state.pop("purchase_review_v22", None)
                    for key in [
                        "purchase_description_v22", "purchase_supplier_v22",
                        "purchase_value_v22", "purchase_notes_v22",
                    ]:
                        st.session_state.pop(key, None)
                    st.info("Lançamento descartado. Nada foi salvo.")
                    st.rerun()

                if confirm_purchase:
                    if save_purchase_record(
                        d["description"],
                        d["category"],
                        d["supplier"],
                        d["total_value"],
                        d["purchase_date"],
                        d["due_date"],
                        d["payment_crop"],
                        d["selected_season"],
                        d["notes"],
                    ):
                        st.session_state.pop("purchase_review_v22", None)
                        st.success("Compra confirmada e salva.")
                        st.rerun()

        with st.expander("🎙️ Compra por voz", expanded=False):
            st.caption(
                "Dite a compra. O AGRIZA interpreta e mostra o resumo antes de salvar."
            )
            with st.form("voice_purchase_interpret_v22"):
                spoken_purchase = st.text_area(
                    "Dite ou escreva",
                    placeholder=(
                        "Comprei fertilizante da Cooperativa Alfa por 35 mil reais, "
                        "vence em 30 dias, pagar com soja"
                    ),
                    height=100,
                )
                interpret_purchase = st.form_submit_button(
                    "Interpretar",
                    use_container_width=True,
                )

            if interpret_purchase:
                if not spoken_purchase.strip():
                    st.error("Dite ou escreva os dados da compra.")
                else:
                    st.session_state.voice_purchase_draft_v22 = parse_spoken_purchase(
                        spoken_purchase, seasons
                    )

            voice_draft = st.session_state.get("voice_purchase_draft_v22")
            if voice_draft:
                season_labels = list(season_map)
                with st.form("voice_purchase_review_form_v22", clear_on_submit=False):
                    vd1, vd2 = st.columns(2)
                    v_description = vd1.text_input(
                        "O que foi comprado?",
                        value=voice_draft.get("description", ""),
                    )
                    v_supplier = vd2.text_input(
                        "Fornecedor",
                        value=voice_draft.get("supplier", ""),
                    )
                    vd3, vd4 = st.columns(2)
                    v_total = vd3.number_input(
                        "Valor total (R$)",
                        min_value=0.0,
                        value=float(voice_draft.get("total_value", 0.0)),
                    )
                    v_category = vd4.selectbox(
                        "Categoria",
                        categories,
                        index=(
                            categories.index(voice_draft.get("category"))
                            if voice_draft.get("category") in categories else len(categories) - 1
                        ),
                    )
                    vd5, vd6 = st.columns(2)
                    v_purchase_date = vd5.date_input(
                        "Data da compra",
                        value=date.today(),
                        format="DD/MM/YYYY",
                    )
                    v_due = vd6.date_input(
                        "Vencimento",
                        value=voice_draft.get("due_date", date.today()),
                        format="DD/MM/YYYY",
                    )
                    v_crop = st.selectbox(
                        "Fonte prevista de pagamento",
                        payment_options,
                        index=(
                            payment_options.index(voice_draft.get("payment_crop"))
                            if voice_draft.get("payment_crop") in payment_options else 0
                        ),
                    )
                    v_season = st.selectbox(
                        "Safra relacionada",
                        season_labels,
                        index=(
                            season_labels.index(voice_draft.get("season_label"))
                            if voice_draft.get("season_label") in season_labels else 0
                        ),
                    )
                    v_notes = st.text_area(
                        "Observação",
                        value=voice_draft.get("notes", ""),
                    )
                    review_voice_purchase = st.form_submit_button(
                        "Revisar compra por voz",
                        use_container_width=True,
                    )

                if review_voice_purchase:
                    if not v_description.strip() or v_total <= 0:
                        st.error("Confira a descrição e o valor.")
                    else:
                        st.session_state.purchase_review_v22 = {
                            "description": v_description.strip(),
                            "supplier": v_supplier.strip(),
                            "category": v_category,
                            "total_value": float(v_total),
                            "purchase_date": v_purchase_date,
                            "due_date": v_due,
                            "payment_crop": v_crop,
                            "selected_season": v_season,
                            "notes": v_notes.strip(),
                            "purchase_type": "🎙️ Compra por voz",
                        }
                        st.session_state.pop("voice_purchase_draft_v22", None)
                        st.rerun()

    if show_purchase_history:
        st.markdown("---")
        st.caption("Use os filtros para localizar compras, contratos e parcelas já registrados.")
    contracts = q(
        """SELECT pc.*,
                  COALESCE(SUM(c.total_value),0) AS installment_total,
                  COALESCE(SUM(
                    CASE WHEN COALESCE(c.status,'aberto')='encerrado'
                         THEN c.total_value ELSE 0 END
                  ),0) AS closed_total
           FROM purchase_contracts pc
           LEFT JOIN commitments c ON c.contract_id=pc.id
           WHERE COALESCE(pc.status,'aberto')!='cancelado'
           GROUP BY pc.id
           ORDER BY pc.purchase_date DESC,pc.id DESC"""
    ) if show_purchase_history else []
    if purchase_history_year != "Todos":
        contracts = [
            contract for contract in contracts
            if str(contract.get("purchase_date") or "")[:4] == purchase_history_year
        ]

    if contracts:
        st.markdown("---")
        st.markdown("### 📑 Contratos e parcelas")
        st.caption("Área separada das compras comuns.")
        for contract in contracts:
            installments = q(
                """SELECT * FROM commitments
                   WHERE contract_id=:id
                     AND COALESCE(status,'aberto')!='cancelado'
                   ORDER BY installment_no,due_date""",
                {"id": contract["id"]},
            )
            paid_total = sum(
                float(
                    scalar(
                        "SELECT COALESCE(SUM(amount),0) FROM payments "
                        "WHERE commitment_id=:id",
                        {"id": installment["id"]},
                    ) or 0
                )
                for installment in installments
            )
            balance = float(contract["total_value"]) - paid_total

            with st.expander(
                f"📄 {contract['description']} · "
                f"{money(contract['total_value'])} · saldo {money(balance)}"
            ):
                st.write(
                    f"**Fornecedor:** "
                    f"{contract.get('supplier') or 'Não informado'}"
                )
                st.write(
                    f"**Data da compra:** "
                    f"{br_date(contract.get('purchase_date'))}"
                )
                paid_installments = sum(
                    1 for installment in installments
                    if commitment_status(installment["id"])["remaining"] <= 0.01
                )
                st.caption(
                    f"{paid_installments} parcela(s) paga(s) · "
                    f"{len(installments) - paid_installments} parcela(s) a pagar"
                )
                for installment in installments:
                    installment_status = commitment_status(installment["id"])
                    mark = "✅ Paga" if installment_status["remaining"] <= 0.01 else "⏳ A pagar"
                    st.write(
                        f"{mark} · **Parcela {installment.get('installment_no') or '-'}** "
                        f"— {br_date(installment['due_date'])} — "
                        f"{money(installment['total_value'])} — "
                        f"pagar com **{installment.get('payment_crop') or 'Caixa'}** "
                        f"— falta {money(installment_status['remaining'])}"
                    )

    commitments = q(
        """SELECT * FROM commitments
           WHERE COALESCE(status,'aberto')!='cancelado'
             AND contract_id IS NULL
           ORDER BY due_date,id DESC"""
    ) if show_purchase_history else []
    if show_purchase_history:
        season_crop_by_id = {season["id"]: season["crop"] for season in seasons}
        selected_season_id = season_map.get(purchase_history_season)
        commitments = [
            item for item in commitments
            if (purchase_history_season == "Todas" or item.get("season_id") == selected_season_id)
            and (purchase_history_year == "Todos" or str(item.get("purchase_date") or "")[:4] == purchase_history_year)
            and (
                purchase_history_crop == "Todas"
                or season_crop_by_id.get(item.get("season_id"), item.get("payment_crop")) == purchase_history_crop
                or item.get("payment_crop") == purchase_history_crop
            )
        ]
    if show_purchase_history and not commitments:
        st.caption("Nenhum compromisso registrado.")

    for item in commitments:
        status = commitment_status(item["id"])
        icon = "🟢" if status["pct"] >= 99 else "🟡" if status["pct"] >= 50 else "🔴"
        with st.expander(f"{icon} {item['description']} · {br_date(item['due_date'])}"):
            st.write(f"**Valor:** {money(item['total_value'])}")
            st.write(
                f"**Data da compra:** "
                f"{br_date(item.get('purchase_date'))}"
            )
            st.write(f"**Vencimento:** {br_date(item['due_date'], 'Não informado')}")
            st.write(f"**Fornecedor:** {item['supplier'] or 'Não informado'}")
            st.write(f"**Protegido por vendas:** {money(status['protected'])}")
            st.write(f"**Pago:** {money(status['paid'])}")
            st.write(f"**Ainda falta:** {money(status['remaining'])}")

            a1, a2, a3 = st.columns(3)
            if a1.button("👁️ Visualizar", key=f"view_purchase_{item['id']}"):
                st.info(
                    f"Compra de {money(item['total_value'])}; faltam {money(status['remaining'])} para encerrar."
                )
            if CAN_EDIT and a2.button("✏️ Editar", key=f"open_edit_purchase_{item['id']}"):
                st.session_state[f"edit_purchase_open_{item['id']}"] = True
            if CAN_EDIT and a3.button("🗑️ Excluir", key=f"delete_purchase_{item['id']}"):
                st.session_state[f"confirm_delete_purchase_{item['id']}"] = True

            if CAN_EDIT:
                with st.expander(
                    "✏️ Editar esta compra",
                    expanded=st.session_state.get(f"edit_purchase_open_{item['id']}", False),
                ):
                    season_labels = list(season_map)
                    current_season_label = "Nenhuma"
                    for label, season_id in season_map.items():
                        if season_id == item.get("season_id"):
                            current_season_label = label
                            break

                    current_category = (
                        item.get("category")
                        if item.get("category") in categories
                        else "Outro"
                    )
                    current_payment = (
                        item.get("payment_crop")
                        if item.get("payment_crop") in payment_options
                        else "Caixa"
                    )

                    with st.form(f"edit_purchase_{item['id']}"):
                        edit_description = st.text_input(
                            "O que foi comprado?",
                            value=item.get("description") or "",
                        )
                        edit_category = st.selectbox(
                            "Categoria",
                            categories,
                            index=categories.index(current_category),
                        )
                        edit_supplier = st.text_input(
                            "Fornecedor",
                            value=item.get("supplier") or "",
                        )

                        e1, e2, e3 = st.columns(3)
                        edit_value = e1.number_input(
                            "Valor total (R$)",
                            min_value=0.0,
                            value=float(item.get("total_value") or 0),
                            step=100.0,
                        )
                        edit_purchase_date = e2.date_input(
                            "Data da compra",
                            value=item.get("purchase_date") or date.today(),
                            format="DD/MM/YYYY",
                        )
                        edit_due_date = e3.date_input(
                            "Vencimento",
                            value=item.get("due_date") or date.today(),
                            format="DD/MM/YYYY",
                        )

                        edit_payment_crop = st.selectbox(
                            "Pretende pagar com",
                            payment_options,
                            index=payment_options.index(current_payment),
                        )
                        edit_season = st.selectbox(
                            "Safra relacionada",
                            season_labels,
                            index=season_labels.index(current_season_label),
                        )
                        edit_notes = st.text_area(
                            "Observação",
                            value=item.get("notes") or "",
                        )
                        save_edit = st.form_submit_button(
                            "Salvar alterações",
                            use_container_width=True,
                        )

                    if save_edit:
                        if not edit_description.strip() or edit_value <= 0:
                            st.error("Informe a descrição e o valor.")
                        else:
                            try:
                                ex(
                                    """UPDATE commitments
                                       SET season_id=:s,
                                           category=:c,
                                           description=:d,
                                           supplier=:f,
                                           total_value=:v,
                                           purchase_date=:pd,
                                           due_date=:dt,
                                           payment_crop=:p,
                                           notes=:n
                                       WHERE id=:id""",
                                    {
                                        "s": season_map[edit_season],
                                        "c": edit_category,
                                        "d": edit_description.strip(),
                                        "f": edit_supplier.strip(),
                                        "v": edit_value,
                                        "pd": edit_purchase_date,
                                        "dt": edit_due_date,
                                        "p": edit_payment_crop,
                                        "n": edit_notes.strip(),
                                        "id": item["id"],
                                    },
                                )
                                log_action(
                                    user["id"],
                                    "editou",
                                    "compromisso",
                                    item["id"],
                                    edit_description.strip(),
                                )
                                st.session_state.current_page = "🛒 Compras"
                                st.success("Compra atualizada com sucesso.")
                                st.session_state.pop(f"edit_purchase_open_{item['id']}", None)
                            except Exception as error:
                                st.error("Não foi possível atualizar a compra.")
                                st.caption("Confira os dados e tente novamente.")

            if st.session_state.get(f"confirm_delete_purchase_{item['id']}"):
                st.warning("A compra será cancelada e deixará de aparecer nos totais em aberto.")
                d1, d2 = st.columns(2)
                if d1.button("Confirmar exclusão", key=f"confirm_purchase_delete_{item['id']}"):
                    ex("UPDATE commitments SET status='cancelado' WHERE id=:id", {"id": item["id"]})
                    log_action(user["id"], "cancelou", "compromisso", item["id"], item["description"])
                    st.success("Compra cancelada.")
                    st.rerun()
                if d2.button("Cancelar", key=f"cancel_purchase_delete_{item['id']}"):
                    st.session_state.pop(f"confirm_delete_purchase_{item['id']}", None)
                    st.rerun()

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
                        format="DD/MM/YYYY",
                        key=f"payment_date_{item['id']}",
                    )
                    note = st.text_input(
                        "Observação do pagamento",
                        key=f"payment_note_{item['id']}",
                    )
                    submit_payment = st.form_submit_button("Salvar pagamento")

                if submit_payment and amount > 0:
                    try:
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
                        log_action(
                            user["id"],
                            "pagou",
                            "compromisso",
                            item["id"],
                            money(amount),
                        )
                        st.session_state.current_page = "🛒 Compras"
                        st.success("Pagamento registrado.")
                    except Exception as error:
                        st.error("Não foi possível registrar o pagamento.")
                        st.caption("Confira os dados e tente novamente.")

                if st.button(
                    "Marcar como encerrado",
                    key=f"close_commitment_{item['id']}",
                ):
                    ex(
                        "UPDATE commitments SET status='encerrado' WHERE id=:id",
                        {"id": item["id"]},
                    )
                    log_action(
                        user["id"],
                        "encerrou",
                        "compromisso",
                        item["id"],
                        item["description"],
                    )
                    st.session_state.current_page = "🛒 Compras"
                    st.success("Compromisso encerrado.")


elif page == "🚜 Máquinas e financiamentos":
    payment_options = PAYMENT_OPTIONS
    st.subheader("Máquinas e financiamentos")
    st.success(
        "Cadastre aqui a plantadeira e todas as parcelas da compra, "
        "em um único passo."
    )

    tab_new, tab_list = st.tabs(
        ["➕ Cadastrar máquina financiada", "📋 Máquinas cadastradas"]
    )

    with tab_new:
        st.markdown("### 1. Dados da máquina")

        use_planter = st.checkbox(
            "Usar o exemplo da minha plantadeira",
            value=True,
            key="machine_planter_example_v102",
        )

        if use_planter:
            default_machine_name = "Plantadeira"
            default_contract_value = 405000.0
            default_count = 4
        else:
            default_machine_name = ""
            default_contract_value = 0.0
            default_count = 1

        st.markdown("### Tipo de financiamento")
        finance_table = st.selectbox(
            "Tabela de financiamento",
            ["Manual", "SAC", "Price", "Americana"],
            key="machine_finance_table_v103",
            help=(
                "A seleção atualiza imediatamente os campos abaixo. Manual mantém as parcelas "
                "preenchidas por você; as demais opções fazem a simulação automática."
            ),
        )

        with st.form("machine_financing_v102", clear_on_submit=False):
            m1, m2 = st.columns(2)
            machine_name = m1.text_input(
                "Nome da máquina ou implemento",
                value=default_machine_name,
                placeholder="Ex.: Plantadeira 13 linhas",
            )
            supplier = m2.text_input(
                "Fornecedor / vendedor",
                placeholder="Ex.: Agro Máquinas",
            )

            m3, m4, m5 = st.columns(3)
            brand = m3.text_input("Marca")
            model = m4.text_input("Modelo")
            machine_year = m5.number_input(
                "Ano",
                min_value=1950,
                max_value=2100,
                value=date.today().year,
                step=1,
            )

            m6, m7 = st.columns(2)
            purchase_date = m6.date_input(
                "Data da compra",
                value=date.today(),
                format="DD/MM/YYYY",
            )
            total_value = m7.number_input(
                "Valor total da compra (R$)",
                min_value=0.0,
                value=default_contract_value,
                step=1000.0,
            )

            notes = st.text_area(
                "Observações",
                value="Entrada mais 3 parcelas anuais" if use_planter else "",
            )

            st.markdown("### 2. Parcelas do financiamento")
            interest_rate = 0.0
            first_due_date = date.today()
            interval_months = 1
            financed_value = float(total_value)

            if finance_table == "Manual":
                st.caption("Informe cada parcela manualmente, como já é feito hoje.")
                installment_count = st.number_input(
                    "Quantas parcelas serão pagas?",
                    min_value=1,
                    max_value=20,
                    value=default_count,
                    step=1,
                )
            else:
                table_descriptions = {
                    "SAC": "SAC: a amortização é constante e as parcelas diminuem ao longo do prazo.",
                    "Price": "Price: as parcelas têm valor fixo durante todo o financiamento.",
                    "Americana": "Americana: são pagos juros por período e o principal é quitado na última parcela.",
                }
                st.info(table_descriptions[finance_table])
                st.markdown(f"#### Simular financiamento pela tabela {finance_table}")
                f1, f2 = st.columns(2)
                financed_value = f1.number_input(
                    "Valor financiado (R$)",
                    min_value=0.01,
                    value=max(float(total_value), 0.01),
                    step=1000.0,
                    help="Valor que será usado no cálculo das parcelas. Pode ser diferente do valor total do bem caso exista entrada.",
                )
                installment_count = f2.number_input(
                    "Prazo (número de parcelas)",
                    min_value=1,
                    max_value=240,
                    value=max(int(default_count), 1),
                    step=1,
                )
                f3, f4, f5 = st.columns(3)
                interest_rate = f3.number_input(
                    "Taxa de juros por período (%)",
                    min_value=0.0,
                    value=1.0,
                    step=0.1,
                )
                first_due_date = f4.date_input(
                    "Primeiro vencimento",
                    value=date.today() + timedelta(days=30),
                    format="DD/MM/YYYY",
                )
                interval_months = f5.number_input(
                    "Periodicidade (meses)", min_value=1, max_value=24, value=1
                )
                st.caption(
                    f"Simulação: {int(installment_count)} parcelas, juros de {interest_rate:.2f}% por período "
                    f"sobre {money(financed_value)}."
                )

            example_dates = [
                date(2026, 11, 20),
                date(2027, 5, 20),
                date(2028, 5, 20),
                date(2029, 5, 20),
            ]
            example_values = [60000.0, 115000.0, 115000.0, 115000.0]
            example_crops = ["Trigo", "Soja", "Soja", "Soja"]

            rows = []
            if finance_table == "Manual":
                for index in range(int(installment_count)):
                    st.markdown(f"#### Parcela {index + 1}")
                    p1, p2, p3 = st.columns(3)

                    if use_planter and index < 4:
                        due_default = example_dates[index]
                        value_default = example_values[index]
                        crop_default = example_crops[index]
                    else:
                        due_default = date.today()
                        value_default = 0.0
                        crop_default = "Caixa"

                    due_date_value = p1.date_input(
                        "Vencimento", value=due_default, format="DD/MM/YYYY",
                        key=f"machine_due_v102_{index}",
                    )
                    installment_value = p2.number_input(
                        "Valor da parcela (R$)", min_value=0.0,
                        value=value_default, step=1000.0,
                        key=f"machine_value_v102_{index}",
                    )
                    payment_crop_value = p3.selectbox(
                        "Será paga com", payment_options,
                        index=payment_options.index(crop_default) if crop_default in payment_options else 0,
                        key=f"machine_crop_v102_{index}",
                    )
                    rows.append({"number": index + 1, "due_date": due_date_value,
                                 "value": installment_value, "crop": payment_crop_value})
            else:
                balance = float(financed_value)
                rate = float(interest_rate) / 100
                count = int(installment_count)
                fixed_payment = (
                    balance * rate / (1 - (1 + rate) ** -count)
                    if finance_table == "Price" and rate > 0 else balance / count
                )
                schedule_preview = []
                total_amortization = 0.0
                total_interest = 0.0
                total_installments = 0.0
                for index in range(count):
                    interest = balance * rate
                    if finance_table == "SAC":
                        amortization = float(financed_value) / count
                        installment_value = amortization + interest
                    elif finance_table == "Americana":
                        amortization = balance if index == count - 1 else 0
                        installment_value = interest + amortization
                    else:  # Price
                        installment_value = fixed_payment
                        amortization = installment_value - interest
                    amortization = min(amortization, balance)
                    balance = max(balance - amortization, 0)
                    total_amortization += amortization
                    total_interest += interest
                    total_installments += installment_value
                    due_date_value = first_due_date + timedelta(days=30 * int(interval_months) * index)
                    rows.append({"number": index + 1, "due_date": due_date_value,
                                 "value": round(installment_value, 2), "crop": "Caixa"})
                    schedule_preview.append({
                        "Parcela": index + 1,
                        "Vencimento": br_date(due_date_value),
                        "Amortização": money(amortization),
                        "Juros": money(interest),
                        "Valor": money(installment_value),
                        "Saldo devedor": money(balance),
                    })
                schedule_preview.append({
                    "Parcela": "Total",
                    "Vencimento": "—",
                    "Amortização": money(total_amortization),
                    "Juros": money(total_interest),
                    "Valor": money(total_installments),
                    "Saldo devedor": money(balance),
                })
                st.dataframe(pd.DataFrame(schedule_preview), use_container_width=True, hide_index=True)

            submitted = st.form_submit_button(
                "✅ Salvar máquina e parcelas",
                use_container_width=True,
            )

        if submitted:
            valid_rows = [row for row in rows if float(row["value"] or 0) > 0]
            installment_sum = sum(float(row["value"]) for row in valid_rows)

            if not machine_name.strip():
                st.error("Informe o nome da máquina.")
            elif total_value <= 0:
                st.error("Informe o valor total da compra.")
            elif len(valid_rows) != int(installment_count):
                st.error("Preencha o valor de todas as parcelas.")
            elif finance_table == "Manual" and abs(installment_sum - total_value) > 0.01:
                st.error(
                    f"A soma das parcelas é {money(installment_sum)}, "
                    f"mas o valor total da compra é {money(total_value)}."
                )
            else:
                st.session_state.machine_draft_v103 = {
                    "machine_name": machine_name.strip(),
                    "supplier": supplier.strip(),
                    "brand": brand.strip(),
                    "model": model.strip(),
                    "year": int(machine_year),
                    "purchase_date": purchase_date,
                    "total_value": float(total_value),
                    "notes": (
                        notes.strip()
                        + (
                            f" · Tabela: {finance_table} · Financiado: {money(financed_value)} "
                            f"· Juros: {interest_rate:.2f}% por período"
                            if finance_table != "Manual"
                            else ""
                        )
                    ).strip(" ·"),
                    "rows": valid_rows,
                }

        d = st.session_state.get("machine_draft_v103")
        if d:
            st.markdown("---")
            confirmation_card(
                "🚜 Confirme a máquina e as parcelas",
                [
                    ("Máquina", d["machine_name"]),
                    ("Fornecedor", d["supplier"] or "Não informado"),
                    ("Marca/modelo", f"{d['brand'] or '—'} {d['model'] or ''}".strip()),
                    ("Data da compra", d["purchase_date"].strftime("%d/%m/%Y")),
                    ("Quantidade de parcelas", len(d["rows"])),
                ],
                "Valor total",
                money(d["total_value"]),
            )
            for row in d["rows"]:
                st.write(
                    f"**Parcela {row['number']}** — "
                    f"{row['due_date'].strftime('%d/%m/%Y')} — "
                    f"{money(row['value'])} — pagar com **{row['crop']}**"
                )

            c1, c2, c3 = st.columns(3)
            confirm_machine = c1.button(
                "✅ Confirmar e salvar", use_container_width=True,
                key="confirm_machine_v103"
            )
            correct_machine = c2.button(
                "✏️ Corrigir informações", use_container_width=True,
                key="correct_machine_v103"
            )
            discard_machine = c3.button(
                "🗑️ Descartar lançamento", use_container_width=True,
                key="discard_machine_v103"
            )

            if discard_machine:
                del st.session_state.machine_draft_v103
                st.info("Lançamento descartado. Nada foi salvo.")
                st.rerun()

            if correct_machine:
                del st.session_state.machine_draft_v103
                st.info("Corrija os campos acima e gere o resumo novamente.")
                st.rerun()

            if confirm_machine:
                try:
                    duplicate_contract = q(
                        """SELECT id FROM purchase_contracts
                           WHERE COALESCE(status,'aberto') != 'cancelado'
                             AND lower(trim(description)) = lower(trim(:description))
                             AND COALESCE(lower(trim(supplier)), '') = COALESCE(lower(trim(:supplier)), '')
                             AND total_value = :value
                             AND purchase_date = :purchase_date
                           LIMIT 1""",
                        {
                            "description": d["machine_name"],
                            "supplier": d["supplier"],
                            "value": d["total_value"],
                            "purchase_date": d["purchase_date"],
                        },
                    )
                    if duplicate_contract:
                        st.warning(
                            "Este contrato de máquina já está registrado. Nada foi salvo."
                        )
                        st.stop()
                    contract_id = insert_id(
                        """INSERT INTO purchase_contracts
                           (description,supplier,category,total_value,
                            purchase_date,notes,status,created_by)
                           VALUES(:d,:f,'Máquinas',:v,:pd,:n,'aberto',:u)""",
                        {
                            "d": d["machine_name"], "f": d["supplier"],
                            "v": d["total_value"], "pd": d["purchase_date"],
                            "n": d["notes"], "u": user["id"],
                        },
                    )
                    machine_id = insert_id(
                        """INSERT INTO machinery
                           (name,brand,model,year,acquisition_date,
                            acquisition_value,contract_id,status,notes,created_by)
                           VALUES(:n,:b,:m,:y,:d,:v,:c,'ativo',:o,:u)""",
                        {
                            "n": d["machine_name"], "b": d["brand"],
                            "m": d["model"], "y": d["year"],
                            "d": d["purchase_date"], "v": d["total_value"],
                            "c": contract_id, "o": d["notes"],
                            "u": user["id"],
                        },
                    )
                    for row in d["rows"]:
                        insert_id(
                            """INSERT INTO commitments
                               (contract_id,installment_no,season_id,category,
                                description,supplier,total_value,purchase_date,
                                due_date,payment_crop,notes,status,created_by)
                               VALUES(:ct,:ino,NULL,'Máquinas',:d,:f,:v,:pd,
                                      :dt,:p,:n,'aberto',:u)""",
                            {
                                "ct": contract_id, "ino": row["number"],
                                "d": f"{d['machine_name']} · Parcela {row['number']}",
                                "f": d["supplier"], "v": float(row["value"]),
                                "pd": d["purchase_date"], "dt": row["due_date"],
                                "p": row["crop"], "n": d["notes"],
                                "u": user["id"],
                            },
                        )
                    log_action(
                        user["id"], "criou", "maquina_financiada",
                        machine_id, f"{d['machine_name']} · {len(d['rows'])} parcelas"
                    )
                    del st.session_state.machine_draft_v103
                    st.success("Máquina e parcelas salvas com sucesso.")
                    st.rerun()
                except Exception as error:
                    st.error("Não foi possível salvar a máquina e as parcelas.")
                    st.caption(f"Detalhe técnico: {error}")

    with tab_list:
        machines = q(
            """SELECT m.*,pc.description AS contract_description,
                      pc.total_value AS contract_total
               FROM machinery m
               LEFT JOIN purchase_contracts pc ON pc.id=m.contract_id
               WHERE COALESCE(m.status,'ativo')!='excluido'
               ORDER BY m.id DESC"""
        )

        if not machines:
            st.info("Nenhuma máquina cadastrada.")
        else:
            for machine in machines:
                with st.expander(f"🚜 {machine['name']}", expanded=False):
                    st.write(
                        f"**Marca/modelo:** "
                        f"{machine.get('brand') or '—'} "
                        f"{machine.get('model') or ''}"
                    )
                    st.write(
                        f"**Valor da compra:** "
                        f"{money(machine.get('contract_total') or machine.get('acquisition_value') or 0)}"
                    )
                    a1, a2, a3 = st.columns(3)
                    if a1.button("👁️ Visualizar", key=f"view_machine_{machine['id']}"):
                        st.info(
                            f"Aquisição em {br_date(machine.get('acquisition_date'))}; "
                            f"status: {machine.get('status') or 'ativo'}."
                        )
                    if CAN_EDIT and a2.button("✏️ Editar", key=f"open_edit_machine_{machine['id']}"):
                        st.session_state[f"edit_machine_{machine['id']}"] = True
                    if CAN_EDIT and a3.button("🗑️ Excluir", key=f"delete_machine_{machine['id']}"):
                        st.session_state[f"confirm_delete_machine_{machine['id']}"] = True

                    if st.session_state.get(f"edit_machine_{machine['id']}"):
                        with st.form(f"machine_edit_form_{machine['id']}"):
                            em1, em2 = st.columns(2)
                            edit_machine_name = em1.text_input("Nome", value=machine.get("name") or "")
                            edit_machine_brand = em2.text_input("Marca", value=machine.get("brand") or "")
                            em3, em4 = st.columns(2)
                            edit_machine_model = em3.text_input("Modelo", value=machine.get("model") or "")
                            edit_machine_year = em4.number_input("Ano", min_value=1950, max_value=2100, value=int(machine.get("year") or date.today().year))
                            edit_machine_notes = st.text_area("Observação", value=machine.get("notes") or "")
                            save_machine_edit = st.form_submit_button("Salvar alterações")
                        if save_machine_edit:
                            ex("""UPDATE machinery SET name=:n,brand=:b,model=:m,year=:y,notes=:o WHERE id=:id""",
                               {"n": edit_machine_name.strip(), "b": edit_machine_brand.strip(), "m": edit_machine_model.strip(), "y": edit_machine_year, "o": edit_machine_notes.strip(), "id": machine["id"]})
                            log_action(user["id"], "editou", "máquina", machine["id"], edit_machine_name.strip())
                            st.session_state.pop(f"edit_machine_{machine['id']}", None)
                            st.success("Máquina atualizada.")
                            st.rerun()

                    if st.session_state.get(f"confirm_delete_machine_{machine['id']}"):
                        st.warning("A máquina e as parcelas abertas do contrato serão marcadas como excluídas/canceladas.")
                        d1, d2 = st.columns(2)
                        if d1.button("Confirmar exclusão", key=f"confirm_machine_delete_{machine['id']}"):
                            ex("UPDATE machinery SET status='excluido' WHERE id=:id", {"id": machine["id"]})
                            if machine.get("contract_id"):
                                ex("UPDATE purchase_contracts SET status='cancelado' WHERE id=:id", {"id": machine["contract_id"]})
                                ex("UPDATE commitments SET status='cancelado' WHERE contract_id=:id AND COALESCE(status,'aberto')='aberto'", {"id": machine["contract_id"]})
                            log_action(user["id"], "excluiu", "máquina", machine["id"], machine["name"])
                            st.success("Máquina excluída.")
                            st.rerun()
                        if d2.button("Cancelar", key=f"cancel_machine_delete_{machine['id']}"):
                            st.session_state.pop(f"confirm_delete_machine_{machine['id']}", None)
                            st.rerun()

                    installments = q(
                        """SELECT * FROM commitments
                           WHERE contract_id=:id
                             AND COALESCE(status,'aberto')!='cancelado'
                           ORDER BY installment_no""",
                        {"id": machine.get("contract_id")},
                    )

                    if installments:
                        st.markdown("#### Parcelas")
                        paid_installments = sum(
                            1 for installment in installments
                            if commitment_status(installment["id"])["remaining"] <= 0.01
                        )
                        st.caption(
                            f"{paid_installments} parcela(s) paga(s) · "
                            f"{len(installments) - paid_installments} parcela(s) a pagar"
                        )
                        for installment in installments:
                            status = commitment_status(installment["id"])
                            mark = "✅ Paga" if status["remaining"] <= 0.01 else "⏳ A pagar"
                            st.write(
                                f"{mark} · **Parcela "
                                f"{installment.get('installment_no') or '-'}** — "
                                f"{installment.get('due_date')} — "
                                f"{money(installment.get('total_value') or 0)} — "
                                f"pagar com **"
                                f"{installment.get('payment_crop') or 'Caixa'}** — "
                                f"falta {money(status['remaining'])}"
                            )

elif page == "💰 Vendas":
    st.subheader("Comercialização")
    seasons = q("SELECT id,name,crop FROM seasons WHERE active=TRUE ORDER BY id DESC")
    show_sale_history = st.session_state.get("sale_show_history", False)
    sale_nav_1, sale_nav_2 = st.columns(2)
    if sale_nav_1.button("➕ Nova venda", key="open_new_sale", use_container_width=True, type="primary" if not show_sale_history else "secondary"):
        st.session_state.sale_show_history = False
        st.rerun()
    if sale_nav_2.button("📚 Histórico de vendas", key="open_sale_history", use_container_width=True, type="primary" if show_sale_history else "secondary"):
        st.session_state.sale_show_history = True
        st.rerun()

    if not seasons:
        st.info("Cadastre uma safra antes de registrar vendas.")
    else:
        season_map = {f"{s['name']} · {s['crop']}": s["id"] for s in seasons}
        sale_history_season = "Todas"
        sale_history_year = "Todos"
        sale_history_crop = "Todas"
        if show_sale_history:
            st.markdown("### Histórico de vendas")
            hf1, hf2, hf3 = st.columns(3)
            sale_history_season = hf1.selectbox(
                "Safra", ["Todas"] + list(season_map), key="sale_history_season"
            )
            sale_history_year = hf2.selectbox(
                "Ano", ["Todos"] + [str(year) for year in range(date.today().year, date.today().year - 11, -1)],
                key="sale_history_year",
            )
            sale_history_crop = hf3.selectbox(
                "Cultura", ["Todas", "Soja", "Milho", "Trigo", "Canola"],
                key="sale_history_crop",
            )
        commitments = q(
            """SELECT id,description,due_date FROM commitments
               WHERE COALESCE(status,'aberto')='aberto'
               ORDER BY due_date"""
        )
        commitment_map = {"Venda livre": None}
        commitment_map.update(
            {f"{c['description']} · {br_date(c['due_date'])}": c["id"] for c in commitments}
        )

        def save_sale_record(
            season_label,
            quantity,
            price,
            buyer,
            objective,
            sale_date,
            payment_date,
            notes,
        ):
            if quantity <= 0 or price <= 0:
                st.error("Informe quantidade e preço.")
                return False

            season_id = season_map[season_label]
            summary = season_summary(
                q("SELECT * FROM seasons WHERE id=:id", {"id": season_id})[0]
            )
            if quantity > summary["balance"]:
                st.error(
                    f"A venda supera o saldo livre de "
                    f"{num(summary['balance'], 0)} sc."
                )
                return False

            try:
                sale_id = insert_id(
                    """INSERT INTO sales
                       (season_id,sale_date,payment_date,quantity_sc,price_sc,buyer,
                        commitment_id,notes,created_by)
                       VALUES(:s,:d,:pay,:q,:p,:b,:c,:n,:u)""",
                    {
                        "s": season_id,
                        "d": sale_date,
                        "pay": payment_date,
                        "q": quantity,
                        "p": price,
                        "b": buyer.strip(),
                        "c": commitment_map[objective],
                        "n": notes.strip(),
                        "u": user["id"],
                    },
                )
                log_action(
                    user["id"],
                    "criou",
                    "venda",
                    sale_id,
                    f"{quantity} sc a {price}",
                )
                st.session_state.current_page = "💰 Vendas"
                st.success("Venda salva com sucesso.")
                return True
            except Exception as error:
                st.error("Não foi possível salvar a venda.")
                st.caption("Confira os dados e tente novamente.")
                return False

        if CAN_EDIT and not show_sale_history:
            st.markdown("### Nova venda")
            st.caption(
                "Preencha os dados, revise o resumo e só depois confirme o salvamento."
            )

            sale_mode = st.radio(
                "Forma de lançamento",
                ["⌨️ Digitar venda", "🎙️ Venda por voz"],
                horizontal=True,
                key="sale_mode_v22",
            )

            if sale_mode == "⌨️ Digitar venda":
                labels = list(season_map)
                selected_label = st.selectbox(
                    "Safra",
                    labels,
                    key="sale_season_v22",
                )
                selected_id = season_map[selected_label]
                selected_row = q(
                    "SELECT * FROM seasons WHERE id=:id",
                    {"id": selected_id},
                )[0]
                selected_summary = season_summary(selected_row)
                quote_reference = latest_quote_for_crop(selected_row["crop"])
                suggested_price = (
                    float(quote_reference["price_sc"])
                    if quote_reference else 0.0
                )

                st.info(
                    f"Saldo livre: {num(selected_summary['balance'], 0)} sc"
                    + (
                        f" · Referência regional: {money(suggested_price)}/sc "
                        f"({quote_reference.get('source') or 'fonte não informada'})"
                        if quote_reference else ""
                    )
                )

                with st.form("guided_sale_v22", clear_on_submit=False):
                    s1, s2 = st.columns(2)
                    quantity = s1.number_input(
                        "Quantidade (sc)",
                        min_value=0.0,
                        step=10.0,
                        key="sale_quantity_v22",
                    )
                    price = s2.number_input(
                        "Preço por saca (R$)",
                        min_value=0.0,
                        value=suggested_price,
                        step=0.50,
                        key="sale_price_v22",
                    )
                    buyer = st.text_input(
                        "Comprador/cooperativa",
                        key="sale_buyer_v22",
                    )
                    objective = st.selectbox(
                        "Esta venda será vinculada a",
                        list(commitment_map),
                        key="sale_objective_v22",
                    )
                    sale_date = st.date_input(
                        "Data da venda",
                        value=date.today(),
                        format="DD/MM/YYYY",
                        key="sale_date_v22",
                    )
                    payment_date = st.date_input(
                        "Data prevista do pagamento",
                        value=sale_date,
                        format="DD/MM/YYYY",
                        key="sale_payment_date_v22",
                    )
                    notes = st.text_area(
                        "Observação (opcional)",
                        key="sale_notes_v22",
                    )
                    review_sale = st.form_submit_button(
                        "Revisar venda",
                        use_container_width=True,
                        type="primary",
                    )

                if review_sale:
                    if quantity <= 0:
                        st.error("Informe a quantidade.")
                    elif price <= 0:
                        st.error("Informe o preço por saca.")
                    elif quantity > selected_summary["balance"]:
                        st.error(
                            f"A quantidade supera o saldo livre de "
                            f"{num(selected_summary['balance'], 0)} sc."
                        )
                    else:
                        st.session_state.sale_review_v22 = {
                            "season_label": selected_label,
                            "quantity": float(quantity),
                            "price": float(price),
                            "buyer": buyer.strip(),
                            "objective": objective,
                            "sale_date": sale_date,
                            "payment_date": payment_date,
                            "notes": notes.strip(),
                            "balance_before": float(selected_summary["balance"]),
                            "crop": selected_row["crop"],
                        }

            else:
                with st.form("voice_sale_interpret_v22"):
                    spoken_sale = st.text_area(
                        "Dite ou escreva a venda",
                        placeholder=(
                            "Vendi 500 sacas de soja a 122 reais "
                            "para Cooperativa Alfa hoje"
                        ),
                        height=100,
                    )
                    interpret_sale = st.form_submit_button(
                        "Interpretar",
                        use_container_width=True,
                    )

                if interpret_sale:
                    if not spoken_sale.strip():
                        st.error("Dite ou escreva os dados da venda.")
                    else:
                        st.session_state.voice_sale_draft_v22 = parse_spoken_sale(
                            spoken_sale, seasons
                        )

                voice_sale_draft = st.session_state.get("voice_sale_draft_v22")
                if voice_sale_draft:
                    labels = list(season_map)
                    default_label = (
                        voice_sale_draft.get("season_label")
                        if voice_sale_draft.get("season_label") in labels
                        else labels[0]
                    )
                    with st.form("voice_sale_review_form_v22", clear_on_submit=False):
                        v_season = st.selectbox(
                            "Safra",
                            labels,
                            index=labels.index(default_label),
                        )
                        vs1, vs2 = st.columns(2)
                        v_quantity = vs1.number_input(
                            "Quantidade (sc)",
                            min_value=0.0,
                            value=float(voice_sale_draft.get("quantity", 0.0)),
                        )
                        v_price = vs2.number_input(
                            "Preço por saca (R$)",
                            min_value=0.0,
                            value=float(voice_sale_draft.get("price", 0.0)),
                        )
                        v_buyer = st.text_input(
                            "Comprador/cooperativa",
                            value=voice_sale_draft.get("buyer", ""),
                        )
                        v_objective = st.selectbox(
                            "Esta venda será vinculada a",
                            list(commitment_map),
                        )
                        v_date = st.date_input(
                            "Data da venda",
                            value=voice_sale_draft.get("sale_date", date.today()),
                            format="DD/MM/YYYY",
                        )
                        v_payment_date = st.date_input(
                            "Data prevista do pagamento",
                            value=v_date,
                            format="DD/MM/YYYY",
                        )
                        v_notes = st.text_area(
                            "Observação",
                            value=voice_sale_draft.get("notes", ""),
                        )
                        review_voice_sale = st.form_submit_button(
                            "Revisar venda por voz",
                            use_container_width=True,
                        )

                    if review_voice_sale:
                        season_id = season_map[v_season]
                        season_row = q(
                            "SELECT * FROM seasons WHERE id=:id",
                            {"id": season_id},
                        )[0]
                        current_summary = season_summary(season_row)
                        if v_quantity <= 0 or v_price <= 0:
                            st.error("Confira quantidade e preço.")
                        elif v_quantity > current_summary["balance"]:
                            st.error("A venda supera o saldo livre da safra.")
                        else:
                            st.session_state.sale_review_v22 = {
                                "season_label": v_season,
                                "quantity": float(v_quantity),
                                "price": float(v_price),
                                "buyer": v_buyer.strip(),
                                "objective": v_objective,
                                "sale_date": v_date,
                                "payment_date": v_payment_date,
                                "notes": v_notes.strip(),
                                "balance_before": float(current_summary["balance"]),
                                "crop": season_row["crop"],
                            }
                            st.session_state.pop("voice_sale_draft_v22", None)
                            st.rerun()

            sale_review = st.session_state.get("sale_review_v22")
            if sale_review:
                d = sale_review
                sale_total = d["quantity"] * d["price"]
                balance_after = d["balance_before"] - d["quantity"]

                st.markdown("---")
                confirmation_card(
                    "✅ Confirmar venda",
                    [
                        ("Safra", d["season_label"]),
                        ("Produto", d["crop"]),
                        ("Quantidade", f"{num(d['quantity'], 0)} sc"),
                        ("Preço por saca", money(d["price"])),
                        ("Comprador", d["buyer"] or "Não informado"),
                        ("Data", d["sale_date"].strftime("%d/%m/%Y")),
                        ("Pagamento previsto", d["payment_date"].strftime("%d/%m/%Y")),
                        ("Vinculação", d["objective"]),
                        ("Saldo após a venda", f"{num(balance_after, 0)} sc"),
                    ],
                    "Valor total",
                    money(sale_total),
                    warnings=(
                        ["Comprador não informado. Confirme se deseja continuar."]
                        if not d["buyer"] else None
                    ),
                )

                sb1, sb2, sb3 = st.columns(3)
                confirm_sale = sb1.button(
                    "✅ Confirmar e salvar",
                    use_container_width=True,
                    type="primary",
                    key="confirm_sale_v22",
                )
                correct_sale = sb2.button(
                    "✏️ Corrigir",
                    use_container_width=True,
                    key="correct_sale_v22",
                )
                discard_sale = sb3.button(
                    "🗑️ Descartar",
                    use_container_width=True,
                    key="discard_sale_v22",
                )

                if correct_sale:
                    st.session_state.pop("sale_review_v22", None)
                    st.info("Corrija os campos acima e clique novamente em Revisar venda.")
                    st.rerun()

                if discard_sale:
                    st.session_state.pop("sale_review_v22", None)
                    for key in [
                        "sale_quantity_v22", "sale_price_v22",
                        "sale_buyer_v22", "sale_notes_v22",
                    ]:
                        st.session_state.pop(key, None)
                    st.info("Venda descartada. Nada foi salvo.")
                    st.rerun()

                if confirm_sale:
                    if save_sale_record(
                        d["season_label"],
                        d["quantity"],
                        d["price"],
                        d["buyer"],
                        d["objective"],
                        d["sale_date"],
                        d["payment_date"],
                        d["notes"],
                    ):
                        st.session_state.pop("sale_review_v22", None)
                        st.success("Venda confirmada e salva.")
                        st.rerun()

            st.markdown("---")
    sales = q(
        """SELECT sales.*,seasons.name AS season_name,seasons.crop
           FROM sales JOIN seasons ON seasons.id=sales.season_id
           ORDER BY sale_date DESC,sales.id DESC"""
    ) if show_sale_history else []
    if show_sale_history and seasons:
        selected_sale_season_id = season_map.get(sale_history_season)
        sales = [
            item for item in sales
            if (sale_history_season == "Todas" or item.get("season_id") == selected_sale_season_id)
            and (sale_history_year == "Todos" or str(item.get("sale_date") or "")[:4] == sale_history_year)
            and (sale_history_crop == "Todas" or item.get("crop") == sale_history_crop)
        ]
    if show_sale_history and not sales:
        st.caption("Nenhuma venda registrada.")
    for item in sales:
        with st.expander(
            f"{item['season_name']} · {num(item['quantity_sc'], 0)} sc · {br_date(item['sale_date'])}"
        ):
            st.write(f"**Preço:** {money(item['price_sc'])}/sc")
            st.write(f"**Comprador:** {item['buyer'] or 'Não informado'}")
            st.write(f"**Pagamento previsto:** {br_date(item.get('payment_date'), 'Não informado')}")
            st.write(f"**Observação:** {item.get('notes') or '—'}")
            a1, a2, a3 = st.columns(3)
            if a1.button("👁️ Visualizar", key=f"view_sale_{item['id']}"):
                st.info(
                    f"Total da venda: {money(float(item['quantity_sc']) * float(item['price_sc']))}."
                )
            if CAN_EDIT and a2.button("✏️ Editar", key=f"edit_sale_{item['id']}"):
                st.session_state[f"editing_sale_{item['id']}"] = True
            if CAN_EDIT and a3.button("🗑️ Excluir", key=f"delete_sale_{item['id']}"):
                st.session_state[f"confirm_delete_sale_{item['id']}"] = True

            if st.session_state.get(f"editing_sale_{item['id']}"):
                with st.form(f"sale_edit_form_{item['id']}"):
                    e1, e2 = st.columns(2)
                    edit_price = e1.number_input("Preço por saca (R$)", min_value=0.0, value=float(item['price_sc']))
                    edit_buyer = e2.text_input("Comprador", value=item.get("buyer") or "")
                    e3, e4 = st.columns(2)
                    edit_sale_date = e3.date_input("Data da venda", value=item['sale_date'], format="DD/MM/YYYY")
                    edit_payment_date = e4.date_input("Data prevista do pagamento", value=item.get("payment_date") or item['sale_date'], format="DD/MM/YYYY")
                    edit_notes = st.text_area("Observação", value=item.get("notes") or "")
                    save_sale_edit = st.form_submit_button("Salvar alterações")
                if save_sale_edit:
                    ex(
                        """UPDATE sales SET price_sc=:p,buyer=:b,sale_date=:d,
                           payment_date=:pd,notes=:n WHERE id=:id""",
                        {"p": edit_price, "b": edit_buyer.strip(), "d": edit_sale_date,
                         "pd": edit_payment_date, "n": edit_notes.strip(), "id": item["id"]},
                    )
                    log_action(user["id"], "editou", "venda", item["id"], item["season_name"])
                    st.session_state.pop(f"editing_sale_{item['id']}", None)
                    st.success("Venda atualizada.")
                    st.rerun()

            if st.session_state.get(f"confirm_delete_sale_{item['id']}"):
                st.warning("Excluir esta venda removerá sua proteção vinculada a compromissos.")
                d1, d2 = st.columns(2)
                if d1.button("Confirmar exclusão", key=f"confirm_sale_delete_{item['id']}"):
                    ex("DELETE FROM sales WHERE id=:id", {"id": item["id"]})
                    log_action(user["id"], "excluiu", "venda", item["id"], item["season_name"])
                    st.success("Venda excluída.")
                    st.rerun()
                if d2.button("Cancelar", key=f"cancel_sale_delete_{item['id']}"):
                    st.session_state.pop(f"confirm_delete_sale_{item['id']}", None)
                    st.rerun()


elif page == "🤖 AgroIA":
    st.subheader("Assistente AgroIA")
    st.caption(
        "Resumo inteligente baseado nos dados cadastrados no AGRIZA."
    )

    open_commitments = q(
        """SELECT * FROM commitments
           WHERE COALESCE(status,'aberto')='aberto'
           ORDER BY due_date"""
    )
    active_seasons = q(
        "SELECT * FROM seasons WHERE active=TRUE ORDER BY id DESC"
    )
    recent_sales = q(
        """SELECT s.*,se.crop,se.name AS season_name
           FROM sales s
           LEFT JOIN seasons se ON se.id=s.season_id
           ORDER BY s.sale_date DESC,s.id DESC
           LIMIT 10"""
    )

    total_open = 0.0
    by_crop = {}
    for commitment in open_commitments:
        status = commitment_status(commitment["id"])
        remaining = float(status["remaining"])
        total_open += remaining
        crop = commitment.get("payment_crop") or "Caixa"
        by_crop[crop] = by_crop.get(crop, 0.0) + remaining

    a1, a2, a3 = st.columns(3)
    a1.metric("Compromissos em aberto", money(total_open))
    a2.metric("Safras ativas", len(active_seasons))
    a3.metric("Vendas recentes", len(recent_sales))

    st.markdown("### Alertas financeiros")
    if not open_commitments:
        st.success("Não há compromissos em aberto.")
    else:
        today = date.today()
        urgent = []
        for commitment in open_commitments:
            due = commitment.get("due_date")
            if isinstance(due, str):
                due = date.fromisoformat(due)
            days = (due - today).days if due else 99999
            status = commitment_status(commitment["id"])
            if status["remaining"] > 0 and days <= 90:
                urgent.append((days, commitment, status))
        if urgent:
            for days, commitment, status in urgent[:8]:
                if days < 0:
                    label = f"vencido há {abs(days)} dias"
                elif days == 0:
                    label = "vence hoje"
                else:
                    label = f"vence em {days} dias"
                st.warning(
                    f"{commitment['description']}: "
                    f"{money(status['remaining'])} restantes, {label}. "
                    f"Fonte prevista: {commitment.get('payment_crop') or 'Caixa'}."
                )
        else:
            st.info("Nenhum vencimento em aberto nos próximos 90 dias.")

    st.markdown("### Necessidade por cultura")
    if by_crop:
        crop_rows = []
        for crop, value in sorted(by_crop.items(), key=lambda item: item[1], reverse=True):
            quote = latest_quote_for_crop(crop) if crop in PAYMENT_OPTIONS else None
            price_per_sc = float(quote["price_sc"]) if quote else 0.0
            crop_rows.append(
                {
                    "Cultura/Fonte": crop,
                    "Valor necessário": value,
                    "Cotação usada": money(price_per_sc) + "/sc" if price_per_sc else "Informe a cotação",
                    "Sacas necessárias": (
                        f"{num(value / price_per_sc, 0)} sc" if price_per_sc else "—"
                    ),
                }
            )
        crop_df = pd.DataFrame(
            crop_rows
        )
        st.dataframe(
            crop_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Valor necessário": st.column_config.NumberColumn(
                    "Valor necessário",
                    format="R$ %.2f",
                ),
                "Cotação usada": st.column_config.TextColumn("Cotação usada"),
                "Sacas necessárias": st.column_config.TextColumn("Sacas necessárias"),
            },
        )
        st.caption(
            "As sacas são estimadas pela cotação mais recente cadastrada para cada cultura."
        )

    st.markdown("### Pergunta rápida")
    question = st.text_input(
        "Pergunte sobre seus dados",
        placeholder="Ex.: quanto preciso de soja para pagar os compromissos?",
    )
    if question:
        normalized = question.lower()
        answered = False
        for crop, value in by_crop.items():
            if crop.lower() in normalized:
                st.info(
                    f"Os compromissos vinculados a {crop} somam "
                    f"{money(value)} em saldo aberto."
                )
                answered = True
        if "total" in normalized or "compromiss" in normalized:
            st.info(
                f"O saldo total dos compromissos em aberto é {money(total_open)}."
            )
            answered = True
        if "safra" in normalized:
            st.info(f"Existem {len(active_seasons)} safras ativas.")
            answered = True
        if not answered:
            st.info(
                "Posso responder sobre compromissos, culturas de pagamento, "
                "safras ativas e vencimentos cadastrados."
            )

elif page == "📈 Mercado regional":
    st.subheader("Cotações de preços · Mercado regional")
    st.caption(
        "Compare referências de Soja, Milho, Trigo e Canola por praça e fonte. "
        "Use a cotação que melhor representa sua negociação."
    )

    if st.button("🔄 Consultar Grupo Uggeri", use_container_width=True, type="primary"):
        with st.spinner("Buscando cotações regionais..."):
            result = update_regional_quotes(user["id"])
        if result["updated"]:
            st.success(f"{len(result['updated'])} cotações atualizadas.")
        for error in result["errors"]:
            st.warning(error)
        st.rerun()

    st.info(
        "Consulta automática disponível: Grupo Uggeri. Você também pode registrar "
        "preços de cooperativas, tradings, compradores locais e indicadores de mercado."
    )

    latest = q(
        """SELECT q1.* FROM quotes q1
           JOIN (
             SELECT crop,MAX(quoted_at) AS max_date
             FROM quotes GROUP BY crop
           ) q2 ON q1.crop=q2.crop AND q1.quoted_at=q2.max_date
           ORDER BY q1.crop"""
    )
    latest_map = {item["crop"]: item for item in latest}
    with st.container(key="quote_summary"):
        cols = st.columns(4)
        for index, crop_name in enumerate(["Soja", "Milho", "Trigo", "Canola"]):
            item = latest_map.get(crop_name)
            if item:
                cols[index].metric(crop_name, money(item["price_sc"]) + "/sc")
                cols[index].caption(
                    f"{item.get('source') or 'Fonte não informada'} · {br_date(item.get('quoted_at'))}"
                )
            else:
                cols[index].metric(crop_name, "Sem cotação")

    if CAN_EDIT:
        with st.expander("✏️ Informar ou corrigir preço", expanded=False):
            with st.form("regional_quote_manual", clear_on_submit=True):
                crop = st.selectbox("Produto", ["Soja", "Milho", "Trigo", "Canola"])
                price = st.number_input("Preço (R$/sc)", min_value=0.0, step=0.50)
                source = st.selectbox(
                    "Fonte",
                    [
                        "Grupo Uggeri",
                        "Agrofel",
                        "Copermil",
                        "Cooperativa local",
                        "Trading / exportadora",
                        "Comprador local",
                        "Indicador CEPEA/ESALQ",
                        "Outro",
                    ],
                )
                region = st.text_input("Praça/região", value="Santo Ângelo/RS")
                save_quote = st.form_submit_button(
                    "Salvar preço manual", use_container_width=True
                )

            if save_quote:
                if price <= 0:
                    st.error("Informe um preço maior que zero.")
                else:
                    quote_id = insert_id(
                        """INSERT INTO quotes
                           (crop,price_sc,source,quoted_at,created_by,region,quote_type)
                           VALUES(:c,:p,:s,CURRENT_TIMESTAMP,:u,:r,'manual')""",
                        {
                            "c": crop,
                            "p": price,
                            "s": source,
                            "u": user["id"],
                            "r": region.strip(),
                        },
                    )
                    log_action(
                        user["id"], "criou", "cotação",
                        quote_id, f"{crop} {price} {source}"
                    )
                    st.success("Preço manual salvo.")
                    st.rerun()

    history = q(
        """SELECT id,crop,price_sc,source,region,quote_type,quoted_at
           FROM quotes ORDER BY quoted_at DESC,id DESC LIMIT 40"""
    )
    if history:
        st.markdown("### Histórico recente")
        history_frame = pd.DataFrame(history)
        history_frame["quoted_at"] = history_frame["quoted_at"].map(br_date)
        history_frame = history_frame.drop(columns=["id"])
        history_frame = history_frame.rename(
            columns={
                "crop": "Produto",
                "price_sc": "Preço (R$/sc)",
                "source": "Fonte",
                "region": "Praça/região",
                "quote_type": "Tipo",
                "quoted_at": "Data",
            }
        )
        st.dataframe(history_frame, use_container_width=True, hide_index=True)
        for quote in history:
            with st.expander(f"{quote['crop']} · {money(quote['price_sc'])}/sc · {br_date(quote['quoted_at'])}"):
                st.write(f"**Fonte:** {quote.get('source') or 'Não informada'}")
                st.write(f"**Praça/região:** {quote.get('region') or 'Não informada'}")
                a1, a2, a3 = st.columns(3)
                if a1.button("👁️ Visualizar", key=f"view_quote_{quote['id']}"):
                    st.info("Esta cotação é usada como referência para recomendações e vendas da cultura.")
                if CAN_EDIT and a2.button("✏️ Editar", key=f"open_edit_quote_{quote['id']}"):
                    st.session_state[f"edit_quote_{quote['id']}"] = True
                if CAN_EDIT and a3.button("🗑️ Excluir", key=f"delete_quote_{quote['id']}"):
                    st.session_state[f"confirm_delete_quote_{quote['id']}"] = True

                if st.session_state.get(f"edit_quote_{quote['id']}"):
                    with st.form(f"quote_edit_form_{quote['id']}"):
                        eq1, eq2 = st.columns(2)
                        edit_quote_crop = eq1.selectbox("Produto", ["Soja", "Milho", "Trigo", "Canola"], index=["Soja", "Milho", "Trigo", "Canola"].index(quote["crop"]) if quote["crop"] in ["Soja", "Milho", "Trigo", "Canola"] else 0)
                        edit_quote_price = eq2.number_input("Preço (R$/sc)", min_value=0.0, value=float(quote["price_sc"]))
                        edit_quote_source = st.text_input("Fonte", value=quote.get("source") or "")
                        edit_quote_region = st.text_input("Praça/região", value=quote.get("region") or "")
                        save_quote_edit = st.form_submit_button("Salvar alterações")
                    if save_quote_edit:
                        ex("""UPDATE quotes SET crop=:c,price_sc=:p,source=:s,region=:r WHERE id=:id""",
                           {"c": edit_quote_crop, "p": edit_quote_price, "s": edit_quote_source.strip(), "r": edit_quote_region.strip(), "id": quote["id"]})
                        log_action(user["id"], "editou", "cotação", quote["id"], edit_quote_crop)
                        st.session_state.pop(f"edit_quote_{quote['id']}", None)
                        st.success("Cotação atualizada.")
                        st.rerun()

                if st.session_state.get(f"confirm_delete_quote_{quote['id']}"):
                    st.warning("A cotação será removida do histórico e deixará de ser usada como referência.")
                    d1, d2 = st.columns(2)
                    if d1.button("Confirmar exclusão", key=f"confirm_quote_delete_{quote['id']}"):
                        ex("DELETE FROM quotes WHERE id=:id", {"id": quote["id"]})
                        log_action(user["id"], "excluiu", "cotação", quote["id"], quote["crop"])
                        st.success("Cotação excluída.")
                        st.rerun()
                    if d2.button("Cancelar", key=f"cancel_quote_delete_{quote['id']}"):
                        st.session_state.pop(f"confirm_delete_quote_{quote['id']}", None)
                        st.rerun()


elif page == "⚙️ Cadastro":
    st.subheader("Cadastro")
    st.caption("Cadastre as informações-base que serão reutilizadas nos lançamentos.")
    if not CAN_EDIT:
        st.info("Seu perfil permite apenas consulta dos cadastros.")

    company_tab, product_tab, unit_tab = st.tabs(["Empresa", "Produto", "Unidade"])

    with company_tab:
        if CAN_EDIT:
            with st.form("new_company", clear_on_submit=True):
                c1, c2 = st.columns(2)
                company_name = c1.text_input("Nome da empresa")
                company_document = c2.text_input("CNPJ/CPF (opcional)")
                c3, c4 = st.columns(2)
                company_city = c3.text_input("Cidade")
                company_state = c4.text_input("UF", max_chars=2).upper()
                save_company = st.form_submit_button("Salvar empresa", use_container_width=True)
            if save_company:
                if not company_name.strip():
                    st.error("Informe o nome da empresa.")
                else:
                    try:
                        company_id = insert_id(
                            """INSERT INTO companies(name,document,city,state,created_by)
                               VALUES(:n,:d,:c,:s,:u)""",
                            {
                                "n": company_name.strip(),
                                "d": company_document.strip(),
                                "c": company_city.strip(),
                                "s": company_state.strip(),
                                "u": user["id"],
                            },
                        )
                        log_action(user["id"], "criou", "empresa", company_id, company_name.strip())
                        st.success("Empresa cadastrada.")
                        st.rerun()
                    except Exception:
                        st.error("Não foi possível cadastrar. Verifique se a empresa já existe.")
        companies = q("SELECT name,document,city,state FROM companies WHERE active=TRUE ORDER BY name")
        if companies:
            st.dataframe(pd.DataFrame(companies), use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhuma empresa cadastrada.")

    with product_tab:
        units = q("SELECT id,code,description FROM units WHERE active=TRUE ORDER BY code")
        unit_map = {f"{unit['code']} · {unit.get('description') or unit['code']}": unit["id"] for unit in units}
        if CAN_EDIT:
            with st.form("new_product", clear_on_submit=True):
                product_name = st.text_input("Nome do produto")
                product_unit = st.selectbox("Unidade padrão", list(unit_map)) if unit_map else None
                save_product = st.form_submit_button("Salvar produto", use_container_width=True)
            if save_product:
                if not product_name.strip() or not product_unit:
                    st.error("Informe o produto e sua unidade padrão.")
                else:
                    try:
                        product_id = insert_id(
                            """INSERT INTO products(name,unit_id,created_by)
                               VALUES(:n,:u,:by)""",
                            {"n": product_name.strip(), "u": unit_map[product_unit], "by": user["id"]},
                        )
                        log_action(user["id"], "criou", "produto", product_id, product_name.strip())
                        st.success("Produto cadastrado.")
                        st.rerun()
                    except Exception:
                        st.error("Não foi possível cadastrar. Verifique se o produto já existe.")
        products = q(
            """SELECT products.name,units.code AS unit
               FROM products LEFT JOIN units ON units.id=products.unit_id
               WHERE products.active=TRUE ORDER BY products.name"""
        )
        if products:
            st.dataframe(pd.DataFrame(products), use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhum produto cadastrado.")

    with unit_tab:
        if CAN_EDIT:
            with st.form("new_unit", clear_on_submit=True):
                u1, u2 = st.columns(2)
                unit_code = u1.text_input("Sigla", max_chars=12).upper()
                unit_description = u2.text_input("Descrição")
                save_unit = st.form_submit_button("Salvar unidade", use_container_width=True)
            if save_unit:
                if not unit_code.strip():
                    st.error("Informe a sigla da unidade.")
                else:
                    try:
                        unit_id = insert_id(
                            """INSERT INTO units(code,description,created_by)
                               VALUES(:c,:d,:u)""",
                            {"c": unit_code.strip(), "d": unit_description.strip(), "u": user["id"]},
                        )
                        log_action(user["id"], "criou", "unidade", unit_id, unit_code.strip())
                        st.success("Unidade cadastrada.")
                        st.rerun()
                    except Exception:
                        st.error("Não foi possível cadastrar. Verifique se a sigla já existe.")
        units_display = q("SELECT code,description FROM units WHERE active=TRUE ORDER BY code")
        st.dataframe(pd.DataFrame(units_display), use_container_width=True, hide_index=True)

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


elif page == "📦 BACKUP":
    st.subheader("Backup e conferência")
    st.caption(
        "Baixe este arquivo sempre que quiser guardar uma cópia dos seus dados. "
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
            "version": "agriza-enterprise-3.0",
        }
        archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    st.download_button(
        "Baixar backup",
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
