# AGRIZA Enterprise 3.0

Base consolidada do AGRIZA para continuidade do desenvolvimento no Codex.

## Estado desta entrega

Esta versão preserva o aplicativo operacional recebido do repositório e organiza o projeto para análise técnica. Ela **não implementa automaticamente todas as melhorias futuras listadas no roadmap**. O objetivo é oferecer ao Codex uma fonte única, documentada e segura para evoluir o sistema sem depender do histórico do chat.

## Tecnologias

- Python 3.12
- Streamlit
- SQLAlchemy 2
- PostgreSQL no Render
- SQLite como fallback local
- pandas
- psycopg 3

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\\Scripts\\activate      # Windows
pip install -r requirements.txt
streamlit run app.py
```

Sem `DATABASE_URL`, o aplicativo cria/usa `agriza_local.db`. Para PostgreSQL, configure a variável de ambiente antes de iniciar.

## Deploy no Render

O arquivo `render.yaml` contém a configuração do serviço. Defina `DATABASE_URL` no painel do Render e não apague o banco existente.

## Documentação para o Codex

Comece por:

1. `CODEX_CONTEXT.md`
2. `ARCHITECTURE.md`
3. `ROADMAP.md`
4. `CHANGELOG.md`
5. `INSTALL.md`

## Arquivos legados

Versões históricas foram movidas para `legacy/`. Elas servem apenas para consulta e não devem ser importadas pelo aplicativo principal.
