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
remoto() { ssh -o BatchMode=yes -o ConnectTimeout=20 "$I9" "wsl -d Ubuntu -- bash -lc '$1'"; }

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
  read -r -p "  Confirma? [s/N] " ok
  if [ "$ok" = "s" ] || [ "$ok" = "S" ]; then
    # `chmod 600` no mesmo comando: um .env que existe por um instante com
    # permissão de leitura para todos é um .env vazado, e o WSL é multiusuário.
    ssh -o BatchMode=yes "$I9" \
      "wsl -d Ubuntu -- bash -lc 'cat > $DESTINO/.env && chmod 600 $DESTINO/.env'" \
      < "$RAIZ/.env"
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
# `--with-deps` puxa as bibliotecas de sistema (libnss3, libatk…). Sem elas o
# Chromium instala, existe, e falha ao abrir com erro de símbolo ausente — que
# não se parece nem um pouco com "falta pacote do sistema".
remoto "cd $DESTINO && ./.venv/bin/python -m playwright install --with-deps chromium 2>&1 | tail -3"

echo
echo "✅ Publicado. Conferir:"
echo "   ssh $I9 \"wsl -d Ubuntu -- bash -lc 'cd $DESTINO && ./.venv/bin/python verificar_servicos.py'\""
