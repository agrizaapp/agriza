# Roadmap

## Prioridade 1 — consistência e segurança de uso

- Padronizar datas para `DD/MM/AAAA` e `DD/MM/AAAA HH:MM` em toda a interface.
- Adicionar Editar, Excluir e Cancelar a todos os lançamentos aplicáveis.
- Confirmar exclusões com resumo do item.
- Impedir gravações duplicadas.
- Auditar compras e vendas para confirmar antes de salvar.
- Melhorar separação entre compras comuns, contratos, parcelas e financiamentos.

## Prioridade 2 — AgroIA / chat interno

- Interface de chat persistente no aplicativo.
- Conexão segura a provedor de IA por variável de ambiente.
- Ferramentas somente leitura para consultar safras, compras, vendas e parcelas.
- Ações de escrita somente via rascunho + card de confirmação.
- Registro em `activity_log` de consultas e ações relevantes.
- Proteção de dados e controle por perfil.

## Prioridade 3 — gestão operacional

- Fluxo de caixa completo.
- Contas a pagar e receber.
- Estoque de insumos e grãos.
- Manutenção preventiva e custos por máquina.
- Contratos de comercialização.
- Custos reais por talhão/safra.
- Relatórios e exportações.

## Prioridade 4 — qualidade de engenharia

- Testes unitários das regras analíticas.
- Testes de integração do banco.
- Validação de migrations em PostgreSQL.
- Modularização progressiva de `app.py`.
- CI para compile/test/lint.
