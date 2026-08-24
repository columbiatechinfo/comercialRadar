#!/usr/bin/env bash
# dataset_estadual.sh — a extração de POIs de uma UF inteira, no i9.
#
# POR QUE ESTE TRABALHO NÃO MORA NO NOTEBOOK
#
# É o passo pesado do processo: DuckDB sobre o Overture no S3 mais o PBF do
# OpenStreetMap, para um estado inteiro — 383 mil POIs no RS. Horas de CPU e
# dezenas de GB de disco.
#
# No notebook ele disputaria tudo com os dez Chromiums da captura, e travaria a
# rodada antes de ela começar. No i9 são 16 CPUs, 94 GB de RAM e 682 GB livres,
# e é onde o OSRM, o Photon e o Nominatim já vivem pelo mesmo motivo.
#
# O QUE **NÃO** MUDOU DE MÁQUINA, e por quê
#
# O painel e a captura seguem no notebook. As chaves do Google são restritas por
# REFERRER, e o mapa do painel manda a própria origem: servido do i9, o Google
# recusa e o mapa não desenha. Foi medido em 24/08/2026 — ver a seção 35 da
# DOCUMENTACAO.md. Só o trabalho sem navegador atravessa.
#
# USO
#   # no i9 (produz — horas, uma vez por UF):
#   ./scripts/i9/dataset_estadual.sh RS
#
#   # no notebook (traz a pasta pronta):
#   bash scripts/i9/dataset_estadual.sh --baixar RS
#
#   # no notebook (dispara no i9 e espera):
#   bash scripts/i9/dataset_estadual.sh --remoto RS
set -euo pipefail

I9="${I9_SSH:-orbisgrid@100.115.117.49}"
DIR_I9="${I9_DIR:-/home/orbisgrid/comercialradar}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MODO="produzir"
UF=""
for a in "$@"; do
  case "$a" in
    --baixar) MODO="baixar" ;;
    --remoto) MODO="remoto" ;;
    -*) echo "opção desconhecida: $a"; exit 2 ;;
    *) UF="$(echo "$a" | tr '[:lower:]' '[:upper:]')" ;;
  esac
done
[ -n "$UF" ] || { echo "uso: dataset_estadual.sh [--baixar|--remoto] <UF>"; exit 2; }

DESTINO_I9="$DIR_I9/dados_externos/estadual/$UF"
DESTINO_LOCAL="$RAIZ/dados_externos/estadual/$UF"
MARCADOR="_pronto.txt"

# `-n`: sem ele o ssh consome o stdin de quem chamou este script.
remoto() { ssh -n -o BatchMode=yes -o ConnectTimeout=20 "$I9" "wsl -d Ubuntu -- bash -lc '$1'"; }

produzir() {
  echo "▶ produzindo o dataset de $UF"
  echo "  Horas de trabalho. É retomável: parar e rodar de novo CONTINUA."
  mkdir -p "$DESTINO_I9"

  # As fontes são montadas pelo que a máquina de fato alcança. Declarar
  # `overture,osm,fsq` e deixar duas falharem produziria um dataset OSM-only com
  # nome de "bases públicas" — cobertura menor, e ninguém saberia.
  FONTES="osm"
  command -v overturemaps >/dev/null && FONTES="overture,$FONTES" \
    || echo "  ⚠️  sem overture: CLI ausente (pip install overturemaps)"
  [ -n "${HF_TOKEN:-}" ] && FONTES="$FONTES,fsq" \
    || echo "  ⚠️  sem foursquare: HF_TOKEN ausente no .env"
  echo "  fontes: $FONTES"

  cd "$DIR_I9/skills/extracao-poi-estadual"
  "$DIR_I9/.venv/bin/python" poi_estadual.py run \
    --uf "$UF" --fontes "$FONTES" --formatos csv,geoparquet \
    --base-dir "$DESTINO_I9"

  # O marcador só nasce quando a skill termina INTEIRA. A existência da pasta
  # não é prova: uma execução interrompida na etapa 6 de 8 tem pasta, tem
  # arquivos e não serve para importar.
  printf 'extracao-poi-estadual · uf=%s · fontes=%s\n' "$UF" "$FONTES" \
    > "$DESTINO_I9/$MARCADOR"
  echo "✅ pronto em $DESTINO_I9"
  echo "   Traga com:  bash scripts/i9/dataset_estadual.sh --baixar $UF"
}

baixar() {
  echo "▶ trazendo o dataset de $UF do i9"
  if ! remoto "test -f $DESTINO_I9/$MARCADOR"; then
    echo "❌ O i9 não tem o dataset de $UF pronto (falta o marcador $MARCADOR)."
    echo "   Produza lá primeiro:"
    echo "     bash scripts/i9/dataset_estadual.sh --remoto $UF"
    exit 1
  fi
  mkdir -p "$(dirname "$DESTINO_LOCAL")"
  # tar por cano SSH: o rsync não está garantido dos dois lados, e o WSL do i9
  # só é alcançável através do PowerShell do Windows.
  ssh -n -o BatchMode=yes "$I9" \
    "wsl -d Ubuntu -- bash -lc 'tar cf - -C $DIR_I9/dados_externos/estadual $UF'" \
    | tar xf - -C "$RAIZ/dados_externos/estadual"
  echo "✅ $DESTINO_LOCAL"
  du -sh "$DESTINO_LOCAL" 2>/dev/null || true
}

case "$MODO" in
  produzir) produzir ;;
  baixar)   baixar ;;
  remoto)
    echo "▶ disparando a produção de $UF NO i9 (isto vai demorar)"
    ssh -n -o BatchMode=yes "$I9" \
      "wsl -d Ubuntu -- bash -lc 'cd $DIR_I9 && ./scripts/i9/dataset_estadual.sh $UF'"
    baixar
    ;;
esac
