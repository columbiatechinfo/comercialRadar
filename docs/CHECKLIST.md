# Checklist — ComercialRadar

> Perfil `saas-multi-cliente`. Gerado em 12/08/2026 pela `/projeto-novo`.
> A fonte da verdade é [`estado.json`](estado.json); este arquivo é a leitura
> humana dele. Nada é marcado `ok` sem verificação — consulta no banco, comando
> executado ou arquivo lido.

| | Requisito | Estado | O que falta |
|---|---|---|---|
| 1 | **PRD** | ✅ ok | [PRD.md](PRD.md) com "fora de escopo" preenchido pelo dono do produto |
| 2 | **UML versionada** | ✅ ok | [ARQUITETURA.md](ARQUITETURA.md) — entidades e o fluxo crítico em mermaid |
| 3 | **Repo + docs padrão** | 🟡 parcial | Repositório e docs existem; falta proteger a `master` (PR obrigatório, CI verde) |
| 4 | **Matriz RBAC** | 🟡 parcial | [RBAC.md](RBAC.md) é **proposta**. Não há login, papel nem usuário no código |
| 5 | **`tenant_id` + RLS** | ❌ pendente | Nenhuma tabela tem `tenant_id`. Nenhuma política de RLS existe. É o maior buraco entre o perfil e o código |
| 6 | **Segredos** | 🟡 parcial | `.env` fora do git e `.env.example` existem. Falta gitleaks no pre-commit e no CI, e **falta rotacionar a chave do Google exposta no histórico** (commit `1c7f081`) |
| 7 | **Módulos + flags** | ❌ pendente | [MODULOS.md](MODULOS.md) cataloga 10 módulos; `tenant_features` não existe e tudo está sempre ligado |
| 8 | **Botão de erro** | ❌ pendente | Não há captura de erro do usuário nem log correlacionado por requisição |
| 9 | **Testes** | ❌ pendente | Não há suíte. A skill `leitura-fachada-cadastral` traz `selftest.py` próprio, que cobre o validador dela — não este sistema |
| 10 | **Backup** | ❌ pendente | Sem rotina verificada. O banco tem 60 GB, dos quais 6,3 GB são bytes de imagem |
| 11 | **Borda (TLS · WAF · rate limit)** | ❌ pendente | Roda em `127.0.0.1:8765`, HTTP puro, sem autenticação. Aceitável enquanto for local; bloqueia qualquer exposição |
| 12 | **LGPD** | 🟡 parcial | Há dado pessoal: sócios da Receita e nomes em fachada. A skill de fachada já proíbe transcrever nome de morador. Falta avaliação formal |

---

## A ordem que importa

Os pendentes não têm o mesmo peso. Esta é a ordem em que atacá-los custa menos:

**1 · `tenant_id` + RLS (req. 5).** Antes de a base crescer. Retrofitar coluna em
`pois` (26.482), `cadastro_cliente` (102.065) e nas anotações é barato hoje; cada
índice precisa ser recriado com `tenant_id` na frente, e isso só piora com volume.
Chama `/modelo-acesso`.

**2 · Rotação da chave exposta (req. 6).** É a única pendência que já causou dano
— a chave está no histórico público do git e não se apaga reescrevendo commit.
Rotacionar é a única correção real. Chama `/segredos`.

**3 · Backup verificado (req. 10).** 60 GB sem restauração testada. Backup que
nunca foi restaurado é esperança, não backup.

**4 · Testes (req. 9).** O sistema hoje é verificado rodando lote e olhando
galeria. Funciona para julgar leitura de imagem; não pega regressão em derivação
de oportunidade, que é onde os erros silenciosos moram.

**Borda (req. 11) só depois de 1.** Expor antes de existir isolamento é servir a
base de um cliente para o outro.

---

## Marcado `na`

Nenhum. No perfil `saas-multi-cliente` os doze requisitos se aplicam — a LGPD
inclusive, porque o sistema guarda sócios de CNPJ e imagens de fachada com pessoas.
