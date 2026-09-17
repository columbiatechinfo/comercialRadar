# -*- coding: utf-8 -*-
"""frota_cliente.py — como uma etapa pede trabalho à frota permanente (dono do produto, 17/09/2026).

    from frota_cliente import enviar, acompanhar
    lote = enviar("ifood", "ifood.ponto", [{"nome": "Centro", "lat": -29.68, "lon": -53.81}, ...], pedido_por="job68")
    for t in acompanhar(lote):
        t["estado"], t["argumentos"], t["resultado"], t["erro"]

SEM SERVIÇO NO AR, A TAREFA ESPERA na fila e o acompanhamento diz isso em voz alta — nunca cai para um navegador
local nem para o IP da casa (regra 4). Uma conexão, em autocommit, por quem pede.
"""
from __future__ import annotations

import json
import os
import time

AMBIENTE = "desenvolvimento" if os.environ.get("RADAR_AMBIENTE", "").strip() == "desenvolvimento" else "producao"


def _conectar():
    import realtime_ingest
    con = realtime_ingest.conectar()
    con.autocommit = True
    return con


def enviar(site, tipo, lista_de_argumentos, lote=None, pedido_por=None, prioridade=5, con=None):
    """Grava uma tarefa por item de `lista_de_argumentos` (dicts JSON). Devolve o lote."""
    import psycopg2.extras
    lote = lote or "%s:%s:%d" % (pedido_por or "etapa", site, int(time.time() * 1000))
    proprio = con is None
    con = con or _conectar()
    try:
        with con.cursor() as cur:
            psycopg2.extras.execute_values(
                cur, """insert into navegacao.tarefa (ambiente, site, tipo, argumentos, lote, pedido_por, prioridade)
                        values %s""",
                [(AMBIENTE, site, tipo, json.dumps(a, ensure_ascii=False), lote, pedido_por, prioridade)
                 for a in lista_de_argumentos],
                template="(%s, %s, %s, %s::jsonb, %s, %s, %s)", page_size=500)
    finally:
        if proprio:
            con.close()
    return lote


def acompanhar(lote, intervalo=2.0, con=None, log=print, aviso_fila_s=60):
    """Gera cada tarefa do lote quando ela termina (ok ou erro), na ordem em que terminam, até acabar o lote."""
    proprio = con is None
    con = con or _conectar()
    vistos = set()
    ultimo_movimento = time.time()
    avisado = 0.0
    try:
        while True:
            with con.cursor() as cur:
                cur.execute("""select id, estado, argumentos, resultado, erro, dono, tentativas
                                 from navegacao.tarefa
                                where lote = %s and estado in ('ok', 'erro', 'cancelada') and not (id = any(%s))
                                order by terminado_em nulls last, id""", (lote, list(vistos)))
                prontas = cur.fetchall()
                cur.execute("""select count(*) filter (where estado = 'fila'), count(*) filter (where estado = 'rodando')
                                 from navegacao.tarefa where lote = %s""", (lote,))
                na_fila, rodando = cur.fetchone()
            for tid, estado, args, resultado, erro, dono, tent in prontas:
                vistos.add(tid)
                ultimo_movimento = time.time()
                yield {"id": tid, "estado": estado, "argumentos": args, "resultado": resultado, "erro": erro,
                       "dono": dono, "tentativas": tent}
            if not na_fila and not rodando:
                return
            if rodando:
                ultimo_movimento = max(ultimo_movimento, time.time() - aviso_fila_s / 2)
            if na_fila and not rodando and time.time() - ultimo_movimento > aviso_fila_s \
                    and time.time() - avisado > aviso_fila_s:
                log("frota: %d tarefa(s) do lote %s na fila e nenhuma rodando — o serviço da frota (%s) está no ar?"
                    % (na_fila, lote, AMBIENTE))
                avisado = time.time()
            time.sleep(intervalo)
    finally:
        if proprio:
            con.close()


def cancelar(lote, con=None):
    proprio = con is None
    con = con or _conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""update navegacao.tarefa set estado = 'cancelada', terminado_em = now()
                            where lote = %s and estado = 'fila'""", (lote,))
            return cur.rowcount
    finally:
        if proprio:
            con.close()
