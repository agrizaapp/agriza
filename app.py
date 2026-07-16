import os, hashlib, hmac
from datetime import date
import streamlit as st
from sqlalchemy import create_engine, text

st.set_page_config(page_title="AGRIZA • AgroIA", page_icon="🌱", layout="wide")

DB = os.getenv("DATABASE_URL", "sqlite:///agriza_local.db")
if DB.startswith("postgres://"):
    DB = DB.replace("postgres://", "postgresql+psycopg://", 1)
elif DB.startswith("postgresql://"):
    DB = DB.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DB, pool_pre_ping=True, future=True)

def hpw(p):
    salt=os.urandom(16); r=210000
    d=hashlib.pbkdf2_hmac("sha256",p.encode(),salt,r)
    return f"pbkdf2_sha256${r}${salt.hex()}${d.hex()}"

def vpw(p,e):
    try:
        _,r,s,d=e.split("$",3)
        x=hashlib.pbkdf2_hmac("sha256",p.encode(),bytes.fromhex(s),int(r)).hex()
        return hmac.compare_digest(x,d)
    except: return False

def q(sql, p=None):
    with engine.connect() as c:
        return [dict(x._mapping) for x in c.execute(text(sql),p or {})]

def ex(sql,p=None):
    with engine.begin() as c:
        return c.execute(text(sql),p or {})

def init():
    ddl=[
    """CREATE TABLE IF NOT EXISTS users(
       id SERIAL PRIMARY KEY,name VARCHAR(120),email VARCHAR(180) UNIQUE,
       password_hash TEXT,role VARCHAR(30) DEFAULT 'operador',active BOOLEAN DEFAULT TRUE)""",
    """CREATE TABLE IF NOT EXISTS seasons(
       id SERIAL PRIMARY KEY,name VARCHAR(160),crop VARCHAR(50),area_ha NUMERIC(14,2),
       cost_ha NUMERIC(14,2),yield_sc_ha NUMERIC(14,2),margin_pct NUMERIC(8,2) DEFAULT 20,
       active BOOLEAN DEFAULT TRUE,created_by INTEGER)""",
    """CREATE TABLE IF NOT EXISTS commitments(
       id SERIAL PRIMARY KEY,season_id INTEGER,category VARCHAR(80),description VARCHAR(240),
       supplier VARCHAR(180),total_value NUMERIC(16,2),due_date DATE,payment_crop VARCHAR(80),
       notes TEXT,created_by INTEGER)""",
    """CREATE TABLE IF NOT EXISTS sales(
       id SERIAL PRIMARY KEY,season_id INTEGER,sale_date DATE,quantity_sc NUMERIC(16,2),
       price_sc NUMERIC(16,2),buyer VARCHAR(180),commitment_id INTEGER,notes TEXT,created_by INTEGER)""",
    """CREATE TABLE IF NOT EXISTS quotes(
       id SERIAL PRIMARY KEY,crop VARCHAR(50),price_sc NUMERIC(16,2),
       source VARCHAR(180),quoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,created_by INTEGER)"""
    ]
    with engine.begin() as c:
        for s in ddl: c.execute(text(s))
        email=os.getenv("ADMIN_EMAIL","admin@agriza.local").strip().lower()
        admin_params = {
            "n": os.getenv("ADMIN_NAME","Fabio").strip() or "Fabio",
            "e": email,
            "p": hpw(os.getenv("ADMIN_PASSWORD","troque-esta-senha")),
        }

        # Cria o administrador somente se ele ainda não existir.
        # A proteção é feita diretamente no banco para evitar erro de concorrência
        # quando o Streamlit executa o script mais de uma vez ao mesmo tempo.
        if engine.dialect.name == "sqlite":
            c.execute(
                text("""INSERT OR IGNORE INTO users(name,email,password_hash,role)
                        VALUES(:n,:e,:p,'admin')"""),
                admin_params,
            )
        else:
            c.execute(
                text("""INSERT INTO users(name,email,password_hash,role)
                        VALUES(:n,:e,:p,'admin')
                        ON CONFLICT (email) DO NOTHING"""),
                admin_params,
            )
init()

st.markdown("""
<style>
.block-container{max-width:1050px;padding-top:1rem}
.card{border:1px solid #dfe8d8;border-radius:16px;padding:1rem;margin:.5rem 0;background:#fff}
.stButton button,.stFormSubmitButton button{min-height:3rem;border-radius:12px;font-weight:700}
@media(max-width:700px){div[data-testid="column"]{min-width:100%!important}}
</style>""",unsafe_allow_html=True)

st.markdown("# 🌱 AGRIZA")
st.caption("AgroIA • Transformando informação em decisão.")

if "user" not in st.session_state:
    with st.form("login"):
        e=st.text_input("E-mail").lower().strip()
        p=st.text_input("Senha",type="password")
        ok=st.form_submit_button("Entrar",use_container_width=True)
    if ok:
        u=q("SELECT * FROM users WHERE lower(email)=:e AND active=TRUE",{"e":e})
        if u and vpw(p,u[0]["password_hash"]):
            st.session_state.user={k:v for k,v in u[0].items() if k!="password_hash"}
            st.rerun()
        else: st.error("E-mail ou senha incorretos.")
    st.stop()

