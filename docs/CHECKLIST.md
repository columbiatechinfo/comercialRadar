# Checklist — ComercialRadar

> Perfil `saas-multi-cliente`. Criado em 12/08/2026 pela `/projeto-novo`,
> **remedido em 24/08/2026**. A fonte da verdade é [`estado.json`](estado.json);
> este arquivo é a leitura humana dele. Nada é marcado `ok` sem verificação —
> consulta no banco, comando executado ou arquivo lido.

| | Requisito | Estado | O que falta |
|---|---|---|---|
| 1 | **PRD** | ✅ ok | [PRD.md](PRD.md) com "fora de escopo" preenchido pelo dono do produto |
| 2 | **UML versionada** | ✅ ok | [ARQUITETURA.md](ARQUITETURA.md) — entidades e o fluxo crítico em mermaid |
| 3 | **Repo + docs padrão** | 🟡 parcial | Repositório e docs existem; falta proteger a `master` (PR obrigatório, CI verde) |
| 4 | **Matriz RBAC** | ✅ ok | Existe login, papel e usuário: 48 usuários em 4 níveis, 42 empresas, `auth.py` resolve o tenant da requisição, 45 rotas autenticadas e nenhuma sem auth |
| 5 | **`tenant_id` + RLS** | ✅ ok | 28 tabelas com `tenant_id`, **todas** com RLS, policy e gatilho (migração 0029). 54 índices com `tenant_id` na primeira coluna. `test_isolamento_tenant.py` cobra a **regra**, não a lista |
| 6 | **Segredos** | 🟡 parcial | Chave do Google **revogada e confirmada morta**; três chaves novas separadas por função; gitleaks no pre-commit e no CI. **Aberto em 24/08:** 100 credenciais de proxy Webshare no histórico desde o commit inicial — desrastreadas hoje, rotação pendente |
| 7 | **Módulos + flags** | ❌ pendente | [MODULOS.md](MODULOS.md) cataloga 12 módulos; `tenant_features` não existe e tudo está sempre ligado |
| 8 | **Botão de erro** | ❌ pendente | Não há captura de erro do usuário nem log correlacionado por requisição |
| 9 | **Testes** | ✅ ok | 266 coletados, **231 passando e 35 pulados** (medido em 24/08). Mais `prova_ponta_a_ponta.py` (**49/49**) e `prova_carga_chat.py` |
| 10 | **Backup** | ✅ ok | Dump diário do banco do produto (31 MB) + espelho do Storage com `link-dest`, 14 dias de retenção, cron das 03:10. **Restauração validada** em 12/08: 9 tabelas conferem e o schema `auth` volta junto |
| 11 | **Borda (TLS · WAF · rate limit)** | ❌ pendente | Roda em `127.0.0.1:8765`, HTTP puro. Aceitável enquanto for local; bloqueia qualquer exposição |
| 12 | **LGPD** | 🟡 parcial | Há dado pessoal: sócios da Receita e nomes em fachada. A skill de fachada proíbe transcrever nome de morador e o Cadastur não grava linha de pessoa física — só o total. Falta avaliação formal |

---

## A ordem que importa

**1 · Módulos + flags (req. 7).** Doze módulos sempre ligados, para todas as 42
empresas. `leitura_fachada` a US$ 0,017/POI são US$ 356 nas fachadas já
capturadas: ligar para um cliente novo sem que ele saiba é gastar o dinheiro
dele. A flag precisa morar em tabela — constante no código significa deploy para
ativar módulo.

**2 · Botão de erro (req. 8).** Hoje um erro na tela do usuário só existe se
alguém contar. Sem log correlacionado por requisição, o relato chega sem o
rastro que o explicaria.

**3 · Proteção da `master` (req. 3).** PR obrigatório e CI verde. É o que teria
evitado o estado em que este repositório passou o mês: trabalho real num branch,
`master` um mês atrás, e 19 commits sem enviar.

**4 · Rotação dos proxies Webshare (req. 6).** 100 credenciais
`ip:porta:usuario:senha` estão no histórico do git desde o commit inicial. O
arquivo saiu do rastreamento em 24/08, mas o histórico já foi enviado —
reescrever commit não apaga credencial exposta. Rotacionar no painel da Webshare
é a única correção real.

**5 · Avaliação formal de LGPD (req. 12).** O sistema guarda sócios de CNPJ e
imagens de fachada com pessoas. As decisões pontuais já foram bem tomadas —
guia de turismo é contado e não guardado, a skill proíbe transcrever nome de
morador —, mas nunca houve o inventário completo.

**Borda (req. 11) por último.** Agora que o isolamento está completo ela deixou
de estar bloqueada; segue pendente porque expor um serviço que roda em
`127.0.0.1` sem TLS nem rate limit é decisão de negócio, não de código.

---

## Fechados em 24/08/2026

- **RLS completa (req. 5).** As quatro tabelas que nasceram sem política ganharam
  policy, gatilho e índice pela migração
  [`0029`](../migrations/0029_rls_nas_quatro_que_ficaram_de_fora.sql). O teste
  passou a cobrar a regra geral, então a quinta tabela sem política reprova a
  suíte em vez de esperar a próxima auditoria.
- **As 25.432 linhas não versionadas (req. 3).** 30+ módulos, 2 skills e 3
  documentos entraram no repositório. A `DOCUMENTACAO.md` deixou de apontar para
  arquivos que o repositório não tinha.

**Ficou registrado, sem ação:** 66 registros de `auditoria` têm `tenant_id` nulo
e nenhum papel os enxerga. São anteriores ao ajuste. **Não foram
retro-atribuídos** — trilha de auditoria editada depois do fato vale menos que
trilha incompleta.

## Marcado `na`

Nenhum. No perfil `saas-multi-cliente` os doze requisitos se aplicam — a LGPD
inclusive, porque o sistema guarda sócios de CNPJ e imagens de fachada com pessoas.
