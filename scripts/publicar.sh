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
#   4. build da imagem nova e `import server` nela, com a
#      mesma configuração só-leitura                         → a API no ar nem foi tocada
#   5. recriação da API                                      → segundos fora do ar
#   6. /api/saude local, /seek/api/saude público e a tela    → avisa e diz como voltar
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

# OS SCRIPTS COMO O NAVEGADOR RECEBE (16/09/2026): o `PrefixoPublico` reescreve texto de código, e em 15/09 isso
# quebrou a gestão inteira sem nenhum erro no servidor. Baixa cada página pela URL pública, separa os <script>
# embutidos e passa cada um pelo `node --check`.
conferir_scripts() {
  local falhou=0 pag tmp
  for pag in "" "gestao" "extrair" "validacao"; do
    tmp=$(mktemp -d)
    curl -s --max-time 20 "$URL_PUBLICA/$pag" -o "$tmp/pagina.html" || true
    python3 - "$tmp" <<'PY'
import re, sys, pathlib
d = pathlib.Path(sys.argv[1])
html = (d / "pagina.html").read_text(encoding="utf-8", errors="replace")
for i, corpo in enumerate(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)):
    (d / ("s%02d.js" % i)).write_text(corpo, encoding="utf-8")
PY
    for js in "$tmp"/s*.js; do
      [ -f "$js" ] || continue
      if ! node --check "$js" > "$tmp/erro.txt" 2>&1; then
        echo "  script de /$pag: ERRO DE SINTAXE — $(grep -m1 -i 'error' "$tmp/erro.txt")"
        falhou=1
      fi
    done
    rm -rf "$tmp"
  done
  [ $falhou = 0 ] && echo "  scripts das paginas publicas: sintaxe ok"
  return $falhou
}

conferir() {
  local ok=0
  for i in $(seq 1 30); do
    curl -sf -o /dev/null --max-time 5 http://127.0.0.1:7740/api/saude && ok=1 && break
    sleep 3
  done
  [ $ok = 1 ] && echo "  api local: ok" || { echo "  api local: NAO RESPONDEU"; return 1; }
  local pub; pub=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 $URL_PUBLICA/api/saude)
  echo "  $URL_PUBLICA/api/saude: $pub"
  # A página vai para uma variável antes do grep: com `pipefail`, o `grep -q` fecha o
  # cano no primeiro acerto, o curl morre de SIGPIPE e a conferência dava "SEM o
  # prefixo" com a tela certa (14/09/2026).
  local tela=0 pagina
  for i in 1 2 3 4 5; do
    pagina=$(curl -s --max-time 15 $URL_PUBLICA/ || true)
    if grep -q 'src="/seek/static/sessao.js' <<< "$pagina"; then tela=1; break; fi
    sleep 3
  done
  [ $tela = 1 ] && echo "  tela publica: ok" || echo "  tela publica: SEM o prefixo /seek (conferir o túnel e o nginx)"
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
  # AS PASTAS VAZIAS, que o git não guarda. O server.py as cria no import e a API roda
  # somente-leitura: sem elas na imagem a API não sobe. Foi a primeira publicação, em
  # 14/09/2026 — "Read-only file system: '/app/areas'", 3 min fora, volta pelo --voltar.
  mkdir -p $PROD/{areas,crops,capturas,malhas,mineracao,node_modules,saida_telhados,uploads} \
           $PROD/logs/{prints,medicoes,tiles}
  echo "  fora do git sincronizado (.env, proxies, estado, cache_ibge, dados_externos, pastas vazias)"
}


