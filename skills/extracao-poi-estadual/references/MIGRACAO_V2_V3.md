# Migração v2.0.0 → v3.x

**Mudança incompatível.** Esquema do entregável, defaults e motor de dedup mudaram.
Base gerada na v2.0.0 **não** é comparável linha a linha com base gerada na v3.0.0.

## Por que

Auditoria de duas execuções reais da v2.0.0 (Canoas-RS, 16.123 POIs; Santa Maria-RS,
15.776 POIs), ambas **APROVADAS 14/14** no gate antigo:

| | Canoas | Santa Maria |
|---|---:|---:|
| clusters com fusão | 415 | 1.452 |
| fusões indevidas comprovadas | **42 (10,1%)** | **66 (4,5%)** |
| estabelecimentos distintos apagados | ~71 | ~115 |
| âncora Overture em fusão Overture+OSM | 100,0% (1.682 casos) | 100,0% |
| `bairro` = nome do próprio município | 95,4% | 81,5% |
| segmento `Outros` | 23,0% | 30,2% |
| `--min-conf 0.50` descartaria | 22,1% | >35% |

O gate conferia aritmética (funil fecha, ids únicos, nada fora do polígono), não
semântica. Nenhuma dessas falhas era capaz de reprovar uma entrega.

## O que quebra

| Mudança | Efeito |
|---|---|
| `bairro` do Overture → `localidade_fonte` | quem lia `bairro` do Overture passa a ler `localidade_fonte`; `bairro` agora só tem bairro de verdade (OSM `addr:suburb` ou parse) |
| `endereco_completo` deixa de conter o município | strings mudam em ~91% das linhas do Overture |
| `--min-conf` default `0.50` → `0.0` | a base cresce ~22% a ~35%; para reproduzir a v2 passe `--min-conf 0.50` |
| predicado OSM `ampliado` e `name` opcional | nós OSM ~2×; **invalida o `fetch`** (novo hash de escopo) |
| motor de dedup `evidencia` | contagem final muda nos dois sentidos: menos fusão indevida, mais fusão legítima por telefone |
| `validate` ganhou 2 verificações | entrega com fusão suspeita acima de `--max-fusao-suspeita` (2%) **reprova** |
| `COMUNS` ganhou 2 colunas | fonte externa que produza COMUNS precisa incluir `localidade_fonte` e `categoria_hier` |
| dedup com blocking por grade (v3.2.0) | pares na divisa municipal passam a ser avaliados: a contagem final pode cair um pouco onde havia duplicata entre municípios vizinhos; `--dedup-celula 0` reproduz a v3.1.0 |
| bbox de coleta = UF completa (v3.1.0) | `--excluir` deixa de reduzir o download; cache de Overture com grade diferente tem os marcadores `DONE` invalidados na primeira execução |

## Colunas novas no padronizado

`cluster_id` · `sem_nome` · `categoria_hier` · `precisao_coord_m` · `coord_empilhada` ·
`localidade_fonte` · `flag_localidade_divergente` · `n_registros_fundidos` ·
`dedup_motivos` · `nucleo_discriminante`

## Artefatos novos

`poi_dedup_vinculos_*` · `poi_rejeitados_*` · `poi_observacoes_*` ·
`poi_source_snapshot_*` · `poi_fusao_suspeita_*` (condicional)

## Como reprocessar uma execução da v2

O cache da coleta é **reaproveitável** para tudo, menos para a ampliação do predicado
OSM. `territorio._compat_v3` deriva `localidade_fonte` e `categoria_hier` dos parquets
antigos (o `locality` estava em `bairro`; a hierarquia já viajava em `ov.*`), então:

```bash
# reprocessa do `raw` para baixo, sem baixar nada de novo
python poi_estadual.py run --uf RS --excluir 4314902 --fontes overture,osm \
  --base-dir ./execucao_rs --etapa raw
python poi_estadual.py run --uf RS --excluir 4314902 --fontes overture,osm \
  --base-dir ./execucao_rs --ate validate
```

Para também ganhar o recall do OSM (predicado ampliado + POI sem nome), o `fetch` do
OSM precisa rodar de novo — apague `cache/osm/nodes.parquet`, `cache/osm/ways.parquet`
e `cache/osm/_ways_raw.pkl`. O `.pbf` baixado é reaproveitado.

## Como reproduzir o comportamento da v2 (comparação A/B)

```bash
python poi_estadual.py run --uf RS ... \
  --min-conf 0.50 --dedup legado --osm-predicado classico --osm-exigir-nome \
  --max-fusao-suspeita 1.0
```

Único desvio: o defeito `_name_core(nan) == 'nan'` foi corrigido também no modo
`legado` — dois registros sem nome deixam de casar com similaridade 100.

## Verificação recomendada antes de aceitar a nova base

1. `relatorio_qualidade_*.json` → `fusao_suspeita.taxa` deve ficar perto de 0.
2. `poi_dedup_vinculos_*` → conferir uma amostra de `motivo = telefone` (fusão a até
   200 m é onde mora o falso positivo; se houver ruído, baixe `--dedup-ctx-min` para 2
   ou `tel_max_locais` antes de mexer no raio).
3. `poi_rejeitados_*` → o total deve bater com os descartes do funil fora do dedup.
4. `coord_empilhada > 1` e `precisao_coord_m > 60` → decidir no consumo o que fazer com
   esses ~5% antes de levar a campo.
