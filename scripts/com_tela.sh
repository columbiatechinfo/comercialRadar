#!/bin/sh
# roda a sonda da ficha dentro da imagem do minerador, com tela virtual
mkdir -p /tmp/.X11-unix; D=0
for n in $(seq 200 260); do
  if [ ! -e /tmp/.X$n-lock ]; then
    Xvfb :$n -screen 0 1920x1080x24 -nolisten tcp > /tmp/xvfb.err 2>&1 &
    sleep 2
    if [ -e /tmp/.X$n-lock ] && ! grep -q already /tmp/xvfb.err; then D=$n; break; fi
  fi
done
export DISPLAY=:$D
exec "$@"
