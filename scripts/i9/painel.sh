#!/usr/bin/env bash
# painel.sh — sobe, para e confere o painel do comercialRadar NO i9.
#
# RODA DENTRO DO WSL DO i9. Do notebook, chame por:
#   ssh orbisgrid@100.115.117.49 "wsl -d Ubuntu -- bash -lc \
#     '/home/orbisgrid/comercialradar/scripts/i9/painel.sh subir'"
#
# O DESENHO, E POR QUE ELE TEM DUAS PEÇAS
#
#   server.py  escuta em 127.0.0.1:8765, DENTRO do WSL. Não é alcançável de
#              fora: é o único lugar onde o painel não precisa de TLS, porque
#              o tráfego não sai da máquina.
#   caddy      escuta em :8443 com o certificado da Tailscale e repassa para o
#              8765. É ele que qualquer navegador do tailnet encosta.
#
# Separar as duas é o que permite o painel nunca falar HTTP na rede. Se o
# `server.py` escutasse em 0.0.0.0 direto, a porta ficaria aberta em claro no
# WSL — e bastaria uma regra de portproxy criada por engano para publicá-la.
#
# O CERTIFICADO
#
# Vem de `tailscale cert`, que é Let's Encrypt de verdade para o nome MagicDNS
# — não é certificado interno, então o navegador não reclama. A Tailscale roda
# no WINDOWS do i9 (não no WSL), então os arquivos moram em C:\ferramentas e
# entram aqui pelo /mnt/c. Renovar é rodar `tailscale cert` de novo, no
# Windows; o Caddy relê o arquivo sozinho.
#
# O ALCANCE
#
# Chegar em 8443 vindo do tailnet depende da regra `netsh portproxy` do
# Windows, a mesma máquina do `reamarrar-wsl.ps1`. Sem ela o painel responde
# dentro do WSL e ninguém o alcança — que é exatamente o modo de falha que
# derrubou os dois Postgres em 24/08 com os containers todos `healthy`.
set -euo pipefail

RAIZ="${CR_DIR:-/home/orbisgrid/comercialradar}"
PORTA_APP="${CR_PORTA_APP:-8765}"
PORTA_TLS="${CR_PORTA_TLS:-8443}"
NOME_TS="${CR_NOME_TS:-desktop-s8l7nat.tail7e301b.ts.net}"
CERTS="${CR_CERTS:-/mnt/c/ferramentas}"
PID="$RAIZ/.painel.pid"
LOG="$RAIZ/logs/painel.log"
CADDY_CT="cr-painel-tls"

cd "$RAIZ"
mkdir -p logs

_vivo() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

_caddyfile() {
  cat > "$RAIZ/Caddyfile" <<EOF
{
	admin off
	auto_https off
}

:$PORTA_TLS {
	tls /certs/radar.crt /certs/radar.key
	# `127.0.0.1` e não o IP do WSL: o container roda em rede do host, então
	# o loopback dele É o loopback do WSL. Assim o tráfego em claro entre o
	# Caddy e o painel nunca toca uma interface de rede.
	reverse_proxy 127.0.0.1:$PORTA_APP {
		# O painel usa WebSocket (/ws) para o mapa em tempo real. Sem estas
		# duas linhas o upgrade é rejeitado e o mapa fica mudo — sem erro
		# visível, só marcador que nunca cai.
		header_up Host {host}
		header_up X-Forwarded-For {remote_host}
	}
}
EOF
}

subir() {
  if _vivo; then echo "painel já está de pé (PID $(cat "$PID"))"; else
    [ -f .env ] || { echo "❌ .env ausente. Rode publicar.sh --env do notebook."; exit 1; }
    echo "▶ subindo o painel em 127.0.0.1:$PORTA_APP"
    # `nohup` + `setsid`: a sessão SSH fecha assim que o comando volta, e sem
    # isso o painel morreria junto — o modo de falha é achar que subiu.
    PYTHONUTF8=1 PYTHONUNBUFFERED=1 CR_HOST=127.0.0.1 CR_PORTA="$PORTA_APP" \
      setsid nohup ./.venv/bin/python server.py >> "$LOG" 2>&1 &
    echo $! > "$PID"
    sleep 4
    _vivo && echo "  OK  PID $(cat "$PID") · log em $LOG" || { echo "  ❌ morreu no boot:"; tail -15 "$LOG"; exit 1; }
  fi

  [ -f "$CERTS/radar.crt" ] || {
    echo "❌ Certificado ausente em $CERTS/radar.crt."
    echo "   No WINDOWS do i9, uma vez:"
    echo "   tailscale cert --cert-file C:\\ferramentas\\radar.crt \\"
    echo "                  --key-file  C:\\ferramentas\\radar.key $NOME_TS"
    exit 1; }

  _caddyfile
  docker rm -f "$CADDY_CT" >/dev/null 2>&1 || true
  echo "▶ subindo o TLS em :$PORTA_TLS"
  docker run -d --name "$CADDY_CT" --restart unless-stopped --network host \
    -v "$RAIZ/Caddyfile:/etc/caddy/Caddyfile:ro" \
    -v "$CERTS:/certs:ro" \
    caddy:2-alpine >/dev/null
  sleep 3
  docker ps --format '{{.Names}}' | grep -q "$CADDY_CT" \
    && echo "  OK  https://$NOME_TS:$PORTA_TLS" \
    || { echo "  ❌ o Caddy não ficou de pé:"; docker logs --tail 15 "$CADDY_CT"; exit 1; }
}

parar() {
  _vivo && { kill "$(cat "$PID")" 2>/dev/null || true; echo "painel parado"; } || echo "painel já estava parado"
  rm -f "$PID"
  docker rm -f "$CADDY_CT" >/dev/null 2>&1 && echo "TLS parado" || true
}

estado() {
  _vivo && echo "painel  : de pé (PID $(cat "$PID"))" || echo "painel  : parado"
  docker ps --format '{{.Names}}' | grep -q "$CADDY_CT" && echo "TLS     : de pé" || echo "TLS     : parado"
  echo -n "responde: "
  curl -sk -o /dev/null -w '%{http_code}\n' "https://127.0.0.1:$PORTA_TLS/" || echo "sem resposta"
  echo "log     : $LOG"
}

case "${1:-estado}" in
  subir)  subir ;;
  parar)  parar ;;
  estado) estado ;;
  log)    tail -n "${2:-40}" "$LOG" ;;
  *) echo "uso: painel.sh subir|parar|estado|log [n]"; exit 2 ;;
esac
