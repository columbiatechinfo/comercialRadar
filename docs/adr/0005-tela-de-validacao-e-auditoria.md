# ADR 0005 — A tela de validação e auditoria do POI

- **Data:** 23/08/2026
- **Estado:** aceita
- **Decisores:** Columbia Tech

---

## Contexto

`assets/modelo_frontend/` trouxe um modelo de tela pronto e bem especificado —
`tela_radar_comercial.zip`, com HTML, modelo de dados, DDL e dataset de exemplo.
É uma bancada de auditoria: lateral com fila e contagem por status, régua de
probabilidade por fonte, cabeçalho da âncora, abas por fonte e barra de decisão
com atalho de teclado.

Junto vieram dezoito PNGs — que **não são o design final**: são referências de
componente Flowbite, cada uma nomeada pela função que deve cumprir aqui (a
timeline para o fluxo de aprovações, os cards para a lateral, a tabela para os
POIs). Lista de compras de componentes, não maquete.

E duas skills de design a usar daqui em diante: `taste-skill` como camada de
gosto sempre ativa, `ui-ux-pro-max` como banco de referências sob demanda.

O modelo, porém, foi escrito para outro produto. Quatro pontos precisavam de
decisão antes de qualquer linha de código.

---

## Decisão

### 1 · O POI continua sendo a âncora

O modelo é ancorado em **ligação** — a matrícula da distribuidora — e cada fonte
externa é uma observação que casa com ela. O ComercialRadar é ancorado em
**POI**, e assim fica.

A ligação da concessionária passa a ser **mais uma fonte** que cruza com o POI,
ao lado de Receita, Google e Delivery. Quando houver base de ligações do cliente,
ela entra como aba; não vira o centro.

**Por quê:** o produto existe para *achar* ponto comercial. Quem tem a base de
ligações é o cliente, e nem todo cliente tem — inverter a âncora tornaria o
sistema dependente de um dado que não é nosso, e a busca que hoje é por nome de
estabelecimento passaria a exigir matrícula.

### 2 · Só as abas com dado real

Seis abas: **POI · Informações Google · Receita Federal · Redes Sociais ·
Delivery · IA das Imagens**.

Ficam **fora** por ora:

| Aba do modelo | Por que não |
|---|---|
| Energia | a ANEEL/BDGD foi descartada como fonte |
| Faturas emitidas, Adimplência, Hidrômetro | dado de faturamento do cliente, não nosso |
| Comentários da internet, Outras Fontes | ainda sem pipeline próprio |

**Por quê:** cinco abas vazias dizendo "sem fonte conectada" parecem sistema
quebrado, não sistema em construção. E o modelo já prevê a extensão certa —
campo novo entra em `radar.campo` e a aba nasce sem tocar no HTML. A tela lê
catálogo, não colunas fixas.

### 3 · Quatro saídas de decisão, não três

| Ação | Status | Quando |
|---|---|---|
| Aprovar | `APROVADO_USUARIO` | a evidência sustenta |
| A revisar | `A_REVISAR` | falta dado, mas não falta ir ao local |
| Visita de campo | `DIRECIONADO_CAMPO` | só o local resolve — **abre a pauta** |
| Rejeitar | `REJEITADO` | a evidência não sustenta, e sai da fila |

Substitui o trio do [RBAC](../RBAC.md) — aprovar, reprovar, devolver. O
"devolver" vira **A revisar**, e ganha-se **Visita de campo**.

**Por quê:** visita de campo não é rótulo, é pauta. O modelo traz `CHECK
ck_campo_tem_pauta` no banco recusando direcionamento sem dizer o que verificar —
*mandar a campo sem pauta é mandar de novo depois*. Cada item da pauta só vem
marcado se um fato do caso o justifica, e o painel imprime o porquê embaixo.

Duas exigências do RBAC continuam valendo e se somam: **reprovar exige motivo
escrito mais motivo de lista**, e a tabela de decisão é **append-only** — o
status vigente é derivado da última linha, nunca sobrescrito.

### 4 · Três marcas no cabeçalho

```
Cadastrae 360   ·   para <logo do cliente>              por A2L
```

A logo da empresa cliente entra ao lado do produto, não no lugar dele. Cada
usuário vê a marca da própria empresa — a imagem é do **tenant**, não do usuário.

**Por quê:** três papéis distintos — o produto, quem o usa, quem o fez. Fundir
os dois primeiros esconderia de quem é o sistema.

---

## O que isto NÃO decide

**A procedência do dado continua invisível fora do `root`.** O modelo imprime a
fonte de cada campo com naturalidade; o [RBAC](../RBAC.md) diz que só o `root`
enxerga procedência. Prevalece o RBAC: para `admin`, `supervisor` e `user` a
fonte fica embaçada, e a régua de probabilidade mostra o *peso* sem nomear a base.

**Quem processa e quem aprova são papéis diferentes.** `admin` executa mineração
e enriquecimento e distribui POIs; `supervisor` só decide sobre o que lhe foi
enviado; `user` só vê. A barra de decisão aparece para quem tem o POI atribuído.

---

## Consequências

- O DDL do zip (`radar_comercial_schema.sql`) é **referência**, não migração: as
  tabelas de decisão, pauta e catálogo de campos entram adaptadas ao schema
  `comercialradar`, com `tenant_id` como primeira coluna de todo índice.
- As duas skills de design passam a ser o caminho padrão de geração de front.
  Instalação pendente — ver `assets/modelo_frontend/GUIA_SETUP_SKILLS_E_LLM_LOCAL.md`.
- O [processo de resgate](../RESGATE-POI.md) alimenta esta tela: o que ele
  encontra chega aqui como evidência a validar.

---

## Instalação das skills — o que entrou e como reverter

Feito em 23/08/2026.

| Onde | O que |
|---|---|
| global (npm) | `ui-ux-pro-max-cli@2.15.0` — 23 pacotes, nenhum outro global alterado |
| `.agents/skills/` | `design-taste-frontend` (86 KB, um `SKILL.md`) |
| `.claude/skills/` | junção para o acima, mais **sete** vindas do `uipro`: `banner-design`, `brand`, `design`, `design-system`, `slides`, `ui-styling`, `ui-ux-pro-max` |

Ambas as pastas estão no `.gitignore`: reinstalam-se com um comando, e versionar
4,7 MB de instrução de terceiro dentro do repositório não faz sentido.

**Reverter:**

```bash
npm uninstall -g ui-ux-pro-max-cli
rm -rf .agents .claude/skills
```

### Uma correção ao que o guia diz

O `GUIA_SETUP_SKILLS_E_LLM_LOCAL.md` afirma que a `ui-ux-pro-max` "roda só um
`search.py` em Python stdlib, sem chamadas de rede". Auditado depois de
instalar: o `uipro init` traz **sete** skills, não uma, e duas delas alcançam o
mundo externo —

- `design-system/scripts/fetch-background.py` baixa imagens do Pexels;
- `ui-styling/scripts/shadcn_add.py` roda `npx shadcn@latest add` por
  `subprocess`.

Nenhuma das duas roda sozinha: só se uma skill for invocada e pedir. Mas a frase
do guia vale para o `search.py`, não para o pacote inteiro — e quem ler o guia
sem auditar vai acreditar em garantia mais larga do que a que existe.

A `taste-skill` é instrução pura: zero chamadas de rede ou execução, conferido.
