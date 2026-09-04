# A lista de visita — KML e CSV dos aprovados

Exporta os POIs que a IA aprovou (`aprovado_exato` e `aprovado_comercial`)
como **roteiro de campo**, e não como despejo do banco.

```bash
docker run --rm --name radar-kml --network host --env-file .env \
  -v "$PWD":/app -v /tmp/saida:/app/saida -w /app \
  radar-minerador:latest python -u scripts/campo/gerar_kml.py
```

## O que o desenho decide

**Uma pasta por RUA, não por veredito.** Separado por veredito, quem visita
percorreria cada rua duas vezes — uma atrás dos exatos, outra atrás dos
comerciais. O grau de certeza está na **cor do pino**: verde é
`aprovado_exato` (o estabelecimento foi identificado na fachada), âmbar é
`aprovado_comercial` (há comércio visível, mas não se confirmou que é aquele).

**O nome do pino começa pelo número da porta**, que é o que se lê andando na
rua — antes do nome, que muitas vezes é um CNPJ de MEI e não existe em placa
nenhuma. Depois vem o **número da ligação**, que é o que a pessoa digita no
sistema da companhia ao chegar.

**O balão traz o link que reabre o panorama exato** que a IA analisou, com o
mesmo `pano_id`, heading e fov. Quem chega e encontra porta fechada confere no
celular se a foto era outra.

## Duas armadilhas do endereço, ambas medidas

**O tipo da via partia a rua em duas.** O mesmo imóvel (ligação 351446, nº 177)
aparece como `AVENIDA RIO GRANDE DO SUL` pela Receita e `RUA RIO GRANDE DO SUL`
por outra fonte. Agrupar pelo texto cru punha o mesmo endereço em duas pastas.
O tipo saiu da chave de agrupamento; o rótulo da pasta usa o tipo que mais
aparece no grupo, e o balão mostra sempre o endereço **original**.

**O CEP virava número de porta.** `R. Jáguari - Mathias Velho, Canoas - RS,
92330-120` não tem número, e um `search` por `, \d+` casava o CEP: o ponto
aparecia como "nº 92330" e ia para o fim da rua. O número agora é ancorado no
começo — só casa o que vem logo depois do nome da via.
