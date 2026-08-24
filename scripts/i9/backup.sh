#!/usr/bin/env bash
# Backup do comercialRadar no i9.
#
# O que entra e o que NAO entra, e por que:
#
#   banco do produto  220 MB   ENTRA. POIs, cadastro do cliente, anotacoes de
#                              fachada e os usuarios do Auth. Nada disso se
#                              refaz — e o dado que so existe aqui.
#   Storage           6,7 GB   ENTRA. Cada fachada custou uma chamada de API e
#                              uma captura; "rebaixar de novo" e dinheiro e
#                              semanas, nao um comando.
#   referencia         60 GB   FICA DE FORA. E base publica do IBGE e da
#                              Receita: rebaixa da fonte. Guardar 60 GB por dia
#                              para poupar um download e trocar disco por nada.
#
# Retencao de 14 dias. Dump do banco INTEIRO (nao so do schema): o auth e o
# storage do Supabase vivem em schemas proprios, e restaurar o comercialradar
# sem eles daria um banco com dado e sem quem possa le-lo.
set -uo pipefail

BASE=/home/orbisgrid/comercialradar-infra
DEST=$BASE/backup
DIA=$(date +%Y%m%d-%H%M)
LOG=$DEST/backup.log
RETENCAO=14

mkdir -p "$DEST/bancos" "$DEST/storage"
registrar() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$LOG"; }

registrar "=== inicio ==="

# ── 1. Banco do produto ───────────────────────────────────────────────────
ARQ="$DEST/bancos/produto-$DIA.dump"
if docker exec supabase-db pg_dump -U postgres -d postgres -Fc -Z6 > "$ARQ" 2>>"$LOG"; then
  registrar "banco: $(du -h "$ARQ" | cut -f1) em $(basename "$ARQ")"
else
  registrar "ERRO no dump do banco"; rm -f "$ARQ"
fi

# ── 2. Storage ────────────────────────────────────────────────────────────
# Espelho com --delete e link-dest: o diario custa so o que MUDOU, e cada dia
# fica navegavel como se fosse copia inteira. Tar diario de 6,7 GB encheria o
# disco em duas semanas para guardar quatorze vezes o mesmo arquivo.
ONTEM=$(ls -1d "$DEST"/storage/2* 2>/dev/null | tail -1)
ALVO="$DEST/storage/$DIA"
if [ -n "$ONTEM" ]; then
  rsync -a --delete --link-dest="$ONTEM" "$BASE/pilha/volumes/storage/" "$ALVO/" 2>>"$LOG"
else
  rsync -a "$BASE/pilha/volumes/storage/" "$ALVO/" 2>>"$LOG"
fi
registrar "storage: $(du -sh --apparent-size "$ALVO" 2>/dev/null | cut -f1) aparente, $(du -sh "$ALVO" 2>/dev/null | cut -f1) real (o resto e link para o dia anterior)"

# ── 3. Retencao ───────────────────────────────────────────────────────────
find "$DEST/bancos" -name 'produto-*.dump' -mtime +$RETENCAO -delete 2>/dev/null
ls -1d "$DEST"/storage/2* 2>/dev/null | head -n -$RETENCAO | xargs -r rm -rf

registrar "guardados: $(ls -1 "$DEST"/bancos/*.dump 2>/dev/null | wc -l) dumps, $(ls -1d "$DEST"/storage/2* 2>/dev/null | wc -l) espelhos"
registrar "ocupacao total do backup: $(du -sh "$DEST" | cut -f1)"
registrar "=== fim ==="
