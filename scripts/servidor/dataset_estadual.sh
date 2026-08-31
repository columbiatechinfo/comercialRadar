#!/usr/bin/env bash
# dataset_estadual.sh — a extração de POIs de uma UF inteira.
#
# É o passo pesado do processo: DuckDB sobre o Overture no S3 mais o PBF do
# OpenStreetMap, para um estado inteiro — 383 mil POIs no RS. Horas de CPU e
# dezenas de GB de disco. Roda no servidor, ao lado do banco, do OSRM, do
# Photon e do Nominatim, que estão lá pelo mesmo motivo.
#
# ESTE SCRIPT PERDEU DUAS METADES EM 30/08/2026, e vale saber quais.
#
# Ele tinha três modos: `produzir` (aqui), `--baixar` (trazia a pasta pronta da
# outra máquina por `tar` sobre SSH) e `--remoto` (disparava a produção lá e
# esperava). Os dois últimos existiam porque quem editava era um notebook e o
# dataset de 10 GB morava junto do banco, no i9.
#
# No padrão A2L o sistema mora no servidor: quem edita, quem produz e quem
# consome são a mesma máquina. Não há de onde baixar nem para onde disparar, e
# `dados_externos/estadual/<UF>` é o único lugar onde a pasta existe.
#
# USO
#   ./scripts/servidor/dataset_estadual.sh RS              # produz (horas)
#   ./scripts/servidor/dataset_estadual.sh --atualizar RS  # repina a fonte
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ATUALIZAR=0
UF=""
for a in "$@"; do
  case "$a" in
    --atualizar) ATUALIZAR=1 ;;
    -*) echo "opção desconhecida: $a"; exit 2 ;;
    *) UF="$(echo "$a" | tr '[:lower:]' '[:upper:]')" ;;
  esac
done
[ -n "$UF" ] || { echo "uso: dataset_estadual.sh [--atualizar] <UF>"; exit 2; }

DESTINO="$RAIZ/dados_externos/estadual/$UF"
MARCADOR="_pronto.txt"

echo "▶ produzindo o dataset de $UF"
echo "  Horas de trabalho. É retomável: parar e rodar de novo CONTINUA."
mkdir -p "$DESTINO"

# TRAVA POR UF — a que protege o workspace de verdade.
#
# A do `dataset_brasil.sh` impede dois laços; esta impede dois pipelines na
# mesma UF, venham de onde vierem (laço, disparo manual, painel). Em 25/08/2026
# dois `poi_estadual.py run --uf PE` gravaram no mesmo diretório ao mesmo tempo:
# o cache da skill é endereçado por hash de escopo, então os dois concordavam
# sobre o caminho de cada arquivo — e escreviam por cima um do outro sem nenhum
# erro até o `os.replace` achar o `.tmp` que o outro já tinha promovido.
exec 8>"$DESTINO/.lock"
if ! flock -n 8; then
  echo "JÁ HÁ uma produção de $UF rodando nesta máquina — nada a fazer aqui."
  echo "  Duas no mesmo diretório corrompem o cache da skill em silêncio."
  exit 1
fi