u=st.session_state.user
c1,c2=st.columns([4,1])
c1.caption(f"Olá, **{u['name']}** · {u['role']}")
if c2.button("Sair",use_container_width=True):
    st.session_state.pop("user"); st.rerun()

pages=["🏠 Painel","➕ Registrar","🌾 Safras","🛒 Compras","💰 Vendas","📈 Mercado"]
if u["role"]=="admin": pages.append("⚙️ Usuários")
pg=st.radio("Menu",pages,horizontal=True,label_visibility="collapsed")

def money(v):
    return "R$ "+f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X",".")

def summary(s):
    prod=float(s["area_ha"])*float(s["yield_sc_ha"])
    cost=float(s["area_ha"])*float(s["cost_ha"])
    sales=q("SELECT quantity_sc,price_sc FROM sales WHERE season_id=:i",{"i":s["id"]})
    sold=sum(float(x["quantity_sc"]) for x in sales)
    rec=sum(float(x["quantity_sc"])*float(x["price_sc"]) for x in sales)
    bal=max(prod-sold,0)
    target=cost*(1+float(s["margin_pct"])/100)
    req=max(target-rec,0)/bal if bal else 0
    return prod,cost,sold,rec,bal,req

if pg=="🏠 Painel":
    st.subheader("Painel de decisão")
    ss=q("SELECT * FROM seasons WHERE active=TRUE ORDER BY id DESC")
    if not ss: st.info("Cadastre a primeira safra.")
    else:
        labels={f"{x['name']} · {x['crop']}":x for x in ss}
        s=labels[st.selectbox("Safra",labels,label_visibility="collapsed")]
        prod,cost,sold,rec,bal,req=summary(s)
        a,b,c=st.columns(3)
        a.metric("Produção",f"{prod:,.0f} sc")
        b.metric("Vendido",f"{(sold/prod*100 if prod else 0):.1f}%")
        c.metric("Saldo",f"{bal:,.0f} sc")
        quote=q("SELECT * FROM quotes WHERE crop=:c ORDER BY quoted_at DESC LIMIT 1",{"c":s["crop"]})
        st.markdown("### 🤖 Recomendação AgroIA")
        if not quote: st.info("Registre a cotação atual para ativar a recomendação.")
        else:
            price=float(quote[0]["price_sc"])
            if price>=req and sold/prod<.4:
                st.success(f"Consideraria vender entre 5% e 10%. A cotação de {money(price)}/sc supera o preço necessário de {money(req)}/sc.")
            elif price<req:
                st.warning(f"Eu aguardaria ou venderia apenas o necessário ao caixa. A cotação de {money(price)}/sc está abaixo dos {money(req)}/sc necessários.")
            else:
                st.info("Manteria uma estratégia gradual.")
        st.caption("A recomendação é apoio gerencial e não garante resultado.")

elif pg=="➕ Registrar":
    st.subheader("O que aconteceu hoje?")
    st.info("Use as abas Compras, Vendas ou Mercado. A entrada por foto e voz será ativada após o piloto.")

elif pg=="🌾 Safras":
    st.subheader("Safras")
    with st.form("safra",clear_on_submit=True):
        name=st.text_input("Nome",placeholder="Soja 2026/27")
        crop=st.selectbox("Cultura",["Soja","Milho","Trigo","Canola"])
        a,b=st.columns(2)
        area=a.number_input("Área (ha)",min_value=0.0)
        cost=b.number_input("Custo/ha (R$)",min_value=0.0)
        c,d=st.columns(2)
        yld=c.number_input("Produtividade (sc/ha)",min_value=0.0)
        margin=d.number_input("Margem-alvo (%)",min_value=0.0,value=20.0)
        save=st.form_submit_button("Salvar safra",use_container_width=True)
    if save and name and area>0 and cost>0 and yld>0:
        ex("""INSERT INTO seasons(name,crop,area_ha,cost_ha,yield_sc_ha,margin_pct,created_by)
              VALUES(:n,:c,:a,:co,:y,:m,:u)""",
           {"n":name,"c":crop,"a":area,"co":cost,"y":yld,"m":margin,"u":u["id"]})
        st.success("Safra salva."); st.rerun()
    for s in q("SELECT * FROM seasons ORDER BY id DESC"):
        prod,_,sold,_,bal,_=summary(s)
        st.markdown(f"<div class='card'><b>{s['name']} · {s['crop']}</b><br>{prod:,.0f} sc · Vendido {(sold/prod*100 if prod else 0):.1f}% · Livre {bal:,.0f} sc</div>",unsafe_allow_html=True)

