# -*- coding: utf-8 -*-
"""seek_trava.py — a ligacao em analise fica com quem abriu (14/09/2026).

Decisao do dono do produto: "um usuario nao consegue editar uma ligacao que esta
em visualizacao por outro". Ao abrir um caso na SEEK, a tela pede a trava; na
fila dos outros a ligacao fica cinza, "em analise por <nome>", e nao abre para
decidir. Solta quando a pessoa abre outra ligacao, fecha a tela ou fica 2 minutos
sem dar sinal.

    GET  /api/seek/travas          as travas vivas (a tela pede ao abrir e ao reconectar)
    POST /api/seek/trava           assume ou renova a trava de uma ligacao (e o sinal)
    POST /api/seek/trava/liberar   solta as travas da aba (troca de ligacao, aba fechada)

A AUTORIDADE E O BANCO (`seek_trava`, 0105), e nao a memoria da API: producao e
desenvolvimento sao dois processos no mesmo banco. Os avisos para as telas vao por
`seek_eventos` — que e aviso, nao autoridade.

QUEM TRAVA: editor para cima, que e quem decide. Quem so consulta ve a ligacao
sem prender ninguem.
"""
from __future__ import annotations

import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth as _auth
import seek_eventos

router = APIRouter()

#: 2 minutos sem sinal e a trava deixa de valer (decisao do dono do produto). A
#: tela manda sinal a cada 30 s: perder um ou dois nao solta ninguem.
TTL_S = 120
#: A varredura das vencidas roda no maximo uma vez a cada 20 s por empresa, por
#: processo — e e o que avisa as telas que uma trava venceu.
VARRE_S = 20
_varrida: dict = {}
_trava_varrida = threading.Lock()


def _quem() -> _auth.Usuario:
    u = _auth.USUARIO_DA_REQUISICAO.get()
    if u is None:
        raise HTTPException(401, "sessão ausente")
    return u


def _uuid(s, campo="sessao"):
    try:
        return str(uuid.UUID(str(s)))
    except (TypeError, ValueError):
        raise HTTPException(400, "%s inválida" % campo)


def _nome(u):
    return u.nome or u.email or "outro usuário"


def empresas_do_usuario(cur, u):
    """As empresas onde a pessoa pode ter trava: a dela, ou todas para o suporte."""
    if u.nivel == "root":
        cur.execute("select id::text from core.tb_empresas")
        return [r[0] for r in cur.fetchall()]
    return [u.id_empresa] if u.id_empresa else []


def varrer(cur, empresas, forcar=False):
    """Apaga as travas vencidas destas empresas e devolve o que soltou, por empresa.

    DELETE ... RETURNING: com dois processos varrendo junto, cada linha volta para
    um so — e o aviso de liberacao sai uma vez."""
    agora = time.time()
    alvo = []
    with _trava_varrida:
        for e in empresas:
            if forcar or agora - _varrida.get(e, 0) >= VARRE_S:
                _varrida[e] = agora
                alvo.append(e)
    if not alvo:
        return {}
    cur.execute("""delete from radar_comercial.seek_trava
                    where id_empresa = any(%s::uuid[]) and expira_em <= now()
                returning id_empresa::text, ligacao""", (alvo,))
    soltas = {}
    for emp, lig in cur.fetchall():
        soltas.setdefault(emp, []).append(lig)
    return soltas


def avisar_soltas(soltas, motivo, quem=None):
    for emp, ligs in soltas.items():
        seek_eventos.publicar({"ev": "liberada", "id_empresa": emp, "ligacoes": ligs,
                               "quem": quem, "motivo": motivo})


