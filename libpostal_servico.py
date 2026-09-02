# -*- coding: utf-8 -*-
"""libpostal_servico.py — o parser de endereços atendendo por HTTP.

POR QUE UM SERVIÇO, E NÃO UM `docker exec` POR RODADA

O libpostal mora numa imagem própria porque carrega 2 GB de modelo (ver
`deploy/Dockerfile.libpostal`). Até agora ele era chamado por arquivo: exporta
JSON, roda o container, lê JSON de volta. Funciona para medir, não para o
pipeline — cada chamada paga o carregamento do modelo, e o pipeline chama uma
vez por município.

Como serviço, o modelo é carregado uma vez e fica. **Medido em 02/09/2026:**
27.694 endereços de Canoas em 0,5 s, 51.736 endereços por segundo. Uma cidade
inteira cabe numa requisição.

O QUE ELE DEVOLVE, E POR QUE ISSO IMPORTA

`parse_address` rotula cada pedaço do endereço. Dois rótulos resolvem problemas
que regra de vírgula não resolve:

    house         o nome do estabelecimento
    road          a via

`Canoas Shopping Avenida Guilherme Schell` não tem vírgula entre os dois, e sai
separado corretamente. Toda regra que escrevemos à mão para isso só aprendia
depois de ver o exemplo falhar — e o exemplo seguinte ainda não tinha aparecido.

SEM REDE, SEM GPU, SEM ESTADO

Não consulta nada, não guarda nada e não depende de banco. Reiniciar o container
não perde trabalho: quem guarda é `endereco_segmentado`, no Postgres.

O SERVIÇO NÃO DECIDE NADA. Ele lê o texto e devolve os campos. Julgar se aquela
rua existe, se o CEP bate e se o POI está mesmo nela é trabalho do
`resolver_logradouro`, contra o cadastro do IBGE.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from postal.parser import parse_address

PORTA = int(os.environ.get("LIBPOSTAL_PORTA", "7250"))
# Um município grande cabe folgado; o teto existe para que um cliente com defeito
# não peça a memória inteira do container.
TETO_LOTE = int(os.environ.get("LIBPOSTAL_TETO", "200000"))


def campos(texto: str) -> dict:
    """Os campos de um endereço, com o PRIMEIRO valor de cada rótulo.

    O libpostal repete rótulo quando o texto traz mais de uma ocorrência — dois
    números, duas cidades. No endereço brasileiro a primeira ocorrência é a mais
    específica (`Rua X, 100 - Bairro, Cidade`), então é ela que fica. As demais
    não são descartadas em silêncio: vão em `repetidos`, para quem precisar.
    """
    saida, repetidos = {}, {}
    for valor, rotulo in parse_address(texto or ""):
        if rotulo in saida:
            repetidos.setdefault(rotulo, []).append(valor)
        else:
            saida[rotulo] = valor
    if repetidos:
        saida["_repetidos"] = repetidos
    return saida


class Atendente(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _responder(self, codigo: int, corpo: dict) -> None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):                                          # noqa: N802
        if self.path.rstrip("/") in ("/saude", ""):
            self._responder(200, {"ok": True, "servico": "libpostal"})
        else:
            self._responder(404, {"erro": "use POST /parse ou GET /saude"})

    def do_POST(self):                                         # noqa: N802
        if self.path.rstrip("/") != "/parse":
            self._responder(404, {"erro": "use POST /parse"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            pedido = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError) as erro:
            self._responder(400, {"erro": "JSON invalido: %s" % erro})
            return

        enderecos = pedido.get("enderecos")
        if not isinstance(enderecos, list):
            self._responder(400, {"erro": "esperado {\"enderecos\": [...]}"})
            return
        if len(enderecos) > TETO_LOTE:
            self._responder(413, {"erro": "lote de %d acima do teto de %d"
                                          % (len(enderecos), TETO_LOTE)})
            return

        t0 = time.time()
        campos_de = [campos(e if isinstance(e, str) else "") for e in enderecos]
        dt = time.time() - t0
        self._responder(200, {"campos": campos_de, "quantos": len(campos_de),
                              "segundos": round(dt, 3)})

    def log_message(self, formato, *args):                     # noqa: A003
        # O servidor é interno e chamado uma vez por município: uma linha por
        # requisição basta, e sem o ruído padrão do BaseHTTPRequestHandler.
        sys.stderr.write("[libpostal] %s\n" % (formato % args))


def main() -> int:
    # Uma chamada de aquecimento: o `postal` carrega os 2 GB no primeiro parse,
    # e é melhor pagar isso antes de abrir a porta do que na primeira requisição
    # de verdade, que ficaria com uma latência inexplicável.
    t0 = time.time()
    campos("Rua Exemplo, 100 - Centro, Canoas - RS, 92000-000")
    sys.stderr.write("[libpostal] modelo carregado em %.1f s\n" % (time.time() - t0))

    servidor = ThreadingHTTPServer(("0.0.0.0", PORTA), Atendente)
    servidor.daemon_threads = True
    sys.stderr.write("[libpostal] ouvindo em 0.0.0.0:%d\n" % PORTA)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
