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
        .block-container{max-width:1120px;padding-top:3.25rem;padding-bottom:5rem}
        .brand{display:flex;align-items:center;min-height:3.2rem;font-size:2rem;font-weight:850;line-height:1.25;letter-spacing:-.04em;padding-top:.25rem;overflow:visible}
        .subbrand{opacity:.7;margin-top:.05rem;margin-bottom:.9rem;line-height:1.4}
        .card{border:1px solid rgba(60,90,45,.16);border-radius:18px;padding:1rem;margin:.55rem 0;background:rgba(255,255,255,.72)}
        .positive{border-left:6px solid #4F7D32}
        .warning{border-left:6px solid #D39B2A}
        .danger{border-left:6px solid #B74B45}
        div[data-testid="stMetric"]{border:1px solid rgba(60,90,45,.14);border-radius:16px;padding:.75rem;background:rgba(255,255,255,.65)}
        .stButton button,.stFormSubmitButton button{min-height:3.6rem;border-radius:15px;font-size:1.02rem;font-weight:750;padding:.75rem 1rem}
        /* Somente os botões do menu principal: mais compactos, sem alterar ações. */
        [class*="st-key-nav_"] button{
            min-height:1.9rem!important;
            border-radius:10px!important;
            font-size:.86rem!important;
            padding:.28rem .45rem!important;
        }
        @media(max-width:700px){
            .block-container{padding-top:2.8rem;padding-left:.65rem;padding-right:.65rem}
            .brand{font-size:1.65rem}
            div[data-testid="column"]{min-width:100%!important}
            .stButton button,.stFormSubmitButton button{
                min-height:4.35rem;
                font-size:1.15rem;
                border-radius:18px;
                padding:.9rem 1rem;
                touch-action:manipulation;
            }
            [class*="st-key-nav_"] button{
                min-height:2.15rem!important;
                border-radius:10px!important;
                font-size:.82rem!important;
                line-height:1.1!important;
                padding:.28rem .35rem!important;
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
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
