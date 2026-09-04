#!/usr/bin/env bash
# Manda o codigo do i9 para o notebook e reconstroi o minerador nas duas.
#
# POR QUE ISTO EXISTE. O codigo NAO e montado do disco no conteiner do
# minerador: ele e copiado para dentro da imagem. Entao `git pull` no i9 nao
# muda nada no que esta rodando, e o notebook nem repositorio tem — recebe
# arquivo por rsync. Sem um script, "sincronizar" virava uma sequencia de mao
# que alguem ia esquecer pela metade; ja aconteceu de o conserto existir no i9
# e a outra maquina seguir com a versao velha, o que aparece como "o conserto
# nao funcionou".
#
# O `.env` FICA DE FORA, e isso nao e detalhe. E nele que cada maquina guarda o
# proprio teto — `MAX_TRABALHADORES=10` e `MEM_WORKER=12g` no notebook, 30 e 24g
# aqui — alem dos segredos. Com `--delete` e sem esta excecao, sincronizar o
# codigo apagaria a configuracao da outra maquina e a rodada seguinte tentaria
# subir trinta navegadores em 45 GB de RAM.
set -euo pipefail

REPO=~/Documentos/sistemas/radarComercial
REMOTO=${1:-notebook}
cd "$REPO"

echo "→ enviando o codigo para $REMOTO"
rsync -az --delete \
  --exclude '.git' --exclude '.env' --exclude '.env.*' \
  --exclude 'dados_externos' --exclude 'capturas' --exclude 'crops' \
  --exclude 'logs' --exclude 'saida' --exclude '__pycache__' \
  --exclude 'node_modules' --exclude '*.pyc' \
  ./ "$REMOTO:$REPO/"

# O `--env-file .env` E OBRIGATORIO, e custou uma maquina afogada para
# aparecer. Com `-f deploy/x.yml`, o Compose toma `deploy/` como diretorio do
# projeto e procura o `.env` LA — nao na raiz. Sem apontar o arquivo, os tetos
# viram os padroes embutidos e o notebook subiu com 30 navegadores e 24g.
echo "→ reconstruindo aqui"
docker compose --env-file .env -f deploy/compose.radar-comercial-minerador.yml up -d --build

echo "→ reconstruindo em $REMOTO"
ssh "$REMOTO" "cd $REPO && docker compose --env-file .env -f deploy/compose.radar-comercial-minerador.yml up -d --build"

echo "✓ as duas maquinas na mesma versao"
