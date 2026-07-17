# AGRIZA v10 Enterprise

Pacote consolidado para substituir o projeto atual no GitHub.

## Recursos incluídos

- login persistente por até 1 ano;
- compras parceladas e contratos;
- lançamento por voz melhorado para compras e vendas;
- cadastro de máquinas e implementos;
- Assistente AgroIA com alertas financeiros;
- safras, produção real, compras, vendas, mercado, usuários e backup;
- migrações automáticas do banco PostgreSQL.

## Como publicar corretamente no GitHub

1. Abra o ZIP no computador.
2. Entre na pasta extraída.
3. Selecione os arquivos e pastas que estão dentro dela:
   `.streamlit`, `core`, `modules`, `services`, `app.py`,
   `requirements.txt`, `render.yaml` e `README.md`.
4. Envie esses itens para a raiz do repositório.
5. Confirme a substituição do `app.py` antigo.
6. Não envie a pasta `agriza_v10_enterprise` inteira para dentro do repositório.
7. Apague as pastas antigas `agriza_v8_correcao_login_compras` e semelhantes.

Depois do deploy, o topo deve mostrar:

`Versão ativa: AGRIZA v10 Enterprise`

## Observação sobre biometria

A versão 10 mantém o dispositivo conectado com token seguro. Digital, Face ID
e Windows Hello exigem uma integração WebAuthn/passkey própria. A senha não é
armazenada no navegador pelo AGRIZA.


## Correção v10.1

O editor de parcelas em grade (`st.data_editor`) foi removido do formulário de
contratos. Em alguns navegadores ele causava o erro React `removeChild`.
Agora cada parcela utiliza campos individuais de vencimento, valor e cultura,
o que é mais estável em computador e celular.


## Versão 10.2 — fluxo intuitivo

Foi criada uma página própria chamada `Máquinas e financiamentos`.
Nela existe uma aba `Cadastrar máquina financiada`, com um exemplo pronto da
plantadeira e as quatro parcelas já preenchidas. O cadastro da máquina, do
contrato e das parcelas ocorre em um único botão.


## v10.3
Cards de confirmação antes de salvar compras, vendas e máquinas financiadas.
