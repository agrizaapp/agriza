# Arquitetura

## Visão geral

O AGRIZA é atualmente um monólito modular em Streamlit. `app.py` controla navegação, estado da sessão e formulários. As pastas `core/`, `services/` e `modules/` concentram infraestrutura e regras reutilizáveis.

```text
app.py
├── core/config.py       configuração, engine e estilo
├── core/database.py     esquema, migrações e helpers SQL
├── core/security.py     hash e verificação de senha
├── core/utils.py        formatação monetária e numérica
├── services/auth.py     sessão persistente e autenticação
├── services/analytics.py regras de safra e indicadores
├── services/voice_*.py  interpretação de linguagem natural
├── market_prices.py     coleta/normalização de mercado
└── modules/registry.py  catálogo de módulos/perfis
```

## Persistência

`core/config.py` cria uma `engine` SQLAlchemy. O banco padrão local é SQLite. Produção usa PostgreSQL por `DATABASE_URL`.

`core/database.py` oferece helpers:

- `q`: consulta com retorno de linhas
- `scalar`: valor único
- `ex`: execução sem retorno
- `insert_id`: inserção com ID
- `init_db`: criação/migração idempotente
- `log_action`: auditoria

## Estado de interface

O Streamlit usa `st.session_state` para página atual, rascunhos e confirmações. Ao alterar fluxos, garanta chaves únicas por formulário e remova rascunhos somente após confirmação ou cancelamento.

## Dívida técnica conhecida

- `app.py` concentra responsabilidade excessiva.
- SQL e interface ainda estão próximos.
- Cobertura automatizada de testes é inexistente.
- Datas e ações CRUD ainda não são padronizadas em todos os módulos.
- A página AgroIA ainda não é um chat completo conectado a um provedor de LLM.

## Direção futura

Adotar gradualmente:

```text
pages/          páginas Streamlit
components/     cards, diálogos e formulários
repositories/   acesso ao banco
services/       regras de negócio
models/         tipos e validações
 tests/          testes unitários e de integração
```

Não executar uma migração estrutural completa em um único commit.
