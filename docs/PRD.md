# PRD — ComercialRadar

> Documento de produto. O que o sistema resolve, para quem, e — principalmente —
> o que ele **não** faz. Atualizado em 12/08/2026.

Perfil: **`saas-multi-cliente`**. Ver [ADR 0001](adr/0001-perfil-e-caminho-de-dados.md).

---

## 1 · O problema

Concessionárias de saneamento faturam pela categoria cadastrada, não pelo uso real
do imóvel. Um cadastro diz "residencial"; no local funciona uma lavanderia, um
salão, uma oficina. A diferença é receita que a empresa não cobra porque não sabe
que existe — e descobrir manualmente exige mandar alguém à rua, endereço por
endereço, num município inteiro.

O ComercialRadar descobre, valida e enriquece pontos comerciais de um município
cruzando Google Maps, busca web, Receita Federal, CNEFE do IBGE e imagem de
fachada, e entrega **o que vale visita** — com a evidência que sustenta cada
achado, para auditoria humana.

O produto não é a lista. É a lista **com o porquê**, auditável.

---

## 2 · Fora de escopo

Esta seção existe para o escopo não crescer sozinho. Nada aqui entra sem virar
decisão explícita registrada em ADR.

**Não faz roteirização.** Divisão de áreas entre equipes, sequenciamento de
pontos, otimização de trajeto e gestão de campo são de outro sistema. A exceção
combinada: **abrir a rota do Google até o local** de um ponto específico é
funcionalidade legítima — é levar o usuário até lá, não planejar o dia dele.

**Não mapeia telhados nem alinha endereço à via.** Detecção de edificação por
*footprint*, régua de numeração e o processo de quadras saíram para o
**radarTelhados** em 11/08/2026. Código separado, banco compartilhado.

**Não é CRM e não faz proposta comercial.** Não guarda negociação, contrato,
funil, histórico de contato nem gera documento de venda.

---

## 3 · Atores

| Ator | O que faz |
|---|---|
| **Administrador da plataforma** | Cria empresas clientes, gerencia usuários, liga e desliga módulos por cliente |
| **Gestor do cliente** | Vê a base da própria empresa, define áreas de trabalho, acompanha o dashboard e o retorno |
| **Auditor** | Aprova, reprova e manda reanalisar cada achado — é quem decide se vira alteração de cadastro |
| **Operador** | Dispara as rodadas de extração e enriquecimento, acompanha o progresso |

Todo usuário pertence a **uma empresa cliente**, e só enxerga dado dessa empresa.
A matriz de permissões detalhada está em [RBAC.md](RBAC.md) e é implementada pela
`/modelo-acesso`.

---

## 4 · O fluxo crítico

É o fluxo que, se quebrar, o sistema não serve. Desenhado em
[ARQUITETURA.md](ARQUITETURA.md#fluxo-crítico).

1. **Extrair** os POIs do município — captura + OCR do Maps, mineração de área
   desenhada, ou extração estadual por OSM + Overture.
2. **Separar o que a base do cliente já tem como comercial.** Isso *não é ganho*:
   sai da fila de oportunidade e vira, no máximo, complemento de informação do
   registro existente.
3. **Achar a divergência de tipo.** Do que sobra, os que batem com um imóvel da
   base do cliente mas com categoria diferente — cadastrado como simples ou
   residencial, com atividade comercial no local. Esta é a **pré-lista**.
4. **Validar a pré-lista** com Street View, dados web e do Google, e dados
   empresariais da Receita (CNPJ, CNAE, situação, sócios).
5. **Descrever pela IA.** Endereço, sócios, fotos e Street View vão para o modelo
   de visão, que devolve descrição estruturada do local com a informação útil à
   decisão — no contrato da skill `leitura-fachada-cadastral`, validado por ela.
6. **Auditar.** O humano aprova, reprova ou manda reanalisar. A decisão de
   alterar o cadastro é dele, sempre.

**Regra que atravessa o fluxo inteiro:** o sistema não corrige o cadastro do
cliente e não une registros. Divergência vira fila de conferência com a evidência
anexa. Automatizar a correção é transformar um erro de leitura em erro cadastral
permanente — e a leitura de imagem erra.

---

## 5 · Critérios de aceite por etapa

| Etapa | Aceite |
|---|---|
| Extração | Todo POI gravado tem coordenada, município e UF de uma fonte só — o select, o clique no mapa ou o polígono desenhado |
| Cruzamento com o cliente | Casamento por coordenada com raio declarado; o que casou some da fila de oportunidade e fica registrado como já-comercial |
| Pré-lista | Cada item traz o imóvel do cliente que ele contradiz e o motivo da contradição |
| Validação | POI pobre (sem telefone, sem foto, sem avaliação) passa pela cascata antes de ir à IA |
| Leitura por IA | A anotação passa no validador da própria skill; reprovada não entra na base |
| Achado | `achado_convergente` exige **duas fontes independentes**; uma fonte só é `sinal` |
| Auditoria | Todo veredito guarda quem decidiu, quando e sobre quais imagens |

---

## 6 · Sistemas externos

| Sistema | Uso | Falha significa |
|---|---|---|
| Google Maps / Places / Street View | Descoberta, enriquecimento e fachada | Sem foto nova; a base já capturada continua servindo |
| SearXNG (i9, `100.115.117.49`) | Busca web do enriquecimento em cascata | Cai para as instâncias de reserva |
| Receita Federal (base local) | CNPJ, CNAE, situação, sócios | Sem validação empresarial; o achado fica em `sinal` |
| IBGE — CNEFE e malha municipal | Terceira fonte de unidades; divisa oficial | Sem coletivas e sem recorte por município |
| OSM / Overture | Extração estadual de POI e vias | Sem varredura em massa |
| OpenAI | Leitura de fachada (`gpt-4o`) | Fila para no passo 5 |
| Ollama (i9) | Leitura local e triagem de nitidez | Cai para o modelo pago |
| Webshare | Proxies residenciais para o Maps | Bloqueio por IP volta a acontecer |

---

## 7 · Métrica de sucesso

**Oportunidades confirmadas em campo.** Dos achados que viraram visita, quantos se
confirmaram. Mede se o sistema aponta **certo** — não se aponta muito. É a métrica
que impede o número bonito de achados falsos.

**Cobertura da área.** Percentual de POIs do município com dado completo — CNPJ,
telefone, fachada e vínculo com a base do cliente. Mede se a varredura fecha.

As duas juntas, porque uma sem a outra mente: cobertura alta com confirmação baixa
é ruído em escala; confirmação alta com cobertura baixa é sorte numa amostra.

---

## 8 · Escala

A premissa é **uso em massa por grandes clientes**. O tamanho atual não reflete a
carga alvo. Os pontos onde este sistema quebra sob carga estão em
[ARQUITETURA.md](ARQUITETURA.md#onde-quebra-sob-carga) — analítico no mesmo
Postgres que atende usuário, ausência de pooler, e ausência de `tenant_id` nos
índices são os três primeiros.
