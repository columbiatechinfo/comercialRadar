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

## Estado conhecido

Registrado por honestidade, não por conformidade:

- **Não há autenticação.** O serviço roda em `127.0.0.1:8765` sobre HTTP. Enquanto
  for local e de operador único, é aceitável; **expor à rede sem resolver isto
  serve a base de um cliente para outro**.
- **Não há isolamento por cliente.** Nenhuma tabela tem `tenant_id` e não há RLS.
  Ver [docs/CHECKLIST.md](docs/CHECKLIST.md), requisito 5.
- **Uma chave do Google foi exposta no histórico** (commit `1c7f081`) e ainda não
  foi rotacionada. Reescrever o histórico não corrige — só a rotação corrige.
