# -*- coding: utf-8 -*-
"""seek_api.py — a API da tela SEEK, a nova home (13/09/2026).

O EIXO E A LIGACAO. Cada fonte e uma hipotese sobre ela e da o SEU veredito —
achou ou nao achou (decisao do dono do produto) —, a IA da o dela, e a decisao
de uma PESSOA e a oficial, gravada a parte em `seek_decisao` sem tocar no
veredito da IA nem no das fontes.

    GET  /api/seek/fila            a lista leve: todas as ligacoes julgadas
    GET  /api/seek/caso/{ligacao}  a ficha completa, pedida ao abrir o caso
    POST /api/seek/decidir         a decisao oficial, de uma ou de varias
    GET  /api/seek/foto/{id}       a foto publicada do Maps (<img>, token na query)
    GET  /api/seek/busca/{id}      o print da busca web (<img>, token na query)

A FILA E LEVE DE PROPOSITO. Sao 22 mil ligacoes: com a arvore inteira de cada
uma seriam dezenas de megabytes. Ela traz o que filtra (as fontes, a IA, a
decisao); a ficha vem quando alguem abre o caso.

TUDO PELA CONEXAO DO USUARIO (`auth.conectar_como`): a RLS isola a empresa.
Os cruzamentos sao conjuntos em Python, e nao joins caros — ver
`nao-usar-sql-caro-tem-py`.
"""
from __future__ import annotations

import collections
import gzip
import json
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import auth as _auth

router = APIRouter()

#: As fontes, na ordem em que a tela as mostra. `ia` e a do julgamento; `busca`
#: e a busca no Google Maps pelo endereco; `foto` e a foto de rua.
FONTES = ("ia", "receita", "ifood", "maps", "ibge", "estadual", "cadastur", "airbnb", "busca", "foto")
ACOES = ("aprovar", "campo", "revisar", "rejeitar")

#: A FILA FICA 60 s EM MEMORIA, por empresa. Montar custa segundos (conjuntos de
#: 100 mil vinculos e 300 mil POIs); a tela a pede ao abrir e a cada filtro nao.
_CACHE_S = 60
_cache = {}
_trava = threading.Lock()


def _quem() -> _auth.Usuario:
    """O usuario que o PORTAO ja validou. `usuario_atual` le so o cabecalho, e o
    `<img>` das fotos manda o token na query — com ele, toda foto voltava 401.
    Ler o cracha publicado pelo portao tambem poupa uma segunda ida ao banco."""
    u = _auth.USUARIO_DA_REQUISICAO.get()
    if u is None:
        raise HTTPException(401, "sessão ausente")
    return u


def _con(u):
    con = _auth.conectar_como(u)
    with con.cursor() as cur:
        # SEM LACO ANINHADO: o planejador estima mal os anti-joins desta base
        # (13 min contra 0,1 s na fila da avaliacao, 13/09/2026).
        cur.execute("set local enable_nestloop = off")
    return con


def _economias(r):
    return sum(int(x or 0) for x in r)


