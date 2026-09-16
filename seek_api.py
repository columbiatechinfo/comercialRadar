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
    GET  /api/seek/rua/poi/{id}    a foto de rua do julgamento leve, no pin do Maps (`poi_evidencia`)
    GET  /api/seek/rua/ligacao/{id}  a foto de rua no hidrometro (`ligacao_evidencia`)
    GET  /api/seek/ficha/{id}      o print da ficha do CNPJ no Serasa (`ficha_cnpj_web`)
    GET  /api/seek/caso/{ligacao}/dados  o texto integral que a IA recebeu

    /api/seek/trava*               a ligacao em analise fica com quem abriu (`seek_trava.py`)
    /api/seek/gestao/*  e /gestao  a gestao das aprovacoes, admin para cima (`seek_gestao.py`)
    avisos em tempo real           pelo `/ws`, entre processos pelo Realtime (`seek_eventos.py`)

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
import seek_eventos
import seek_gestao
import seek_trava

router = APIRouter()
# A TRAVA E A GESTAO (14/09/2026) moram em modulos proprios e entram por aqui, e
# nao por mais um `include_router` no server.py.
router.include_router(seek_trava.router)
router.include_router(seek_gestao.router)

#: As fontes, na ordem em que a tela as mostra. `ia` e a do julgamento; `busca`
#: e a busca web pelo endereco; `foto` sao as fotos de rua e as publicadas.
#:
#: BUSCA E FOTO SO "ACHAM" QUANDO CONFIRMAM (dono do produto, 13/09/2026): a
#: coleta nao pinta de verde. A busca conta quando a IA usou um resultado que
#: confirma um registro (`resposta.busca[].confirma`); a foto, quando a IA
#: identificou o comercio nela (`resposta.fotos.confirmam`). Veredito do prompt
#: anterior, sem esses campos, conta como nao confirmado ate ser rejulgado.
FONTES = ("ia", "receita", "ifood", "maps", "ibge", "estadual", "cadastur", "airbnb", "busca", "foto")
ACOES = ("aprovar", "campo", "revisar", "rejeitar")
#: Os motores da busca web desde 13/09/2026 (ver `buscar_web.py`).
MOTORES_DA_BUSCA = ("duckduckgo", "yahoo", "google")

#: A FILA FICA 60 s EM MEMORIA, por empresa. Montar custa segundos (conjuntos de
#: 100 mil vinculos e 300 mil POIs); a tela a pede ao abrir e a cada filtro nao.
_CACHE_S = 60
_cache = {}
_trava = threading.Lock()


def _decisao_de_fora(evento):
    """DECISAO GRAVADA POR OUTRO PROCESSO (a producao, ou outra replica) tambem
    invalida a fila guardada aqui — senao quem abrisse a tela nos 60 s seguintes
    receberia o status velho."""
    if evento.get("ev") in ("decisao", "decisao_lote"):
        with _trava:
            _cache.clear()


seek_eventos.ouvir(_decisao_de_fora)


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


def _e_serasa(x):
    """A prova da resposta leve que e a ficha do Serasa (`busca[].fonte`, 15/09/2026)."""
    return str((x or {}).get("fonte") or "").strip().lower() == "serasa"


def _busca_confirma(itens):
    """Se a IA usou ao menos um resultado da BUSCA NA WEB que CONFIRMA um registro.

    O SERASA NAO ACENDE O VERDE DA BUSCA (15/09/2026): no julgamento leve a IA diz
    `{fonte: "Serasa"|"busca na web", poi, confirma}`, e a ficha do Serasa e a
    Receita republicada — a mesma fonte do CNPJ, nao a web. Ligacao em que so o
    Serasa confirmou ficava com a busca verde na fila e na ficha."""
    return isinstance(itens, list) and any(isinstance(x, dict) and x.get("confirma") is True and not _e_serasa(x)
                                           for x in itens)


def _url_normal(u):
    """A mesma pagina com cara de mesma: o Yahoo embrulha o link num redirecionador
    (`.../RU=<link>/RK=...`), e um motor da `https://www.` onde o outro nao da."""
    import urllib.parse
    s = str(u or "").strip()
    if "/RU=" in s:
        s = urllib.parse.unquote(s.split("/RU=", 1)[1].split("/RK=", 1)[0])
    s = s.lower().split("#", 1)[0]
    for p in ("https://", "http://"):
        if s.startswith(p):
            s = s[len(p):]
    if s.startswith("www."):
        s = s[4:]
    return s.rstrip("/")


def _prova_busca(resp, buscas):
    """([usados], recusados, [serasa]): os resultados que a IA disse que confirmam um registro,
    com titulo e endereco da pagina. O numero que ela cita e a posicao na lista do
    motor SO COM OS RESULTADOS NO ENDERECO — a mesma lista de `buscar_web.texto_para_dossie`.
    O mesmo resultado achado pelos dois motores aparece uma vez, com os dois nomes.

    A RESPOSTA LEVE (15/09/2026) nao cita motor nem numero: diz so a fonte ("Serasa" ou
    "busca na web") e o registro. A do Serasa vai para a terceira lista, e a da web entra
    em `usados` sem titulo — o texto que a IA leu esta em `buscas[].texto_anotado`."""
    por_motor = {b["motor"]: b for b in buscas}
    usados, chaves, recusados, serasa = [], {}, 0, []
    for x in resp.get("busca") or []:
        if not isinstance(x, dict):
            continue
        if x.get("fonte"):
            if _e_serasa(x):
                serasa.append({"poi": x.get("poi"), "confirma": x.get("confirma") is True})
            elif x.get("confirma") is True:
                if not any(u.get("fonte") and u.get("poi") == x.get("poi") for u in usados):
                    usados.append({"fonte": str(x.get("fonte")), "motores": [], "numero": None, "poi": x.get("poi"),
                                   "casa": x.get("casa") or "", "titulo": None, "url": None, "trecho": ""})
            else:
                recusados += 1
            continue
        if x.get("confirma") is not True:
            recusados += 1
            continue
        m = str(x.get("motor") or "").strip().lower().split(" ")[0]
        try:
            n = int(x.get("resultado"))
        except (TypeError, ValueError):
            n = 0
        b = por_motor.get(m)
        r = b["resultados"][n - 1] if b and 1 <= n <= len(b["resultados"]) else {}
        # DUAS CHAVES: o endereco da pagina e o comeco do titulo com o registro —
        # os motores cortam o titulo em pontos diferentes ("... - RS ..." e "... ...").
        k_url = _url_normal(r.get("url")) or None
        k_tit = ("%s|%s" % (x.get("poi"), " ".join(str(r.get("titulo") or "").lower().split())[:32])
                 if r.get("titulo") else None)
        u = chaves.get(k_url) or chaves.get(k_tit)
        if u:
            if m not in u["motores"]:
                u["motores"].append(m)
            continue
        u = {"motores": [m], "numero": n, "poi": x.get("poi"), "casa": x.get("casa") or "",
             "titulo": r.get("titulo"), "url": r.get("url"), "trecho": (r.get("trecho") or "")[:240]}
        for k in (k_url, k_tit) if r else ("%s:%s" % (m, n),):
            if k:
                chaves[k] = u
        usados.append(u)
    return usados, recusados, serasa


def _prova_fotos(resp, refs):
    """O que a IA disse das fotos, com o POI e a visada de cada uma que confirma;
    None quando o veredito e do prompt anterior, que nao dizia."""
    f = resp.get("fotos")
    if not isinstance(f, dict):
        return None
    quais = []
    for q in f.get("quais") or []:
        try:
            i = int(q)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(refs or []):
            quais.append(refs[i - 1])
    return {"confirmam": f.get("confirmam") is True, "o_que_mostram": f.get("o_que_mostram") or "", "quais": quais}


def _julgamento():
    """Os modulos do julgamento leve, para a ficha mostrar o que a IA recebeu com as MESMAS regras que o montaram
    (15/09/2026): a foto de rua escolhida, a fachada da seta, a fonte de cada resultado da busca, as redes sociais
    e os comentarios. Recalcular aqui do jeito da tela seria contar outra historia que a do veredito.

    A IMAGEM DA API NAO ABRE O cv2 (falta a `libxcb`, o `opencv-python` com tela ganha do headless), e
    `fachada_da_seta` importa de `desenho_seta` so a altura da ponta da seta — o desenho nao roda aqui. Sem o cv2,
    entra no lugar um modulo so com `PONTA_REL`, lida do proprio `desenho_seta.py`: a constante continua num lugar so.
    Import tardio: a rota da fila e as demais nao pagam por isso."""
    import sys
    if "desenho_seta" not in sys.modules:
        try:
            import desenho_seta  # noqa: F401
        except Exception:                                      # noqa: BLE001
            import os
            import re
            import types
            m = types.ModuleType("desenho_seta")
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "desenho_seta.py"), encoding="utf-8") as f:
                m.PONTA_REL = float(re.search(r"^PONTA_REL\s*=\s*([0-9.]+)", f.read(), re.M).group(1))
            sys.modules["desenho_seta"] = m
    import avaliar_enxuto
    import fachada_da_seta
    import fonte_da_busca
    import provas_datadas
    return avaliar_enxuto, fachada_da_seta, fonte_da_busca, provas_datadas


#: A COR DE CADA FACHADA NA FOTO DE RUA, pelo papel que o codigo deu a ela (`fachada_da_seta`, 15/09/2026):
#: seta = a fachada da ponta da seta; divisa = a ponta na divisa, sem desempate; vizinho_com_nome = placa de vizinho
#: com o nome em outra fonte (vale); vizinho_sem_nome = placa ou sinal de vizinho sem o nome em fonte nenhuma (nao
#: vale); sem_sinal = fachada sem texto nem sinal.
PAPEIS_DA_FACHADA = ("seta", "divisa", "vizinho_com_nome", "vizinho_sem_nome", "sem_sinal")


def _leitura_da_rua(fds, ae, leitura, mira_x, pecas, excluir):
    """A leitura da foto de rua como a tela desenha: cada fachada com a caixa (0-1000), os textos e o papel dela, e o
    texto que a IA recebeu (`para_julgamento`). A leitura do formato anterior (sem `fachadas`) volta so com o texto."""
    if not isinstance(leitura, dict):
        return None
    if leitura.get("fachadas") is None:
        return {"fachadas": [], "texto_ia": ae._texto_da_leitura(leitura), "vale": None, "desempate": None,
                "resumo": leitura.get("resumo"), "mira_x": mira_x, "formato": "anterior"}
    alvo, divisa = fds.escolher_final(leitura, mira_x)
    texto, vale = fds.para_julgamento(leitura, mira_x, pecas, excluir)
    saida = []
    for f in fds.fachadas(leitura):
        textos = fds._textos(f)
        nome_em = []
        for t in textos:
            if t.get("tipo") == "aluga_vende":
                continue
            fonte = fds.casar(t["texto"], pecas, excluir)
            if fonte:
                nome_em.append({"texto": t["texto"], "fonte": fonte})
        sinais = [str(s) for s in (f.get("sinais_sem_texto") or []) if str(s).strip()]
        if f is alvo:
            papel = "seta"
        elif divisa and any(f is d for d in divisa):
            papel = "divisa"
        elif nome_em:
            papel = "vizinho_com_nome"
        elif sinais or any(t.get("tipo") != "aluga_vende" for t in textos):
            papel = "vizinho_sem_nome"
        else:
            papel = "sem_sinal"
        saida.append({"n": f.get("n"), "caixa": fds._caixa(f), "descricao": f.get("descricao") or "",
                      "textos": [{"texto": t["texto"], "tipo": t.get("tipo"), "aluga": fds._e_aluga(t)} for t in textos],
                      "sinais_sem_texto": sinais, "sinal_comercial": bool(f.get("sinal_comercial")),
                      "encoberta": bool(f.get("encoberta")), "papel": papel, "nome_em": nome_em})
    return {"fachadas": saida, "texto_ia": texto, "vale": vale, "desempate": leitura.get("desempate"),
            "resumo": leitura.get("resumo"), "mira_x": mira_x, "formato": "fachadas",
            "aluga_na_seta": fds.aluga_na_seta(leitura, mira_x)}


def _foto_de_rua(cur, ligacao, ids, mods, pecas, excluir, julgado_em=None):
    """A FOTO DE RUA QUE O JULGAMENTO LEVE USA, e so ela (15/09/2026): a de frente do registro com pin do Maps mais
    perto do hidrometro (`avaliar_enxuto.poi_da_foto_de_rua`), quando ele tem a captura nova; sem registro com pin a
    ate 60 m, a do hidrometro (`ligacao_evidencia`). Registro com pin e sem a captura nova fica sem foto de rua —
    como no julgamento. None quando nao ha foto."""
    ae, fds, _fdb, pdat = mods
    escolha = ae.poi_da_foto_de_rua(cur, ligacao, ids) if ids else None
    if escolha and escolha[2]:
        cur.execute("""select id, data_imagem, distancia_m, leitura, mira_x, capturado_em
                         from radar_comercial.poi_evidencia where poi_id = %s and tipo = 'sv_frente'""", (escolha[0],))
        r = cur.fetchone()
        origem, pid, tipo, rota = "pin", escolha[0], "sv_frente", "/api/seek/rua/poi/%s"
    elif not escolha:
        cur.execute("""select id, data_imagem, distancia_m, leitura, mira_x, capturado_em
                         from radar_comercial.ligacao_evidencia
                        where ligacao = %s and tipo = 'sv_frente' and (dados is not null or storage_path is not null)""",
                    (str(ligacao),))
        r = cur.fetchone()
        origem, pid, tipo, rota = "hidrometro", None, "sv_hidrometro", "/api/seek/rua/ligacao/%s"
    else:
        return None
    if not r:
        return None
    ev, data, dist, leitura, mira_x, cap = r
    try:
        # RECAPTURADA DEPOIS DO JULGAMENTO: a IA viu a captura anterior, que a nova substituiu
        depois = bool(cap and julgado_em and cap > julgado_em)
    except TypeError:                                          # data com e sem fuso
        depois = False
    return {"id": "rua%s%s" % ("p" if pid else "l", ev), "poi_id": pid, "ligacao": None if pid else str(ligacao),
            "depois_do_julgamento": depois,
            "fonte": "foto", "tipo": tipo, "origem": origem,
            "url": (rota % ev) + ("?v=%d" % int(cap.timestamp()) if cap else ""),
            "quando": data or (cap.strftime("%Y-%m") if cap else None),
            "mes_ano": pdat.mes_ano(pdat.data_de_texto(data)) if data else None,
            "distancia_m": round(float(dist)) if dist is not None else None,
            "capturado_em": cap.isoformat(timespec="minutes") if cap else None, "mira": mira_x is not None,
            "metros_do_hidrometro": round(float(escolha[1]), 1) if escolha else None,
            "leitura": _leitura_da_rua(fds, ae, leitura, mira_x, pecas, excluir)}


class _Parte:
    """UMA PARTE OPCIONAL DA FICHA: se quebrar, a ficha sai sem ela e com o aviso. O savepoint desfaz so o que a
    parte fez — o crachá do usuario (`set_config` local) foi posto antes e continua valendo para o resto."""

    def __init__(self, cur, nome, avisos):
        self.cur, self.nome, self.avisos = cur, nome, avisos

    def __enter__(self):
        self.cur.execute("savepoint parte_da_ficha")
        return self

    def __exit__(self, tipo, erro, _tb):
        if tipo is None:
            self.cur.execute("release savepoint parte_da_ficha")
            return False
        if issubclass(tipo, HTTPException):
            return False
        self.cur.execute("rollback to savepoint parte_da_ficha")
        self.avisos.append("%s: %s" % (self.nome, str(erro)[:160]))
        import logging
        logging.getLogger("seek").exception("ficha: parte %s falhou", self.nome)
        return True


def _tipo_da_imagem(b, padrao):
    """O tipo pelo comeco dos bytes: a foto de rua e WebP, a captura antiga JPEG, o print do Serasa PNG."""
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return padrao


def _montar_fila(u, cidade: str | None):
    con = _con(u)
    try:
        cur = con.cursor()
        cur.execute("""select ligacao, veredito, avaliado_em,
                              percepcao::jsonb->'checagem'->>'porque', id_empresa,
                              percepcao::jsonb->'resposta'->'fotos'->>'confirmam',
                              percepcao::jsonb->'resposta'->'busca', percepcao::jsonb->>'prioridade',
                              percepcao::jsonb->'classe'
                         from radar_comercial.ligacao_veredito""")
        vered, empresas, prova, prioridade, classe = {}, set(), {}, {}, {}
        for l, v, t, p, emp, fotos_ok, busca, prio, cls in cur.fetchall():
            prioridade[str(l)] = prio
            # A CLASSE (16/09/2026): segmento do negócio e o que a IA viu nas imagens, para os filtros da fila
            classe[str(l)] = cls if isinstance(cls, dict) else {}
            vered[str(l)] = (v, t, p)
            empresas.add(str(emp))
            prova[str(l)] = (_busca_confirma(busca), fotos_ok == "true")
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
        for l, f, _pid in cur.fetchall():
            fontes[l].add(f)
        # BUSCA E FOTO VEM DA RESPOSTA DA IA, lida acima (ver `FONTES`). Antes a
        # foto "achava" por existir imagem coletada, e a busca por ter resultado
        # no endereco — o verde nao dizia se confirmou.
        cur.execute("""select distinct on (ligacao) ligacao, acao, em, quem_nome
                         from radar_comercial.seek_decisao
                        where ligacao = any(%s) order by ligacao, em desc""", (list(cad),))
        decisao = {str(l): (a, t, q) for l, a, t, q in cur.fetchall()}
    finally:
        con.close()
    colunas = ["ligacao", "titular", "endereco", "bairro", "cidade", "qualificacao", "economias",
               "ia", "checagem", "avaliado_em"] + ["f_" + f for f in FONTES[1:]] + ["decisao", "decidido_em", "decidido_por",
                                                                                   "prioridade", "segmentos", "visual"]
    linhas = []
    # PRIORIDADE BAIXA NO FIM (15/09/2026): revisao so pela Receita e sem sinal nas imagens
    for lig in sorted(cad, key=lambda x: (prioridade.get(x) == "baixa", int(x) if x.isdigit() else 0)):
        r = cad[lig]
        v, t, porque = vered.get(lig, (None, None, None))
        fs = fontes.get(lig, set())
        d = decisao.get(lig)
        linhas.append([lig, r[1], r[2], r[3], r[4], r[5], _economias(r[6:10]),
                       v, porque, t.isoformat(timespec="minutes") if t else None,
                       "receita" in fs, "ifood" in fs, "maps" in fs, "ibge" in fs, "estadual" in fs,
                       "cadastur" in fs, "airbnb" in fs,
                       prova.get(lig, (False, False))[0], prova.get(lig, (False, False))[1],
                       d[0] if d else None, d[1].isoformat(timespec="minutes") if d else None,
                       d[2] if d else None, prioridade.get(lig),
                       (classe.get(lig) or {}).get("segmentos") or [],
                       (classe.get(lig) or {}).get("visual") or []])
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
        resp, refs, processo = {}, [], ""
        avisos = []
        if v:
            p = v[2] or {}
            resp = p.get("resposta") or {}
            refs = [q for q in (p.get("fotos_ref") or []) if isinstance(q, dict)]
            dados = p.get("dados") or ""
            processo = p.get("processo") or "antigo"
            fotos_ia = resp.get("fotos") if isinstance(resp.get("fotos"), dict) else {}
            ia = {"veredito": v[0], "motivo": resp.get("motivo") or v[1], "justificativa": v[1],
                  "aderentes": resp.get("aderentes") or [], "nao_combinam": resp.get("nao_combinam") or [],
                  "registros_antigos": resp.get("registros") or [],
                  # a checagem vai inteira: regra, porque, validos, removidos, ficha_do_maps, redes_sociais
                  "checagem": p.get("checagem"), "processo": processo,
                  "fotos_vistas": p.get("fotos") or [], "modelo": v[3],
                  "avaliado_em": v[4].isoformat(timespec="minutes") if v[4] else None,
                  "texto_busca": dados.split("TEXTO DA BUSCA NA WEB", 1)[1].split(":", 1)[-1].strip()
                  if "TEXTO DA BUSCA NA WEB" in dados else None,
                  # O JULGAMENTO LEVE (15/09/2026): o uso, o estado do imovel, as fontes que a IA citou, os
                  # comentarios e o sinal concreto das fotos; a prioridade sai do codigo (revisao so pela Receita)
                  "uso": resp.get("uso"), "imovel": resp.get("imovel"), "comentarios": resp.get("comentarios"),
                  "fontes": resp.get("fontes") or [], "sinal": fotos_ia.get("sinal"),
                  "prioridade": p.get("prioridade"), "tem_dados": bool(dados),
                  "classe": p.get("classe") or {}}
        imagens, redes, cidade, cep = [], [], None, None
        # AS EVIDENCIAS DO JULGAMENTO LEVE, pelas funcoes que o montaram (`_julgamento`). Cada parte e opcional: se
        # uma quebrar (um modulo do julgamento mudou de forma), a ficha sai sem ela e com o aviso.
        mods = pecas = excluir = None
        with _Parte(cur, "módulos do julgamento", avisos):
            mods = _julgamento()
            pecas, excluir = mods[1].fontes_de_nome(cur, ligacao, ids)
        if mods:
            with _Parte(cur, "foto de rua", avisos):
                rua = _foto_de_rua(cur, ligacao, ids, mods, pecas, excluir, v[4] if v else None)
                if rua:
                    rua["vista_ia"] = any(q.get("tipo") == rua["tipo"] and q.get("poi") == rua["poi_id"] for q in refs)
                    imagens.append(rua)
        # A FOTO DO GOOGLE QUE A IA VIU (15/09/2026). No leve, o MESMO FILTRO de `avaliar_ia._fotos_do_maps_datadas`
        # — so a foto do proprio lugar (`secao`, ou a capa na coleta antiga), a mais recente com data —, aqui sem
        # baixar os bytes: a funcao devolve imagem e nao id. No processo anterior, a primeira publicada do POI.
        pid_pub = next((q.get("poi") for q in refs if q.get("tipo") == "foto publicada"), None)
        vista_maps = None
        if pid_pub is not None:
            if "leve" in processo:
                cur.execute("""select id from radar_comercial.images_urls
                                where poi_id = %s and url like '%%gps-cs-s%%' and (secao is not null or ordem = 0)
                                  and (storage_path is not null or dados is not null)
                                order by data_imagem desc nulls last, ordem limit 1""", (pid_pub,))
            else:
                cur.execute("""select id from radar_comercial.images_urls
                                where poi_id = %s and url like '%%gps-cs-s%%'
                                  and (bytes_tam is not null or storage_path is not null)
                                order by ordem nulls last, id limit 1""", (pid_pub,))
            x = cur.fetchone()
            vista_maps = x[0] if x else None
        cur.execute("""select id, poi_id, data_imagem, secao, ordem from radar_comercial.images_urls
                        where poi_id = any(%s) and url like '%%gps-cs-s%%'
                          and (bytes_tam is not null or storage_path is not null or id = %s)
                        order by coalesce(id = %s, false) desc, poi_id, ordem nulls last, id limit 12""",
                    (ids, vista_maps, vista_maps))
        imagens += [{"id": "mp%s" % i, "poi_id": pid, "fonte": "maps", "tipo": "foto publicada",
                     "quando": d, "url": "/api/seek/foto/%s" % i, "secao": secao,
                     # "lugares tambem pesquisados": a coleta antiga gravava toda imagem da ficha
                     "do_lugar": secao is not None or ordem == 0, "vista_ia": i == vista_maps}
                    for i, pid, d, secao, ordem in cur.fetchall()]
        # A FICHA DO CNPJ NO SERASA (15/09/2026): texto que a IA leu e o print, por registro com CNPJ
        cnpj_de = {r["poi_id"]: "".join(ch for ch in str(r["cnpj"] or "") if ch.isdigit()) for r in regs}
        fichas = collections.defaultdict(list)
        cnpjs = sorted({c for c in cnpj_de.values() if len(c) == 14})
        if cnpjs:
            cur.execute("""select id, cnpj, fonte, consultado_em, situacao, data_abertura, texto_ia, storage_path is not null
                             from radar_comercial.ficha_cnpj_web
                            where cnpj = any(%s) and not bloqueado and (texto_ia is not null or storage_path is not null)
                            order by cnpj, consultado_em desc""", (cnpjs,))
            for fid, cnpj, fonte, em, sit, abertura, texto, tem_print in cur.fetchall():
                fichas[cnpj].append({"id": fid, "fonte": fonte, "consultado_em": em.isoformat(timespec="minutes") if em else None,
                                     "situacao": sit, "abertura": abertura.isoformat() if abertura else None,
                                     "texto_ia": texto, "print": "/api/seek/ficha/%s" % fid if tem_print else None})
        no_rolo = set()
        for r in regs:
            r["fichas_web"] = fichas.get(cnpj_de.get(r["poi_id"]), [])
            for f in r["fichas_web"] if not r["descartado_em"] else []:
                if f["print"] and f["id"] not in no_rolo:
                    no_rolo.add(f["id"])
                    imagens.append({"id": "fc%s" % f["id"], "poi_id": r["poi_id"], "fonte": "serasa",
                                    "tipo": "ficha do CNPJ", "quando": (f["consultado_em"] or "")[:10] or None,
                                    "url": f["print"], "cnpj": cnpj_de.get(r["poi_id"]), "situacao": f["situacao"]})
        # OS COMENTARIOS DE CLIENTE que a IA recebeu: os 3 mais recentes, de ate 2 anos, com data e nota
        if mods:
            with _Parte(cur, "comentários recentes", avisos):
                coments = mods[3].comentarios_recentes(con, ids)
                for r in regs:
                    r["comentarios_recentes"] = coments.get(r["poi_id"], [])
        # A BUSCA WEB NOVA, uma por motor: DuckDuckGo, Yahoo e o Google da reserva.
        # A do Google Maps (12 a 13/09/2026) nao aparece mais.
        cur.execute("""select distinct on (motor) id, consulta, motor, feito_em, no_endereco,
                              jsonb_array_length(resultados), resultados, dados is not null or storage_path is not null,
                              texto
                         from radar_comercial.busca_web
                        where ligacao = %s and tipo = 'endereco' and not bloqueado
                          and motor = any(%s) and resultados is not null
                        order by motor, feito_em desc""", (ligacao, list(MOTORES_DA_BUSCA)))
        linhas_busca = cur.fetchall()
        if mods and linhas_busca:
            with _Parte(cur, "fonte de cada resultado da busca", avisos):
                cidade, cep = mods[2].cidade_e_cep(cur, ligacao)
        buscas = []
        for i, q, m, t, n, tot, res, tem_print, texto in linhas_busca:
            anotado = None
            if mods and texto:
                try:
                    # O TEXTO QUE A IA LEU: cada resultado com a fonte (rede social, site de CNPJ, guia...) e a data do post
                    anotado = mods[2].anotar_texto(texto, cidade, cep)
                except Exception as e:                         # noqa: BLE001
                    avisos.append("texto anotado da busca: %s" % str(e)[:160])
            buscas.append({"id": i, "consulta": q, "motor": m, "feito_em": t.isoformat(timespec="minutes") if t else None,
                           "no_endereco": n or 0, "total": tot or 0,
                           "resultados": [r for r in (res or []) if r.get("no_endereco")],
                           "print": "/api/seek/busca/%s" % i if tem_print else None, "texto_anotado": anotado})
        buscas.sort(key=lambda b: MOTORES_DA_BUSCA.index(b["motor"]))
        if mods:
            with _Parte(cur, "redes sociais no endereço", avisos):
                redes = mods[2].redes_sociais_no_endereco(cur, ligacao, ids)
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
    usados, recusados, serasa = _prova_busca(resp, buscas)
    fontes["busca"] = {"achou": _busca_confirma(resp.get("busca")), "informado": "busca" in resp,
                       "usados": usados, "recusados": recusados, "serasa": serasa, "buscas": buscas,
                       "redes_sociais": redes}
    pf = _prova_fotos(resp, refs)
    fontes["foto"] = {"achou": bool(pf and pf["confirmam"]), "informado": pf is not None,
                      "o_que_mostram": (pf or {}).get("o_que_mostram") or "", "quais": (pf or {}).get("quais") or [],
                      "sinal": (resp.get("fotos") or {}).get("sinal") if isinstance(resp.get("fotos"), dict) else None,
                      "vistas": refs}
    return {"ligacao": ligacao, "base": base, "fontes": fontes, "ia": ia, "imagens": imagens,
            "decisoes": decisoes, "decisao": decisoes[0] if decisoes else None, "avisos": avisos,
            # SEM DADO no Comercial Radar: a tela mostra a secao vazia, com o aviso.
            "os": None, "impacto": None}


@router.get("/api/seek/caso/{ligacao}/dados")
def seek_caso_dados(ligacao: str, u: _auth.Usuario = Depends(_quem)):
    """O TEXTO INTEGRAL QUE A IA RECEBEU (`percepcao.dados`) e os rotulos das imagens, na ordem. Fica fora da ficha:
    sao 3 a 12 mil caracteres que so se leem quando alguem pede."""
    if not ligacao.isdigit():
        raise HTTPException(400, "ligacao invalida")
    con = _con(u)
    try:
        cur = con.cursor()
        cur.execute("""select percepcao::jsonb->>'dados', percepcao::jsonb->'fotos', percepcao::jsonb->>'processo',
                              modelo, avaliado_em
                         from radar_comercial.ligacao_veredito where ligacao = %s""", (ligacao,))
        r = cur.fetchone()
    finally:
        con.close()
    if not r:
        raise HTTPException(404, "ligação sem julgamento")
    return {"ligacao": ligacao, "dados": r[0], "fotos": r[1] or [], "processo": r[2], "modelo": r[3],
            "avaliado_em": r[4].isoformat(timespec="minutes") if r[4] else None}


class DecisaoEntrada(BaseModel):
    ligacoes: list[str]
    acao: str
    motivo: str | None = None
    observacoes: dict | None = None


#: Ate quantas ligacoes a decisao vai nominal no aviso as outras telas. Acima disso
#: o aviso diz so "houve um lote" e as telas buscam a fila de novo.
AVISO_NOMINAL = 2000


@router.post("/api/seek/decidir")
def seek_decidir(e: DecisaoEntrada, u: _auth.Usuario = Depends(_quem)):
    """Grava a decisao oficial de uma ligacao ou de varias (as filtradas, de uma vez).

    O COMENTARIO (`motivo`) E ACEITO EM TODA ACAO desde 14/09/2026 e continua
    obrigatorio so para rejeitar.

    LIGACAO EM ANALISE POR OUTRA PESSOA (`seek_trava`) NAO RECEBE A DECISAO: uma
    ligacao so volta 409 com o nome de quem esta nela. No LOTE, as travadas ficam
    de fora e voltam em `travadas` — recusar o lote inteiro porque uma de cinco mil
    esta aberta na tela de alguem tornaria o lote inutil numa equipe grande; so
    quando todas estao travadas o lote volta 409."""
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
        emp = {str(l): str(i) for l, i in cur.fetchall()}
        faltam = [l for l in ligs if l not in emp]
        travadas = seek_trava.travadas_por_outro(cur, u, [(emp[l], l) for l in ligs if l in emp])
        if travadas and len(travadas) >= len([l for l in ligs if l in emp]):
            con.rollback()
            nome = next(iter(travadas.values()))
            raise HTTPException(409, ("em análise por %s" % nome) if len(ligs) == 1
                                else "todas as %d ligações estão em análise por outras pessoas" % len(travadas))
        # O TEMPO DE AVALIACAO: a ultima abertura desta pessoa nesta ligacao. No lote
        # ninguem avaliou caso a caso, e fica nulo.
        aberta = seek_trava.abertura_de(cur, u, emp[ligs[0]], ligs[0]) if len(ligs) == 1 and ligs[0] in emp else None
        linhas = [(emp[l], l, acao, motivo, json.dumps(e.observacoes, ensure_ascii=False) if e.observacoes else None,
                   lote, u.id, u.nome or u.email, aberta) for l in ligs if l in emp and l not in travadas]
        # RETURNING, e nao `rowcount`: com mais de uma pagina o `rowcount` e so o da
        # ultima, e sob RLS o que conta e o que o banco de fato gravou.
        gravadas = execute_values(cur, """insert into radar_comercial.seek_decisao
                                              (id_empresa, ligacao, acao, motivo, observacoes, lote, quem,
                                               quem_nome, aberta_em)
                                          values %s
                                          returning id_empresa::text, ligacao, em""",
                                  linhas, page_size=1000, fetch=True) if linhas else []
        con.commit()
    finally:
        con.close()
    with _trava:
        _cache.clear()
    # O AVISO AS OUTRAS TELAS, por empresa: cada uma so recebe o que e dela.
    por_emp = {}
    for em_id, l, t in gravadas:
        por_emp.setdefault(em_id, []).append(l)
    quando = gravadas[0][2].isoformat(timespec="minutes") if gravadas else None
    for em_id, ls in por_emp.items():
        base = {"id_empresa": em_id, "acao": acao, "quem": u.id, "quem_nome": u.nome or u.email,
                "em": quando, "lote": lote}
        if len(ls) <= AVISO_NOMINAL:
            seek_eventos.publicar(dict(base, ev="decisao", ligacoes=ls, motivo=motivo))
        else:
            seek_eventos.publicar(dict(base, ev="decisao_lote", n=len(ls)))
    return {"gravadas": len(gravadas), "pedidas": len(ligs), "sem_veredito": faltam[:20],
            "travadas": [{"ligacao": l, "quem_nome": n} for l, n in list(travadas.items())[:50]],
            "n_travadas": len(travadas), "lote": lote, "acao": acao, "em": quando}


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


# A FOTO DE RUA DO JULGAMENTO LEVE E O PRINT DO SERASA (15/09/2026), por id e pela conexao do usuario. A foto de
# rua e recapturada na MESMA linha (uma por POI e visada): a URL da ficha leva `?v=<captura>`, e o cache longo
# nao serve a foto velha depois da recaptura.
@router.get("/api/seek/rua/poi/{evidencia_id}")
def seek_rua_poi(evidencia_id: int, u: _auth.Usuario = Depends(_quem)):
    b = _bytes(u, "poi_evidencia", evidencia_id)
    if not b:
        raise HTTPException(404, "sem foto de rua")
    return Response(content=b, media_type=_tipo_da_imagem(b, "image/webp"),
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/seek/rua/ligacao/{evidencia_id}")
def seek_rua_ligacao(evidencia_id: int, u: _auth.Usuario = Depends(_quem)):
    b = _bytes(u, "ligacao_evidencia", evidencia_id)
    if not b:
        raise HTTPException(404, "sem foto de rua")
    return Response(content=b, media_type=_tipo_da_imagem(b, "image/webp"),
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/seek/ficha/{ficha_id}")
def seek_ficha_print(ficha_id: int, u: _auth.Usuario = Depends(_quem)):
    # `ficha_cnpj_web` nao tem a coluna `dados`: `imagens._buscar` le so o Storage
    b = _bytes(u, "ficha_cnpj_web", ficha_id)
    if not b:
        raise HTTPException(404, "sem print da ficha")
    return Response(content=b, media_type=_tipo_da_imagem(b, "image/png"),
                    headers={"Cache-Control": "private, max-age=86400"})
