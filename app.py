import streamlit as st
import pandas as pd
import io
import zipfile
from datetime import date, datetime, timedelta
from core.config import engine, IS_POSTGRES, apply_page_config, apply_global_style
from core.database import init_db, q, scalar, ex, insert_id, log_action
from core.security import vpw
from core.utils import money, num
from services.analytics import season_summary, commitment_status, agroia_recommendation
from services.auth import setup_complete, save_setting, create_initial_admin


apply_page_config()
apply_global_style()
init_db()

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
