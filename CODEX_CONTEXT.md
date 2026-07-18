# Contexto do projeto para o Codex

## Identidade

**AGRIZA** é um ERP rural/agropecuário em português do Brasil. O aplicativo é usado por produtores para registrar safras, produção, compras, contratos, máquinas, financiamentos, vendas, cotações regionais, usuários e backups.

A prioridade do produto é ser simples no celular, exigir poucos cliques e evitar lançamentos acidentais.

## Fonte de verdade

- Entrada principal: `app.py`
- Persistência e migrações: `core/database.py`
- Configuração do banco e interface: `core/config.py`
- Autenticação: `services/auth.py` e `core/security.py`
- Regras analíticas: `services/analytics.py`
- Interpretação de voz: `services/voice_purchases.py`, `services/voice_sales.py`
- Mercado regional: `market_prices.py`
- Deploy: `render.yaml`

Não use arquivos de `legacy/` como implementação ativa. Eles são históricos.

## Stack e ambiente

- Python 3.12
- Streamlit
- SQLAlchemy
- PostgreSQL em produção no Render
- SQLite local quando `DATABASE_URL` não está definida
- `DATABASE_URL` pode chegar como `postgres://` ou `postgresql://`; `core/config.py` normaliza para psycopg 3

## Regras de segurança e compatibilidade

1. Não apagar nem recriar o banco PostgreSQL de produção.
2. Toda mudança de esquema deve ser aditiva e idempotente.
3. Executar migrações por `CREATE TABLE IF NOT EXISTS` e `ALTER TABLE ... ADD COLUMN` condicionado à existência.
4. Não renomear/remover colunas existentes sem uma migração explícita e plano de rollback.
5. Consultas devem usar parâmetros SQL, nunca interpolação de dados do usuário.
6. Ações destrutivas exigem confirmação explícita.
7. Preservar perfis e permissões: `admin`, `operador`, `consulta`.
8. Não gravar compras ou vendas antes da etapa de revisão/confirmação.

## Módulos atuais

O aplicativo principal contém páginas para:

- Início
- Lançar
- Safras
- Compras
- Máquinas e financiamentos
- Vendas
- AgroIA
- Mercado regional
- Teste 7 dias
- Usuários
- Backup

## Entidades principais do banco

Criadas e mantidas por `core/database.py`:

- `users`
- `app_settings`
- `auth_sessions`
- `seasons`
- `machinery`
- `purchase_contracts`
- `commitments`
- `sales`
- `quotes`
- `payments`
- `activity_log`
- `pilot_feedback`

Antes de alterar qualquer entidade, leia o esquema real em `core/database.py` e procure todas as consultas no projeto.

## Regras de negócio importantes

### Safras

A safra concentra cultura, área, produtividade prevista, produção real, custo e saldo disponível. As análises são calculadas em `services/analytics.py`.

### Compras

Há compra à vista, compra com vencimento, contrato parcelado e máquina/financiamento. Compras comuns devem permanecer visualmente separadas de contratos e parcelas.

Fluxo desejado:

1. escolher o tipo;
2. preencher somente os campos aplicáveis;
3. revisar resumo;
4. confirmar e salvar, corrigir ou cancelar.

### Vendas

O sistema relaciona venda à safra, quantidade, preço por saca, comprador, data e compromisso. Antes de salvar, deve mostrar total e saldo restante.

### Mercado

Cotações regionais podem ser automáticas ou manuais. A fonte e a data devem permanecer visíveis. Não assumir que scraping externo sempre estará disponível.

### Autenticação

Há login persistente por sessão/cookie e tabela `auth_sessions`. Não remover esse comportamento sem testes completos de login, restauração e logout.

## Padrões de interface obrigatórios para próximas alterações

- Idioma: pt-BR.
- Moeda: real brasileiro.
- Datas visíveis: `DD/MM/AAAA`.
- Data e hora visíveis: `DD/MM/AAAA HH:MM`.
- Manter formato ISO apenas internamente no banco/API quando necessário.
- Interface mobile-first.
- Botões grandes e rótulos diretos.
- Mensagens claras após salvar, editar, cancelar ou excluir.
- Evitar formulários longos e campos irrelevantes.

## Pendências já aprovadas pelo proprietário

1. Padronizar todas as datas visíveis para dia/mês/ano.
2. Todo lançamento deve oferecer `Editar`, `Excluir` e `Cancelar`.
3. Exclusão deve exibir resumo do registro e confirmação adicional.
4. Cancelar não pode gravar nem alterar dados.
5. Edição deve carregar os dados atuais e preservar o que foi digitado.
6. Revisar todos os fluxos para evitar salvamento duplicado.
7. Evoluir a página AgroIA para chat integrado ao aplicativo.
8. No futuro, permitir consultas e lançamentos pelo chat, sempre com confirmação.
9. Evoluir financeiro, estoque, manutenção de máquinas, contratos e relatórios.

## Critérios mínimos para aceitar uma alteração

- `python -m compileall .` sem erros.
- Aplicativo inicia com SQLite local vazio.
- Aplicativo não perde compatibilidade com PostgreSQL existente.
- Login, logout e restauração de sessão continuam funcionando.
- Compra e venda não são salvas antes da confirmação.
- Alterações destrutivas possuem confirmação.
- Datas visíveis seguem pt-BR.
- Não há imports ativos apontando para `legacy/`.

## Estratégia recomendada de refatoração

O `app.py` é grande. Refatorar gradualmente, sem reescrever tudo de uma vez:

1. extrair componentes visuais reutilizáveis;
2. extrair repositórios/serviços por entidade;
3. criar camada de validação;
4. criar testes para regras puras;
5. somente depois separar páginas Streamlit.

Cada etapa deve manter o deploy funcional e o banco compatível.