elif pg=="🛒 Compras":
    st.subheader("Compras e compromissos")
    ss=q("SELECT id,name,crop FROM seasons ORDER BY id DESC")
    sm={"Nenhuma":None}|{f"{x['name']} · {x['crop']}":x["id"] for x in ss}
    with st.form("compra",clear_on_submit=True):
        desc=st.text_input("O que foi comprado?")
        cat=st.selectbox("Categoria",["Sementes","Fertilizantes","Defensivos","Máquinas","Custeio","Outro"])
        sup=st.text_input("Fornecedor")
        a,b=st.columns(2)
        val=a.number_input("Valor total (R$)",min_value=0.0)
        due=b.date_input("Vencimento")
        crop=st.selectbox("Pagar com",["Soja","Milho","Trigo","Canola","Caixa","Mais de uma"])
        season=st.selectbox("Safra relacionada",list(sm))
        save=st.form_submit_button("Salvar compra",use_container_width=True)
    if save and desc and val>0:
        ex("""INSERT INTO commitments(season_id,category,description,supplier,total_value,due_date,payment_crop,created_by)
              VALUES(:s,:c,:d,:f,:v,:dt,:p,:u)""",
           {"s":sm[season],"c":cat,"d":desc,"f":sup,"v":val,"dt":due,"p":crop,"u":u["id"]})
        st.success("Compra salva."); st.rerun()
    for x in q("SELECT * FROM commitments ORDER BY due_date"):
        protected=float(q("SELECT COALESCE(SUM(quantity_sc*price_sc),0) v FROM sales WHERE commitment_id=:i",{"i":x["id"]})[0]["v"])
        pct=min(protected/float(x["total_value"])*100,100) if x["total_value"] else 0
        st.markdown(f"<div class='card'><b>{x['description']}</b><br>{money(x['total_value'])} · vence {x['due_date']} · protegido {pct:.1f}%</div>",unsafe_allow_html=True)

elif pg=="💰 Vendas":
    st.subheader("Comercialização")
    ss=q("SELECT id,name,crop FROM seasons ORDER BY id DESC")
    if not ss: st.info("Cadastre uma safra primeiro.")
    else:
        sm={f"{x['name']} · {x['crop']}":x["id"] for x in ss}
        cc=q("SELECT id,description,due_date FROM commitments ORDER BY due_date")
        cm={"Venda livre":None}|{f"{x['description']} · {x['due_date']}":x["id"] for x in cc}
        with st.form("venda",clear_on_submit=True):
            season=st.selectbox("Safra",list(sm))
            a,b=st.columns(2)
            qty=a.number_input("Quantidade (sc)",min_value=0.0)
            price=b.number_input("Preço (R$/sc)",min_value=0.0)
            buyer=st.text_input("Comprador")
            obj=st.selectbox("Esta venda protege",list(cm))
            dt=st.date_input("Data",value=date.today())
            save=st.form_submit_button("Salvar venda",use_container_width=True)
        if save and qty>0 and price>0:
            ex("""INSERT INTO sales(season_id,sale_date,quantity_sc,price_sc,buyer,commitment_id,created_by)
                  VALUES(:s,:d,:q,:p,:b,:c,:u)""",
               {"s":sm[season],"d":dt,"q":qty,"p":price,"b":buyer,"c":cm[obj],"u":u["id"]})
            st.success("Venda salva."); st.rerun()
        for x in q("SELECT * FROM sales ORDER BY sale_date DESC,id DESC"):
            st.markdown(f"<div class='card'><b>{float(x['quantity_sc']):,.0f} sc a {money(x['price_sc'])}</b><br>{x['buyer'] or 'Comprador não informado'} · {x['sale_date']}</div>",unsafe_allow_html=True)

elif pg=="📈 Mercado":
    st.subheader("Mercado")
    with st.form("quote",clear_on_submit=True):
        crop=st.selectbox("Cultura",["Soja","Milho","Trigo","Canola"])
        price=st.number_input("Preço regional (R$/sc)",min_value=0.0)
        source=st.text_input("Fonte")
        save=st.form_submit_button("Salvar cotação",use_container_width=True)
    if save and price>0:
        ex("INSERT INTO quotes(crop,price_sc,source,created_by) VALUES(:c,:p,:s,:u)",
           {"c":crop,"p":price,"s":source,"u":u["id"]})
        st.success("Cotação salva."); st.rerun()

elif pg=="⚙️ Usuários":
    st.subheader("Usuários da família")
    with st.form("user",clear_on_submit=True):
        name=st.text_input("Nome")
        email=st.text_input("E-mail").lower()
        role=st.selectbox("Permissão",["operador","consulta","admin"])
        pw=st.text_input("Senha provisória",type="password")
        save=st.form_submit_button("Criar usuário",use_container_width=True)
    if save and name and email and len(pw)>=8:
        try:
            ex("INSERT INTO users(name,email,password_hash,role) VALUES(:n,:e,:p,:r)",
               {"n":name,"e":email,"p":hpw(pw),"r":role})
            st.success("Usuário criado."); st.rerun()
        except: st.error("E-mail já cadastrado ou dados inválidos.")
    st.dataframe(q("SELECT name,email,role,active FROM users ORDER BY id"),use_container_width=True,hide_index=True)
