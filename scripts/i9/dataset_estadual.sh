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
ATUALIZAR=0
UF=""
for a in "$@"; do
  case "$a" in
    --baixar)    MODO="baixar" ;;
    --remoto)    MODO="remoto" ;;
    --atualizar) ATUALIZAR=1 ;;
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

  # TRAVA POR UF — a que protege o workspace de verdade.
  #
  # A do `dataset_brasil.sh` impede dois lacos; esta impede dois pipelines na
  # mesma UF, venham de onde vierem (laco, disparo manual, painel). Em
  # 25/08/2026 dois `poi_estadual.py run --uf PE` gravaram no mesmo diretorio ao
  # mesmo tempo: o cache da skill e enderecado por hash de escopo, entao os dois
  # concordavam sobre o caminho de cada arquivo — e escreviam por cima um do
  # outro sem nenhum erro ate o `os.replace` achar o `.tmp` que o outro ja tinha
  # promovido.
  exec 8>"$DESTINO_I9/.lock"
  if ! flock -n 8; then
    echo "JA HA uma producao de $UF rodando nesta maquina — nada a fazer aqui."
    echo "  Duas no mesmo diretorio corrompem o cache da skill em silencio."
    exit 1
  fi

  # As fontes são montadas pelo que a máquina de fato alcança. Declarar
  # `overture,osm,fsq` e deixar duas falharem produziria um dataset OSM-only com
  # nome de "bases públicas" — cobertura menor, e ninguém saberia.
  # O `.env` É CARREGADO AQUI, e a falta disso já custou uma rodada.
  #
  # O `HF_TOKEN` do Foursquare mora nele. Sem carregar, a checagem de `fsq`
  # olhava uma variável que nunca existiria e escrevia
  # "sem foursquare: HF_TOKEN ausente no .env" — com o token presente no
  # arquivo. A mensagem apontava para o lugar certo e a conclusão era falsa,
  # que é o pior tipo de aviso.
  #
  # `set -a` exporta tudo que for atribuído; a skill lê `HF_TOKEN` do ambiente.
  # O `tr -d` no CR do `publicar.sh` é o que permite `.` funcionar aqui — .env
  # com CRLF quebra o `source` com um erro que fala do shell, não do arquivo.
  if [ -f "$DIR_I9/.env" ]; then
    set -a; . "$DIR_I9/.env"; set +a
  fi

  # O `bin` do venv ENTRA no PATH antes de procurar o `overturemaps`.
  #
  # Ele é instalado como dependência do projeto, então mora em
  # `.venv/bin/overturemaps` — não no PATH do shell. Sem esta linha,
  # `command -v` não o acha, a lista de fontes cai para `osm` sozinha, e sai um
  # dataset OSM-only com nome de "bases públicas": cobertura menor, marcado
  # como pronto, e ninguém sabe. Aconteceu na primeira execução do RS.
  export PATH="$DIR_I9/.venv/bin:$PATH"

  FONTES="osm"
  command -v overturemaps >/dev/null && FONTES="overture,$FONTES" \
    || echo "  ⚠️  sem overture: CLI ausente mesmo com o venv no PATH"
  [ -n "${HF_TOKEN:-}" ] && FONTES="$FONTES,fsq" \
    || echo "  ⚠️  sem foursquare: HF_TOKEN ausente no .env"
  echo "  fontes: $FONTES"

  # `--source-mode`, e ele É a diferença entre produzir e consultar.
  #
  # A skill se recusa a rodar em `cache` sem um snapshot já pinado:
  #
  #   "--source-mode cache exige snapshot ja resolvido para `osm`, e nao ha
  #    nenhum em disco. Consultar a fonte aqui seria materializar bytes do
  #    mundo atual sob uma identidade que nao foi verificada."
  #
  # É uma boa recusa: sem o pin, dois runs "iguais" podem ler mundos
  # diferentes e ninguém saberia qual. Então:
  #
  #   latest  RESOLVE e pina a identidade da fonte. É o que a primeira
  #           produção faz, e é o que `--atualizar` refaz quando você quer
  #           dado novo — deliberadamente, nunca por acidente.
  #   cache   usa o snapshot pinado. É o modo de reexecutar sem trocar de
  #           mundo debaixo do resultado.
  if [ "$ATUALIZAR" = 1 ] || [ ! -f "$DESTINO_I9/$MARCADOR" ]; then
    MODO_FONTE="latest"
    [ "$ATUALIZAR" = 1 ] && echo "  ATUALIZANDO: repina a identidade da fonte (dado novo)"
  else
    MODO_FONTE="cache"
  fi
  echo "  source-mode: $MODO_FONTE"

  cd "$DIR_I9/skills/extracao-poi-estadual"
  set +e
  "$DIR_I9/.venv/bin/python" poi_estadual.py run \
    --uf "$UF" --fontes "$FONTES" --formatos csv,geoparquet \
    --source-mode "$MODO_FONTE" \
    --base-dir "$DESTINO_I9"
  RC=$?
  set -e

  # PRODUZIDO e APROVADO SÃO COISAS DIFERENTES — e confundi-las tornava toda
  # base inutilizável.
  #
  # O marcador nascia só com código 0. Mas a skill REPROVA no `validate` por
  # taxa de fusão suspeita (RS 11,47%, PI 8,96%, limite 2%) mesmo tendo
  # produzido o entregável inteiro: 945.716 POIs no RS, 6,6 GB de saída. Sem
  # marcador, o `minerar_tudo` recusa o dataset — então nenhuma UF ficaria
  # utilizável por mais que se produzisse.
  #
  # A distinção agora é explícita: o marcador registra o VEREDITO. Base
  # reprovada é usável e a ressalva viaja com ela, em vez de o dado ser
  # descartado por causa de um número que quem usa deveria poder ver e julgar.
  #
  # O que continua NÃO gerando marcador é a execução que não chegou ao fim —
  # pasta pela metade não é base, e é para isso que o marcador existe.
  PADRAO=$(ls "$DESTINO_I9/saida"/poi_padronizado_*.parquet 2>/dev/null | head -1)
  if [ -z "$PADRAO" ]; then
    echo "❌ $UF: a skill parou antes de gerar o entregável (código $RC)."
    echo "   Sem marcador — rodar de novo CONTINUA de onde parou."
    exit "${RC:-1}"
  fi

  if [ "$RC" -eq 0 ]; then
    VEREDITO="aprovado"
  else
    VEREDITO="produzido_com_ressalva"
  fi
  {
    printf 'extracao-poi-estadual · uf=%s · fontes=%s\n' "$UF" "$FONTES"
    printf 'veredito=%s · codigo=%s\n' "$VEREDITO" "$RC"
    printf 'entregavel=%s\n' "$(basename "$PADRAO")"
    [ "$RC" -ne 0 ] && printf 'ressalva=ver relatorio_qualidade_*.json na saida\n'
  } > "$DESTINO_I9/$MARCADOR"

  if [ "$RC" -eq 0 ]; then
    echo "✅ $UF pronto e APROVADO em $DESTINO_I9"
  else
    echo "⚠️  $UF produzido, mas REPROVADO no controle de qualidade da skill."
    echo "   O entregável existe e é utilizável; a ressalva está no marcador e no"
    echo "   relatorio_qualidade_*.json. Veja antes de confiar nos números."
    grep -E "FALHA:" "$DIR_I9/logs/estadual_$UF.log" 2>/dev/null | tail -4 | sed 's/^/     /'
  fi
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
    # PRODUZ NO i9 E FICA LÁ. Não baixa, e isso é o desenho.
    #
    # O banco também está no i9. Trazer dezenas de GB para o notebook só para
    # reenviar o recorte de um município de volta seria atravessar a rede duas
    # vezes à toa. Quem importa o município é o próprio i9, contra o próprio
    # banco. `--baixar` continua existindo para quem quiser uma cópia local.
    echo "▶ produzindo $UF NO i9 (fica lá — o banco também está lá)"
    ARGS="$UF"
    [ "$ATUALIZAR" = 1 ] && ARGS="--atualizar $UF"
    ssh -n -o BatchMode=yes "$I9" \
      "wsl -d Ubuntu -- bash -lc 'cd $DIR_I9 && ./scripts/i9/dataset_estadual.sh $ARGS'"
    ;;
esac
