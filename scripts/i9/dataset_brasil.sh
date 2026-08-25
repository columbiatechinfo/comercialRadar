#!/usr/bin/env bash
# dataset_brasil.sh — produz a base estadual das 27 UFs, uma por vez, no i9.
#
# RODA NO i9. Do notebook:
#   ssh orbisgrid@100.115.117.49 "wsl -d Ubuntu -- bash -lc \
#     'cd /home/orbisgrid/comercialradar && setsid nohup ./scripts/i9/dataset_brasil.sh \
#      > logs/estadual_BRASIL.log 2>&1 < /dev/null &'"
#
# UMA POR VEZ, e não em paralelo. A skill já satura CPU e rede sozinha (DuckDB
# sobre o Overture no S3 e o PBF do OSM), e duas ao mesmo tempo disputariam o
# mesmo disco enquanto o Postgres de produção tenta escrever nele.
#
# RETOMÁVEL EM DOIS NÍVEIS: a UF que tem o marcador `_pronto.txt` é PULADA, e a
# que está pela metade continua de onde parou (a própria skill é retomável).
# Então rodar isto de novo depois de uma queda custa o que falta, não o todo.
#
# A ORDEM não é alfabética: começa pelas UFs onde há trabalho (RS, PI), porque
# base pronta cedo é base que já serve. O resto vem depois.
set -uo pipefail          # SEM `-e`: uma UF que falha não pode derrubar as 26.

RAIZ="${CR_DIR:-/home/orbisgrid/comercialradar}"
DATASETS="$RAIZ/dados_externos/estadual"

# UMA RODADA POR VEZ, e a trava e do sistema — nao do costume de quem dispara.
#
# Em 25/08/2026 duas copias deste laco rodaram juntas por quatro horas (10:18 e
# 10:20). O comentario "uma por vez" nao impede nada: o segundo disparo nao le
# comentario. O estrago foi silencioso e caro:
#
#   - dois `poi_estadual.py` na MESMA UF e no MESMO base-dir (PE);
#   - `.parquet.tmp -> .parquet` sumindo debaixo do outro processo (BA, SP);
#   - SP morto pelo OOM, com os dois `dedup` disputando 94 GB de RAM;
#   - o funil do RS acumulando tres passadas — e o `validate` reprovando por
#     comparar esse acumulado com a tabela de rejeitados de UMA passada.
#
# O descritor 9 fica aberto enquanto o script vive; o kernel solta a trava
# sozinho se o processo morrer, entao queda nao deixa cadeado orfao.
exec 9>"$RAIZ/logs/.dataset_brasil.lock"
if ! flock -n 9; then
  echo "JA HA uma producao do Brasil rodando nesta maquina."
  echo "  Ela e retomavel: quando terminar, rodar de novo pula o que ficou pronto."
  echo "  Para ver onde esta:  tail -f $RAIZ/logs/estadual_BRASIL.log"
  exit 1
fi
MARCADOR="_pronto.txt"

# PISO DE DISCO — e ele não é conservadorismo, é o Postgres de produção.
#
# O banco do produto mora neste disco. Postgres que fica sem espaço para o WAL
# PARA DE ACEITAR ESCRITA, e o sintoma não se parece com "disco cheio": vira
# erro de transação no meio da rodada de outra pessoa. Uma base estadual não
# vale isso, então a produção para antes.
PISO_GB="${CR_PISO_DISCO_GB:-80}"

UFS="${CR_UFS:-RS PI SC PR SP RJ MG ES BA PE CE GO MT MS DF TO PA AM MA PB RN AL SE AC AP RO RR}"

_livre_gb() { df -BG --output=avail "$RAIZ" | tail -1 | tr -dc '0-9'; }

echo "==============================================================="
echo " base estadual do BRASIL — 27 UFs, uma por vez"
echo " inicio : $(date '+%d/%m/%Y %H:%M')"
echo " disco  : $(_livre_gb) GB livres · piso de seguranca: ${PISO_GB} GB"
echo "==============================================================="

