# Política de segurança

## Reportar uma vulnerabilidade

Envie para **columbiatechinfo@gmail.com** com o assunto `[SEGURANÇA] comercialRadar`.

Inclua o que permitir reproduzir: passos, requisição, versão ou commit. Se houver
dado de cliente envolvido, **descreva sem anexar** — não mande o dado.

Resposta em até **3 dias úteis**. Correção conforme a gravidade; críticas primeiro.

Não abra issue pública para vulnerabilidade. Issue é registro aberto, e o aviso
chega ao atacante junto com o aviso a nós.

## Escopo

Vale este repositório e o serviço que ele sobe. Não valem os sistemas de terceiros
que ele consome (Google, IBGE, Receita Federal, OSM, Overture, OpenAI) — reporte
a eles diretamente.

## Dado sensível que o sistema guarda

Quem for avaliar precisa saber o que está em jogo:

- **Sócios de empresas** — nome e qualificação, vindos da base pública da Receita
  Federal.
- **Imagens de fachada** — capturas de Street View que podem conter pessoas e
  placas de veículo. A skill de leitura proíbe transcrever nome de morador,
  telefone particular, placa e texto manuscrito pessoal.
- **Cadastro do cliente** — carteira de imóveis da concessionária, com matrícula,
  endereço e categoria tarifária. É dado do cliente, não nosso.
- **Chaves de API** em `.env`, fora do git.

## Registro de segredos expostos

| Segredo | Exposto em | Desde | Situação |
|---|---|---|---|
| Chave do Google Maps Platform `AIzaSyA0BL…oSmY` | commit `1c7f081`, em `recover_pois.py` e `src/capture.ts` | 27/04/2026 | **aguardando revogação no console do Google** |

A chave saiu do código, mas continua no histórico do git e em todo clone
existente. Reescrever o histórico não corrige — quem clonou já tem. A única
correção é revogar no provedor.

Varredura completa em 12/08/2026 (gitleaks 8.30.1, 51 commits, 50,8 MB): 7
achados brutos, **1 vazamento real**. Os demais são o token de compartilhamento
público dos Dados Abertos CNPJ e marcadores `<senha>` na documentação, ambos
justificados na allowlist do `.gitleaks.toml`.

## Estado conhecido

Registrado por honestidade, não por conformidade:

- **Não há autenticação.** O serviço roda em `127.0.0.1:8765` sobre HTTP. Enquanto
  for local e de operador único, é aceitável; **expor à rede sem resolver isto
  serve a base de um cliente para outro**.
- **Não há isolamento por cliente.** Nenhuma tabela tem `tenant_id` e não há RLS.
  Ver [docs/CHECKLIST.md](docs/CHECKLIST.md), requisito 5.
- **Uma chave do Google foi exposta no histórico** — ver o registro acima.
- **O portão de segredo está ativo**: gitleaks no pre-commit (`.githooks/pre-commit`,
  ligado por `git config core.hooksPath .githooks`) e no CI sobre o histórico
  inteiro. O hook local pode ser pulado com `--no-verify`; o CI não pode.