def travadas_por_outro(cur, u, pares):
    """{ligacao: nome} das ligacoes com trava VIVA de outra pessoa.

    `pares` e [(id_empresa, ligacao)] — a busca vai pela chave primaria."""
    if not pares:
        return {}
    cur.execute("""select t.ligacao, coalesce(t.quem_nome, 'outro usuário')
                     from radar_comercial.seek_trava t
                     join unnest(%s::uuid[], %s::text[]) as p(emp, lig)
                       on t.id_empresa = p.emp and t.ligacao = p.lig
                    where t.quem <> %s and t.expira_em > now()""",
                ([p[0] for p in pares], [p[1] for p in pares], u.id))
    return {l: n for l, n in cur.fetchall()}


def abertura_de(cur, u, id_empresa, ligacao):
    """Quando esta pessoa abriu esta ligacao pela ultima vez (para o tempo de avaliacao)."""
    cur.execute("""select aberta_em from radar_comercial.seek_abertura
                    where id_empresa = %s and ligacao = %s and quem = %s
                    order by aberta_em desc limit 1""", (id_empresa, ligacao, u.id))
    r = cur.fetchone()
    return r[0] if r else None


def _travas(cur, empresas):
    cur.execute("""select id_empresa::text, ligacao, quem::text, quem_nome, sessao::text, desde,
                          greatest(0, extract(epoch from expira_em - now()))::int
                     from radar_comercial.seek_trava
                    where id_empresa = any(%s::uuid[]) and expira_em > now()""", (empresas,))
    return [{"id_empresa": e, "ligacao": l, "quem": q, "quem_nome": n, "sessao": s,
             "desde": d.isoformat(timespec="seconds") if d else None, "ttl_s": t}
            for e, l, q, n, s, d, t in cur.fetchall()]


@router.get("/api/seek/travas")
def seek_travas(u: _auth.Usuario = Depends(_quem)):
    """As travas vivas das empresas que a pessoa enxerga."""
    con = _auth.conectar_como(u)
    try:
        cur = con.cursor()
        empresas = empresas_do_usuario(cur, u)
        soltas = varrer(cur, empresas)
        travas = _travas(cur, empresas)
        con.commit()
    finally:
        con.close()
    avisar_soltas(soltas, "expirou")
    return {"travas": travas, "ttl_s": TTL_S, "eu": u.id}


class TravaEntrada(BaseModel):
    ligacao: str
    sessao: str


