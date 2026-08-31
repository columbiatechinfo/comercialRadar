#!/usr/bin/env bash
# carregar_bases.sh — baixa as bases públicas para `resources_root`.
#
# POR QUE ELE RODA DESTACADO, e não como um comando comum.
#
# São dezenas de GB e horas de download. Quem dispara costuma estar longe, numa
# rede que pode cair — e um processo preso à sessão de SSH morre junto com ela,
# no meio de um arquivo, deixando carga pela metade.
#
# A resposta NÃO é `nohup` nem `setsid`: quem garante a sobrevivência aqui é o
# `docker run -d`. O processo passa a pertencer ao daemon do Docker, que é do
# sistema e não da sessão. A conexão pode cair no segundo seguinte ao disparo.
#
# É RETOMÁVEL. Cada arquivo carregado é registrado em `fonte_arquivos`, e uma
# segunda execução PULA o que já entrou. Cair no meio custa o arquivo corrente,
# não o trabalho todo — então rodar de novo é sempre seguro.
#
# USO
#     ./scripts/servidor/carregar_bases.sh              # CNEFE + CNPJ, tudo
#     ./scripts/servidor/carregar_bases.sh cnefe RS,SC  # só o CNEFE dessas UFs
#     ./scripts/servidor/carregar_bases.sh --situacao   # o que está rodando
#     ./scripts/servidor/carregar_bases.sh --log        # acompanhar ao vivo
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NOME="radar-carga-bases"
IMAGEM="python:3.10-slim"
VENV="radar-venv"

# ── situação e log: não disparam nada ───────────────────────────────────────
if [ "${1:-}" = "--situacao" ]; then
  docker ps -a --filter "name=$NOME" \
    --format 'estado: {{.Status}}
comando: {{.Command}}' | head -4
  echo
  echo "--- últimas linhas ---"
  docker logs --tail 20 "$NOME" 2>&1 | tail -20
  exit 0
fi
if [ "${1:-}" = "--log" ]; then
  docker logs -f --tail 60 "$NOME"
  exit 0
fi

# ── uma carga por vez ───────────────────────────────────────────────────────
#
# Duas cargas simultâneas baixariam os mesmos arquivos e disputariam o mesmo
# `COPY`. O registro em `fonte_arquivos` evita duplicata NO BANCO, mas não evita
# o desperdício de baixar 50 GB duas vezes.
if docker ps --filter "name=$NOME" --format '{{.Names}}' | grep -q "$NOME"; then
  echo "JÁ HÁ uma carga rodando. Acompanhe com:"
  echo "  ./scripts/servidor/carregar_bases.sh --log"
  exit 1
fi
docker rm -f "$NOME" >/dev/null 2>&1 || true

QUAL="${1:-tudo}"
UFS="${2:-}"

# ── o roteiro que roda lá dentro ────────────────────────────────────────────
cat > "$RAIZ/.carga.sh" <<'ROTEIRO'
set -uo pipefail
cd /app
export PYTHONUNBUFFERED=1 PYTHONUTF8=1
P=/venv/bin/python
[ -x "$P" ] || P=python

echo "════════ carga das bases públicas ════════"
echo "início: $(date -Is)"
echo

if [ "$QUAL" = "tudo" ] || [ "$QUAL" = "cnefe" ]; then
  echo "──── CNEFE (IBGE) ────"
  if [ -n "$UFS" ]; then "$P" base_cnefe.py --uf "$UFS"; else "$P" base_cnefe.py; fi
  echo "  CNEFE terminou com código $?"
  echo
fi

if [ "$QUAL" = "tudo" ] || [ "$QUAL" = "cnpj" ]; then
  echo "──── CNPJ (Receita Federal) ────"
  "$P" base_cnpj.py
  echo "  CNPJ terminou com código $?"
  echo
fi

echo "fim: $(date -Is)"
ROTEIRO

echo "▶ disparando a carga destacada ($QUAL${UFS:+ · $UFS})"
docker run -d --name "$NOME" \
  --network host \
  --restart no \
  -v "$RAIZ":/app -v "$VENV":/venv -w /app \
  -e QUAL="$QUAL" -e UFS="$UFS" \
  -e PG_CONNECT_TIMEOUT=30 \
  --log-opt max-size=50m --log-opt max-file=5 \
  "$IMAGEM" bash /app/.carga.sh >/dev/null

sleep 2
echo
docker ps --filter "name=$NOME" --format '  estado: {{.Status}}'
echo
echo "A partir daqui a rede pode cair: o processo é do daemon do Docker."
echo
echo "  acompanhar:  ./scripts/servidor/carregar_bases.sh --log"
echo "  situação:    ./scripts/servidor/carregar_bases.sh --situacao"