def _montar_fila(u, cidade: str | None):
    con = _con(u)
    try:
        cur = con.cursor()
        cur.execute("""select ligacao, veredito, avaliado_em,
                              percepcao::jsonb->'checagem'->>'porque', id_empresa
                         from radar_comercial.ligacao_veredito""")
        vered, empresas = {}, set()
        for l, v, t, p, emp in cur.fetchall():
            vered[str(l)] = (v, t, p)
            empresas.add(str(emp))
        ligs = sorted(vered)
        onde_cidade = "and upper(cidade) = upper(%s)" if cidade else ""
        # A EMPRESA NA FRENTE: a chave do cadastro e (id_empresa, num_ligacao), e
        # sem a empresa o Postgres varre os 2,5 milhoes de linhas.
        cur.execute("""select num_ligacao::text, coalesce(nom_cliente,''), coalesce(end_ligacao,''),
                              coalesce(nom_bairro,''), coalesce(cidade,''), coalesce(qualificacao,''),
                              qtd_eco_res, qtd_eco_com, qtd_eco_ind, qtd_eco_pub
                         from resources_root.cadastro_corsan
                        where id_empresa = any(%s::uuid[]) and num_ligacao = any(%s::bigint[]) """ + onde_cidade,
                    (sorted(empresas), [int(x) for x in ligs if x.isdigit()]) + ((cidade,) if cidade else ()))
        cad = {r[0]: r for r in cur.fetchall()}
        cur.execute("""select lp.ligacao, lower(coalesce(p.fonte,'')), p.id
                         from radar_comercial.ligacao_poi lp
                         join radar_comercial.pois p on p.id = lp.poi_id
                        where lp.descartado_em is null and p.fundido_em is null
                          and lp.ligacao = any(%s)""", (list(cad),))
        fontes = collections.defaultdict(set)
        pois_da = collections.defaultdict(set)
        for l, f, pid in cur.fetchall():
            fontes[l].add(f)
            pois_da[l].add(pid)
        todos = sorted({p for s in pois_da.values() for p in s})
        cur.execute("""select distinct poi_id from radar_comercial.poi_evidencia
                        where poi_id = any(%s) and tipo like 'sv_%%'
                          and (bytes_tam is not null or storage_path is not null)""", (todos,))
        com_foto = {r[0] for r in cur.fetchall()}
        cur.execute("""select distinct poi_id from radar_comercial.images_urls
                        where poi_id = any(%s) and url like '%%gps-cs-s%%'
                          and (bytes_tam is not null or storage_path is not null)""", (todos,))
        com_foto |= {r[0] for r in cur.fetchall()}
        cur.execute("""select distinct ligacao from radar_comercial.busca_web
                        where ligacao = any(%s) and tipo = 'endereco' and not bloqueado
                          and (texto is not null or ia is not null)""", (list(cad),))
        com_busca = {str(r[0]) for r in cur.fetchall()}
        cur.execute("""select distinct on (ligacao) ligacao, acao, em, quem_nome
                         from radar_comercial.seek_decisao
                        where ligacao = any(%s) order by ligacao, em desc""", (list(cad),))
        decisao = {str(l): (a, t, q) for l, a, t, q in cur.fetchall()}
    finally:
        con.close()
    colunas = ["ligacao", "titular", "endereco", "bairro", "cidade", "qualificacao", "economias",
               "ia", "checagem", "avaliado_em"] + ["f_" + f for f in FONTES[1:]] + ["decisao", "decidido_em", "decidido_por"]
    linhas = []
    for lig in sorted(cad, key=lambda x: int(x) if x.isdigit() else 0):
        r = cad[lig]
        v, t, porque = vered.get(lig, (None, None, None))
        fs = fontes.get(lig, set())
        d = decisao.get(lig)
        linhas.append([lig, r[1], r[2], r[3], r[4], r[5], _economias(r[6:10]),
                       v, porque, t.isoformat(timespec="minutes") if t else None,
                       "receita" in fs, "ifood" in fs, "maps" in fs, "ibge" in fs, "estadual" in fs,
                       "cadastur" in fs, "airbnb" in fs, lig in com_busca,
                       bool(pois_da.get(lig, set()) & com_foto),
                       d[0] if d else None, d[1].isoformat(timespec="minutes") if d else None,
                       d[2] if d else None])
    return {"colunas": colunas, "linhas": linhas, "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S")}


