# Checklist — ComercialRadar

> Perfil `saas-multi-cliente`. Criado em 12/08/2026 pela `/projeto-novo`,
> **remedido em 24/08/2026**. A fonte da verdade é [`estado.json`](estado.json);
> este arquivo é a leitura humana dele. Nada é marcado `ok` sem verificação —
> consulta no banco, comando executado ou arquivo lido.

| | Requisito | Estado | O que falta |
|---|---|---|---|
| 1 | **PRD** | ✅ ok | [PRD.md](PRD.md) com "fora de escopo" preenchido pelo dono do produto |
| 2 | **UML versionada** | ✅ ok | [ARQUITETURA.md](ARQUITETURA.md) — entidades e o fluxo crítico em mermaid |
| 3 | **Repo + docs padrão** | 🟡 parcial | Repositório e docs existem; falta proteger a `master` (PR obrigatório, CI verde). **E há 25.432 linhas nunca commitadas** — ver abaixo |
| 4 | **Matriz RBAC** | ✅ ok | Existe login, papel e usuário: 48 usuários em 4 níveis, 42 empresas, `auth.py` resolve o tenant da requisição, 45 rotas autenticadas e nenhuma sem auth |
| 5 | **`tenant_id` + RLS** | 🟡 parcial | 27 tabelas com `tenant_id`, 24 com RLS e policy, 52 índices com `tenant_id` na primeira coluna. **4 tabelas ficaram de fora** do passe: `atribuicao_divergente`, `fachada_triagem`, `foto_maps_triagem`, `ifood_merchant` |
| 6 | **Segredos** | ✅ ok | Chave exposta **revogada e confirmada morta**; três chaves novas separadas por função; gitleaks no pre-commit e no CI; `.env.example` conferido contra o código |
| 7 | **Módulos + flags** | ❌ pendente | [MODULOS.md](MODULOS.md) cataloga 12 módulos; `tenant_features` não existe e tudo está sempre ligado |
| 8 | **Botão de erro** | ❌ pendente | Não há captura de erro do usuário nem log correlacionado por requisição |
| 9 | **Testes** | ✅ ok | 264 coletados, **229 passando e 35 pulados** (medido em 24/08). Mais `prova_ponta_a_ponta.py` (49 asserções) e `prova_carga_chat.py` |
| 10 | **Backup** | ✅ ok | Dump diário do banco do produto (31 MB) + espelho do Storage com `link-dest`, 14 dias de retenção, cron das 03:10. **Restauração validada** em 12/08: 9 tabelas conferem e o schema `auth` volta junto |
| 11 | **Borda (TLS · WAF · rate limit)** | ❌ pendente | Roda em `127.0.0.1:8765`, HTTP puro. Aceitável enquanto for local; bloqueia qualquer exposição |
| 12 | **LGPD** | 🟡 parcial | Há dado pessoal: sócios da Receita e nomes em fachada. A skill de fachada proíbe transcrever nome de morador e o Cadastur não grava linha de pessoa física — só o total. Falta avaliação formal |

---

## A ordem que importa

**1 · As 25.432 linhas não versionadas (req. 3).** É a única pendência em que uma
falha de disco custa trabalho, não tempo. São 30+ módulos, 2 skills e 3 documentos
que existem só nesta máquina — entre eles `chat_api.py`, `leitura_fachada.py`,
`extracao_estadual.py`, `tratamento_cnpj.py` e a família iFood. Não estão no
`.gitignore`: nunca foram adicionados. Documentos que a `DOCUMENTACAO.md` já
referencia (`docs/RESGATE-POI.md`, `docs/PROCESSO-OPORTUNIDADE.md`, o ADR 0005)
estão nesse grupo — a doc aponta para arquivos que o repositório não tem.

**2 · As 4 tabelas sem RLS (req. 5).** São exatamente as criadas *depois* do passe
de 12/08 — o padrão é claro: tabela nova não herda a política. Enquanto não
tiverem, qualquer papel enxerga a linha de qualquer empresa nelas. Chama
`/modelo-acesso`.

**3 · Módulos + flags (req. 7).** Doze módulos sempre ligados. `leitura_fachada` a
US$ 0,017/POI são US$ 356 nas fachadas já capturadas: ligar para um cliente novo
sem que ele saiba é gastar o dinheiro dele.

**4 · Botão de erro (req. 8).** Hoje um erro na tela do usuário só existe se
alguém contar.

**Borda (req. 11) só depois de 2.** Expor antes de o isolamento estar completo é
servir a base de um cliente para o outro.

---

## Marcado `na`

Nenhum. No perfil `saas-multi-cliente` os doze requisitos se aplicam — a LGPD
inclusive, porque o sistema guarda sócios de CNPJ e imagens de fachada com pessoas.