# As fontes são montadas pelo que a máquina de fato alcança. Declarar
# `overture,osm,fsq` e deixar duas falharem produziria um dataset OSM-only com
# nome de "bases públicas" — cobertura menor, e ninguém saberia.
#
# O `.env` É CARREGADO AQUI, e a falta disso já custou uma rodada. O `HF_TOKEN`
# do Foursquare mora nele; sem carregar, a checagem de `fsq` olhava uma variável
# que nunca existiria e escrevia "sem foursquare: HF_TOKEN ausente no .env" —
# com o token presente no arquivo. A mensagem apontava para o lugar certo e a
# conclusão era falsa, que é o pior tipo de aviso.
#
# `set -a` exporta tudo que for atribuído; a skill lê `HF_TOKEN` do ambiente.
#
# O `tr -d` NO CR SUMIU JUNTO COM O `publicar.sh`, e por isso a limpeza está
# aqui: era ele quem tirava o CRLF do `.env` na viagem. Sem viagem não há quem
# limpe, e um `.env` salvo no Windows quebra o `.` com um erro que fala do
# shell, não do arquivo.
if [ -f "$RAIZ/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . <(tr -d '\r' < "$RAIZ/.env")
  set +a
fi

# O `bin` DO VENV ENTRA NO PATH antes de procurar o `overturemaps`, e ONDE fica
# esse venv é configurável.
#
# O `overturemaps` é dependência do projeto e mora em `<venv>/bin/` — não no
# PATH do shell. Sem esta linha `command -v` não o acha, a lista de fontes cai
# para `osm` sozinha, e sai um dataset OSM-only com nome de "bases públicas":
# cobertura menor, marcado como pronto, e ninguém sabe. Aconteceu na primeira
# execução do RS, e quase aconteceu de novo em 31/08/2026 — daquela vez porque o
# pacote sequer estava no `requirements.txt`.
#
#
# Rodando na maquina, ele e `$RAIZ/.venv`. Rodando no conteiner da carga, e
# `/venv` — um volume proprio, para nao refazer 200 pacotes a cada execucao nem
# sujar a pasta do sistema. Chumbar `$RAIZ/.venv` fazia o `command -v
# overturemaps` falhar la dentro, e a consequencia NAO e um erro: a lista de
# fontes cai para `osm` sozinha e sai um dataset OSM-only com nome de "bases
# publicas", marcado como pronto.
VENV="${VENV:-$RAIZ/.venv}"
export PATH="$VENV/bin:$PATH"

# O TOKEN DO HUGGING FACE, sob qualquer um dos dois nomes.
#
# A skill le `HF_TOKEN` do ambiente. O `.env` do servidor guarda o mesmo valor
# como `huggingface`, em minusculas — e variavel de ambiente e sensivel a
# maiuscula, entao uma nao enxerga a outra.
#
# A alternativa seria duplicar o segredo no `.env` sob os dois nomes, e segredo
# em dois lugares e segredo que so e rotacionado num deles. Aqui a ponte custa
# uma linha e o valor continua tendo um dono so.
#
# SEM ISTO A BASE SAI MENOR E NAO FALHA: as fontes caem para `overture,osm`, o
# script avisa e continua, e o dataset fica sem o Foursquare — com o token
# presente no arquivo o tempo todo. Foi o que aconteceu na primeira execucao do
# RS em 31/08/2026.
export HF_TOKEN="${HF_TOKEN:-${huggingface:-}}"

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
#    nenhum em disco. Consultar a fonte aqui seria materializar bytes do mundo
#    atual sob uma identidade que nao foi verificada."
#
# É uma boa recusa: sem o pin, dois runs "iguais" podem ler mundos diferentes e
# ninguém saberia qual. Então:
#
#   latest  RESOLVE e pina a identidade da fonte. É o que a primeira produção
#           faz, e o que `--atualizar` refaz quando você quer dado novo —
#           deliberadamente, nunca por acidente.
#   cache   usa o snapshot pinado. É o modo de reexecutar sem trocar de mundo
#           debaixo do resultado.
if [ "$ATUALIZAR" = 1 ] || [ ! -f "$DESTINO/$MARCADOR" ]; then
  MODO_FONTE="latest"
  [ "$ATUALIZAR" = 1 ] && echo "  ATUALIZANDO: repina a identidade da fonte (dado novo)"
else
  MODO_FONTE="cache"
fi
echo "  source-mode: $MODO_FONTE"

cd "$RAIZ/skills/extracao-poi-estadual"
set +e
"$VENV/bin/python" poi_estadual.py run \
  --uf "$UF" --fontes "$FONTES" --formatos csv,geoparquet \
  --source-mode "$MODO_FONTE" \
  --base-dir "$DESTINO"
RC=$?
set -e

# PRODUZIDO e APROVADO SÃO COISAS DIFERENTES — e confundi-las tornava toda base
# inutilizável.
#
# O marcador nascia só com código 0. Mas a skill REPROVA no `validate` por taxa
# de fusão suspeita (RS 11,47%, PI 8,96%, limite 2%) mesmo tendo produzido o
# entregável inteiro: 945.716 POIs no RS, 6,6 GB de saída. Sem marcador, o
# `minerar_tudo` recusa o dataset — então nenhuma UF ficaria utilizável por mais
# que se produzisse.
#
# A distinção agora é explícita: o marcador registra o VEREDITO. Base reprovada
# é usável e a ressalva viaja com ela, em vez de o dado ser descartado por causa
# de um número que quem usa deveria poder ver e julgar.
#
# O que continua NÃO gerando marcador é a execução que não chegou ao fim — pasta
# pela metade não é base, e é para isso que o marcador existe.
PADRAO=$(ls "$DESTINO/saida"/poi_padronizado_*.parquet 2>/dev/null | head -1)
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
} > "$DESTINO/$MARCADOR"

if [ "$RC" -eq 0 ]; then
  echo "✅ $UF pronto e APROVADO em $DESTINO"
else
  echo "⚠️  $UF produzido, mas REPROVADO no controle de qualidade da skill."
  echo "   O entregável existe e é utilizável; a ressalva está no marcador e no"
  echo "   relatorio_qualidade_*.json. Veja antes de confiar nos números."
  grep -E "FALHA:" "$RAIZ/logs/estadual_$UF.log" 2>/dev/null | tail -4 | sed 's/^/     /'
fi
