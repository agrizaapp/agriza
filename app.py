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
from core.utils import money, num, br_date, add_months
from services.analytics import (
    season_summary,
    commitment_statuses,
    agroia_recommendation,
)
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
from services.market_data import build_market_view, price_series
from services.market_data.indicators import rolling_average
from services.market_data.sources import available_sources, planned_sources, collect
from services.market_data.importer import parse_price_csv, importar_linhas
from services.market_data.fundamentals import (
    COMMODITY_POR_CULTURA,
    coletar as coletar_usda,
    tem_chave as usda_tem_chave,
)
from services.market_data.fundamentals_store import leitura_de_oferta, serie_anual
from services.market_data.fas import (
    COMMODITY_POR_CULTURA as COMMODITY_FAS,
    PAISES_DE_INTERESSE,
    ROTAS_DE_REFERENCIA as ROTAS_FAS,
    buscar_commodities as buscar_commodities_fas,
    coletar as coletar_fas,
    diagnosticar as diagnosticar_fas,
    diagnosticar_dados as diagnosticar_dados_fas,
    tem_chave as fas_tem_chave,
)
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


def currency_input(
    target,
    label,
    *,
    value=0.0,
    min_value=0.0,
    max_value=None,
    step=100.0,
    key=None,
    help=None,
    disabled=False,
):
    """Campo numérico seguro com uma leitura auxiliar no padrão monetário brasileiro."""
    options = {
        "min_value": float(min_value),
        "value": float(value),
        "step": float(step),
        "format": "%.2f",
        "disabled": disabled,
    }
    if max_value is not None:
        options["max_value"] = float(max_value)
    if key is not None:
        options["key"] = key
    if help is not None:
        options["help"] = help

    result = target.number_input(label, **options)
    target.caption(f"Leitura em reais: **{money(result)}**")
    return float(result)




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

