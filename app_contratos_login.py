import io
import json
import zipfile
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
    remembered_token = cookie_manager.get(COOKIE_NAME)
    if remembered_token:
        remembered_user = get_user_from_session_token(remembered_token)
        if remembered_user:
            st.session_state.user = remembered_user
            st.session_state.persistent_token = remembered_token
        else:
            try:
                cookie_manager.delete(COOKIE_NAME)
            except Exception:
                pass

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
                        key="set_agriza_remember_cookie",
                    )
                    st.session_state.persistent_token = token
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
    "💰 Vendas",
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
        with st.expander("📄 Nova compra parcelada / contrato", expanded=False):
            st.info(
                "Use para máquinas, financiamentos, arrendamentos e compras com "
                "vários vencimentos. Cada parcela pode ser vinculada a uma cultura."
            )

            use_planter_example = st.checkbox(
                "Preencher exemplo da plantadeira",
                value=False,
                key="use_planter_example",
            )

            if use_planter_example:
                default_contract_description = "Plantadeira"
                default_contract_value = 405000.0
                default_installments = pd.DataFrame(
                    [
                        {"Parcela": 1, "Vencimento": date(2026, 11, 20),
                         "Valor (R$)": 60000.0, "Pagar com": "Trigo"},
                        {"Parcela": 2, "Vencimento": date(2027, 5, 20),
                         "Valor (R$)": 115000.0, "Pagar com": "Soja"},
                        {"Parcela": 3, "Vencimento": date(2028, 5, 20),
                         "Valor (R$)": 115000.0, "Pagar com": "Soja"},
                        {"Parcela": 4, "Vencimento": date(2029, 5, 20),
                         "Valor (R$)": 115000.0, "Pagar com": "Soja"},
                    ]
                )
            else:
                default_contract_description = ""
                default_contract_value = 0.0
                default_installments = pd.DataFrame(
                    [
                        {"Parcela": 1, "Vencimento": date.today(),
                         "Valor (R$)": 0.0, "Pagar com": "Caixa"}
                    ]
                )

            with st.form("new_installment_contract"):
                contract_description = st.text_input(
                    "Descrição da compra",
                    value=default_contract_description,
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
                )
                contract_total = cc3.number_input(
                    "Valor total contratado (R$)",
                    min_value=0.0,
                    value=default_contract_value,
                    step=1000.0,
                )
                contract_notes = st.text_area("Observações do contrato")

                edited_installments = st.data_editor(
                    default_installments,
                    num_rows="dynamic",
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Parcela": st.column_config.NumberColumn(
                            "Parcela", min_value=1, step=1, required=True
                        ),
                        "Vencimento": st.column_config.DateColumn(
                            "Vencimento", required=True, format="DD/MM/YYYY"
                        ),
                        "Valor (R$)": st.column_config.NumberColumn(
                            "Valor (R$)", min_value=0.01, step=100.0,
                            required=True, format="R$ %.2f"
                        ),
                        "Pagar com": st.column_config.SelectboxColumn(
                            "Pagar com", options=payment_options, required=True
                        ),
                    },
                    key="contract_installments_editor",
                )

                save_contract = st.form_submit_button(
                    "Salvar contrato e parcelas",
                    use_container_width=True,
                )

            if save_contract:
                rows = edited_installments.to_dict("records")
                rows = [
                    row for row in rows
                    if row.get("Vencimento") is not None
                    and float(row.get("Valor (R$)") or 0) > 0
                ]
                installment_sum = sum(float(row["Valor (R$)"]) for row in rows)

                if not contract_description.strip():
                    st.error("Informe a descrição da compra.")
                elif contract_total <= 0:
                    st.error("Informe o valor total contratado.")
                elif not rows:
                    st.error("Informe pelo menos uma parcela válida.")
                elif abs(installment_sum - contract_total) > 0.01:
                    st.error(
                        f"A soma das parcelas é {money(installment_sum)}, mas o "
                        f"contrato é {money(contract_total)}."
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

                        for position, row in enumerate(rows, start=1):
                            installment_no = int(row.get("Parcela") or position)
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
                            f"{len(rows)} parcelas · {money(contract_total)}",
                        )
                        st.success(
                            f"Contrato salvo com {len(rows)} parcelas."
                        )
                        st.session_state.current_page = "🛒 Compras"
                    except Exception as error:
                        st.error("Não foi possível salvar o contrato parcelado.")
                        st.exception(error)

        with st.expander("🎙️ Lançamento rápido por voz", expanded=False):
            st.info(
                "No celular, toque no campo e use o microfone do teclado. "
                "Exemplo: “Comprei fertilizante da Cooperativa Alfa por "
                "35 mil reais, vence em 30 dias, para a safra de milho”."
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
                st.caption("Confira ou corrija os dados antes de salvar.")

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
                    "No celular, toque no campo abaixo e use o microfone do teclado. "
                    "Exemplo: “Vendi 500 sacas de milho a 72 reais para Cooperativa Alfa hoje”."
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
                    st.caption("Confira os dados interpretados antes de salvar.")
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
