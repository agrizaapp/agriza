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
from core.utils import money, num
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
init_db()

COOKIE_NAME = "agriza_remember_session"
cookie_manager = stx.CookieManager(key="agriza_cookie_manager")

if "session_cleanup_done" not in st.session_state:
    try:
        cleanup_expired_sessions()
    except Exception:
        pass
    st.session_state.session_cleanup_done = True

st.markdown('<div class="brand">🌱 AGRIZA</div>', unsafe_allow_html=True)
st.markdown('<div class="subbrand">AgroIA • Transformando informação em decisão.</div>', unsafe_allow_html=True)
st.caption("Versão ativa: AGRIZA v10.3 · confirmação antes de salvar")

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
                    st.exception(error)

            st.rerun()
        else:
            st.error("E-mail ou senha incorretos.")
    st.stop()

user = st.session_state.user
CAN_EDIT = user["role"] in ("admin", "operador")

top_left, top_right = st.columns([4, 1])
top_left.caption(f"Olá, **{user['name']}** · {user['role'].capitalize()}")
if top_right.button("Sair", use_container_width=True):
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

pages = [
    "🏠 Painel",
    "🌾 Safras",
    "🛒 Compras",
    "🚜 Máquinas e financiamentos",
    "💰 Vendas",
    "🤖 AgroIA",
    "📈 Mercado",
    "🧪 Teste 7 dias",
]
if user["role"] == "admin":
    pages.extend(["👥 Usuários", "📦 Backup"])

if "current_page" not in st.session_state or st.session_state.current_page not in pages:
    st.session_state.current_page = pages[0]

with st.expander(
    f"☰ Menu principal — {st.session_state.current_page}",
    expanded=True,
):
    st.caption("Toque em uma área para abrir. Os botões foram ampliados para uso no celular.")
    for start in range(0, len(pages), 2):
        cols = st.columns(2, gap="small")
        for offset, label in enumerate(pages[start:start + 2]):
            with cols[offset]:
                button_type = "primary" if label == st.session_state.current_page else "secondary"
                if st.button(
                    label,
                    key=f"nav_{start + offset}",
                    use_container_width=True,
                    type=button_type,
                ):
                    st.session_state.current_page = label
                    st.rerun()