with st.container(key="top_identity_bar"):
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
    menu_pages.extend(["👥 Usuários", "📦 BACKUP"])
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
    st.caption("Acompanhe a safra ativa, os compromissos e os próximos vencimentos em um só lugar.")
    st.markdown("### Resumo da gestão")
    seasons = q("SELECT * FROM seasons WHERE active=TRUE ORDER BY id DESC")

    if not IS_POSTGRES:
        st.error(
            "Atenção: o sistema está usando banco local temporário. "
            "Antes de inserir dados reais, configure DATABASE_URL no Render."
        )

    if not seasons:
        st.info("Comece cadastrando a safra atual. Ela conecta custos, compras, vendas e indicadores.")
        if CAN_EDIT and st.button("🌾 Cadastrar primeira safra", use_container_width=True, type="primary"):
            st.session_state.current_page = "🌾 Safras"
            st.rerun()
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
        home_statuses = commitment_statuses()
        st.markdown("### Proteção dos compromissos")
        if not commitments:
            st.caption("Nenhum compromisso aberto vinculado a esta safra.")
        else:
            total = 0
            covered = 0
            for item in commitments:
                status = home_statuses[item["id"]]
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
    st.caption("Cadastre a área, o custo e a produtividade para acompanhar resultado e necessidade de venda.")

    if CAN_EDIT:
        with st.expander("➕ Nova safra", expanded=not bool(q("SELECT id FROM seasons LIMIT 1"))):
            with st.form("new_season", clear_on_submit=True):
                name = st.text_input("Nome", placeholder="Ex.: Soja 2026/27")
                crop = st.selectbox("Cultura", ["Soja", "Milho", "Trigo", "Canola"])
                c1, c2 = st.columns(2)
                area = c1.number_input("Área (ha)", min_value=0.0)
                cost = currency_input(
                    c2,
                    "Custo estimado por hectare (R$)",
                    step=50.0,
                    key="season_cost_ha",
                )
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
                        new_cost = currency_input(
                            st,
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
    account_statuses = commitment_statuses()
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
                    st.session_state[f"confirm_full_payment_{account['id']}"] = True
                    st.rerun()
                if st.session_state.get(f"confirm_full_payment_{account['id']}"):
                    st.warning(
                        f"Confirmar a baixa integral de {money(payment['remaining'])} "
                        f"em {account['description']}?"
                    )
                    confirm_col, cancel_col = st.columns(2)
                    confirm_payment = confirm_col.button(
                        "Confirmar pagamento",
                        key=f"confirm_full_payment_action_{account['id']}",
                        type="primary",
                        use_container_width=True,
                    )
                    cancel_payment = cancel_col.button(
                        "Cancelar",
                        key=f"cancel_full_payment_action_{account['id']}",
                        use_container_width=True,
                    )
                    if cancel_payment:
                        st.session_state.pop(f"confirm_full_payment_{account['id']}", None)
                        st.rerun()
                    if confirm_payment:
                        st.session_state.pop(f"confirm_full_payment_{account['id']}", None)
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
    st.caption("Registre uma compra nova ou consulte o histórico com seus pagamentos e vencimentos.")
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
    purchase_statuses = commitment_statuses()
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

            # A safra precisa vir junto: sem ela o compromisso não entra na proteção
            # da safra nem no cálculo de saldo descoberto do AgroIA.
            selected_season_label = st.selectbox(
                "Safra",
                list(season_map),
                index=1 if len(season_map) > 1 else 0,
                key="insumo_season_v31",
                help="Vincula a compra à safra para entrar na proteção e na recomendação do AgroIA.",
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
            unit_price = currency_input(
                st,
                "Valor unitário (R$)",
                step=0.01,
                key="insumo_unit_price_v31",
            )
            total_value = round(float(quantity) * float(unit_price), 2)
            st.metric("Valor total calculado", money(total_value))

            if st.button("🔎 Revisar compra", key="review_insumo_purchase_v31", use_container_width=True, type="primary"):
                if quantity <= 0 or unit_price <= 0:
                    st.error("Informe uma quantidade e um valor unitário maiores que zero.")
                else:
                    st.session_state.insumo_purchase_review_v31 = {
                        "company": company_map[selected_company_name],
                        "product": selected_product,
                        "unit": unit_map[selected_unit_label],
                        "season_label": selected_season_label,
                        "season_id": season_map[selected_season_label],
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
                    ("Safra", draft.get("season_label") or "Nenhuma"),
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
                    "insumo_season_v31", "insumo_purchase_date_v31", "insumo_payment_date_v31",
                    "insumo_quantity_v31", "insumo_unit_v31", "insumo_unit_price_v31",
                ]:
                    st.session_state.pop(key, None)
                st.session_state.current_page = "📝 Lançar / Visualizar"
                st.rerun()
            if save_insumo:
                duplicate = q(
                    """SELECT id FROM commitments
                       WHERE COALESCE(status,'aberto') != 'cancelado'
                         AND company_id=:company_id AND product_id=:product_id
                         AND COALESCE(season_id,-1)=COALESCE(:season_id,-1)
                         AND purchase_date=:purchase_date AND due_date=:payment_date
                         AND quantity=:quantity AND unit_price=:unit_price
                       LIMIT 1""",
                    {
                        "company_id": draft["company"]["id"],
                        "product_id": draft["product"]["id"],
                        "season_id": draft.get("season_id"),
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
                           (season_id,company_id,product_id,unit_id,quantity,unit_price,category,description,
                            supplier,total_value,purchase_date,due_date,payment_crop,notes,status,created_by)
                           VALUES(:season_id,:company_id,:product_id,:unit_id,:quantity,:unit_price,'Insumos',:description,
                                  :supplier,:total_value,:purchase_date,:payment_date,'Caixa',:notes,'aberto',:created_by)""",
                        {
                            "season_id": draft.get("season_id"),
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
                    if purchase_statuses[installment["id"]]["remaining"] <= 0.01
                )
                st.caption(
                    f"{paid_installments} parcela(s) paga(s) · "
                    f"{len(installments) - paid_installments} parcela(s) a pagar"
                )
                for installment in installments:
                    installment_status = purchase_statuses[installment["id"]]
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
        status = purchase_statuses[item["id"]]
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
                        edit_value = currency_input(
                            e1,
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
                    amount = currency_input(
                        st,
                        "Registrar pagamento (R$)",
                        min_value=0.0,
                        max_value=max(float(status["remaining"]), 0.0),
                        step=100.0,
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

                if submit_payment and amount <= 0:
                    st.error("Informe um valor de pagamento maior que zero.")
                elif submit_payment and amount > float(status["remaining"]) + 0.01:
                    st.error(
                        f"O pagamento não pode ultrapassar o saldo de {money(status['remaining'])}."
                    )
                elif submit_payment:
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
    machine_statuses = commitment_statuses()
    st.subheader("Máquinas e financiamentos")
    st.caption("Registre a máquina e escolha pagamento à vista, parcelado ou financiado.")

    machine_screen = st.radio(
        "Área de máquinas",
        ["➕ Nova máquina", "📋 Máquinas cadastradas"],
        horizontal=True,
        key="machine_screen_v31",
    )
    if CAN_EDIT and machine_screen == "➕ Nova máquina":
        companies = q("SELECT id,name FROM companies WHERE active=TRUE ORDER BY name")
        if not companies:
            st.warning("Cadastre primeiro o fornecedor na área Cadastro → Empresa.")
            if st.button("⚙️ Abrir Cadastro", key="open_catalog_from_machine", use_container_width=True):
                st.session_state.current_page = "⚙️ Cadastro"
                st.rerun()
            st.stop()

        supplier_map = {company["name"]: company for company in companies}
        machine_seasons = q("SELECT id,name,crop FROM seasons WHERE active=TRUE ORDER BY id DESC")
        machine_season_map = {"Nenhuma": None}
        machine_season_map.update(
            {f"{item['name']} · {item['crop']}": item["id"] for item in machine_seasons}
        )
        review = st.session_state.get("machine_purchase_review_v31")
        if not review:
            st.markdown("### Nova máquina")
            model = st.text_input("Modelo da máquina", placeholder="Ex.: Plantadeira 13 linhas", key="machine_model_v31")
            supplier_name = st.selectbox("Fornecedor", list(supplier_map), key="machine_supplier_v31")
            # As parcelas da máquina também são compromissos da safra: sem o vínculo
            # elas ficam fora da proteção e da recomendação do AgroIA.
            machine_season_label = st.selectbox(
                "Safra que responde pelas parcelas",
                list(machine_season_map),
                index=1 if len(machine_season_map) > 1 else 0,
                key="machine_season_v31",
                help="Use 'Nenhuma' se as parcelas não devem pesar em nenhuma safra.",
            )
            machine_mode = st.radio(
                "Forma de pagamento",
                ["À vista", "Parcelada", "Financiada"],
                horizontal=True,
                key="machine_mode_v31",
            )
            rows = []
            purchase_date = date.today()
            financed_value = 0.0
            entry_value = 0.0
            financed_principal = 0.0
            interest_rate = 0.0
            finance_table = None
            interest_periodicity = None

            if machine_mode == "À vista":
                c1, c2, c3 = st.columns(3)
                purchase_date = c1.date_input("Data da compra", value=date.today(), format="DD/MM/YYYY", key="machine_cash_date_v31")
                payment_date = c2.date_input("Data do pagamento", value=purchase_date, format="DD/MM/YYYY", key="machine_cash_payment_date_v31")
                paid_value = currency_input(
                    c3,
                    "Valor pago (R$)",
                    step=1000.0,
                    key="machine_cash_value_v31",
                )
                rows = [{"number": 1, "due_date": payment_date, "value": float(paid_value)}]
                financed_value = float(paid_value)

            elif machine_mode == "Parcelada":
                c1, c2, c3 = st.columns(3)
                purchase_date = c1.date_input("Data da compra", value=date.today(), format="DD/MM/YYYY", key="machine_installment_date_v31")
                financed_value = currency_input(
                    c2,
                    "Valor da máquina (R$)",
                    step=1000.0,
                    key="machine_installment_total_v31",
                )
                installment_count = c3.number_input("Número de parcelas", min_value=1, max_value=60, value=2, step=1, key="machine_installment_count_v31")
                st.markdown("#### Parcelas")
                default_value = float(financed_value) / int(installment_count) if installment_count else 0.0
                for index in range(int(installment_count)):
                    p1, p2 = st.columns(2)
                    due_date_value = p1.date_input(
                        f"Vencimento da parcela {index + 1}",
                        value=add_months(purchase_date, index + 1),
                        format="DD/MM/YYYY",
                        key=f"machine_installment_due_v31_{index}",
                    )
                    installment_value = currency_input(
                        p2,
                        f"Valor da parcela {index + 1} (R$)",
                        min_value=0.0,
                        value=default_value,
                        step=1000.0,
                        key=f"machine_installment_value_v31_{index}",
                    )
                    rows.append({"number": index + 1, "due_date": due_date_value, "value": float(installment_value)})

            else:
                st.markdown("#### Dados do financiamento")
                c1, c2, c3 = st.columns(3)
                purchase_date = c1.date_input("Data da compra", value=date.today(), format="DD/MM/YYYY", key="machine_financed_date_v31")
                financed_value = currency_input(
                    c2,
                    "Valor do bem (R$)",
                    step=1000.0,
                    key="machine_financed_value_v31",
                )
                entry_value = currency_input(
                    c3,
                    "Entrada (R$)",
                    step=1000.0,
                    key="machine_financed_entry_v31",
                )

                c4, c5, c6 = st.columns(3)
                years = c4.number_input("Anos para pagar", min_value=1, max_value=30, value=3, step=1, key="machine_financed_years_v31")
                interest_rate = c5.number_input("Taxa de juros (%)", min_value=0.0, value=10.0, step=0.1, key="machine_financed_rate_v31")
                interest_periodicity = c6.selectbox(
                    "Periodicidade dos juros",
                    ["Mensal", "Trimestral", "Semestral", "Anual"],
                    index=3,
                    key="machine_financed_periodicity_v31",
                )

                periodicity_config = {
                    "Mensal": (1, 12),
                    "Trimestral": (3, 4),
                    "Semestral": (6, 2),
                    "Anual": (12, 1),
                }
                interval_months, periods_per_year = periodicity_config[interest_periodicity]
                count = int(years) * periods_per_year
                c7, c8 = st.columns(2)
                finance_table = c7.selectbox("Tabela", ["SAC", "Price"], key="machine_financed_table_v31")
                first_due_date = c8.date_input(
                    "Primeiro vencimento",
                    value=add_months(purchase_date, interval_months),
                    format="DD/MM/YYYY",
                    key="machine_financed_first_due_v31",
                    help="As próximas datas serão calculadas automaticamente a partir deste vencimento.",
                )

                financed_principal = max(float(financed_value) - float(entry_value), 0)
                balance = financed_principal
                rate = float(interest_rate) / 100
                fixed_payment = (
                    balance * rate / (1 - (1 + rate) ** -count)
                    if finance_table == "Price" and rate > 0 else balance / count
                ) if count else 0.0
                st.markdown("#### Parcelas calculadas")
                st.caption(
                    "Valores e datas são automáticos. Para alterar o calendário, ajuste somente o primeiro vencimento acima. "
                    "No SAC, a amortização é constante; na Price, as parcelas são iguais."
                )
                schedule_preview = []
                total_amortization = 0.0
                total_interest = 0.0
                total_installments = 0.0
                for index in range(count):
                    opening_balance = balance
                    calculated_due_date = add_months(first_due_date, interval_months * index)
                    interest = opening_balance * rate
                    if finance_table == "SAC":
                        amortization = min(financed_principal / count, opening_balance)
                        calculated_value = amortization + interest
                    else:
                        calculated_value = fixed_payment
                        amortization = calculated_value - interest
                        amortization = min(amortization, opening_balance)
                    balance = max(opening_balance - amortization, 0)
                    total_amortization += amortization
                    total_interest += interest
                    total_installments += calculated_value
                    rows.append({"number": index + 1, "due_date": calculated_due_date, "value": round(calculated_value, 2)})
                    schedule_preview.append(
                        {
                            "Parcela": index + 1,
                            "Vencimento": br_date(calculated_due_date),
                            "Amortização": money(amortization),
                            "Juros": money(interest),
                            "Valor": money(calculated_value),
                            "Saldo devedor": money(balance),
                        }
                    )
                schedule_preview.append(
                    {
                        "Parcela": "Total",
                        "Vencimento": "—",
                        "Amortização": money(total_amortization),
                        "Juros": money(total_interest),
                        "Valor": money(total_installments),
                        "Saldo devedor": money(balance),
                    }
                )
                st.dataframe(pd.DataFrame(schedule_preview), use_container_width=True, hide_index=True)
                financing_total = sum(row["value"] for row in rows)
                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Valor do bem", money(financed_value))
                s2.metric("Entrada", money(entry_value))
                s3.metric("Saldo financiado", money(financed_principal))
                s4.metric("Total de juros", money(max(financing_total - financed_principal, 0)))
                st.metric("Total da operação com juros", money(float(entry_value) + financing_total))

            total_due = sum(row["value"] for row in rows) + float(entry_value)
            action_review, action_cancel = st.columns(2)
            review_operation = action_review.button("🔎 Conferir operação", key="review_machine_purchase_v31", use_container_width=True, type="primary")
            cancel_operation = action_cancel.button("↩️ Cancelar e voltar", key="cancel_machine_before_review_v31", use_container_width=True)
            if cancel_operation:
                st.session_state.current_page = "📝 Lançar / Visualizar"
                st.rerun()
            if review_operation:
                if not model.strip():
                    st.error("Informe o modelo da máquina.")
                elif machine_mode == "Financiada" and entry_value >= financed_value:
                    st.error("A entrada deve ser menor que o valor do bem.")
                elif financed_value <= 0 or not rows or any(row["value"] <= 0 for row in rows):
                    st.error("Confira o valor da máquina e todas as parcelas.")
                else:
                    st.session_state.machine_purchase_review_v31 = {
                        "model": model.strip(),
                        "supplier": supplier_map[supplier_name],
                        "season_label": machine_season_label,
                        "season_id": machine_season_map[machine_season_label],
                        "mode": machine_mode,
                        "purchase_date": purchase_date,
                        "financed_value": float(financed_value),
                        "entry_value": float(entry_value),
                        "financed_principal": float(financed_principal),
                        "interest_rate": float(interest_rate),
                        "finance_table": finance_table,
                        "interest_periodicity": interest_periodicity,
                        "total_due": float(total_due),
                        "rows": rows,
                    }
                    st.rerun()
        else:
            confirmation_card(
                "🚜 Conferência da operação",
                [
                    ("Modelo", review["model"]),
                    ("Fornecedor", review["supplier"]["name"]),
                    ("Safra das parcelas", review.get("season_label") or "Nenhuma"),
                    ("Forma de pagamento", review["mode"]),
                    ("Data da compra", br_date(review["purchase_date"])),
                    ("Valor do bem", money(review["financed_value"])),
                    ("Entrada", money(review.get("entry_value", 0))),
                    ("Saldo financiado", money(review.get("financed_principal", review["financed_value"]))),
                    ("Tabela", review["finance_table"] or "Não se aplica"),
                    ("Periodicidade", review.get("interest_periodicity") or "Não se aplica"),
                    ("Juros totais", money(max(review["total_due"] - review["financed_value"], 0))),
                ],
                "Total a pagar",
                money(review["total_due"]),
            )
            for row in review["rows"]:
                st.write(f"Parcela {row['number']} · {br_date(row['due_date'])} · {money(row['value'])}")
            r1, r2, r3 = st.columns(3)
            confirm_machine = r1.button("✅ Confirmar", key="confirm_machine_purchase_v31", use_container_width=True, type="primary")
            cancel_machine = r2.button("↩️ Cancelar e voltar", key="cancel_machine_purchase_v31", use_container_width=True)
            edit_machine = r3.button("✏️ Editar", key="edit_machine_purchase_v31", use_container_width=True)

            if edit_machine:
                st.session_state.pop("machine_purchase_review_v31", None)
                st.rerun()
            if cancel_machine:
                for key in ["machine_purchase_review_v31", "machine_model_v31", "machine_supplier_v31", "machine_season_v31", "machine_mode_v31"]:
                    st.session_state.pop(key, None)
                st.session_state.current_page = "📝 Lançar / Visualizar"
                st.rerun()
            if confirm_machine:
                duplicate = q(
                    """SELECT id FROM purchase_contracts
                       WHERE COALESCE(status,'aberto') != 'cancelado'
                         AND lower(trim(description))=lower(trim(:description))
                         AND lower(trim(supplier))=lower(trim(:supplier))
                         AND purchase_date=:purchase_date AND total_value=:total_value
                       LIMIT 1""",
                    {"description": review["model"], "supplier": review["supplier"]["name"], "purchase_date": review["purchase_date"], "total_value": review["total_due"]},
                )
                if duplicate:
                    st.warning("Esta operação de máquina já está registrada. Nada foi salvo.")
                else:
                    notes = (
                        f"{review['mode']}"
                        + (
                            f" · Entrada {money(review.get('entry_value', 0))}"
                            f" · {review['finance_table']} · juros {review['interest_rate']:.2f}%"
                            f" · {review.get('interest_periodicity') or ''}"
                            if review["finance_table"] else ""
                        )
                    )
                    contract_id = insert_id(
                        """INSERT INTO purchase_contracts(description,supplier,category,total_value,purchase_date,notes,status,created_by)
                           VALUES(:d,:s,'Máquinas',:v,:pd,:n,'aberto',:u)""",
                        {"d": review["model"], "s": review["supplier"]["name"], "v": review["total_due"], "pd": review["purchase_date"], "n": notes, "u": user["id"]},
                    )
                    machine_id = insert_id(
                        """INSERT INTO machinery(name,model,acquisition_date,acquisition_value,contract_id,status,notes,created_by)
                           VALUES(:n,:m,:d,:v,:c,'ativo',:o,:u)""",
                        {"n": review["model"], "m": review["model"], "d": review["purchase_date"], "v": review["financed_value"], "c": contract_id, "o": notes, "u": user["id"]},
                    )
                    if float(review.get("entry_value", 0)) > 0:
                        entry_commitment_id = insert_id(
                            """INSERT INTO commitments(contract_id,installment_no,season_id,category,description,supplier,total_value,purchase_date,due_date,payment_crop,notes,status,created_by)
                               VALUES(:contract_id,0,:season_id,'Máquinas',:description,:supplier,:total_value,:purchase_date,:purchase_date,'Caixa',:notes,'encerrado',:created_by)""",
                            {
                                "contract_id": contract_id,
                                "season_id": review.get("season_id"),
                                "description": f"{review['model']} · Entrada",
                                "supplier": review["supplier"]["name"],
                                "total_value": float(review["entry_value"]),
                                "purchase_date": review["purchase_date"],
                                "notes": notes,
                                "created_by": user["id"],
                            },
                        )
                        insert_id(
                            """INSERT INTO payments(commitment_id,payment_date,amount,notes,created_by)
                               VALUES(:commitment_id,:payment_date,:amount,'Entrada da compra',:created_by)""",
                            {
                                "commitment_id": entry_commitment_id,
                                "payment_date": review["purchase_date"],
                                "amount": float(review["entry_value"]),
                                "created_by": user["id"],
                            },
                        )
                    for row in review["rows"]:
                        status = "encerrado" if review["mode"] == "À vista" else "aberto"
                        commitment_id = insert_id(
                            """INSERT INTO commitments(contract_id,installment_no,season_id,category,description,supplier,total_value,purchase_date,due_date,payment_crop,notes,status,created_by)
                               VALUES(:contract_id,:installment_no,:season_id,'Máquinas',:description,:supplier,:total_value,:purchase_date,:due_date,'Caixa',:notes,:status,:created_by)""",
                            {"contract_id": contract_id, "installment_no": row["number"], "season_id": review.get("season_id"), "description": f"{review['model']} · Parcela {row['number']}", "supplier": review["supplier"]["name"], "total_value": row["value"], "purchase_date": review["purchase_date"], "due_date": row["due_date"], "notes": notes, "status": status, "created_by": user["id"]},
                        )
                        if review["mode"] == "À vista":
                            insert_id(
                                """INSERT INTO payments(commitment_id,payment_date,amount,notes,created_by)
                                   VALUES(:commitment_id,:payment_date,:amount,:notes,:created_by)""",
                                {"commitment_id": commitment_id, "payment_date": row["due_date"], "amount": row["value"], "notes": "Pagamento à vista", "created_by": user["id"]},
                            )
                    log_action(user["id"], "criou", "maquina", machine_id, review["model"])
                    st.session_state.pop("machine_purchase_review_v31", None)
                    st.success("Máquina e operação registradas com sucesso.")
                    st.rerun()
        st.stop()

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
                        if machine_statuses[installment["id"]]["remaining"] <= 0.01
                    )
                    st.caption(
                        f"{paid_installments} parcela(s) paga(s) · "
                        f"{len(installments) - paid_installments} parcela(s) a pagar"
                    )
                    for installment in installments:
                        status = machine_statuses[installment["id"]]
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
    st.caption("Registre uma venda nova ou consulte o histórico por safra, ano e cultura.")
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
        st.info("Cadastre uma safra antes de registrar vendas e controlar o saldo disponível.")
        if CAN_EDIT and st.button("🌾 Cadastrar safra agora", use_container_width=True, type="primary"):
            st.session_state.current_page = "🌾 Safras"
            st.rerun()
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
                    price = currency_input(
                        s2,
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
                        v_price = currency_input(
                            vs2,
                            "Preço por saca (R$)",
                            min_value=0.0,
                            value=float(voice_sale_draft.get("price", 0.0)),
                            step=0.50,
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
                    edit_price = currency_input(
                        e1,
                        "Preço por saca (R$)",
                        value=float(item["price_sc"]),
                        step=0.50,
                    )
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
    agroia_statuses = commitment_statuses()

    total_open = 0.0
    by_crop = {}
    for commitment in open_commitments:
        status = agroia_statuses[commitment["id"]]
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
            status = agroia_statuses[commitment["id"]]
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

    # ----- Inteligência de mercado -------------------------------------------
    st.markdown("### 📊 Inteligência de mercado")
    st.caption(
        "Como o preço de cada cultura se comporta ao longo do tempo: posição no "
        "histórico, médias móveis e tendência. Quanto mais cotações registradas, "
        "mais rica a leitura."
    )
    NIVEL_MERCADO = {
        "favoravel": ("positive", "🟢 Favorável"),
        "cautela": ("warning", "🟡 Cautela"),
        "desfavoravel": ("danger", "🔴 Desfavorável"),
        "sem_dados": ("warning", "⚪ Sem dados"),
    }
    analysis_crop = st.selectbox(
        "Cultura para analisar", ["Soja", "Milho", "Trigo", "Canola"],
        key="market_analysis_crop",
    )
    required_for_crop = None
    ref_season = q(
        """SELECT * FROM seasons
           WHERE active=TRUE AND lower(crop)=lower(:crop)
           ORDER BY id DESC LIMIT 1""",
        {"crop": analysis_crop},
    )
    if ref_season:
        required_for_crop = season_summary(ref_season[0])["required_price"]

    # A camada de mercado é acessória à página: se algo falhar nela, o restante
    # (cotações, cadastro manual, histórico) precisa continuar funcionando.
    try:
        view = build_market_view(analysis_crop, required_for_crop)
    except Exception:
        view = None
        st.warning(
            "Não foi possível calcular os indicadores de mercado agora. "
            "As cotações abaixo seguem disponíveis normalmente."
        )

    summary = view["summary"] if view else {"count": 0}
    signal = view["signal"] if view else None

    if not view:
        pass
    elif summary["count"] < 2:
        st.info(
            f"Ainda há poucos registros de {analysis_crop} "
            f"({summary['count']} cotação(ões)). Registre mais preços — "
            "manualmente ou por uma fonte — para liberar os indicadores."
        )
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Preço atual", money(summary["current"]) + "/sc")
        if summary["percentile"] is not None:
            m2.metric("Posição no histórico", f"{num(summary['percentile'], 0)}º pct")
        if summary["sma_short"] is not None:
            m3.metric("Média curta", money(summary["sma_short"]))
        seta = {"alta": "↑", "baixa": "↓", "estável": "→"}.get(summary["trend"], "—")
        m4.metric("Tendência", f"{seta} {summary['trend']}")

        # A curva é o que torna o percentil compreensível: ver o preço subindo
        # há meses decide mais do que o número isolado.
        serie = price_series(analysis_crop, days=180)
        if len(serie) >= 2:
            precos_serie = [item["price"] for item in serie]
            grafico = pd.DataFrame(
                {"Preço": precos_serie},
                index=pd.to_datetime([item["date"] for item in serie]),
            )
            # Só entra a média que tem dados: série curta não completa a janela
            # longa, e uma coluna vazia viraria legenda sem linha no gráfico.
            for rotulo, janela in (("Média curta", 7), ("Média longa", 30)):
                valores = rolling_average(precos_serie, janela)
                if any(v is not None for v in valores):
                    grafico[rotulo] = valores
            if required_for_crop:
                grafico["Preço necessário"] = required_for_crop
            st.line_chart(grafico, use_container_width=True)
            legenda = "Preço registrado e médias móveis dos últimos meses."
            if required_for_crop:
                legenda += (
                    " A linha reta é o preço que sua safra precisa para bater a margem."
                )
            st.caption(legenda)

        level_class, level_label = NIVEL_MERCADO.get(signal["level"], ("warning", ""))
        pct_line = ""
        if signal.get("suggested_sell_pct"):
            pct_line = (
                f"<br><b>Cenário sugerido:</b> avaliar proteção de ~"
                f"{signal['suggested_sell_pct']}% da produção."
            )
        st.markdown(
            f"""<div class="card {level_class}">
            <small>LEITURA DE MERCADO · {level_label}</small>
            <h3>{signal['headline']}</h3>
            <div>{signal['message']}{pct_line}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        with st.expander("Ver os fatores considerados"):
            for factor in signal["factors"]:
                st.write("•", factor)
            st.caption(
                f"Baseado em {summary['count']} cotações dos últimos meses. "
                "É apoio à decisão sobre venda de grão físico, não recomendação "
                "de operação financeira — a decisão é sua."
            )

    with st.expander("🌍 Fundamentos de oferta (USDA)"):
        st.caption(
            "O preço diz onde estamos; o fundamento ajuda a explicar por quê. "
            "Safra americana grande pressiona o preço mundial; safra pequena sustenta."
        )
        st.markdown(
            "**Escopo:** o USDA/NASS publica dados dos **Estados Unidos** — não do "
            "mundo inteiro. É um fundamento forte, porque os EUA são um dos maiores "
            "produtores de soja e milho, mas não é um balanço mundial."
        )

        if not usda_tem_chave():
            st.info(
                "A variável `USDA_API_KEY` não está configurada **neste ambiente**. "
                "Em produção ela vive nas variáveis do Render; localmente o painel "
                "fica indisponível, o que é esperado."
            )
        elif CAN_EDIT and st.button(
            "🔄 Atualizar dados do USDA", key="coletar_usda", use_container_width=True
        ):
            with st.spinner("Consultando o USDA/NASS..."):
                resultado = coletar_usda()
            if resultado["gravados"]:
                st.success(f"{resultado['gravados']} observações atualizadas.")
                log_action(user["id"], "coletou", "fundamento", None,
                           f"USDA: {resultado['gravados']} registros")
            for erro in resultado["erros"]:
                st.warning(erro)
            if resultado["gravados"]:
                st.rerun()

        commodity_usda = COMMODITY_POR_CULTURA.get(analysis_crop)
        if commodity_usda:
            leitura = leitura_de_oferta(commodity_usda, "YIELD")
            if leitura:
                f1, f2, f3 = st.columns(3)
                f1.metric(
                    f"Produtividade {leitura['ano']} (EUA)",
                    f"{num(leitura['valor'])} {leitura['unidade'] or ''}".strip(),
                    delta=f"{leitura['variacao_pct']:+.1f}% vs média",
                )
                f2.metric("Média dos anos anteriores", num(leitura["media_anterior"]))
                f3.metric("Anos na base", leitura["anos_considerados"])
                st.info(
                    f"Safra americana de {analysis_crop.lower()} "
                    f"**{leitura['leitura']}** — {leitura['efeito']}."
                )
                serie_usda = serie_anual(commodity_usda, "YIELD")
                if len(serie_usda) >= 2:
                    st.bar_chart(
                        pd.DataFrame(
                            {"Produtividade (EUA)": [i["valor"] for i in serie_usda]},
                            index=[str(i["ano"]) for i in serie_usda],
                        ),
                        use_container_width=True,
                    )
            else:
                st.caption(
                    f"Ainda não há histórico suficiente de {analysis_crop.lower()} "
                    "para uma leitura de oferta (são necessários ao menos 3 anos)."
                )
        else:
            st.caption(f"O USDA não cobre {analysis_crop.lower()} nesta integração.")

        # Balanço mundial (FAS): produção por país, quando já coletado.
        codigo_fas = COMMODITY_FAS.get(analysis_crop)
        if codigo_fas:
            mundiais = q(
                """SELECT region, year, value, unit, statistic
                   FROM fundamentals
                   WHERE source='FAS' AND commodity=:c
                     AND lower(statistic) LIKE '%production%'
                   ORDER BY year, region""",
                {"c": codigo_fas},
            )
            if mundiais:
                st.markdown("---")
                st.markdown(f"**🌎 Produção mundial de {analysis_crop.lower()}**")
                tabela = pd.DataFrame([
                    {
                        "Ano": int(linha["year"]),
                        "País": PAISES_DE_INTERESSE.get(linha["region"], linha["region"]),
                        "Produção": float(linha["value"]),
                    }
                    for linha in mundiais
                ])
                pivo = tabela.pivot_table(
                    index="Ano", columns="País", values="Produção", aggfunc="sum"
                )
                st.line_chart(pivo, use_container_width=True)
                unidade = mundiais[0].get("unit") or ""
                st.caption(
                    f"Fonte: USDA/FAS PSD · unidade {unidade}. "
                    "Compare a safra brasileira com a dos concorrentes: quando a "
                    "oferta mundial cresce, o preço tende a ceder."
                )

    if user["role"] == "admin":
        with st.expander("🌐 Diagnóstico da FAS PSD (balanço mundial)"):
            st.caption(
                "A FAS traz produção, produtividade e estoques **por país** — o "
                "cenário mundial, não só os EUA. A documentação do formato dela "
                "fica atrás da chave, então este passo descobre a estrutura real "
                "antes de eu escrever a leitura definitiva."
            )
            if not fas_tem_chave():
                st.info(
                    "A variável `FAS_API_KEY` não está configurada **neste ambiente**. "
                    "Em produção ela vive nas variáveis do Render."
                )
            else:
                rota = st.selectbox(
                    "Rota para inspecionar", list(ROTAS_FAS), key="fas_rota_diagnostico"
                )
                if st.button("🔍 Executar diagnóstico", key="rodar_diagnostico_fas",
                             use_container_width=True):
                    with st.spinner("Consultando a FAS..."):
                        relatorio = diagnosticar_fas(rota)
                    st.session_state.fas_relatorio = relatorio

            relatorio = st.session_state.get("fas_relatorio")
            if relatorio:
                if relatorio.get("forma_que_funcionou"):
                    st.success(
                        f"Conectou usando: **{relatorio['forma_que_funcionou']}**"
                    )
                for tentativa in relatorio["tentativas"]:
                    st.write(
                        f"• `{tentativa['forma']}` → HTTP {tentativa['status']} "
                        f"— {tentativa.get('detalhe', '')}"
                    )
                for erro in relatorio["erros"]:
                    st.warning(erro)
                if relatorio.get("estrutura"):
                    st.markdown("**Estrutura da resposta:**")
                    st.json(relatorio["estrutura"])

            if fas_tem_chave():
                st.markdown("---")
                st.markdown("**1. Descobrir o código da cultura**")
                termo = st.text_input(
                    "Procurar no catálogo", value="soybean",
                    key="fas_busca_commodity",
                    help="Os nomes da FAS são em inglês: soybean, corn, wheat.",
                )
                if st.button("🔎 Procurar", key="buscar_commodity_fas",
                             use_container_width=True):
                    achados, erros_busca = buscar_commodities_fas(termo)
                    for erro in erros_busca:
                        st.warning(erro)
                    if achados:
                        st.dataframe(pd.DataFrame(achados),
                                     use_container_width=True, hide_index=True)
                    else:
                        st.caption("Nada encontrado com esse termo.")

                st.markdown("**2. Inspecionar a rota de dados**")
                d1, d2, d3 = st.columns(3)
                codigo = d1.text_input("Código da commodity", value="",
                                       key="fas_codigo_dados")
                pais = d2.text_input("País (sigla)", value="BR", key="fas_pais_dados")
                ano = d3.number_input("Ano", min_value=1960, max_value=2100,
                                      value=2024, step=1, key="fas_ano_dados")
                if st.button("🔬 Inspecionar dados", key="inspecionar_dados_fas",
                             use_container_width=True, disabled=not codigo.strip()):
                    with st.spinner("Consultando a FAS..."):
                        rel_dados = diagnosticar_dados_fas(
                            codigo.strip(), pais.strip().upper() or "BR", int(ano)
                        )
                    st.caption(f"Rota: `{rel_dados['caminho']}`")
                    for erro in rel_dados["erros"]:
                        st.warning(erro)
                    if rel_dados.get("estrutura"):
                        st.json(rel_dados["estrutura"])

                st.markdown("---")
                st.markdown("**3. Coletar o balanço mundial**")
                st.caption(
                    "Busca soja, milho e trigo para Brasil, EUA, Argentina e China. "
                    "Os rótulos dos indicadores são traduzidos automaticamente a "
                    "partir dos catálogos da FAS — o resultado aparece abaixo para "
                    "você conferir se bateu."
                )
                if st.button("🌎 Coletar dados mundiais", key="coletar_fas",
                             use_container_width=True, type="primary"):
                    with st.spinner("Consultando a FAS... isso leva alguns minutos."):
                        resultado_fas = coletar_fas(user_id=user["id"])
                    st.session_state.fas_coleta = resultado_fas

                coleta = st.session_state.get("fas_coleta")
                if coleta:
                    if coleta["gravados"]:
                        st.success(f"{coleta['gravados']} observações gravadas.")
                    for erro in coleta["erros"][:5]:
                        st.warning(erro)
                    if coleta.get("atributos"):
                        st.markdown("**Tradução dos indicadores:**")
                        st.dataframe(
                            pd.DataFrame(
                                [{"ID": k, "Indicador": v}
                                 for k, v in sorted(coleta["atributos"].items())]
                            ),
                            use_container_width=True, hide_index=True, height=200,
                        )
                        st.caption(
                            "Se esta tabela vier com rótulos sem sentido, a tradução "
                            "falhou — me avise antes de confiar nos números."
                        )

    if CAN_EDIT:
        with st.expander("📥 Importar histórico de preços (planilha)"):
            st.caption(
                "Traga de uma vez o histórico que você já tem. Quanto mais fundo o "
                "histórico, melhor a leitura de mercado. Nada é gravado antes de você conferir."
            )
            st.markdown(
                "**Formato:** arquivo `.csv` com as colunas **Data**, **Cultura** e "
                "**Preço**. As colunas *Fonte* e *Praça* são opcionais. "
                "Aceita o CSV que o Excel em português gera (`;` e vírgula decimal)."
            )
            st.code(
                "Data;Cultura;Preço;Fonte;Praça\n"
                "01/03/2026;Soja;138,50;Cooperativa;Santo Ângelo/RS\n"
                "08/03/2026;Soja;140,00;Cooperativa;Santo Ângelo/RS",
                language="text",
            )

            enviado = st.file_uploader(
                "Arquivo CSV", type=["csv", "txt"], key="import_historico_arquivo"
            )
            previa = st.session_state.get("import_historico_previa")

            if enviado is not None and not previa:
                linhas, erros = parse_price_csv(
                    enviado.getvalue(),
                    culturas_validas=["Soja", "Milho", "Trigo", "Canola"],
                )
                st.session_state.import_historico_previa = {
                    "linhas": linhas, "erros": erros, "nome": enviado.name,
                }
                st.rerun()

            if previa:
                linhas, erros = previa["linhas"], previa["erros"]
                st.write(f"**Arquivo:** {previa['nome']}")

                if erros:
                    st.warning(f"{len(erros)} linha(s) com problema — serão ignoradas:")
                    for erro in erros[:10]:
                        st.write("•", erro)
                    if len(erros) > 10:
                        st.caption(f"... e mais {len(erros) - 10}.")

                if linhas:
                    datas = [item["data"] for item in linhas]
                    resumo_cols = st.columns(3)
                    resumo_cols[0].metric("Preços válidos", len(linhas))
                    resumo_cols[1].metric(
                        "Período",
                        f"{min(datas).strftime('%m/%Y')} → {max(datas).strftime('%m/%Y')}",
                    )
                    resumo_cols[2].metric(
                        "Culturas", ", ".join(sorted({i["cultura"] for i in linhas}))
                    )
                    previa_frame = pd.DataFrame([
                        {
                            "Data": item["data"].strftime("%d/%m/%Y"),
                            "Cultura": item["cultura"],
                            "Preço": money(item["preco"]),
                            "Fonte": item["fonte"] or "—",
                            "Praça": item["regiao"] or "—",
                        }
                        for item in linhas[:15]
                    ])
                    st.dataframe(previa_frame, use_container_width=True, hide_index=True)
                    if len(linhas) > 15:
                        st.caption(f"Mostrando 15 de {len(linhas)} linhas.")

                imp1, imp2 = st.columns(2)
                confirmar = imp1.button(
                    f"✅ Importar {len(linhas)} preço(s)",
                    key="confirmar_import_historico",
                    use_container_width=True,
                    type="primary",
                    disabled=not linhas,
                )
                cancelar = imp2.button(
                    "↩️ Cancelar", key="cancelar_import_historico",
                    use_container_width=True,
                )
                if cancelar:
                    st.session_state.pop("import_historico_previa", None)
                    st.rerun()
                if confirmar:
                    resultado = importar_linhas(linhas, user_id=user["id"])
                    log_action(
                        user["id"], "importou", "cotação", None,
                        f"{resultado['gravadas']} preços de {previa['nome']}",
                    )
                    st.session_state.pop("import_historico_previa", None)
                    st.success(
                        f"{resultado['gravadas']} preço(s) importado(s). "
                        f"{resultado['ignoradas']} já existia(m) e foram ignorados."
                    )
                    st.rerun()

    with st.expander("🌐 Fontes de dados"):
        st.caption("Fontes conectadas e planejadas para alimentar a série de preços.")
        for source in available_sources():
            fonte_cols = st.columns([3, 1])
            fonte_cols[0].write(f"**{source.label}** · {source.note}")
            if CAN_EDIT and fonte_cols[1].button(
                "Atualizar", key=f"collect_{source.key}", use_container_width=True
            ):
                with st.spinner(f"Consultando {source.label}..."):
                    result = collect(source.key, user_id=user["id"])
                if result["updated"]:
                    st.success(f"{len(result['updated'])} cotações atualizadas.")
                for error in result["errors"]:
                    st.warning(error)
                st.rerun()
        for source in planned_sources():
            st.write(f"🔜 **{source.label}** · {source.note} _(planejada)_")

    if CAN_EDIT:
        with st.expander("✏️ Informar ou corrigir preço", expanded=False):
            with st.form("regional_quote_manual", clear_on_submit=True):
                crop = st.selectbox("Produto", ["Soja", "Milho", "Trigo", "Canola"])
                price = currency_input(st, "Preço (R$/sc)", step=0.50)
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
        history_frame["price_sc"] = history_frame["price_sc"].map(money)
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
                        edit_quote_price = currency_input(
                            eq2,
                            "Preço (R$/sc)",
                            value=float(quote["price_sc"]),
                            step=0.50,
                        )
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
        "Ele contém os cadastros e lançamentos em CSV, sem senhas ou sessões de acesso."
    )

    backup_queries = {
        "users": "SELECT id,name,email,role,active,created_at FROM users ORDER BY id",
        "companies": "SELECT * FROM companies ORDER BY id",
        "units": "SELECT * FROM units ORDER BY id",
        "products": "SELECT * FROM products ORDER BY id",
        "seasons": "SELECT * FROM seasons ORDER BY id",
        "machinery": "SELECT * FROM machinery ORDER BY id",
        "purchase_contracts": "SELECT * FROM purchase_contracts ORDER BY id",
        "commitments": "SELECT * FROM commitments ORDER BY id",
        "sales": "SELECT * FROM sales ORDER BY id",
        "quotes": "SELECT * FROM quotes ORDER BY id",
        "payments": "SELECT * FROM payments ORDER BY id",
        "activity_log": "SELECT * FROM activity_log ORDER BY id",
        "pilot_feedback": "SELECT * FROM pilot_feedback ORDER BY id",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for table, query in backup_queries.items():
            rows = q(query)
            frame = pd.DataFrame(rows)
            archive.writestr(
                f"{table}.csv",
                frame.to_csv(index=False).encode("utf-8-sig"),
            )
        metadata = {
            "generated_at": datetime.now().isoformat(),
            "database": engine.dialect.name,
            "version": "agriza-enterprise-3.1",
        }
        archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))

    st.download_button(
        "Baixar backup",
        data=buffer.getvalue(),
        file_name=f"agriza_backup_{date.today().strftime('%d-%m-%Y')}.zip",
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
