#!/usr/bin/env bash
# publicar.sh — leva o código do comercialRadar para o WSL do i9.
#
# RODA NO NOTEBOOK, não no i9.
#
# POR QUE NÃO É `git clone`
#
# O repositório é privado e o i9 não tem credencial de GitHub — nem deve ganhar
# uma só para isto. Uma chave de deploy no i9 é uma credencial a mais para
# vazar, rotacionar e esquecer, e o que ela compraria (um `git pull`) já se
# resolve com o SSH que existe.
#
# Então o código sai do índice do git DAQUI (`git archive`, então é exatamente o
# que está commitado — nada de arquivo solto do working tree) e entra por um
# cano SSH. Atualizar é rodar isto de novo.
#
# O QUE NÃO VAI JUNTO, e por que cada um
#
#   .venv            é de Windows. Lá o venv é outro, e é criado por este script.
#   node_modules     idem.
#   .env             SEGREDO. Vai por `--env`, num passo separado e explícito,
#                    para que enviar segredo nunca seja efeito colateral de
#                    publicar código.
#   capturas/ dados_externos/ logs/   saídas. Pesadas e regeneráveis; e as do i9
#                    são as do i9.
#
# USO
#   scripts/i9/publicar.sh              # código + ambiente (idempotente)
#   scripts/i9/publicar.sh --env        # também o .env  (confirma antes)
#   scripts/i9/publicar.sh --so-codigo  # pula o pip; use quando só o .py mudou
set -euo pipefail

I9="${I9_SSH:-orbisgrid@100.115.117.49}"
DESTINO="${I9_DIR:-/home/orbisgrid/comercialradar}"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

MANDAR_ENV=0
SO_CODIGO=0
for a in "$@"; do
  case "$a" in
    --env)       MANDAR_ENV=1 ;;
    --so-codigo) SO_CODIGO=1 ;;
    *) echo "opção desconhecida: $a"; exit 2 ;;
  esac
done

# `wsl -d Ubuntu` e não `bash` direto: o SSH do i9 cai em PowerShell (é Windows),
# e o Linux de lá é a distro do WSL. Foi o que fez a primeira tentativa de
# `reamarrar-wsl.ps1` pendurar.
# `-n` NÃO É DETALHE: sem ele o ssh lê o stdin do script, e o primeiro comando
# remoto engole a resposta do `read` de confirmação do .env logo abaixo. O
# sintoma é o pior tipo — a pergunta aparece, ninguém responde nada, o script
# segue como se tivesse sido negado e o arquivo simplesmente não chega.
remoto() { ssh -n -o BatchMode=yes -o ConnectTimeout=20 "$I9" "wsl -d Ubuntu -- bash -lc '$1'"; }

echo "▶ 1/4 conferindo o i9"
remoto "mkdir -p $DESTINO && echo ok" >/dev/null
echo "  OK  $I9:$DESTINO"

echo "▶ 2/4 enviando o código (o que está COMMITADO)"
# `git archive HEAD` em vez de tar do diretório: publica o que está no índice,
# não o que está no disco. Publicar working tree é como se descobre, em
# produção, que a correção que faltava era um arquivo nunca commitado.
git -C "$RAIZ" archive --format=tar HEAD \
  | ssh -o BatchMode=yes "$I9" "wsl -d Ubuntu -- bash -lc 'tar xf - -C $DESTINO'"
echo "  OK  $(git -C "$RAIZ" rev-parse --short HEAD) publicado"

