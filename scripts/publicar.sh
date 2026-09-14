#!/bin/bash
# =============================================================================
# Publica uma versão do Comercial Radar em produção (14/09/2026) — docs/DESENVOLVIMENTO.md
#
#   bash scripts/publicar.sh producao-AAAA-MM-DD[.N]
#
# Roda NO i9. A produção vive em ~/producao/radarComercial, uma cópia do repositório
# (git worktree) parada numa TAG. Publicar é: levar essa cópia para a tag nova,
# reconstruir a API dela, e conferir. A ordem é a proteção:
#
#   1. a tag existe e o desenvolvimento está commitado     → nada mudou ainda
#   2. a imagem no ar ganha o apelido :anterior             → há para onde voltar
#   3. a cópia de produção vai para a tag                    → o código novo existe
#   4. build e recriação da API                              → segundos fora do ar
#   5. /api/saude local, /seek/api/saude público e a tela    → avisa e diz como voltar
#
# MIGRAÇÃO NÃO RODA AQUI: o banco é o mesmo do desenvolvimento, então a migração é
# aplicada quando a mudança é testada, antes de publicar. O script lista as que
# entraram entre as duas tags, para conferência.
#
# OS LAÇOS DE PRODUÇÃO (julgamento, busca) montam os arquivos desta cópia: a partir do
# próximo lote, eles já rodam a versão publicada.
#
# Voltar:  bash scripts/publicar.sh --voltar
# =============================================================================
set -euo pipefail
PROD=$HOME/producao/radarComercial
DEV=$HOME/Documentos/sistemas/radarComercial
IMG=radar-comercial-api-radar-comercial-api
URL_PUBLICA=https://a2lsolucoes.com/seek

conferir() {
  local ok=0
  for i in $(seq 1 30); do
    curl -sf -o /dev/null --max-time 5 http://127.0.0.1:7740/api/saude && ok=1 && break
    sleep 3
  done
  [ $ok = 1 ] && echo "  api local: ok" || { echo "  api local: NAO RESPONDEU"; return 1; }
  local pub; pub=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 $URL_PUBLICA/api/saude)
  echo "  $URL_PUBLICA/api/saude: $pub"
  curl -s --max-time 15 $URL_PUBLICA/ | grep -q 'src="/seek/static/sessao.js' \
    && echo "  tela publica: ok" || echo "  tela publica: SEM o prefixo /seek (conferir o túnel e o nginx)"
  [ "$pub" = 200 ]
}

# O QUE O GIT NÃO LEVA. A cópia de produção nasce do git, mas a API e as runs
# também leem arquivos fora dele: o .env, a lista de proxies, o cache de proxies,
# os cookies em estado/, o cache do IBGE e as bases em dados_externos/.
#   · .env: cópia, e não link — as runs montam a cópia de produção em /app, e um
#     link para fora dela não resolve dentro do contêiner. Mudou o .env? Publique.
#   · dados_externos/ (10 GB): cópia pelo rsync, que depois só leva o que mudou. Link
#     físico não dá: os arquivos são do root (as runs rodam como root) e o Ubuntu
#     barra link físico de arquivo alheio (fs.protected_hardlinks=1).
sincronizar_fora_do_git() {
  install -m 600 $DEV/.env $PROD/.env
  local f d
  for f in Webshare_100_proxies.txt .proxy_cache.json; do
    if [ -f $DEV/$f ]; then cp -p $DEV/$f $PROD/$f; fi
  done
  for d in estado cache_ibge dados_externos; do
    if [ -d $DEV/$d ]; then mkdir -p $PROD/$d && rsync -a --no-owner --no-group $DEV/$d/ $PROD/$d/; fi
  done
  echo "  fora do git sincronizado (.env, proxies, estado, cache_ibge, dados_externos)"
}

if [ "${1:-}" = "--voltar" ]; then
  docker image inspect $IMG:anterior >/dev/null 2>&1 || { echo "não há imagem :anterior"; exit 1; }
  docker tag $IMG:anterior $IMG:latest
  cd $PROD/deploy
  RADAR_JOB_REPO=$PROD docker compose -f compose.radar-comercial-api.yml --env-file ../.env up -d --no-build radar-comercial-api
  echo "voltou para a imagem anterior. Código da cópia de produção: $(git -C $PROD describe --tags --always)"
  echo "(para o código dos laços voltar também: git -C $PROD checkout --detach <tag anterior>)"
  conferir
  exit $?
fi

TAG=${1:?"uso: bash scripts/publicar.sh <tag>   ou   --voltar"}
cd $DEV
git fetch -q --tags origin
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "a tag $TAG não existe (crie e envie: git tag -a $TAG -m ... && git push origin $TAG)"; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "há mudança sem commit no desenvolvimento — commite antes de publicar"; exit 1; }

if [ -d $PROD ]; then
  ANTES=$(git -C $PROD describe --tags --always)
  echo "▶ publicar $TAG (no ar: $ANTES)"
  echo "  migrações entre as duas versões (já devem estar aplicadas no banco):"
  git diff --name-only "$ANTES" "$TAG" -- migrations/ | sed 's/^/    /' || true
else
  ANTES="(primeira publicação: a produção rodava do diretório de desenvolvimento)"
  echo "▶ publicar $TAG — cria a cópia de produção em $PROD"
fi

docker image inspect $IMG:latest >/dev/null 2>&1 && docker tag $IMG:latest $IMG:anterior && echo "  imagem no ar guardada como :anterior"
if [ -d $PROD ]; then
  git -C $PROD checkout -q --detach "$TAG"
else
  mkdir -p $(dirname $PROD)
  git worktree add -q --detach $PROD "$TAG"
fi
echo "  cópia de produção em $(git -C $PROD describe --tags --always)"
sincronizar_fora_do_git

cd $PROD/deploy
RADAR_JOB_REPO=$PROD docker compose -f compose.radar-comercial-api.yml --env-file ../.env up -d --build radar-comercial-api
echo "$(date '+%Y-%m-%d %H:%M:%S') $TAG (antes: $ANTES)" >> $HOME/producao/PUBLICACOES.log

if conferir; then
  echo "■ $TAG no ar"
else
  echo "■ CONFERÊNCIA FALHOU. Para voltar: bash scripts/publicar.sh --voltar"
  exit 1
fi
