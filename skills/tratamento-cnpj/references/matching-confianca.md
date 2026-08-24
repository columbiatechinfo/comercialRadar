# Matching e Confiança

> **Atualizado na v2.2.** Ver `auditoria-v21-v22.md` para o que mudou.

## Por que preservar o tipo do logradouro

Remover o tipo de todas as ruas melhora cobertura, mas cria falsos positivos
quando existem RUA, AVENIDA ou TRAVESSA com o mesmo nome no mesmo CEP. A v2 usa:

- chave primária: tipo + base;
- fallback sem tipo apenas quando a base é única no CEP;
- conflito de tipos: `AMBIGUO_TIPO_LOGRADOURO`.

## Fuzzy com margem

O melhor score isolado não basta. Duas ruas podem ter 94 e 92. O candidato só é
aceito quando a diferença para o segundo colocado supera a margem configurada.

**v2.2 — `token_set_ratio` está proibido.** Ele devolve 100 quando um nome é
subconjunto do outro (`BRASIL` × `BRASIL NOVO`), e a v2.1 o usava dentro de um
`max()`, garantindo que o componente permissivo sempre vencesse: score máximo,
margem larga, falso positivo que nenhum limiar barra. A similaridade usa apenas
`token_sort_ratio`, que já separa as duas famílias com folga (typos acima de 94,
truncamentos abaixo de 78 — ver `tests/test_similaridade.py`).

Campos de auditoria:

- `XFERA_LOGR_SCORE`;
- `XFERA_LOGR_SCORE_SEGUNDO`;
- `XFERA_LOGR_CANDIDATOS`;
- `XFERA_MATCH_DETALHE`.

## Duplicidade CNEFE

O endereço pode aparecer várias vezes. A coordenada escolhida deve ser a de
menor NV, com desempate estável. A anotação comercial, por outro lado, agrega
todas as unidades do endereço.

## S/N

O centróide é a mediana das coordenadas do logradouro dentro do CEP. O campo
`XFERA_DESVIO_METROS` contém o percentil 95 das distâncias ao centróide.

O centróide não é um endereço individual. Por isso:

- `XFERA_COD_IBGE` fica vazio;
- `XFERA_FLAG_COMERCIAL` fica vazio;
- confiança é BAIXA;
- **NV sintético é 90** (v2.2). Era 4 na v2.1, que no domínio oficial do IBGE
  significa *face de quadra* — uma coordenada muito melhor que a mediana do
  logradouro. Quem filtrasse `NV <= 4` recebia uma coisa achando que recebia outra.
- o desvio tem piso de 60 m: um centróide nunca é melhor que uma face de quadra,
  e rua com um único ponto tem p95 = 0, o que declararia incerteza nula.
