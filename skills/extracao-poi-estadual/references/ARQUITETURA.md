# Arquitetura

```
poi_estadual.py            entrypoint: run / status / diag
poi_estadual/
  config.py                Config (dataclass frozen), ESCOPO_ETAPA, PREDECESSORA, dirs
  manifest.py              Manifesto: estado por etapa, hash, versão de fonte, funil, gates
  ibge.py                  malha (parquet local | API IBGE), alvo, bbox, grade de tiles
  overture.py              tiles → parts (batches pyarrow, marcador DONE)
  osm.py                   .pbf → nodes/ways (2 scans) + bruto com osm_tags_json
  foursquare.py            dist → copy → build (shards densos)
  territorio.py            raw (consolida + gate + rejeitados) e territory (clip chunked)
  normalizacao.py          normalize (tratar PT) e dedup (evidência por município) + PADRAO
  exportacao.py            padronizado, bruto/fonte, vínculos, rejeitados, observações,
                           source_snapshot, dicionário
  mapa.py                  mapa A2L (esc/safeUrl)
  validacao.py             14 verificações (gate semântico + rastreabilidade) + relatorio
  vendor/                  extrair_pois, tratar_pois, dedup_v3, osm_pbf, limites, categorias_pt
```

## Contratos

- **COMUNS** (`vendor/extrair_pois.COMUNS`) — esquema comum das fontes. Toda fonte nova
  precisa produzi-lo; as colunas nativas vão em prefixo próprio (`ov.`, `osm.`, `fsq.`).
- **PADRAO** (`normalizacao.PADRAO`) — colunas do tratado + 7 do território (`TERR`).
  É o contrato do entregável; mudar exige mudar `validacao` e o dicionário na mesma entrega.
- **dedup_v3** (`vendor/dedup_v3.dedup_evidencia`) — recebe o DataFrame do município e
  devolve `(dedup, vinculos)`. `tratar_pois` importa tarde (`_dv()`) para evitar ciclo.
  Parâmetro novo de dedup entra em `PARAMS`, em `Config`, em `_DEDUP` (escopo de hash)
  e no CLI — nessa ordem, senão o resume reaproveita artefato com regra diferente.
- **Manifesto** — `etapas[<etapa>].hash` tem que ser `cfg.hash_etapa(<etapa>)` para o
  artefato ser reaproveitado. Toda etapa nova entra em `ETAPAS`, `ESCOPO_ETAPA` e
  `PREDECESSORA`.

## Onde estender

- **Nova fonte**: módulo que gere COMUNS + nativas, entrada em `FONTES_VALIDAS`,
  chamada em `_fetch` do entrypoint e em `territorio._fontes_partes`.
- **Novo formato de saída**: `FORMATOS_VALIDOS` + `exportacao._grava`.
- **Nova verificação**: `validacao.executar` (uma linha `_chk`).
- **Nova evidência de fusão**: função `_aresta_*` em `dedup_v3` + motivo próprio na
  tabela de vínculos + teste de regressão em `tests/test_v3.py`.

## Cache (v3.3.0)

`cfg.dir_proc(etapa, ...)` = `cache/proc/<etapa>/<cfg.hash_etapa(etapa)>/...` e
`cfg.dir_fonte(fonte, assinatura, ...)` = `cache/fontes/<fonte>/<assinatura>/...`.
**Nenhum módulo deve montar caminho de cache com `cfg.dir()` cru** — o hash tem de
estar no caminho, senão volta o bug de reaproveitar artefato obsoleto. Assinatura de
fonte: `cfg.sig_ibge()`, `cfg.sig_osm()` (derivam só da config) e
`man.assinatura("overture"|"fsq")` (dependem do bbox/grade, calculadas em
`ibge.assinaturas()` e persistidas no manifesto).

## Diretório de execução

```
<base-dir>/
  manifesto.json
  cache/limites|overture|osm|fsq|territorio|normalizado|dedup/
  saida/  poi_padronizado_*.csv|.parquet  poi_bruto_<fonte>_*.parquet
          poi_dicionario_*.csv  relatorio_qualidade_*.json  Mapa_POI_*.html
```
