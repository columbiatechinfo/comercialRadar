# -*- coding: utf-8 -*-
"""validacao_api.py — a validação por cidade ou área, vista e comandada pela tela (16/09/2026).

Ler é para todos da empresa; criar, pausar, retomar e cancelar é de admin, como toda ação que executa processo
(`SO_ADMIN` no server.py). O ambiente é o desta API: a de desenvolvimento cria e mostra só validações de
desenvolvimento, que só o executor de desenvolvimento pega.
"""
from __future__ import annotations

from datetime import datetime

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import auth as _auth
import validacao as va

router = APIRouter()
FRONT = Path(__file__).parent / "frontend"


def _quem() -> _auth.Usuario:
    u = _auth.USUARIO_DA_REQUISICAO.get()
    if u is None:
        raise HTTPException(401, "sessão ausente")
    return u


def _iso(x):
    return x.isoformat(timespec="seconds") if isinstance(x, datetime) else x


def _saida(v):
    lotes = {}
    for t in v["tarefas"]:
        lote = lotes.setdefault(t["lote"], {"lote": t["lote"], "etapas": {}})
        lote["etapas"][t["etapa"]] = {k: _iso(t[k]) for k in ("estado", "worker", "tentativas", "resumo", "erro",
                                                              "iniciado_em", "terminado_em")}
    feitas = sum(1 for t in v["tarefas"] if t["estado"] in ("ok", "cancelada"))
    return {"id": v["id"], "cidade": v["cidade"], "area": v["area"], "ligacoes": v["ligacoes"], "estado": v["estado"],
            "criado_em": _iso(v["criado_em"]), "terminado_em": _iso(v["terminado_em"]), "erro": v["erro"],
            "ordem": (v["progresso"] or {}).get("ordem") or {}, "por_etapa": v["por_etapa"],
            "tarefas": len(v["tarefas"]), "tarefas_feitas": feitas, "lotes": [lotes[n] for n in sorted(lotes)]}


@router.get("/api/validacoes")
def listar(limite: int = 20, u: _auth.Usuario = Depends(_quem)):
    con = _auth.conectar_como(u)
    try:
        vals = va.status(con, None, va.AMBIENTE, max(1, min(int(limite or 20), 100)))
    finally:
        con.close()
    return {"ambiente": va.AMBIENTE, "tetos": va.TETO,
            "etapas": [{"id": nome, "ordem": e.ordem, "recurso": e.recurso, "ia": e.precisa_ia,
                        "depende": list(e.depende)} for nome, e in va.ETAPAS.items()],
            "validacoes": [_saida(v) for v in vals]}


class NovaValidacao(BaseModel):
    cidade: str
    area: str | None = None
    refazer: bool = False
    empresa: str | None = None


@router.post("/api/validacoes")
def criar(b: NovaValidacao, u: _auth.Usuario = Depends(_quem)):
    if not u.pode("admin"):
        raise HTTPException(403, "exige nível admin")
    empresa = u.id_empresa or (b.empresa if u.nivel == "root" else None)
    if not empresa:
        raise HTTPException(400, "escolha a empresa")
    if not (b.cidade or "").strip():
        raise HTTPException(400, "informe a cidade")
    con = _auth.conectar_como(u)
    try:
        vid, msg = va.criar(con, empresa, b.cidade.strip(), (b.area or "").strip() or None, pedido_por=u.id,
                            refazer=bool(b.refazer))
    except SystemExit as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()
    return {"id": vid, "mensagem": msg}


@router.post("/api/validacoes/{vid}/{acao}")
def mudar(vid: int, acao: str, u: _auth.Usuario = Depends(_quem)):
    if acao not in ("pausar", "retomar", "cancelar"):
        raise HTTPException(404, "ação desconhecida")
    if not u.pode("admin"):
        raise HTTPException(403, "exige nível admin")
    con = _auth.conectar_como(u)
    try:
        n = va.mudar_estado(con, vid, acao)
    finally:
        con.close()
    if not n:
        raise HTTPException(409, "o estado da validação não permite %s" % acao)
    return {"ok": True}


@router.get("/validacao")
def pagina():
    """A pagina e publica como a da gestao: o `sessao.js` pede o login, e os dados so vem pelas rotas acima."""
    alvo = FRONT / "validacao.html"
    if not alvo.exists():
        raise HTTPException(404, "validacao.html não gerada — rode python3 frontend/seek_fonte/montar.py")
    html = alvo.read_text(encoding="utf-8")
    js = FRONT / "sessao.js"
    if js.exists():
        html = html.replace("/static/sessao.js", "/static/sessao.js?v=%d" % int(js.stat().st_mtime))
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})