if [ "$MANDAR_ENV" = 1 ]; then
  echo "▶ 2b/4 enviando o .env"
  echo "  ⚠️  Isto copia SEGREDOS para o i9. Ctrl+C para desistir."
  if [ -t 0 ]; then
    read -r -p "  Confirma? [s/N] " ok
  else
    # Sem terminal (chamado por outro script/CI), a confirmação vem por
    # variável — nunca por silêncio. Segredo não viaja por omissão.
    ok="${CONFIRMA_ENV:-n}"
    echo "  (sem terminal) CONFIRMA_ENV=$ok"
  fi
  if [ "$ok" = "s" ] || [ "$ok" = "S" ]; then
    # `chmod 600` no mesmo comando: um .env que existe por um instante com
    # permissão de leitura para todos é um .env vazado, e o WSL é multiusuário.
    # `tr -d` no CR: o .env é editado no Windows e chega com CRLF. O Python
    # tolera (o dotenv limpa), mas `set -a; . ./.env` no shell do Linux não —
    # ele lê o CR como comando e, pior, a variável nasce com um CR NO VALOR.
    # Chave de API terminada em CR é recusada pelo servidor, e o erro fala de
    # chave inválida, nunca de fim de linha.
    tr -d '\r' < "$RAIZ/.env" | ssh -o BatchMode=yes "$I9" \
      "wsl -d Ubuntu -- bash -lc 'cat > $DESTINO/.env && chmod 600 $DESTINO/.env'"
    echo "  OK  .env enviado (chmod 600)"
  else
    echo "  pulado"
  fi
fi

if [ "$SO_CODIGO" = 1 ]; then
  echo "▶ 3/4 e 4/4 pulados por --so-codigo"
  exit 0
fi

echo "▶ 3/4 ambiente Python (idempotente — reexecutar é barato)"
# `--without-pip` + get-pip, e não `python3 -m venv` puro: o Ubuntu do i9 não tem
# `ensurepip` (o Debian o separa em `python3.12-venv`), e o `sudo` de lá pede
# senha. O caminho por apt exigiria a senha do usuário a cada publicação; este
# não exige nada e não altera pacote de sistema.
remoto "cd $DESTINO && \
  { [ -x .venv/bin/pip ] || { rm -rf .venv && python3 -m venv --without-pip .venv && \
      curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py && \
      ./.venv/bin/python /tmp/get-pip.py -q; }; } && \
  ./.venv/bin/pip install -q \
    playwright playwright-stealth opencv-python-headless numpy easyocr \
    scikit-learn openpyxl aiohttp python-dotenv psycopg2-binary openai Pillow \
    fastapi uvicorn python-multipart requests duckdb pandas geopandas shapely \
    pyarrow rapidfuzz overturemaps && \
  echo pip-ok"

echo "▶ 4/4 Chromium do Playwright"
# DUAS COISAS, e elas foram SEPARADAS de propósito (24/08/2026):
#
#   install chromium        baixa o navegador. NÃO precisa de root.
#   install-deps chromium   instala ~35 pacotes do sistema (libnss3, libatk,
#                           libgbm…). PRECISA de root, e o sudo do i9 pede senha.
#
# `--with-deps` faz as duas numa chamada só — e foi isso que quebrou aqui: ele
# tenta o apt PRIMEIRO, esbarra na senha e ABORTA, deixando o navegador sem
# baixar. O sintoma é `~/.cache/ms-playwright` vazio depois de uma publicação
# que pareceu correr bem.
#
# Então o download roda sempre, sozinho, e a falta das bibliotecas vira AVISO
# com o comando exato — em vez de derrubar a publicação inteira por causa de um
# passo que só o dono da máquina pode dar.
remoto "cd $DESTINO && ./.venv/bin/python -m playwright install chromium 2>&1 | tail -3"

echo "▶ conferindo se o Chromium ABRE (bibliotecas do sistema)"
if remoto "cd $DESTINO && ./.venv/bin/python -c \"
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); b.close()
print('abre')\" 2>/dev/null | grep -q abre"; then
  echo "  OK  o Chromium abre"
else
  echo "  ⚠️  O Chromium NÃO abre — faltam as bibliotecas de sistema."
  echo "      Um comando, uma vez, com a senha do sudo NO PRÓPRIO i9:"
  echo
  echo "        sudo $DESTINO/.venv/bin/python -m playwright install-deps chromium"
  echo
  echo "      Sem isso a captura falha com erro de símbolo ausente, que não se"
  echo "      parece nem um pouco com 'falta pacote do sistema'."
fi

echo
echo "✅ Publicado. Conferir:"
echo "   ssh $I9 \"wsl -d Ubuntu -- bash -lc 'cd $DESTINO && ./.venv/bin/python verificar_servicos.py'\""
