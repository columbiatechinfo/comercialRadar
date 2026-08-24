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

# A EMPRESA EM QUE O TRABALHO NASCE — e por que ela é argumento e não implícito.
#
# O `root` não pertence a nenhuma empresa (é o único usuário assim, de
# propósito). Sem crachá de empresa, tudo que ele grava — a área de trabalho e
# todo POI dos jobs que dispara — é carimbado com `CR_TENANT_ID`.
#
# Se ficasse só no `.env`, o painel subiria calado na empresa que estivesse
# escrita lá, e uma mineração de 80 minutos nasceria dentro do cliente errado
# sem nada na tela dizer. Já quase aconteceu: o `.env` aponta para a Columbia e
# o trabalho de 24/08 era da Aegea.
#
# Então: vem do ambiente de quem chama, e o `subir` IMPRIME a empresa antes de
# ligar. Sem `CR_TENANT_ID`, avisa que está usando o do `.env`.
EMPRESA="${CR_TENANT_ID:-}"

# Quantas sessões de Maps em paralelo. No notebook o teto útil era 3-4 (cada
# Chromium come ~300 MB). O i9 tem 16 CPUs e 94 GB — daí o padrão maior. Baixe
# se quiser um teste mais leve.
SESSOES="${MAPS_SESSOES:-10}"
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
	# Loopback, e nao o IP do WSL: o container roda em rede do host, entao o
	# loopback dele E o loopback do WSL. Assim o trafego em claro entre o
	# Caddy e o painel nunca toca uma interface de rede.
	#
	# SEM CRASE NESTE BLOCO: o heredoc abaixo nao tem delimitador entre aspas
	# (precisa expandir as portas), e crase dentro dele e substituicao de
	# comando. Um comentario com crase virou
	#     painel.sh: line 71: 127.0.0.1: command not found
	# — um erro que aponta para o `cat` e nao para o comentario que o causou.
	reverse_proxy 127.0.0.1:$PORTA_APP {
		# O painel usa WebSocket (/ws) para o mapa em tempo real. Sem estas
		# duas linhas o upgrade e rejeitado e o mapa fica mudo — sem erro
		# visivel, so marcador que nunca cai.
		header_up Host {host}
		header_up X-Forwarded-For {remote_host}
	}
}
EOF
}

subir() {
  if _vivo; then echo "painel já está de pé (PID $(cat "$PID"))"; else
    [ -f .env ] || { echo "❌ .env ausente. Rode publicar.sh --env do notebook."; exit 1; }

    # A empresa é DITA antes de subir. Um painel que não diz em nome de quem
    # está trabalhando é um painel que grava no cliente errado em silêncio.
    if [ -n "$EMPRESA" ]; then
      echo "▶ empresa (CR_TENANT_ID): $EMPRESA"
    else
      DO_ENV=$(grep '^CR_TENANT_ID=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
      echo "▶ empresa: ${DO_ENV:-NENHUMA} (do .env — nada foi passado na chamada)"
      [ -n "$DO_ENV" ] || echo "  ⚠️  Sem empresa, o root não consegue salvar área nem gravar POI."
    fi
    echo "▶ sessões de Maps em paralelo: $SESSOES"
    echo "▶ subindo o painel em 127.0.0.1:$PORTA_APP"
    # `nohup` + `setsid`: a sessão SSH fecha assim que o comando volta, e sem
    # isso o painel morreria junto — o modo de falha é achar que subiu.
    # `env` e não prefixo de atribuição: o bash reconhece `VAR=x cmd` na ANÁLISE,
    # antes de expandir — então `${EMPRESA:+CR_TENANT_ID=...}` não vira
    # atribuição, vira NOME DE COMANDO. O erro é
    # `CR_TENANT_ID=5944…: command not found`, e o painel simplesmente não sobe.
    # Com `env`, as atribuições são argumentos, e a expansão condicional
    # funciona: sem empresa, nenhum argumento é passado e vale o do `.env`.
    env PYTHONUTF8=1 PYTHONUNBUFFERED=1 CR_HOST=127.0.0.1 CR_PORTA="$PORTA_APP" \
      MAPS_SESSOES="$SESSOES" ${EMPRESA:+CR_TENANT_ID="$EMPRESA"} \
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
  if _vivo; then
    p=$(cat "$PID")
    echo "painel  : de pé (PID $p)"
    # A empresa do processo VIVO, lida de /proc — não a do .env nem a da
    # variável de agora. É a única que responde "onde este painel está
    # gravando neste momento".
    emp=$(tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | sed -n 's/^CR_TENANT_ID=//p')
    [ -z "$emp" ] && emp=$(grep '^CR_TENANT_ID=' "$RAIZ/.env" 2>/dev/null | cut -d= -f2- | tr -d '"')" (do .env)"
    echo "empresa : ${emp:-NENHUMA}"
  else
    echo "painel  : parado"
  fi
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