page = st.session_state.current_page


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
                f"Estimativa **{num(summary['estimated_production'], 0)} sc** · "
                f"Vendido **{num(summary['sold_pct'])}%**"
            )

            if CAN_EDIT:
                with st.expander("✏️ Editar custo da safra"):
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
    payment_options = [
        "Soja",
        "Milho",
        "Trigo",
        "Canola",
        "Caixa",
        "Mais de uma",
    ]

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
            st.exception(error)
            return False

    if CAN_EDIT:
        with st.expander("📄 COMPRA PARCELADA / CONTRATO", expanded=True):
            st.info(
                "Use para máquinas, financiamentos, arrendamentos e compras "
                "com vários vencimentos. Cada parcela pode ter uma cultura "
                "de pagamento diferente."
            )

            use_planter_example = st.checkbox(
                "Preencher exemplo da plantadeira",
                value=False,
                key="use_planter_example_v101",
            )

            default_description = "Plantadeira" if use_planter_example else ""
            default_total = 405000.0 if use_planter_example else 0.0
            default_count = 4 if use_planter_example else 1

            with st.form("new_installment_contract_v101", clear_on_submit=False):
                contract_description = st.text_input(
                    "Descrição da compra",
                    value=default_description,
                    placeholder="Ex.: Plantadeira 13 linhas",
                )
                contract_supplier = st.text_input("Fornecedor / vendedor")

                cc1, cc2, cc3 = st.columns(3)
                contract_category = cc1.selectbox(
                    "Categoria",
                    categories,
                    index=categories.index("Máquinas"),
                )
                contract_purchase_date = cc2.date_input(
                    "Data da compra",
                    value=date.today(),
                    format="DD/MM/YYYY",
                )
                contract_total = cc3.number_input(
                    "Valor total contratado (R$)",
                    min_value=0.0,
                    value=default_total,
                    step=1000.0,
                )

                installment_count = st.number_input(
                    "Quantidade de parcelas",
                    min_value=1,
                    max_value=20,
                    value=default_count,
                    step=1,
                )

                contract_notes = st.text_area("Observações do contrato")

                st.markdown("#### Parcelas")
                installment_rows = []

                example_dates = [
                    date(2026, 11, 20),
                    date(2027, 5, 20),
                    date(2028, 5, 20),
                    date(2029, 5, 20),
                ]
                example_values = [60000.0, 115000.0, 115000.0, 115000.0]
                example_crops = ["Trigo", "Soja", "Soja", "Soja"]

                for index in range(int(installment_count)):
                    st.markdown(f"**Parcela {index + 1}**")
                    pc1, pc2, pc3 = st.columns(3)

                    if use_planter_example and index < 4:
                        default_due = example_dates[index]
                        default_value = example_values[index]
                        default_crop = example_crops[index]
                    else:
                        default_due = date.today()
                        default_value = 0.0
                        default_crop = "Caixa"

                    due_date_value = pc1.date_input(
                        "Vencimento",
                        value=default_due,
                        format="DD/MM/YYYY",
                        key=f"contract_due_v101_{index}",
                    )
                    amount_value = pc2.number_input(
                        "Valor (R$)",
                        min_value=0.0,
                        value=default_value,
                        step=1000.0,
                        key=f"contract_amount_v101_{index}",
                    )
                    crop_index = (
                        payment_options.index(default_crop)
                        if default_crop in payment_options
                        else 0
                    )
                    payment_crop_value = pc3.selectbox(
                        "Pagar com",
                        payment_options,
                        index=crop_index,
                        key=f"contract_crop_v101_{index}",
                    )

                    installment_rows.append(
                        {
                            "Parcela": index + 1,
                            "Vencimento": due_date_value,
                            "Valor (R$)": amount_value,
                            "Pagar com": payment_crop_value,
                        }
                    )

                save_contract = st.form_submit_button(
                    "Salvar contrato e parcelas",
                    use_container_width=True,
                )

            if save_contract:
                valid_rows = [
                    row for row in installment_rows
                    if float(row["Valor (R$)"] or 0) > 0
                ]
                installment_sum = sum(
                    float(row["Valor (R$)"]) for row in valid_rows
                )

                if not contract_description.strip():
                    st.error("Informe a descrição da compra.")
                elif contract_total <= 0:
                    st.error("Informe o valor total contratado.")
                elif not valid_rows:
                    st.error("Informe ao menos uma parcela com valor.")
                elif len(valid_rows) != int(installment_count):
                    st.error("Todas as parcelas precisam ter valor maior que zero.")
                elif abs(installment_sum - contract_total) > 0.01:
                    st.error(
                        f"A soma das parcelas é {money(installment_sum)}, "
                        f"mas o contrato é {money(contract_total)}."
                    )
                else:
                    try:
                        contract_id = insert_id(
                            """INSERT INTO purchase_contracts
                               (description,supplier,category,total_value,
                                purchase_date,notes,status,created_by)
                               VALUES(:d,:f,:c,:v,:pd,:n,'aberto',:u)""",
                            {
                                "d": contract_description.strip(),
                                "f": contract_supplier.strip(),
                                "c": contract_category,
                                "v": contract_total,
                                "pd": contract_purchase_date,
                                "n": contract_notes.strip(),
                                "u": user["id"],
                            },
                        )

                        for row in valid_rows:
                            installment_no = int(row["Parcela"])
                            insert_id(
                                """INSERT INTO commitments
                                   (contract_id,installment_no,season_id,category,
                                    description,supplier,total_value,purchase_date,
                                    due_date,payment_crop,notes,status,created_by)
                                   VALUES(:ct,:ino,NULL,:c,:d,:f,:v,:pd,:dt,:p,
                                          :n,'aberto',:u)""",
                                {
                                    "ct": contract_id,
                                    "ino": installment_no,
                                    "c": contract_category,
                                    "d": (
                                        f"{contract_description.strip()} · "
                                        f"Parcela {installment_no}"
                                    ),
                                    "f": contract_supplier.strip(),
                                    "v": float(row["Valor (R$)"]),
                                    "pd": contract_purchase_date,
                                    "dt": row["Vencimento"],
                                    "p": row["Pagar com"],
                                    "n": contract_notes.strip(),
                                    "u": user["id"],
                                },
                            )

                        log_action(
                            user["id"],
                            "criou",
                            "contrato_compra",
                            contract_id,
                            f"{contract_description.strip()} · "
                            f"{len(valid_rows)} parcelas · {money(contract_total)}",
                        )
                        st.success(
                            f"Contrato salvo com {len(valid_rows)} parcelas."
                        )
                        st.session_state.current_page = "🛒 Compras"
                    except Exception as error:
                        st.error("Não foi possível salvar o contrato parcelado.")
                        st.exception(error)

        with st.expander("🎙️ Lançamento rápido por voz", expanded=False):
            st.info(
                "Toque no campo e use o microfone do teclado do celular. "
                "Fale em uma frase com produto, fornecedor, valor, vencimento "
                "e cultura de pagamento."
            )
            st.code(
                "Comprei uma plantadeira do fornecedor Agro Máquinas por "
                "405 mil reais, vencimento dia 20 de novembro de 2026, "
                "pagar com trigo",
                language=None,
            )
            st.caption(
                "Também entende valores como “trinta e cinco mil reais”, "
                "datas faladas e números com ponto ou vírgula."
            )

            with st.form("voice_purchase_interpret"):
                spoken_purchase = st.text_area(
                    "Dite ou escreva a compra",
                    placeholder=(
                        "Comprei 20 toneladas de fertilizante da Cooperativa Alfa "
                        "por 35 mil reais, vence em 30 dias, para a safra de milho"
                    ),
                    height=120,
                )
                interpret_purchase = st.form_submit_button(
                    "Interpretar compra",
                    use_container_width=True,
                )

            if interpret_purchase:
                if not spoken_purchase.strip():
                    st.error("Dite ou escreva os dados da compra.")
                else:
                    st.session_state.voice_purchase_draft = parse_spoken_purchase(
                        spoken_purchase,
                        seasons,
                    )

            draft = st.session_state.get("voice_purchase_draft")
            if draft:
                if draft.get("missing"):
                    st.warning(
                        "Não consegui identificar automaticamente: "
                        + ", ".join(draft["missing"])
                        + ". Preencha esses campos abaixo."
                    )
                else:
                    st.success("Dados principais identificados. Confira antes de salvar.")

                season_labels = list(season_map)
                season_index = (
                    season_labels.index(draft["season_label"])
                    if draft["season_label"] in season_labels
                    else 0
                )
                category_index = (
                    categories.index(draft["category"])
                    if draft["category"] in categories
                    else len(categories) - 1
                )
                payment_index = (
                    payment_options.index(draft["payment_crop"])
                    if draft["payment_crop"] in payment_options
                    else payment_options.index("Caixa")
                )

                with st.form("voice_purchase_confirm"):
                    voice_description = st.text_input(
                        "O que foi comprado?",
                        value=draft["description"],
                    )
                    voice_category = st.selectbox(
                        "Categoria",
                        categories,
                        index=category_index,
                    )
                    voice_supplier = st.text_input(
                        "Fornecedor",
                        value=draft["supplier"],
                    )
                    vp1, vp2, vp3 = st.columns(3)
                    voice_total = vp1.number_input(
                        "Valor total (R$)",
                        min_value=0.0,
                        value=float(draft["total_value"]),
                        step=100.0,
                    )
                    voice_purchase_date = vp2.date_input(
                        "Data da compra",
                        value=date.today(),
                    )
                    voice_due_date = vp3.date_input(
                        "Vencimento",
                        value=draft["due_date"],
                    )
                    voice_payment_crop = st.selectbox(
                        "Pretende pagar com",
                        payment_options,
                        index=payment_index,
                    )
                    voice_season = st.selectbox(
                        "Safra relacionada",
                        season_labels,
                        index=season_index,
                    )
                    voice_notes = st.text_area(
                        "Observação",
                        value=draft["notes"],
                    )
                    save_voice_purchase = st.form_submit_button(
                        "Salvar compra ditada",
                        use_container_width=True,
                    )

                if save_voice_purchase:
                    if save_purchase_record(
                        voice_description,
                        voice_category,
                        voice_supplier,
                        voice_total,
                        voice_purchase_date,
                        voice_due_date,
                        voice_payment_crop,
                        voice_season,
                        voice_notes,
                    ):
                        st.session_state.pop("voice_purchase_draft", None)

        with st.expander("⌨️ Lançamento manual", expanded=True):
            with st.form("new_commitment", clear_on_submit=True):
                description = st.text_input("O que foi comprado?")
                category = st.selectbox("Categoria", categories)
                supplier = st.text_input("Fornecedor")
                c1, c2, c3 = st.columns(3)
                total_value = c1.number_input(
                    "Valor total (R$)",
                    min_value=0.0,
                )
                purchase_date = c2.date_input(
                    "Data da compra",
                    value=date.today(),
                )
                due_date = c3.date_input("Vencimento")
                payment_crop = st.selectbox(
                    "Pretende pagar com",
                    payment_options,
                )
                selected_season = st.selectbox(
                    "Safra relacionada",
                    list(season_map),
                )
                notes = st.text_area("Observação")
                submit = st.form_submit_button(
                    "Salvar compra",
                    use_container_width=True,
                )

            if submit:
                save_purchase_record(
                    description,
                    category,
                    supplier,
                    total_value,
                    purchase_date,
                    due_date,
                    payment_crop,
                    selected_season,
                    notes,
                )

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
    )

    if contracts:
        st.markdown("### Contratos parcelados")
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
                    f"{contract.get('purchase_date') or 'Não informada'}"
                )
                for installment in installments:
                    installment_status = commitment_status(installment["id"])
                    mark = "✅" if installment_status["remaining"] <= 0.01 else "◯"
                    st.write(
                        f"{mark} **Parcela {installment.get('installment_no') or '-'}** "
                        f"— {installment['due_date']} — "
                        f"{money(installment['total_value'])} — "
                        f"pagar com **{installment.get('payment_crop') or 'Caixa'}** "
                        f"— falta {money(installment_status['remaining'])}"
                    )

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
            st.write(
                f"**Data da compra:** "
                f"{item.get('purchase_date') or 'Não informada'}"
            )
            st.write(f"**Vencimento:** {item['due_date'] or 'Não informado'}")
            st.write(f"**Fornecedor:** {item['supplier'] or 'Não informado'}")
            st.write(f"**Protegido por vendas:** {money(status['protected'])}")
            st.write(f"**Pago:** {money(status['paid'])}")
            st.write(f"**Ainda falta:** {money(status['remaining'])}")

            if CAN_EDIT:
                with st.expander("✏️ Editar esta compra"):
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
                        )
                        edit_due_date = e3.date_input(
                            "Vencimento",
                            value=item.get("due_date") or date.today(),
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
                            except Exception as error:
                                st.error("Não foi possível atualizar a compra.")
                                st.exception(error)

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
                        st.exception(error)

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
            installment_count = st.number_input(
                "Quantas parcelas serão pagas?",
                min_value=1,
                max_value=20,
                value=default_count,
                step=1,
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
                    "Vencimento",
                    value=due_default,
                    format="DD/MM/YYYY",
                    key=f"machine_due_v102_{index}",
                )
                installment_value = p2.number_input(
                    "Valor da parcela (R$)",
                    min_value=0.0,
                    value=value_default,
                    step=1000.0,
                    key=f"machine_value_v102_{index}",
                )
                crop_index = (
                    payment_options.index(crop_default)
                    if crop_default in payment_options
                    else 0
                )
                payment_crop_value = p3.selectbox(
                    "Será paga com",
                    payment_options,
                    index=crop_index,
                    key=f"machine_crop_v102_{index}",
                )

                rows.append(
                    {
                        "number": index + 1,
                        "due_date": due_date_value,
                        "value": installment_value,
                        "crop": payment_crop_value,
                    }
                )

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
            elif abs(installment_sum - total_value) > 0.01:
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
                    "notes": notes.strip(),
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
                    st.exception(error)

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

                    installments = q(
                        """SELECT * FROM commitments
                           WHERE contract_id=:id
                             AND COALESCE(status,'aberto')!='cancelado'
                           ORDER BY installment_no""",
                        {"id": machine.get("contract_id")},
                    )

                    if installments:
                        st.markdown("#### Parcelas")
                        for installment in installments:
                            status = commitment_status(installment["id"])
                            mark = "✅" if status["remaining"] <= 0.01 else "◯"
                            st.write(
                                f"{mark} **Parcela "
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

        def save_sale_record(
            season_label,
            quantity,
            price,
            buyer,
            objective,
            sale_date,
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
                st.exception(error)
                return False

        if CAN_EDIT:
            with st.expander("🎙️ Lançamento rápido por voz", expanded=False):
                st.info(
                    "Toque no campo e use o microfone do teclado do celular. "
                    "Fale quantidade, cultura, preço, comprador e data."
                )
                st.code(
                    "Vendi quinhentas sacas de milho a 72 reais por saca "
                    "para Cooperativa Alfa hoje",
                    language=None,
                )
                st.caption(
                    "Também aceita “500 sacas”, datas como 20/11/2026 "
                    "ou “20 de novembro de 2026”."
                )
                with st.form("voice_sale_interpret"):
                    spoken_text = st.text_area(
                        "Dite ou escreva a venda",
                        placeholder=(
                            "Vendi 500 sacas de milho a 72 reais "
                            "para Cooperativa Alfa hoje"
                        ),
                        height=110,
                    )
                    interpret = st.form_submit_button(
                        "Interpretar lançamento",
                        use_container_width=True,
                    )

                if interpret:
                    if not spoken_text.strip():
                        st.error("Dite ou escreva os dados da venda.")
                    else:
                        st.session_state.voice_sale_draft = parse_spoken_sale(
                            spoken_text,
                            seasons,
                        )

                draft = st.session_state.get("voice_sale_draft")
                if draft:
                    if draft.get("missing"):
                        st.warning(
                            "Não consegui identificar automaticamente: "
                            + ", ".join(draft["missing"])
                            + ". Preencha esses campos abaixo."
                        )
                    else:
                        st.success("Dados principais identificados. Confira antes de salvar.")
                    labels = list(season_map)
                    default_season = (
                        labels.index(draft["season_label"])
                        if draft["season_label"] in labels
                        else 0
                    )
                    with st.form("voice_sale_confirm"):
                        voice_season = st.selectbox(
                            "Safra",
                            labels,
                            index=default_season,
                            key="voice_season",
                        )
                        vc1, vc2 = st.columns(2)
                        voice_quantity = vc1.number_input(
                            "Quantidade (sc)",
                            min_value=0.0,
                            value=float(draft["quantity"]),
                            step=10.0,
                            key="voice_quantity",
                        )
                        voice_price = vc2.number_input(
                            "Preço (R$/sc)",
                            min_value=0.0,
                            value=float(draft["price"]),
                            step=0.50,
                            key="voice_price",
                        )
                        voice_buyer = st.text_input(
                            "Comprador/cooperativa",
                            value=draft["buyer"],
                            key="voice_buyer",
                        )
                        voice_objective = st.selectbox(
                            "Esta venda protege",
                            list(commitment_map),
                            key="voice_objective",
                        )
                        voice_date = st.date_input(
                            "Data da venda",
                            value=draft["sale_date"],
                            key="voice_date",
                        )
                        voice_notes = st.text_area(
                            "Observação",
                            value=draft["notes"],
                            key="voice_notes",
                        )
                        voice_save = st.form_submit_button(
                            "Salvar venda ditada",
                            use_container_width=True,
                        )

                    if voice_save:
                        if save_sale_record(
                            voice_season,
                            voice_quantity,
                            voice_price,
                            voice_buyer,
                            voice_objective,
                            voice_date,
                            voice_notes,
                        ):
                            st.session_state.pop("voice_sale_draft", None)

            with st.expander("⌨️ Lançamento manual", expanded=True):
                with st.form("new_sale", clear_on_submit=True):
                    season_label = st.selectbox("Safra", list(season_map))
                    c1, c2 = st.columns(2)
                    quantity = c1.number_input("Quantidade (sc)", min_value=0.0)
                    price = c2.number_input("Preço (R$/sc)", min_value=0.0)
                    buyer = st.text_input("Comprador/cooperativa")
                    objective = st.selectbox("Esta venda protege", list(commitment_map))
                    sale_date = st.date_input("Data da venda", value=date.today())
                    notes = st.text_area("Observação")
                    submit = st.form_submit_button(
                        "Salvar venda",
                        use_container_width=True,
                    )

                if submit:
                    save_sale_record(
                        season_label,
                        quantity,
                        price,
                        buyer,
                        objective,
                        sale_date,
                        notes,
                    )

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
        crop_df = pd.DataFrame(
            [
                {"Cultura/Fonte": crop, "Valor necessário": value}
                for crop, value in sorted(
                    by_crop.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            ]
        )
        st.dataframe(
            crop_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Valor necessário": st.column_config.NumberColumn(
                    "Valor necessário",
                    format="R$ %.2f",
                )
            },
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
