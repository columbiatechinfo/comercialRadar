# Como contribuir

## Branch

`master` é protegida: entra por PR, com CI verde. Nomeie a branch pelo assunto —
`feat/leitura-fachada`, `fix/numero-divergente`, `chore/gitleaks`.

## Commits — Conventional Commits

O prefixo não é enfeite: alimenta o CHANGELOG e decide se a versão sobe em patch,
minor ou major.

| Prefixo | Quando | Versão |
|---|---|---|
| `feat:` | Funcionalidade nova | minor |
| `fix:` | Corrige comportamento errado | patch |
| `refactor:` | Muda estrutura sem mudar comportamento | patch |
| `docs:` | Só documentação | — |
| `chore:` | Infra, dependência, configuração | — |
| `test:` | Só teste | — |

Escopo entre parênteses quando ajudar: `feat(fachada):`, `fix(quadras):`.

Quebra de compatibilidade leva `!` e um rodapé `BREAKING CHANGE:` — sobe major.

## O que um bom commit tem no corpo

**O porquê, não o quê.** O diff já mostra o quê.

Quando a mudança veio de uma medição, o número entra no corpo. "Tirando o endereço
do prompt o modelo mudou 3 dos 5 números" é o que impede alguém de reverter a
mudança daqui a seis meses por parecer arbitrária.

## PR

Use o template. As três perguntas obrigatórias:

1. O que muda para quem usa?
2. Como foi verificado? — comando executado, consulta feita, lote rodado. "Testei
   localmente" não é verificação.
3. O que isto quebra sob carga? — a premissa do projeto é uso em massa.

## Antes de abrir

```bash
node scripts/painel.mjs --check      # estado.json válido
python -m compileall -q .            # nada com erro de sintaxe
```

Se a mudança toca o banco, diga no PR **qual tabela** e se a alteração é
idempotente. As tabelas nascem por `CREATE TABLE IF NOT EXISTS` no Python — não há
migration versionada, e por isso a descrição no PR é o único registro.

## Segredo

Nunca commite `.env`, chave de API, token ou senha. O gitleaks roda no
pre-commit e no CI, mas ele é a última linha, não a primeira.

Se um segredo escapar: **rotacione**. Reescrever o histórico não resolve — quem
clonou já tem.