@router.post("/api/seek/trava")
def seek_trava(e: TravaEntrada, u: _auth.Usuario = Depends(_quem)):
    """Assume a trava da ligacao para esta aba, ou renova (o sinal de 30 s).

    Devolve `{"trava": true}` quando a ligacao e desta pessoa, e 409 com o nome de
    quem esta analisando quando e de outra. A abertura nova (nao a renovacao) fica
    registrada em `seek_abertura` e solta a trava anterior desta aba."""
    lig = str(e.ligacao or "").strip()
    if not lig.isdigit():
        raise HTTPException(400, "ligação inválida")
    sessao = _uuid(e.sessao)
    if not u.pode("editor"):
        # QUEM NAO DECIDE NAO PRENDE NINGUEM: consulta nao vira fila de espera.
        return {"trava": False, "consulta": True, "ttl_s": TTL_S}
    nome = _nome(u)
    con = _auth.conectar_como(u)
    try:
        cur = con.cursor()
        cur.execute("select id_empresa::text from radar_comercial.ligacao_veredito where ligacao = %s", (lig,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "ligação sem julgamento")
        emp = r[0]
        empresas = empresas_do_usuario(cur, u)
        soltas = varrer(cur, list({emp, *empresas}))
        # O `antes` e o retrato de ANTES do upsert (a CTE ve o estado do inicio do
        # comando): e por ele que se sabe se a abertura e nova ou so o sinal.
        cur.execute("""with antes as (
                           select quem, expira_em > now() as viva
                             from radar_comercial.seek_trava
                            where id_empresa = %(emp)s and ligacao = %(lig)s
                       ), grava as (
                           insert into radar_comercial.seek_trava as t
                                  (id_empresa, ligacao, quem, quem_nome, sessao, desde, sinal_em, expira_em)
                           values (%(emp)s, %(lig)s, %(quem)s, %(nome)s, %(sessao)s, now(), now(),
                                   now() + make_interval(secs => %(ttl)s))
                           on conflict (id_empresa, ligacao) do update
                              set quem = excluded.quem, quem_nome = excluded.quem_nome,
                                  sessao = excluded.sessao,
                                  desde = case when t.quem = excluded.quem and t.expira_em > now()
                                               then t.desde else now() end,
                                  sinal_em = now(), expira_em = excluded.expira_em
                            where t.quem = excluded.quem or t.expira_em <= now()
                           returning desde
                       )
                       select (select desde from grava), (select quem::text from antes),
                              (select viva from antes)""",
                    {"emp": emp, "lig": lig, "quem": u.id, "nome": nome, "sessao": sessao, "ttl": TTL_S})
        desde, quem_antes, viva_antes = cur.fetchone()
        if desde is None:
            cur.execute("""select quem::text, quem_nome from radar_comercial.seek_trava
                            where id_empresa = %s and ligacao = %s""", (emp, lig))
            dono = cur.fetchone() or (None, None)
            con.commit()
            avisar_soltas(soltas, "expirou")
            return JSONResponse({"detail": "em análise por %s" % (dono[1] or "outro usuário"),
                                 "trava": False, "ligacao": lig, "id_empresa": emp,
                                 "quem": dono[0], "quem_nome": dono[1]}, status_code=409)
        nova = quem_antes != u.id or not viva_antes
        # A TROCA DE LIGACAO SOLTA A ANTERIOR DESTA ABA (e so desta aba).
        cur.execute("""delete from radar_comercial.seek_trava
                        where id_empresa = any(%s::uuid[]) and quem = %s and sessao = %s
                          and not (id_empresa = %s and ligacao = %s)
                    returning id_empresa::text, ligacao""", (list({emp, *empresas}), u.id, sessao, emp, lig))
        minhas = {}
        for em, l in cur.fetchall():
            minhas.setdefault(em, []).append(l)
        if nova:
            cur.execute("""insert into radar_comercial.seek_abertura (id_empresa, ligacao, quem, quem_nome, sessao)
                           values (%s, %s, %s, %s, %s)""", (emp, lig, u.id, nome, sessao))
        con.commit()
    finally:
        con.close()
    avisar_soltas(soltas, "expirou")
    avisar_soltas(minhas, "trocou", u.id)
    if nova:
        seek_eventos.publicar({"ev": "trava", "id_empresa": emp, "ligacao": lig, "quem": u.id,
                               "quem_nome": nome, "sessao": sessao, "ttl_s": TTL_S})
    return {"trava": True, "nova": nova, "ligacao": lig, "id_empresa": emp,
            "desde": desde.isoformat(timespec="seconds"), "ttl_s": TTL_S}


class LiberarEntrada(BaseModel):
    sessao: str
    ligacao: str | None = None


@router.post("/api/seek/trava/liberar")
def seek_trava_liberar(e: LiberarEntrada, u: _auth.Usuario = Depends(_quem)):
    """Solta as travas desta aba (ou so a de uma ligacao). A tela chama ao fechar."""
    sessao = _uuid(e.sessao)
    lig = str(e.ligacao or "").strip() or None
    con = _auth.conectar_como(u)
    try:
        cur = con.cursor()
        empresas = empresas_do_usuario(cur, u)
        cur.execute("""delete from radar_comercial.seek_trava
                        where id_empresa = any(%s::uuid[]) and quem = %s and sessao = %s
                          and (%s::text is null or ligacao = %s)
                    returning id_empresa::text, ligacao""", (empresas, u.id, sessao, lig, lig))
        soltas = {}
        for em, l in cur.fetchall():
            soltas.setdefault(em, []).append(l)
        con.commit()
    finally:
        con.close()
    avisar_soltas(soltas, "fechou", u.id)
    return {"liberadas": sum(len(v) for v in soltas.values())}