feitas=0; puladas=0; falhas=0; seguidas=0; t_inicio=$(date +%s)

# QUANTAS FALHAS SEGUIDAS ANTES DE DESISTIR, e por que isso precisa existir.
#
# Em 25/08/2026 dezenove UFs falharam em ZERO minuto, uma atrás da outra, todas
# com `HTTP Error 503` na etapa `init` — que e a que consulta a malha municipal
# na API do IBGE. A causa provavel fomos nos: dezenove consultas em sequencia
# dentro de um minuto e pressao suficiente para levar backpressure da fonte.
#
# UF que falha sozinha e problema da UF. Tres seguidas e problema da FONTE, e
# continuar varrendo a lista contra um servico que esta recusando so queima o
# resto do alfabeto e enche o log de falha que nao diz nada.
MAX_SEGUIDAS="${CR_MAX_FALHAS_SEGUIDAS:-3}"

# Respiro entre UFs. Nao e superstiicao: o `init` de cada uma bate na API do
# IBGE, e a producao inteira do Brasil sao 27 rajadas em poucos minutos quando
# as UFs ja estao prontas e o laco so pula.
PAUSA_S="${CR_PAUSA_ENTRE_UFS:-15}"

for uf in $UFS; do
  if [ -f "$DATASETS/$uf/$MARCADOR" ]; then
    echo "[pulada] $uf — ja pronta ($(du -sh "$DATASETS/$uf" 2>/dev/null | cut -f1))"
    puladas=$((puladas + 1))
    continue
  fi

  livre=$(_livre_gb)
  if [ "$livre" -lt "$PISO_GB" ]; then
    echo
    echo "PARANDO em $uf: so ${livre} GB livres (piso ${PISO_GB} GB)."
    echo "  O Postgres de producao mora neste disco e para de aceitar escrita"
    echo "  se ficar sem espaco para o WAL. Libere espaco e rode de novo — as"
    echo "  UFs prontas sao puladas."
    break
  fi

  echo
  echo "---------------------------------------------------------------"
  echo "[$uf]   $(date '+%H:%M') · ${livre} GB livres"
  echo "---------------------------------------------------------------"
  t0=$(date +%s)
  if "$RAIZ/scripts/i9/dataset_estadual.sh" "$uf" >> "$RAIZ/logs/estadual_$uf.log" 2>&1; then
    echo "[ok] $uf em $((($(date +%s) - t0) / 60)) min · $(du -sh "$DATASETS/$uf" 2>/dev/null | cut -f1)"
    feitas=$((feitas + 1))
    seguidas=0
  else
    # NAO derruba o laco. Uma UF pode falhar por indisponibilidade da fonte, e
    # perder as outras 26 por causa dela seria trocar um problema por 26.
    echo "[FALHOU] $uf em $((($(date +%s) - t0) / 60)) min — ultimas linhas:"
    tail -6 "$RAIZ/logs/estadual_$uf.log" | sed 's/^/     /'
    echo "  (as demais continuam; rode de novo depois e so esta sera refeita)"
    falhas=$((falhas + 1))
    seguidas=$((seguidas + 1))
    if [ "$seguidas" -ge "$MAX_SEGUIDAS" ]; then
      echo
      echo "PARANDO: $seguidas UFs seguidas falharam."
      echo "  Isso quase nunca e problema das UFs — e da FONTE. Veja o motivo em"
      echo "  $RAIZ/logs/estadual_$uf.log e rode de novo quando ela voltar:"
      echo "  as prontas sao puladas e so as que faltam sao refeitas."
      break
    fi
  fi
  sleep "$PAUSA_S"
done

echo
echo "==============================================================="
echo " fim: $(date '+%d/%m/%Y %H:%M') · $(( ($(date +%s) - t_inicio) / 60 )) min"
echo " produzidas: $feitas · ja prontas: $puladas · falhas: $falhas"
echo " disco livre agora: $(_livre_gb) GB"
echo " total em disco   : $(du -sh "$DATASETS" 2>/dev/null | cut -f1)"
echo "==============================================================="
[ "$falhas" -eq 0 ]
