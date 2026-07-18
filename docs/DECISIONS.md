# Decisões técnicas registradas

## ADR-001 — PostgreSQL é a fonte de dados de produção

O banco existente no Render deve ser preservado. Migrações precisam ser aditivas e idempotentes.

## ADR-002 — Confirmação antes de escrita sensível

Compras, vendas e futuras ações via AgroIA devem gerar rascunho e resumo antes da gravação definitiva.

## ADR-003 — Datas em pt-BR na interface

A interface mostra `DD/MM/AAAA`; o banco pode continuar usando tipos de data nativos e formatos técnicos.

## ADR-004 — Refatoração incremental

A separação do monólito deve ocorrer por etapas pequenas, mantendo o aplicativo publicável em cada etapa.
