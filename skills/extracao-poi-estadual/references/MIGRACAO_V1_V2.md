# Migração v1.0.0 → v2.0.0

## O que sumiu

| v1 | v2 |
|---|---|
| `scripts/rs_driver.py` (núcleo + orquestrador) | `poi_estadual/` (pacote) |
| `scripts/overture.py` `fsq.py` `osm_ways.py` `osm_bruto.py` `clip.py` `dedup.py` `poi_map.py` | módulos homônimos do pacote, sem `import rs_driver as D` |
| `references/PARAMETRIZACAO.md` ("edite estas constantes") | flags de CLI |
| `scripts/README.md` (ordem manual de 8 comandos) | `poi_estadual.py run` |

## Mapa de comandos

| v1 | v2 |
|---|---|
| (nenhum — exigia `rs_keep.parquet` pronto) | `run --etapa init` |
| `overture.py seed` + `overture.py run` (repetir) | `run --etapa fetch` (retoma sozinho) |
| `fsq.py dist` → `copy` → `build` | idem, dentro de `fetch` |
| `rs_driver.py osm-nodes` + `osm_ways.py a` + `osm_ways.py b` | idem, dentro de `fetch` |
| `clip.py prep` | `run --etapa raw` |
| `clip.py run` + `clip.py finish` | `run --etapa territory` |
| `rs_driver.py treat` (repetir) | `run --etapa normalize` |
| `dedup.py run` + `dedup.py finish` | `run --etapa dedup` |
| `rs_driver.py bruto` + `osm_bruto.py` + `dicionario` | `run --etapa export` |
| `poi_map.py` | `run --etapa map` (exige `--gerar-mapa`) |
| — | `run --etapa validate` |

## Reproduzir a execução do RS da v1

```bash
python poi_estadual.py run \
  --uf RS --excluir 4314902 \
  --fontes overture,osm,fsq --min-conf 0.50 \
  --formatos csv,geoparquet --gerar-mapa \
  --base-dir ./execucao_rs
```

O bbox derivado da malha IBGE é `(-57.65, -33.751, -49.691, -27.082)` — o mesmo valor que
estava fixo em `RS_BBOX`.

## Diferenças de resultado esperadas

Números da v2 **não batem exatamente** com os 632.234 POIs da v1, por três correções:

1. **Posição de way OSM** — `representative_point()` em vez da média das coordenadas.
   Muda a posição de ways côncavos e, na borda, pode mudar o município atribuído.
2. **Fronteira sem simplificação** — o default `--simplificar-graus 0` mantém pontos que a
   v1 perdia na divisa (e vice-versa). Para reproduzir a v1: `--simplificar-graus 0.0005`.
3. **FSQ sem `name IS NOT NULL`** — POIs sem nome deixam de ser descartados na coleta.

Nenhuma das três é reversível "de graça": a 1 e a 2 são correções de exatidão espacial,
a 3 é de cobertura. Rodar as duas versões lado a lado e comparar por município é o
caminho se for preciso justificar a diferença.

## Artefatos de cache

Não reaproveite `cache/` da v1: o layout e os nomes mudaram e não há manifesto para
validar procedência. Comece uma `--base-dir` nova. O `.pbf` da Geofabrik pode ser
reaproveitado via `OSM_PBF_DIR`.
