# AGRIZA Modular v1

Versão reorganizada para reduzir a necessidade de trocar o sistema inteiro a cada evolução.

## Estrutura

- `app.py`: entrada e interface atual.
- `core/config.py`: configuração do Streamlit e conexão.
- `core/database.py`: banco, tabelas e migrações.
- `core/security.py`: senhas.
- `core/utils.py`: formatação.
- `services/analytics.py`: cálculos e recomendações AgroIA.
- `services/auth.py`: configuração inicial e administrador.
- `modules/registry.py`: catálogo de módulos e permissões.

## Evolução futura

As próximas funções podem ser adicionadas como arquivos separados dentro de
`modules/` e `services/`. Assim, na maioria das atualizações, será necessário
substituir somente o arquivo do módulo alterado.


## Ajuste visual da tela inicial

O cabeçalho recebeu mais espaço superior, altura mínima e espaçamento próprio.
Isso evita que o nome AGRIZA e o ícone sejam cortados em computador e celular.
