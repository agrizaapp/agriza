# Instalação e execução

## Requisitos

- Python 3.12
- pip
- PostgreSQL opcional

## Ambiente local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

No Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Banco

Sem variável de ambiente, o sistema usa:

```text
sqlite:///agriza_local.db
```

Para PostgreSQL:

```bash
export DATABASE_URL='postgresql://USUARIO:SENHA@HOST:5432/BANCO'
```

Nunca inclua credenciais reais no GitHub.

## Render

1. Conecte o repositório.
2. Use `render.yaml` ou configure um Web Service.
3. Adicione `DATABASE_URL` como variável secreta.
4. Faça deploy.
5. Verifique `/_stcore/health`.

## Validação rápida

```bash
python -m compileall -q .
streamlit run app.py
```
