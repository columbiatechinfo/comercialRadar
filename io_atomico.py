"""
io_atomico.py — Gravação de JSON incremental que não derruba a rodada.

Todo coletor grava o resultado num `.tmp` e renomeia por cima do `.json` final,
para o leitor nunca ver meio arquivo. No Windows esse rename é a parte frágil:
`os.replace` sobre um arquivo ABERTO por outro processo devolve
`PermissionError [WinError 5]`, porque o `open()` do Python não pede
FILE_SHARE_DELETE. E há sempre outro processo lendo — o watcher do server abre o
mesmo JSON a cada 2 segundos para ingerir no Postgres e alimentar o mapa.

Na madrugada de 06/08/2026 essa colisão matou o enriquecimento aos 18 minutos,
com 437 de 7.567 POIs: a exceção subiu do `salvar()` e levou o `run()` junto.
O processo não morreu numa busca nem num bloqueio — morreu ao guardar o que já
tinha achado.

Duas garantias aqui:
  1. a troca é REPETIDA enquanto o leitor segura o arquivo (janela de ~3 s
     contra uma leitura de milissegundos);
  2. se ainda assim falhar, o save é PULADO e a rodada CONTINUA. O payload é
     sempre completo, nunca um delta — o próximo save regrava tudo, então
     perder um deles não perde dado nenhum.

Sem dependências: é importado por coletores que rodam como subprocesso.
"""

import os
import json
import time
from pathlib import Path

TENTATIVAS = 12
ESPERA_S = 0.25


def escrever_json(destino, payload, tentativas: int = TENTATIVAS,
                  espera: float = ESPERA_S) -> bool:
    """Grava `payload` em `destino` de forma atômica. True se gravou.

    Nunca levanta: uma falha de escrita não pode custar as horas de coleta que
    já estão em memória."""
    destino = Path(destino)
    tmp = destino.with_suffix(destino.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    except Exception as e:
        print(f"⚠️ save: não consegui escrever {tmp.name} ({type(e).__name__}: "
              f"{str(e)[:90]}) — segue o baile", flush=True)
        return False

    for i in range(tentativas):
        try:
            os.replace(tmp, destino)
            return True
        except PermissionError:
            # o leitor está com o arquivo aberto; ele solta em milissegundos
            time.sleep(espera)
        except Exception as e:
            print(f"⚠️ save: troca falhou ({type(e).__name__}: {str(e)[:90]})",
                  flush=True)
            return False

    print(f"⚠️ save: {destino.name} preso por outro processo após "
          f"{tentativas * espera:.0f}s — pulei este save (o próximo regrava "
          f"tudo)", flush=True)
    return False
