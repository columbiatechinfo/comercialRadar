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
#     ./scripts/servidor/carregar_bases.sh                # todas
#     ./scripts/servidor/carregar_bases.sh cnefe RS,SC    # só o CNEFE dessas UFs
#     ./scripts/servidor/carregar_bases.sh cadastur       # só o Cadastur
#     ./scripts/servidor/carregar_bases.sh estadual       # só a base estadual
#     ./scripts/servidor/carregar_bases.sh estadual RS,SC # só essas UFs
#     ./scripts/servidor/carregar_bases.sh --situacao     # o que está rodando
#     ./scripts/servidor/carregar_bases.sh --log          # acompanhar ao vivo
#
# AS QUATRO BASES, e o que cada uma custa:
#
#   cnefe      IBGE, censo 2022 — 111 M de endereços, ~1 h
#   cnpj       Receita Federal  — 220 M de linhas, ~40 min
#   cadastur   MTur             — prestadores de turismo, minutos
#   estadual   Overture+OSM+FSQ — a mais cara: DuckDB sobre o Overture, HORAS
#              POR UF. É a que justifica o processo destacado existir.
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
#
# EM `/tmp` DO HOST, e não na raiz do projeto.
#
# A primeira versão o escrevia em `$RAIZ/.carga.sh`. Parece inofensivo e não
# é: um arquivo oculto na raiz, com cara de temporário, dentro de uma pasta
# que alguém vai limpar. Em 31/08/2026 eu mesmo o apaguei numa faxina de
# resíduo de depuração, com a carga rodando — e o bash lê script do disco por
# DESLOCAMENTO, então apagar no meio pode quebrar a execução no ponto
# seguinte. Passou porque o arquivo é pequeno e já tinha sido lido inteiro,
# não porque era seguro.
#
# Em `/tmp`, com o nome do contêiner, ele não se parece com lixo do projeto.
ROTEIRO_HOST="/tmp/$NOME.sh"
cat > "$ROTEIRO_HOST" <<'ROTEIRO'
set -uo pipefail
cd /app
export PYTHONUNBUFFERED=1 PYTHONUTF8=1
P=/venv/bin/python
[ -x "$P" ] || P=python

# ONDE ESTA O VENV, para quem for chamado daqui.
#
# `dataset_estadual.sh` procura o `overturemaps` no PATH do venv. Sem esta
# linha ele cairia no padrao `$RAIZ/.venv`, que no conteiner nao existe — e a
# consequencia NAO e erro: a lista de fontes cai para `osm` sozinha e sai um
# dataset OSM-only com nome de "bases publicas", marcado como pronto.
export VENV=/venv

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

if [ "$QUAL" = "tudo" ] || [ "$QUAL" = "cadastur" ]; then
  echo "──── Cadastur (MTur) ────"
  # O CADASTUR DEIXOU DE PRECISAR DE IDENTIDADE em 31/08/2026.
  #
  # Ele gravava em `radar_comercial.cadastur_prestador`, com política por
  # empresa — e a carga morria com "new row violates row-level security policy"
  # DEPOIS de baixar os 322 MB. O erro estava certo e a tabela é que estava no
  # lugar errado: Cadastur é base pública do MTur, igual para todo cliente e
  # para toda ferramenta. As duas tabelas foram para `resources_root` (0008), e
  # base pública não tem de quem esconder.
  # SEM `--so-carregar`, e é essa a diferença que importa aqui.
  #
  # `--so-carregar` carrega o que JÁ foi baixado — é o que o `minerar_tudo` usa
  # na etapa 3, porque lá o snapshot já deveria existir. Numa máquina nova não
  # existe, e o comando morre com "nenhum parquet em bronze_parquet — rode sem
  # --so-carregar para baixar primeiro". Foi exatamente o que aconteceu na
  # primeira tentativa: 22 segundos e código 1.
  #
  # Aqui é a carga inicial: baixar É o trabalho. `--gerar` produz o entregável
  # depois de carregar.
  "$P" cadastur.py --gerar
  echo "  Cadastur terminou com código $?"
  echo
fi

if [ "$QUAL" = "tudo" ] || [ "$QUAL" = "estadual" ]; then
  echo "──── base estadual (Overture + OSM + Foursquare) ────"
  # NÃO é um `.py`: é o laço que roda as 27 UFs uma por vez, com trava por UF e
  # marcador de veredito. Ele já é retomável — UF com `_pronto.txt` é PULADA —,
  # então cair no meio custa a UF corrente, não as 26 outras.
  #
  # CADA UF ESCREVE O PRÓPRIO LOG em `logs/estadual_<UF>.log`. O log deste
  # contêiner mostra só o avanço; o detalhe de uma UF está no arquivo dela.
  if [ -n "$UFS" ]; then
    CR_UFS="$(echo "$UFS" | tr ',' ' ')" ./scripts/servidor/dataset_brasil.sh
  else
    ./scripts/servidor/dataset_brasil.sh
  fi
  echo "  base estadual terminou com código $?"
  echo
fi

echo "fim: $(date -Is)"
ROTEIRO

echo "▶ disparando a carga destacada ($QUAL${UFS:+ · $UFS})"
docker run -d --name "$NOME" \
  --network host \
  --restart no \
  -v "$RAIZ":/app -v "$VENV":/venv -v "$ROTEIRO_HOST":/roteiro.sh:ro -w /app \
  -e QUAL="$QUAL" -e UFS="$UFS" \
  -e RADAR_USUARIO_SERVICO="${RADAR_USUARIO_SERVICO:-}" \
  -e PG_CONNECT_TIMEOUT=30 \
  --log-opt max-size=50m --log-opt max-file=5 \
  "$IMAGEM" bash /roteiro.sh >/dev/null

sleep 2
echo
docker ps --filter "name=$NOME" --format '  estado: {{.Status}}'
echo
echo "A partir daqui a rede pode cair: o processo é do daemon do Docker."
echo
echo "  acompanhar:  ./scripts/servidor/carregar_bases.sh --log"
echo "  situação:    ./scripts/servidor/carregar_bases.sh --situacao"