@router.get("/api/seek/fila")
def seek_fila(request: Request, cidade: str = "", u: _auth.Usuario = Depends(_quem)):
    """Todas as ligacoes julgadas, uma linha cada, com o que filtra.

    COMPRIMIDA UMA VEZ SO: sao 6,4 MB de JSON para 22 mil ligacoes. O gzip e
    feito ao montar e guardado junto no cache — comprimir a cada pedido gastaria
    CPU da API por nada."""
    chave = (u.id_empresa, u.nivel, (cidade or "").upper())
    with _trava:
        c = _cache.get(chave)
    if not (c and time.time() - c[0] < _CACHE_S):
        bruto = json.dumps(_montar_fila(u, cidade or None), ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
        c = (time.time(), bruto, gzip.compress(bruto, 6))
        with _trava:
            _cache[chave] = c
    cab = {"Cache-Control": "no-store", "Vary": "Accept-Encoding"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        cab["Content-Encoding"] = "gzip"
        return Response(c[2], media_type="application/json", headers=cab)
    return Response(c[1], media_type="application/json", headers=cab)


def _registro(r):
    (pid, fonte, nome, cat, end, tel, cnpj, site, insta, metros, mesmo_end, mesmo_num, crit, conf, origem,
     aceito, desc_em, desc_mot, aval, total_aval, status_h, rz, cnae, sit, compl, bruto, if_estado) = r
    return {"poi_id": pid, "fonte": fonte, "nome": nome, "categoria": cat, "endereco": end,
            "telefone": tel, "cnpj": cnpj, "site": site, "instagram": insta,
            "metros": round(float(metros), 1) if metros is not None else None,
            "mesmo_endereco": mesmo_end, "mesmo_numero": mesmo_num, "criterios_ok": crit,
            "confianca": float(conf) if conf is not None else None, "origem": origem, "aceito_por": aceito,
            "descartado_em": desc_em.isoformat(timespec="minutes") if desc_em else None,
            "descartado_motivo": desc_mot,
            "avaliacao": float(aval) if aval is not None else None, "total_avaliacoes": total_aval,
            "horario": status_h, "razao_social": rz, "cnae": cnae, "situacao_cadastral": sit,
            "complemento": compl, "abertura": (bruto or {}).get("data_inicio") if isinstance(bruto, dict) else None,
            "estado_ifood": if_estado}


@router.get("/api/seek/caso/{ligacao}")
def seek_caso(ligacao: str, u: _auth.Usuario = Depends(_quem)):
    """A ficha de uma ligacao: cadastro, registros por fonte, o que a IA viu e decidiu,
    as imagens, a busca web e o historico das decisoes."""
    if not ligacao.isdigit():
        raise HTTPException(400, "ligacao invalida")
    con = _con(u)
    try:
        cur = con.cursor()
        cur.execute("select id_empresa from radar_comercial.ligacao_veredito where ligacao = %s", (ligacao,))
        emp = cur.fetchone()
        if not emp:
            raise HTTPException(404, "ligação sem julgamento")
        cur.execute("""select num_ligacao::text, nom_cliente, end_ligacao, nom_logradouro, nro, nom_bairro, cidade,
                              cod_cep, categoria, sub_categoria, sit_ligacao, qualificacao, qualificacao_motivo,
                              qtd_eco_res, qtd_eco_com, qtd_eco_ind, qtd_eco_pub, tipo_faturamento,
                              num_doc_1, num_celular, cod_latitude::float8, cod_longitude::float8, num_medidor,
                              id_empresa
                         from resources_root.cadastro_corsan
                        where id_empresa = %s and num_ligacao = %s::bigint""", (emp[0], ligacao))
        c = cur.fetchone()
        if not c:
            raise HTTPException(404, "ligacao nao encontrada")
        base = dict(zip(["ligacao", "titular", "endereco", "logradouro", "numero", "bairro", "cidade", "cep",
                         "categoria", "subcategoria", "situacao", "qualificacao", "qualificacao_motivo",
                         "eco_res", "eco_com", "eco_ind", "eco_pub", "tipo_faturamento", "documento",
                         "telefone", "lat", "lng", "medidor", "id_empresa"], c))
        base["id_empresa"] = str(base["id_empresa"]) if base["id_empresa"] else None
        base["economias"] = _economias([base["eco_res"], base["eco_com"], base["eco_ind"], base["eco_pub"]])
        cur.execute("""select p.id, lower(coalesce(p.fonte,'')), p.nome, p.categoria, p.endereco, p.telefone,
                              coalesce(rd.cnpj, im.cnpj, p.cnpj), p.website, p.instagram,
                              lp.metros, lp.mesmo_endereco, lp.mesmo_numero, lp.criterios_ok, lp.confianca,
                              lp.origem, lp.aceito_por, lp.descartado_em, lp.descartado_motivo,
                              md.avaliacao, md.total_avaliacoes, md.status_horario,
                              rd.razao_social, rd.cnae, rd.situacao_cadastral, rd.complemento, rd.bruto,
                              im.estado_detalhe
                         from radar_comercial.ligacao_poi lp
                         join radar_comercial.pois p on p.id = lp.poi_id and p.fundido_em is null
                         left join radar_comercial.maps_data md on md.poi_id = p.id
                         left join radar_comercial.receita_data rd on rd.poi_id = p.id
                         left join radar_comercial.ifood_merchant im on im.poi_id = p.id
                        where lp.ligacao = %s
                        order by lp.descartado_em is not null, lp.metros nulls last""", (ligacao,))
        regs = [_registro(r) for r in cur.fetchall()]
        ids = [r["poi_id"] for r in regs if not r["descartado_em"]]
        cur.execute("""select veredito, justificativa, percepcao::jsonb, modelo, avaliado_em
                         from radar_comercial.ligacao_veredito where ligacao = %s""", (ligacao,))
        v = cur.fetchone()
        ia = None
        if v:
            p = v[2] or {}
            resp = p.get("resposta") or {}
            dados = p.get("dados") or ""
            ia = {"veredito": v[0], "motivo": resp.get("motivo") or v[1], "justificativa": v[1],
                  "aderentes": resp.get("aderentes") or [], "nao_combinam": resp.get("nao_combinam") or [],
                  "registros_antigos": resp.get("registros") or [],
                  "checagem": p.get("checagem"), "processo": p.get("processo") or "antigo",
                  "fotos_vistas": p.get("fotos") or [], "modelo": v[3],
                  "avaliado_em": v[4].isoformat(timespec="minutes") if v[4] else None,
                  "texto_busca": dados.split("TEXTO DA BUSCA NA WEB", 1)[1].split(":", 1)[-1].strip()
                  if "TEXTO DA BUSCA NA WEB" in dados else None}
        cur.execute("""select id, poi_id, tipo, data_imagem, capturado_em from radar_comercial.poi_evidencia
                        where poi_id = any(%s) and tipo like 'sv_%%'
                          and (bytes_tam is not null or storage_path is not null)
                        order by poi_id, tipo""", (ids,))
        imagens = [{"id": "sv%s" % i, "poi_id": pid, "fonte": "foto", "tipo": t,
                    "quando": d or (cap.strftime("%Y-%m") if cap else None),
                    "url": "/api/sv/%s/%s" % (pid, t)} for i, pid, t, d, cap in cur.fetchall()]
        cur.execute("""select id, poi_id, data_imagem from radar_comercial.images_urls
                        where poi_id = any(%s) and url like '%%gps-cs-s%%'
                          and (bytes_tam is not null or storage_path is not null)
                        order by poi_id, ordem nulls last, id limit 12""", (ids,))
        imagens += [{"id": "mp%s" % i, "poi_id": pid, "fonte": "maps", "tipo": "foto publicada",
                     "quando": d, "url": "/api/seek/foto/%s" % i} for i, pid, d in cur.fetchall()]
        cur.execute("""select id, consulta, motor, navegador, feito_em, texto is not null, dados is not null
                         from radar_comercial.busca_web
                        where ligacao = %s and tipo = 'endereco' and not bloqueado
                          and (texto is not null or ia is not null)
                        order by (motor = 'google_maps') desc, feito_em desc limit 1""", (ligacao,))
        b = cur.fetchone()
        busca = None
        if b:
            cur.execute("select coalesce(texto, ia::text) from radar_comercial.busca_web where id = %s", (b[0],))
            busca = {"id": b[0], "consulta": b[1], "motor": b[2], "navegador": b[3],
                     "feito_em": b[4].isoformat(timespec="minutes") if b[4] else None,
                     "texto": (cur.fetchone() or [None])[0], "print": "/api/seek/busca/%s" % b[0] if b[6] else None}
        cur.execute("""select acao, motivo, observacoes, quem_nome, em, lote from radar_comercial.seek_decisao
                        where ligacao = %s order by em desc limit 50""", (ligacao,))
        decisoes = [{"acao": a, "motivo": m, "observacoes": o, "quem": q,
                     "em": e.isoformat(timespec="minutes"), "lote": str(lo) if lo else None}
                    for a, m, o, q, e, lo in cur.fetchall()]
    finally:
        con.close()
    fontes = {}
    for f in FONTES[1:-2]:
        vivos = [r for r in regs if r["fonte"] == f and not r["descartado_em"]]
        fontes[f] = {"achou": bool(vivos), "registros": [r for r in regs if r["fonte"] == f]}
    fontes["busca"] = {"achou": bool(busca), "busca": busca}
    fontes["foto"] = {"achou": any(i["fonte"] == "foto" for i in imagens) or any(i["fonte"] == "maps" for i in imagens)}
    return {"ligacao": ligacao, "base": base, "fontes": fontes, "ia": ia, "imagens": imagens,
            "decisoes": decisoes, "decisao": decisoes[0] if decisoes else None,
            # SEM DADO no Comercial Radar: a tela mostra a secao vazia, com o aviso.
            "os": None, "impacto": None}


class DecisaoEntrada(BaseModel):
    ligacoes: list[str]
    acao: str
    motivo: str | None = None
    observacoes: dict | None = None


@router.post("/api/seek/decidir")
def seek_decidir(e: DecisaoEntrada, u: _auth.Usuario = Depends(_quem)):
    """Grava a decisao oficial de uma ligacao ou de varias (as filtradas, de uma vez)."""
    if not u.pode("editor"):
        raise HTTPException(403, "decidir exige nível editor ou acima")
    acao = (e.acao or "").strip().lower()
    if acao not in ACOES:
        raise HTTPException(400, "ação inválida: use " + ", ".join(ACOES))
    motivo = (e.motivo or "").strip() or None
    if acao == "rejeitar" and not motivo:
        raise HTTPException(400, "rejeitar exige o motivo escrito")
    ligs = sorted({str(x).strip() for x in (e.ligacoes or []) if str(x).strip().isdigit()})
    if not ligs:
        raise HTTPException(400, "nenhuma ligação")
    if len(ligs) > 50000:
        raise HTTPException(400, "no máximo 50.000 ligações por vez")
    lote = str(uuid.uuid4()) if len(ligs) > 1 else None
    from psycopg2.extras import execute_values
    con = _con(u)
    try:
        cur = con.cursor()
        # A EMPRESA E A DA LIGACAO JULGADA, e nao a do usuario: o suporte (root)
        # decide por qualquer empresa, e a linha precisa cair na certa.
        cur.execute("""select ligacao, id_empresa from radar_comercial.ligacao_veredito
                        where ligacao = any(%s)""", (ligs,))
        emp = {str(l): i for l, i in cur.fetchall()}
        faltam = [l for l in ligs if l not in emp]
        linhas = [(emp[l], l, acao, motivo, json.dumps(e.observacoes, ensure_ascii=False) if e.observacoes else None,
                   lote, u.id, u.nome or u.email) for l in ligs if l in emp]
        execute_values(cur, """insert into radar_comercial.seek_decisao
                                   (id_empresa, ligacao, acao, motivo, observacoes, lote, quem, quem_nome)
                               values %s""", linhas, page_size=1000)
        gravadas = cur.rowcount if len(linhas) <= 1000 else len(linhas)
        con.commit()
    finally:
        con.close()
    with _trava:
        _cache.clear()
    return {"gravadas": gravadas, "pedidas": len(ligs), "sem_veredito": faltam[:20],
            "lote": lote, "acao": acao}


def _bytes(u, tabela, ident):
    """Os bytes de uma linha, do Storage ou da coluna `dados` — o mesmo leitor da
    rota `/api/sv/`, pela conexao do usuario (a RLS decide se ele pode ver)."""
    import imagens
    con = _con(u)
    try:
        b = imagens._buscar(con, tabela, "id = %s", (ident,), 1)
        return b[0] if b else None
    finally:
        con.close()


@router.get("/api/seek/foto/{image_id}")
def seek_foto(image_id: int, u: _auth.Usuario = Depends(_quem)):
    b = _bytes(u, "images_urls", image_id)
    if not b:
        raise HTTPException(404, "sem imagem")
    return Response(content=b, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/seek/busca/{busca_id}")
def seek_busca_print(busca_id: int, u: _auth.Usuario = Depends(_quem)):
    b = _bytes(u, "busca_web", busca_id)
    if not b:
        raise HTTPException(404, "sem print")
    return Response(content=b, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})
