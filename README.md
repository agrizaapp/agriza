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

## Menu otimizado para celular

O menu principal agora usa botões grandes, organizados em duas colunas no
computador e empilhados no celular. A área de toque foi ampliada para facilitar
o uso por pessoas com mãos grandes, inclusive nos botões de formulários.

## Revisão completa de dependências internas

Foram conferidos todos os nomes utilizados no `app.py`. A revisão incluiu os
imports de datas, pandas, arquivos ZIP, JSON, buffer em memória, conexão com o
banco e geração/verificação de senhas. Isso elimina os erros sequenciais de
`NameError` que apareciam nas páginas de mercado, usuários e backup.


## Produção realizada após a colheita

A safra agora guarda separadamente a produção estimada e a produção realmente
colhida. Ao registrar o total colhido, o sistema calcula automaticamente:

- produtividade real em sacas por hectare;
- diferença em sacas e em percentual;
- classificação: abaixo, dentro ou acima da estimativa;
- motivo principal e uma observação opcional.

Quando a produção fica abaixo do esperado, o formulário apresenta motivos
simples como ano seco, excesso de chuva, geada, pragas, doenças e perdas na
colheita. Os dados antigos são preservados por migração automática.


## Correção da aba Compras

A gravação de compras não força mais um segundo `st.rerun()` após o envio do
formulário. Em algumas conexões móveis ou instâncias gratuitas esse segundo
recarregamento podia interromper a sessão e devolver o usuário à tela de login.
A compra agora é confirmada, a sessão permanece aberta e a lista é atualizada
na própria execução do formulário. Também foi adicionado tratamento de erro
para mostrar a falha real sem encerrar a sessão.


## Edição de custos e vendas por voz

- Cada safra ganhou uma opção simples para corrigir o custo por hectare.
- O custo total estimado é recalculado imediatamente.
- A aba Vendas ganhou “Lançamento rápido por voz”.
- No celular, o agricultor usa o microfone do próprio teclado e dita uma frase.
- O AGRIZA interpreta quantidade, preço, cultura/safra, comprador e data.
- Antes de salvar, todos os campos aparecem para conferência e correção.
- O lançamento manual continua disponível.


## Compras por voz

A aba Compras ganhou um lançamento rápido por voz. No celular, o usuário pode
usar o microfone do próprio teclado e ditar uma frase curta. O AGRIZA tenta
identificar:

- produto ou serviço comprado;
- categoria;
- fornecedor;
- valor total;
- vencimento;
- cultura usada para pagamento;
- safra relacionada.

Todos os dados aparecem para conferência antes da gravação. O formulário manual
permanece disponível. A correção que mantém a sessão aberta ao salvar compras e
pagamentos também foi mantida nesta versão.


## Edição de compras

Cada compra agora pode ser corrigida depois do lançamento. A opção
“Editar esta compra” permite alterar a data da compra, vencimento, descrição,
categoria, fornecedor, valor, forma de pagamento, safra relacionada e
observações. A data da compra fica armazenada separadamente do vencimento.


## Manter conectado

A tela de login ganhou a opção “Manter conectado neste dispositivo por 30
dias”. Quando marcada, o AGRIZA grava no navegador um identificador aleatório e
armazena somente o resumo criptográfico desse identificador no banco. A senha
não é salva no navegador.

O botão “Sair” revoga a sessão persistente imediatamente. Para segurança, essa
opção deve ser usada apenas em celular ou computador pessoal, não em
dispositivos compartilhados.


## Contratos e compras parceladas

A aba Compras permite criar um contrato com várias parcelas. Cada parcela possui
vencimento, valor e cultura prevista para pagamento. Há um modelo preenchido
para a plantadeira de R$ 405.000, com uma parcela de trigo e três parcelas de
soja. A soma das parcelas é validada antes do salvamento.

## Login lembrado

A autorização do dispositivo passa a valer por até 1 ano. A senha não é
armazenada no navegador; o sistema usa um token revogável. O botão Sair remove
a autorização daquele dispositivo.

## Biometria

A estrutura de login persistente está pronta para receber passkeys/WebAuthn.
A biometria não foi simulada nem substituída por armazenamento de senha, pois
isso seria inseguro. A implementação de digital/Face ID exige um componente
WebAuthn próprio no navegador e será uma etapa separada.


## Verificação da versão v8

Depois do deploy, o topo da tela deve mostrar:

`Versão ativa: AGRIZA v8 · login lembrado + compras parceladas`

Na aba Compras, o formulário `COMPRA PARCELADA / CONTRATO` aparece aberto.
Se esses textos não aparecerem, o Render ainda está executando uma versão
anterior ou o arquivo app.py não foi substituído na raiz do repositório.
