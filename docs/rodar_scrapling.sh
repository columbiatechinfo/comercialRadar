#!/bin/sh
# Roda uma sonda com o Scrapling instalado.
#
# O container fica DE PE (`sleep infinity`) e as sondas entram por `docker
# exec`. Instalar os navegadores do Scrapling baixa centenas de MB, e um
# container `--rm` pagaria isso a cada sonda.
#
# Tres detalhes que ja custaram uma tentativa cada:
#   - `python -m scrapling` nao existe: o CLI e um script instalado em
#     `.local/bin`;
#   - `scrapling install` chama `playwright install-deps`, que instala pacote
#     de sistema por apt — precisa de ROOT;
#   - o diretorio das sondas tem de existir antes do container, senao ele o
#     cria como root e o usuario nao copia mais nada para la.
#
# uso: rodar_scrapling.sh <arquivo.py>
set -e
SONDA="${1:?informe o arquivo da sonda}"
R=/home/a2l/Documentos/sistemas/radarComercial

mkdir -p /tmp/sondas
chmod 777 /tmp/sondas 2>/dev/null || true

if ! docker ps --format '{{.Names}}' | grep -qx scrapling; then
  docker rm -f scrapling >/dev/null 2>&1 || true
  echo "== subindo o container e instalando o Scrapling (demora) =="
  docker run -d --name scrapling --user root --network host --shm-size=2g \
    -v "$R":/app -v /app/node_modules -v /tmp/sondas:/sondas \
    -w /app -e HOME=/root -e PYTHONUNBUFFERED=1 -e DISPLAY=:99 \
    -e PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    radar-minerador:latest sleep infinity >/dev/null

  docker exec scrapling sh -c 'Xvfb :99 -screen 0 1360x1000x24 -nolisten tcp >/dev/null 2>&1 & sleep 2'
  echo "  instalando scrapling[fetchers]..."
  docker exec scrapling pip install --quiet --no-input 'scrapling[fetchers]' 2>&1 | tail -2
  echo "  baixando os navegadores (Chromium + Camoufox)..."
  docker exec scrapling scrapling install 2>&1 | tail -8
  echo "  versao:"
  docker exec scrapling python -c 'import scrapling; print("   scrapling", scrapling.__version__)'
fi

cp "$SONDA" /tmp/sondas/
docker exec scrapling python "/sondas/$(basename "$SONDA")"