# =============================================================================
# CÓDIGO PUBLICADO É CÓDIGO RODANDO (18/09/2026).
#
# Até aqui a publicação trocava o código em disco e recriava só a API. Os processos longos seguiam com o que tinham
# carregado ao subir, e isso custou caro no mesmo dia:
#   · o executor da validação estava no ar havia 41 h com a etapa `fichas` na memória; um lote da validação #6 caiu
#     na segunda passada e ficou parado esperando uma etapa que não existe mais;
#   · o minerador leva o código DENTRO da imagem: sem reconstruir, a correção "publicada" não rodava;
#   · o notebook tem a sua própria cópia, que ninguém atualizava.
#
# Cada um só é reiniciado OCIOSO — reiniciar no meio de um job ou de um lote perde trabalho. Ocupado, a publicação
# diz o que ficou para trás e como completar depois: `bash scripts/publicar.sh --recarregar`.
# O `.env` do notebook NÃO viaja: o dele tem os tetos daquela máquina (12 navegadores, 30 GB).
# =============================================================================
recarregar() {
  local host="$1" pre=""
  [ "$host" = "local" ] || pre="ssh -o ConnectTimeout=15 -o BatchMode=yes $host"
  local rotulo=$([ "$host" = "local" ] && echo "i9" || echo "$host")

  # o minerador: reconstrói a imagem quando não há extração rodando
  if $pre docker exec radar-comercial-minerador-worker-1 sh -c "ps ax -o args= | grep -q '[m]inerar_tudo'" 2>/dev/null; then
    echo "  $rotulo · minerador OCUPADO com uma extração — reconstruir depois: bash scripts/publicar.sh --recarregar"
  else
    if $pre sh -c "cd $PROD/deploy && docker compose -f compose.radar-comercial-minerador.yml --env-file ../.env up -d --build >/tmp/recarregar_minerador.log 2>&1"; then
      echo "  $rotulo · minerador reconstruído com o código novo"
    else
      echo "  $rotulo · minerador: a reconstrução FALHOU (log em /tmp/recarregar_minerador.log)"
    fi
  fi

  # a frota: o serviço devolve à fila o que estiver em voo, mas só se reinicia com os postos parados
  local voo
  voo=$($pre sh -c "docker logs --since 3m radar-frota-prod-frota-1 2>&1 | grep -oE 'em voo [0-9]+' | tail -2 | grep -cv 'em voo 0'" 2>/dev/null)
  if [ "${voo:-1}" = "0" ]; then
    $pre docker restart radar-frota-prod-frota-1 >/dev/null 2>&1 && echo "  $rotulo · serviço da frota reiniciado"
  else
    echo "  $rotulo · frota com tarefa em voo — reiniciar depois: bash scripts/publicar.sh --recarregar"
  fi

  # o executor da validação: só sem etapa rodando (cada etapa roda num contêiner radar-val-p-*)
  if $pre sh -c "docker ps --format '{{.Names}}' | grep -q '^radar-val-p-'" 2>/dev/null; then
    echo "  $rotulo · executor da validação OCUPADO — reiniciar depois: bash scripts/publicar.sh --recarregar"
  else
    $pre docker restart radar-validacao-executor-1 >/dev/null 2>&1 && echo "  $rotulo · executor da validação reiniciado"
  fi
}

recarregar_tudo() {
  echo "▶ recarregando os processos longos com o código publicado"
  recarregar local
  if rsync -a --delete --exclude ".git" --exclude ".env" --exclude "uploads" --exclude "capturas" --exclude "crops" \
       --exclude "__pycache__" -e "ssh -o ConnectTimeout=15 -o BatchMode=yes" $PROD/ notebook:$PROD/ 2>/dev/null; then
    echo "  notebook · cópia de produção sincronizada (sem o .env dele)"
    recarregar notebook
  else
    echo "  notebook · FORA DO AR (suspenso ou desligado) — ao voltar: bash scripts/publicar.sh --recarregar"
  fi
}

if [ "${1:-}" = "--recarregar" ]; then
  recarregar_tudo
  exit 0
fi

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

# A imagem DO CONTÊINER NO AR, e não a :latest — depois de uma publicação que falhou
# as duas podem ser diferentes, e aí o :anterior guardaria a imagem quebrada.
NO_AR=$(docker inspect -f '{{.Image}}' radar-comercial-api 2>/dev/null || true)
if [ -n "$NO_AR" ]; then docker tag $NO_AR $IMG:anterior && echo "  imagem no ar guardada como :anterior"; fi
if [ -d $PROD ]; then
  git -C $PROD checkout -q --detach "$TAG"
else
  mkdir -p $(dirname $PROD)
  git worktree add -q --detach $PROD "$TAG"
fi
echo "  cópia de produção em $(git -C $PROD describe --tags --always)"
sincronizar_fora_do_git

cd $PROD/deploy
export RADAR_JOB_REPO=$PROD
COMPOSE="docker compose -f compose.radar-comercial-api.yml --env-file ../.env"
$COMPOSE build radar-comercial-api
# O ENSAIO: a imagem nova importa o server.py com o mesmo compose (só-leitura, volumes,
# variáveis), num contêiner à parte e sem porta. Falhou, a API no ar nem foi tocada.
if ! $COMPOSE run --rm --no-deps -T radar-comercial-api \
     python -c "import os, server; print('import ok'); os._exit(0)" > $HOME/producao/ensaio.log 2>&1; then
  tail -5 $HOME/producao/ensaio.log
  [ -n "$NO_AR" ] && docker tag $NO_AR $IMG:latest
  echo "■ ENSAIO FALHOU: a imagem nova não importa o server.py (log em ~/producao/ensaio.log). A API no ar não foi tocada."
  exit 1
fi
echo "  ensaio: a imagem nova importa o server.py"
$COMPOSE up -d --no-build radar-comercial-api
echo "$(date '+%Y-%m-%d %H:%M:%S') $TAG (antes: $ANTES)" >> $HOME/producao/PUBLICACOES.log

if conferir && conferir_scripts; then
  echo "■ $TAG no ar"
  recarregar_tudo
else
  echo "■ CONFERÊNCIA FALHOU. Para voltar: bash scripts/publicar.sh --voltar"
  exit 1
fi
