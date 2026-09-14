#!/bin/bash
# A busca de Canoas passa para o Camoufox com sessao quente, 16 navegadores
# (dono do produto, 13/09/2026, noite). O julgamento continuo nao e tocado.
REPO=$HOME/producao/radarComercial  # copia de producao desde 14/09/2026 (docs/DESENVOLVIMENTO.md)
cd $HOME/producao_canoas || exit 1
tr -d '\r' < /tmp/camoufox_novo/buscar_web.py > $REPO/buscar_web.py
cp busca_nova_canoas.sh busca_nova_canoas.antes_camoufox.sh
grep -q '^IMGQ=' busca_nova_canoas.sh || \
  sed -i 's#^IMG=radar-comercial-minerador-worker$#IMG=radar-comercial-minerador-worker\nIMGQ=radar-busca-camoufox   \# DuckDuckGo e Yahoo pelo Camoufox quente (13/09/2026)#' busca_nova_canoas.sh
sed -i 's#\$BUSCA \$IMG python3 -u buscar_web.py --sonda#$BUSCA $IMGQ python3 -u buscar_web.py --sonda#' busca_nova_canoas.sh
sed -i 's#radar-busca-nova -e RADAR_CONEXOES=2 \$BUSCA \$IMG \\#radar-busca-nova -e RADAR_CONEXOES=2 $BUSCA $IMGQ \\#' busca_nova_canoas.sh
sed -i 's#--cidade Canoas --trabalhadores 24 --aplicar#--cidade Canoas --trabalhadores 16 --aplicar#' busca_nova_canoas.sh
for p in $(pgrep -f "^bash busca_nova_canoas.sh"); do kill $p; done
docker stop -t 20 radar-busca-nova radar-busca-sonda >/dev/null 2>&1
sleep 3
echo "$(date +%T) reinicio com Camoufox quente, 16 navegadores (pedido do dono do produto)" >> busca_nova/progresso
setsid nohup bash busca_nova_canoas.sh > /dev/null 2>&1 < /dev/null &
echo "== o que mudou no script"
diff busca_nova_canoas.antes_camoufox.sh busca_nova_canoas.sh
echo "== buscar_web.py no repo: $(grep -c 'class SessaoQuente' $REPO/buscar_web.py) SessaoQuente"
