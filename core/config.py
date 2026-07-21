import os
import streamlit as st
from sqlalchemy import create_engine

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///agriza_local.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
IS_POSTGRES = engine.dialect.name == "postgresql"

def apply_page_config():
    st.set_page_config(
        page_title="AGRIZA • Gestão Rural",
        page_icon="🌱",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

def apply_global_style():
    st.markdown(
        """
        <style>
        #MainMenu, footer {visibility:hidden;}
        html,body,[class*="css"]{font-size:1.06rem;}
        .block-container{max-width:1120px;padding-top:3.25rem;padding-bottom:5rem}
        .brand{display:flex;align-items:center;min-height:3.2rem;font-size:2rem;font-weight:850;line-height:1.25;letter-spacing:-.04em;padding-top:.25rem;overflow:visible}
        .subbrand{opacity:.7;margin-top:.05rem;margin-bottom:.9rem;line-height:1.4}
        .card{border:1px solid rgba(60,90,45,.16);border-radius:18px;padding:1rem;margin:.55rem 0;background:rgba(255,255,255,.72)}
        .positive{border-left:6px solid #4F7D32}
        .warning{border-left:6px solid #D39B2A}
        .danger{border-left:6px solid #B74B45}
        div[data-testid="stMetric"]{border:1px solid rgba(60,90,45,.14);border-radius:16px;padding:.75rem;background:rgba(255,255,255,.65);min-width:0;overflow:hidden}
        div[data-testid="stMetricValue"]{font-size:clamp(1.2rem,2.25vw,1.85rem)!important;line-height:1.2!important;overflow-wrap:anywhere;word-break:break-word}
        div[data-testid="stMetricLabel"]{line-height:1.25!important;white-space:normal!important}
        div[data-testid="stNumberInput"] input{font-variant-numeric:tabular-nums;font-size:1rem!important}
        div[data-testid="stDataFrame"]{font-variant-numeric:tabular-nums}
        .stButton button,.stFormSubmitButton button{min-height:3.6rem;border-radius:15px;font-size:1.14rem;font-weight:750;padding:.75rem 1rem;background:#76B947!important;color:#17330E!important;border:1px solid #578D32!important}
        .stButton button:hover,.stFormSubmitButton button:hover{background:#629D3B!important;color:#102509!important;border-color:#3F7428!important}
        .stButton button[kind="primary"],.stFormSubmitButton button[kind="primary"]{background:#3E7D2B!important;color:#FFFFFF!important;border-color:#2E6320!important;box-shadow:inset 0 0 0 2px rgba(255,255,255,.16)!important}
        .stButton button[kind="primary"]:hover,.stFormSubmitButton button[kind="primary"]:hover{background:#2E6320!important;color:#FFFFFF!important}
        [class*="st-key-logout_top"] button{min-height:2.65rem!important;background:#EEF1ED!important;color:#465244!important;border:1px solid #B8C2B5!important;font-size:.95rem!important}
        [class*="st-key-logout_top"] button:hover{background:#E0E5DE!important;color:#273226!important;border-color:#96A291!important}
        [class*="st-key-quote_summary"] div[data-testid="stMetricValue"]{font-size:clamp(1.08rem,1.7vw,1.35rem)!important}
        [class*="st-key-info_"] button{min-height:1.65rem!important;min-width:1.8rem!important;padding:.1rem .35rem!important;border-radius:999px!important;background:#E9EEE7!important;color:#50604C!important;border:1px solid #C6D0C2!important;font-size:.78rem!important}
        [class*="st-key-info_"] button:hover{background:#DDE6D9!important;color:#33402F!important;border-color:#AEBBA8!important}
        @media(max-width:700px){
            .block-container{padding-top:2.8rem;padding-left:.65rem;padding-right:.65rem}
            .brand{font-size:1.65rem}
            div[data-testid="column"]{min-width:100%!important}
            [class*="st-key-top_identity_bar"] div[data-testid="stHorizontalBlock"]{
                flex-wrap:nowrap!important;
                align-items:center!important;
                gap:.5rem!important;
            }
            [class*="st-key-top_identity_bar"] div[data-testid="stColumn"]{
                min-width:0!important;
            }
            [class*="st-key-top_identity_bar"] div[data-testid="stColumn"]:first-child{
                width:calc(68% - .25rem)!important;
                flex:0 0 calc(68% - .25rem)!important;
            }
            [class*="st-key-top_identity_bar"] div[data-testid="stColumn"]:last-child{
                width:calc(32% - .25rem)!important;
                flex:0 0 calc(32% - .25rem)!important;
            }
            [class*="st-key-top_identity_bar"] [class*="st-key-logout_top"] button{
                min-height:2.85rem!important;
                font-size:.86rem!important;
                padding:.45rem .5rem!important;
            }
            .stButton button,.stFormSubmitButton button{
                min-height:4.35rem;
                font-size:1.2rem;
                border-radius:18px;
                padding:.9rem 1rem;
                touch-action:manipulation;
            }
            /* O menu permanece em duas colunas no celular; os demais layouts empilham. */
            [class*="st-key-main_menu_grid"] div[data-testid="stColumn"]{
                min-width:0!important;
                width:calc(50% - .2rem)!important;
                flex:1 1 0!important;
            }
            div[data-testid="stExpander"] summary{
                min-height:4rem;
                font-size:1.08rem;
                font-weight:800;
                padding:.8rem 1rem;
            }
            div[data-baseweb="select"]>div{
                min-height:4rem;
                font-size:1.08rem;
            }
            div[data-testid="stMetricValue"]{font-size:1.35rem!important}
            [class*="st-key-quote_summary"] div[data-testid="stMetricValue"]{font-size:1.15rem!important}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
