# -*- coding: utf-8 -*-
"""seek_chamado.py — abrir chamado no Hippo a partir da ficha da ligacao (14/09/2026).

    GET  /api/seek/chamado/empresas     as empresas que atendem (tenants do Hippo)
    POST /api/seek/chamado              abre: resumo + link + as fotos do veredito
    GET  /api/seek/chamado/{ligacao}    os chamados abertos daquela ligacao

DECISAO DO DONO DO PRODUTO: quem clica so escolhe a empresa que vai atender e
escreve o texto. O resto e do sistema — e a maior parte e do HIPPO, nao daqui:
criar o cliente e o requisitante que faltarem, numerar, atribuir ao administrador
com menos chamados em aberto e avisar. Esta rota junta o que so a SEEK sabe (o
veredito, as decisoes, os registros, as fotos) e entrega pela rota de integracao
do Hippo (`/api/integracao/*`, ADR 0005 do a2lGcp), autenticada por chave de
servico (`HIPPO_CHAVE_SERVICO`) — nunca pelo token da pessoa, que o Hippo nao
aceitaria: ela e de outra empresa.

O QUE A PESSOA PODE: o mesmo nivel que decide na SEEK (editor). E so abre chamado
de ligacao que ENXERGA — a leitura do veredito passa pela conexao dela
(`seek_api._con`), e a RLS da empresa responde antes de qualquer coisa ir ao Hippo.

A CONEXAO NAO ESPERA O HIPPO. O pooler tem 20 sessoes para a casa inteira: tudo o
que se le do banco (texto e bytes das fotos) e lido e a conexao fecha ANTES das
chamadas HTTP; a gravacao do espelho abre outra, curta.

ONDE QUEBRA SOB CARGA: cada abertura carrega ate 8 imagens (~1-2 MB) na memoria
do processo e as sobe uma a uma, em serie, no mesmo pedido — segundos por clique.
Com muitos cliques simultaneos isso ocupa as threads do FastAPI. O passo seguinte
e subir as imagens numa fila (o chamado ja nasce na hora; os anexos chegam
depois), e ninguem precisa mudar o contrato da tela para isso.
"""
from __future__ import annotations

import hashlib
import threading
import time
import urllib.parse
import uuid

import httpx
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

import auth as _auth
import seek_api

router = APIRouter()

#: Situacoes em que o chamado ja nao da trabalho — mesma lista do Hippo.
ENCERRADAS = ("resolvido", "fechado", "cancelado")
#: O que vai de imagem: as fotos que a IA viu (ate 5) e um print por motor de busca.
MAX_FOTOS = 5
TEXTO_MAX = 5000
#: O teto do Hippo para a descricao e 20.000; a margem e para o texto de quem pediu.
DESCRICAO_MAX = 19000


class Falha(Exception):
    """Recusa com status e mensagem para a tela: vira `{"erro": ...}`."""

    def __init__(self, status: int, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def _erro(status: int, mensagem: str) -> JSONResponse:
    return JSONResponse({"erro": mensagem}, status_code=status)


def _config() -> tuple[str, str]:
    """Lidas a cada uso, e nao no import: o `.env` muda sem mudar o codigo."""
    import os
    return ((os.environ.get("HIPPO_API_URL") or "").strip().rstrip("/"),
            (os.environ.get("HIPPO_CHAVE_SERVICO") or "").strip())


def _link_seek(ligacao: str) -> str:
    import os
    base = (os.environ.get("SEEK_URL_PUBLICA") or "https://a2lsolucoes.com/seek/").strip()
    return base.rstrip("/") + "/#" + ligacao


# ── o Hippo ──────────────────────────────────────────────────────────────────

_cliente: httpx.Client | None = None
_trava_cliente = threading.Lock()


def _http() -> httpx.Client:
    """UM cliente por processo: reaproveita a conexao em vez de abrir uma por foto."""
    global _cliente
    with _trava_cliente:
        if _cliente is None:
            _cliente = httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0),
                                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=5))
        return _cliente


def _hippo(metodo: str, caminho: str, **kw):
    """(status, corpo) da rota de integracao, ou `Falha` com a mensagem certa.

    A mensagem do Hippo passa para a tela quando e dele sobre O PEDIDO (400, 403,
    404, 409): "quem pediu ja tem cadastro em outra empresa atendente" e o que a
    pessoa precisa ler. Chave recusada (401) NAO passa como 401 — a pessoa esta
    logada; quem esta errado e este servidor, e a mensagem diz qual variavel."""
    url, chave = _config()
    if not url or not chave:
        raise Falha(503, "a integração com o Hippo não está configurada neste servidor "
                         "(faltam HIPPO_API_URL e HIPPO_CHAVE_SERVICO)")
    cab = dict(kw.pop("headers", None) or {})
    cab["x-chave-servico"] = chave
    try:
        r = _http().request(metodo, url + caminho, headers=cab, **kw)
    except httpx.TimeoutException:
        raise Falha(504, "o Hippo não respondeu a tempo")
    except httpx.HTTPError as e:
        print(f"[seek_chamado] Hippo inalcançável em {url}: {type(e).__name__}: {e}", flush=True)
        raise Falha(503, "o Hippo está indisponível agora")
    try:
        corpo = r.json()
    except ValueError:
        corpo = {}
    if r.status_code < 400:
        return r.status_code, corpo
    print(f"[seek_chamado] Hippo {metodo} {caminho} -> {r.status_code} {corpo}", flush=True)
    if r.status_code == 401:
        raise Falha(502, "o Hippo recusou a chave de serviço deste servidor (HIPPO_CHAVE_SERVICO)")
    if r.status_code in (400, 403, 404, 409, 413, 415):
        raise Falha(r.status_code, corpo.get("mensagem") or "o Hippo recusou o pedido")
    raise Falha(502, "o Hippo falhou ao processar o pedido (request_id %s)" % corpo.get("request_id"))


_empresas: tuple[float, list | None] = (0.0, None)
_trava = threading.Lock()


def _listar_empresas() -> list:
    """60 s em memoria: a lista muda quando uma empresa passa a usar o Hippo, nao a cada clique."""
    global _empresas
    with _trava:
        t, lista = _empresas
    if lista is not None and time.time() - t < 60:
        return lista
    _st, corpo = _hippo("GET", "/api/integracao/empresas")
    lista = [{"id": str(e["id"]), "nome": e["nome"]} for e in (corpo.get("empresas") or [])]
    with _trava:
        _empresas = (time.time(), lista)
    return lista


# ── o que a SEEK sabe da ligacao ─────────────────────────────────────────────

def _tipo_da_imagem(b: bytes) -> tuple[str, str]:
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp", "image/webp"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    return "bin", "application/octet-stream"


def _texto_obs(obs) -> str:
    if not obs:
        return ""
    if isinstance(obs, dict):
        return "; ".join("%s: %s" % (k, v) for k, v in obs.items() if v not in (None, "", [], {}))[:400]
    return str(obs)[:400]


def _coletar(u: _auth.Usuario, lig: str) -> dict:
    """Tudo o que vai para o chamado, lido pela conexao DA PESSOA (a RLS decide).

    Os bytes das imagens que estao no Storage NAO sao baixados aqui: sai daqui so
    o caminho, e o download acontece depois de a conexao fechar."""
    import imagens
    con = seek_api._con(u)
    try:
        cur = con.cursor()
        cur.execute("""select id_empresa, veredito, justificativa, percepcao::jsonb, modelo, avaliado_em
                         from radar_comercial.ligacao_veredito where ligacao = %s""", (lig,))
        v = cur.fetchone()
        if not v:
            raise Falha(404, "ligação não encontrada, ou sem julgamento visível para você")
        emp = v[0]
        cur.execute("""select coalesce(end_ligacao,''), coalesce(nom_bairro,''), coalesce(cidade,''),
                              coalesce(categoria,''), coalesce(qualificacao,''),
                              qtd_eco_res, qtd_eco_com, qtd_eco_ind, qtd_eco_pub
                         from resources_root.cadastro_corsan
                        where id_empresa = %s and num_ligacao = %s::bigint""", (emp, lig))
        cad = cur.fetchone()
        cur.execute("""select acao, motivo, observacoes, quem_nome, em from radar_comercial.seek_decisao
                        where id_empresa = %s and ligacao = %s order by em desc limit 5""", (emp, lig))
        decisoes = cur.fetchall()
        cur.execute("""select p.id, lower(coalesce(p.fonte,'')), coalesce(p.nome,''), coalesce(p.categoria,''),
                              coalesce(p.endereco,''), coalesce(rd.cnpj, p.cnpj, ''), lp.metros, lp.criterios_ok
                         from radar_comercial.ligacao_poi lp
                         join radar_comercial.pois p on p.id = lp.poi_id and p.fundido_em is null
                         left join radar_comercial.receita_data rd on rd.poi_id = p.id
                        where lp.id_empresa = %s and lp.ligacao = %s and lp.descartado_em is null
                        order by lp.metros nulls last limit 30""", (emp, lig))
        registros = cur.fetchall()

        # AS IMAGENS: as que a IA viu no veredito (`fotos_ref`, na ordem do prompt)
        # e o print mais recente de cada motor da busca — as mesmas da ficha.
        percep = v[3] or {}
        refs = percep.get("fotos_ref") or []
        rotulos = percep.get("fotos") or []
        f_ia = (percep.get("resposta") or {}).get("fotos")
        confirmam = set()
        if isinstance(f_ia, dict):
            for q in f_ia.get("quais") or []:
                try:
                    confirmam.add(int(q))
                except (TypeError, ValueError):
                    pass
        linhas_img = []   # (nome_base, legenda, storage_path, dados)
        colunas = {}      # tabela -> tem `dados`? (a coluna some na fase 3 do Storage)
        for i, ref in enumerate(refs[:MAX_FOTOS], 1):
            poi, tipo = ref.get("poi"), str(ref.get("tipo") or "")
            if not poi:
                continue
            if tipo.startswith("sv_"):
                tabela, onde, args = ("poi_evidencia", "poi_id = %s and tipo = %s", (int(poi), tipo))
            elif tipo == "foto publicada":
                tabela, onde, args = ("images_urls", "poi_id = %s and url like %s", (int(poi), "%gps-cs-s%"))
            else:
                continue
            if tabela not in colunas:
                colunas[tabela] = imagens._tem_coluna(con, tabela, "dados")
            tem_dados = colunas[tabela]
            cur.execute("select storage_path%s from radar_comercial.%s where %s"
                        " and (storage_path is not null or bytes_tam is not null)"
                        " order by %s limit 1"
                        % (", dados" if tem_dados else "", tabela, onde,
                           "id" if tabela == "poi_evidencia" else "ordem nulls last, id"), args)
            r = cur.fetchone()
            if r:
                rot = rotulos[i - 1] if i - 1 < len(rotulos) else tipo
                legenda = "%s (POI #%s)%s" % (rot, poi, " — a IA indicou esta foto" if i in confirmam else "")
                base = "foto-%d-%s-poi%s" % (i, "maps" if tipo == "foto publicada" else tipo, poi)
                linhas_img.append((base, legenda, r[0], r[1] if len(r) > 1 else None))
        tem_dados = imagens._tem_coluna(con, "busca_web", "dados")
        cur.execute("""select distinct on (motor) id, motor, feito_em, storage_path%s
                         from radar_comercial.busca_web
                        where id_empresa = %%s and ligacao = %%s and tipo = 'endereco' and not bloqueado
                          and motor = any(%%s) and resultados is not null
                          and (storage_path is not null or bytes_tam is not null)
                        order by motor, feito_em desc""" % (", dados" if tem_dados else ""),
                    (emp, lig, list(seek_api.MOTORES_DA_BUSCA)))
        for bid, motor, feito, sp, *dados in cur.fetchall():
            linhas_img.append(("busca-%s-%s" % (motor, bid),
                               "print da busca no %s (%s)" % (motor, feito.strftime("%d/%m/%Y") if feito else "?"),
                               sp, dados[0] if dados else None))
    finally:
        con.close()
    return {"id_empresa": str(emp), "veredito": v, "cadastro": cad, "decisoes": decisoes,
            "registros": registros, "imagens": linhas_img, "percepcao": percep}


def _endereco(cad) -> str:
    """Endereço, bairro e cidade SEM repetir: o `end_ligacao` da concessionária
    costuma já trazer os dois ("...-MATHIAS VELHO-CANOAS-RS-CEP:..."), e o título
    saía com o bairro e a cidade duas vezes."""
    if not cad:
        return ""
    partes = [cad[0]] if cad[0] else []
    for extra in (cad[1], cad[2]):
        if extra and extra.upper() not in (cad[0] or "").upper():
            partes.append(extra)
    return ", ".join(partes)


def _titulo(lig: str, d: dict) -> str:
    return ("Ligação %s — %s" % (lig, _endereco(d["cadastro"]) or "endereço não informado"))[:200]


def _descricao(lig: str, d: dict, u: _auth.Usuario, texto: str) -> str:
    v, cad, percep = d["veredito"], d["cadastro"], d["percepcao"]
    resp = percep.get("resposta") or {}
    s = ["Aberto pela SEEK (Comercial Radar) a pedido de %s (%s)." % (u.nome or u.email, u.email or "sem e-mail"),
         "Ficha da ligação na SEEK: %s" % _link_seek(lig), "",
         "── PEDIDO ──", texto, "",
         "── LIGAÇÃO %s ──" % lig]
    if cad:
        eco = sum(int(x or 0) for x in cad[5:9])
        s += ["Endereço: %s" % _endereco(cad),
              "Categoria: %s · qualificação: %s · economias: %d" % (cad[3] or "?", cad[4] or "?", eco)]
    else:
        s.append("(sem linha no cadastro da concessionária)")
    s += ["", "── VEREDITO DA IA ──",
          "Veredito: %s%s" % (v[1] or "?", (" · avaliado em %s" % v[5].strftime("%d/%m/%Y %H:%M")) if v[5] else ""),
          "Motivo: %s" % ((resp.get("motivo") or v[2] or "(sem motivo registrado)")[:3000])]
    fotos = resp.get("fotos")
    if isinstance(fotos, dict) and fotos.get("o_que_mostram"):
        s.append("O que as fotos mostram: %s" % str(fotos["o_que_mostram"])[:1500])
    s += ["", "── DECISÃO HUMANA ──"]
    if d["decisoes"]:
        a, m, _o, q, e = d["decisoes"][0]
        s.append("Vigente: %s — por %s em %s%s" % (a, q or "?", e.strftime("%d/%m/%Y %H:%M"),
                                                   (" — motivo: %s" % m) if m else ""))
        s.append("Últimas decisões e comentários:")
        for a, m, o, q, e in d["decisoes"]:
            extra = " · ".join(x for x in ((m or "")[:400], _texto_obs(o)) if x)
            s.append("- %s · %s · %s%s" % (e.strftime("%d/%m/%Y %H:%M"), a, q or "?", (" — " + extra) if extra else ""))
    else:
        s.append("Sem decisão humana registrada.")
    s += ["", "── REGISTROS VINCULADOS (%d) ──" % len(d["registros"])]
    for pid, fonte, nome, cat, end, cnpj, metros, crit in d["registros"]:
        partes = [x for x in (cat, end, ("CNPJ " + cnpj) if cnpj else "",
                              ("a %.0f m" % metros) if metros is not None else "",
                              "%s critério(s)" % crit) if x]
        s.append("- #%s [%s] %s · %s" % (pid, fonte or "?", nome or "(sem nome)", " · ".join(partes)))
    if d["imagens"]:
        s += ["", "── IMAGENS ANEXADAS ──"]
        s += ["%d. %s — %s" % (i, base, leg) for i, (base, leg, _sp, _b) in enumerate(d["imagens"], 1)]
    corpo = "\n".join(s)
    if len(corpo) > DESCRICAO_MAX:
        corpo = corpo[:DESCRICAO_MAX] + "\n[… cortado: o resumo completo está na ficha da SEEK]"
    return corpo


# ── as rotas ─────────────────────────────────────────────────────────────────

@router.get("/api/seek/chamado/empresas")
def seek_chamado_empresas(u: _auth.Usuario = Depends(seek_api._quem)):
    """As empresas que podem atender: os tenants ativos do Hippo."""
    try:
        return {"empresas": _listar_empresas()}
    except Falha as f:
        return _erro(f.status, f.mensagem)


@router.post("/api/seek/chamado")
def seek_chamado_abrir(corpo: dict | None = Body(default=None), u: _auth.Usuario = Depends(seek_api._quem)):
    """Abre o chamado da ligacao na empresa escolhida.

    Entrada: {"ligacao", "id_empresa_atendente", "texto"} e, opcional, "chave" —
    um identificador que a tela gere ao abrir o formulario. Sem ela, a chave sai
    de quem pediu + ligacao + empresa + texto: o clique duplo manda o mesmo e
    recebe o mesmo chamado."""
    try:
        return _abrir(corpo or {}, u)
    except Falha as f:
        return _erro(f.status, f.mensagem)


def _abrir(corpo: dict, u: _auth.Usuario):
    if not u.pode("editor"):
        raise Falha(403, "abrir chamado exige nível editor ou acima")
    lig = str(corpo.get("ligacao") or "").strip()
    if not lig.isdigit() or len(lig) > 20:
        raise Falha(400, "ligação inválida")
    try:
        empresa = str(uuid.UUID(str(corpo.get("id_empresa_atendente") or "")))
    except ValueError:
        raise Falha(400, "id_empresa_atendente precisa ser um uuid")
    texto = str(corpo.get("texto") or "").strip()
    if not texto:
        raise Falha(400, "escreva o que a empresa atendente precisa saber")
    if len(texto) > TEXTO_MAX:
        raise Falha(400, "o texto tem %d caracteres; o máximo é %d" % (len(texto), TEXTO_MAX))
    chave = str(corpo.get("chave") or "").strip()
    if chave and not 8 <= len(chave) <= 200:
        raise Falha(400, "chave precisa ter de 8 a 200 caracteres")
    if not chave:
        chave = "seek:" + hashlib.sha256(
            ("%s|%s|%s|%s" % (u.id, lig, empresa, " ".join(texto.split()))).encode("utf-8")).hexdigest()
    _config_ok = all(_config())
    if not _config_ok:
        raise Falha(503, "a integração com o Hippo não está configurada neste servidor "
                         "(faltam HIPPO_API_URL e HIPPO_CHAVE_SERVICO)")

    # 1 · o que a SEEK sabe, pela conexao da pessoa (e a RLS dela)
    d = _coletar(u, lig)
    nome_empresa = next((e["nome"] for e in _listar_empresas() if e["id"] == empresa), None)
    if nome_empresa is None:
        raise Falha(404, "essa empresa não atende pelo Hippo")

    # 2 · o chamado
    st, r = _hippo("POST", "/api/integracao/chamados", json={
        "referencia": lig, "chave_requisicao": chave, "id_empresa_atendente": empresa,
        "solicitante_id": u.id, "titulo": _titulo(lig, d), "descricao": _descricao(lig, d, u, texto)})
    resp_h = r.get("responsavel") or None

    # 3 · o espelho de ca, antes das fotos: se a remessa cair no meio, a ficha ja
    # sabe do chamado.
    con = seek_api._con(u)
    try:
        with con.cursor() as cur:
            cur.execute("""insert into radar_comercial.seek_chamado
                               (id_empresa, ligacao, chamado_id, numero, empresa_atendente, empresa_atendente_nome,
                                responsavel, responsavel_nome, url, quem, quem_nome)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           on conflict (id_empresa, chamado_id) do nothing""",
                        (d["id_empresa"], lig, r["id"], r["numero"], empresa, nome_empresa,
                         (resp_h or {}).get("id"), (resp_h or {}).get("nome"), r.get("url"),
                         u.id, u.nome or u.email))
        con.commit()
    finally:
        con.close()

    # 4 · as imagens — idempotentes pelo nome no Hippo. So baixa e sobe o que falta.
    import imagens
    ja = {str(n).rsplit(".", 1)[0] for n in (r.get("anexos") or [])}
    enviados, falharam = 0, []
    for base, _leg, sp, dados in d["imagens"]:
        if base in ja:
            continue
        b = imagens._de_linha(sp, dados)
        if not b:
            falharam.append(base)
            continue
        ext, mime = _tipo_da_imagem(b)
        nome = "%s.%s" % (base, ext)
        try:
            _hippo("POST", "/api/integracao/chamados/%s/anexos" % r["id"], content=b,
                   headers={"content-type": mime, "x-anexo-nome": urllib.parse.quote(nome)})
            enviados += 1
        except Falha as f:
            print(f"[seek_chamado] anexo {nome} do chamado {r['numero']} falhou: {f.mensagem}", flush=True)
            falharam.append(nome)

    return JSONResponse(status_code=201 if st == 201 else 200, content={
        "id": r["id"], "numero": r["numero"], "url": r.get("url"),
        "responsavel": {"id": resp_h["id"], "nome": resp_h.get("nome")} if resp_h else None,
        "situacao": r.get("situacao"), "ja_existia": bool(r.get("ja_existia")),
        "empresa_atendente": {"id": empresa, "nome": nome_empresa},
        "anexos": {"enviados": enviados, "ja_estavam": len(ja), "falharam": falharam}})


@router.get("/api/seek/chamado/{ligacao}")
def seek_chamado_da_ligacao(ligacao: str, todos: int = 0, u: _auth.Usuario = Depends(seek_api._quem)):
    """Os chamados que a SEEK abriu para a ligacao — por padrao so os ABERTOS.

    A situacao e perguntada ao Hippo numa chamada so (ate 50 chamados). Se ele nao
    responder, a lista vem mesmo assim, com `situacao: null` e
    `hippo_indisponivel: true` — sem esconder que existe chamado."""
    if not ligacao.isdigit() or len(ligacao) > 20:
        return _erro(400, "ligação inválida")
    con = seek_api._con(u)
    try:
        cur = con.cursor()
        cur.execute("select id_empresa from radar_comercial.ligacao_veredito where ligacao = %s", (ligacao,))
        emp = cur.fetchone()
        if not emp:
            return _erro(404, "ligação não encontrada, ou sem julgamento visível para você")
        cur.execute("""select chamado_id, numero, empresa_atendente, empresa_atendente_nome, responsavel,
                              responsavel_nome, url, quem_nome, em
                         from radar_comercial.seek_chamado
                        where id_empresa = %s and ligacao = %s order by em desc limit 50""", (emp[0], ligacao))
        linhas = cur.fetchall()
    finally:
        con.close()
    itens = [{"id": str(c), "numero": n, "empresa_atendente": {"id": str(ea), "nome": ean},
              "responsavel": {"id": str(r), "nome": rn} if r else None, "url": url,
              "aberto_por": q, "aberto_em": e.isoformat(timespec="minutes"), "situacao": None}
             for c, n, ea, ean, r, rn, url, q, e in linhas]
    indisponivel = False
    if itens:
        try:
            _st, corpo = _hippo("GET", "/api/integracao/chamados",
                                params={"ids": ",".join(i["id"] for i in itens)})
            ao_vivo = {str(x["chamado_id"]): x for x in corpo.get("itens") or []}
            for i in itens:
                x = ao_vivo.get(i["id"])
                if x:
                    i["situacao"] = x.get("situacao")
                    if x.get("responsavel_id"):
                        i["responsavel"] = {"id": str(x["responsavel_id"]), "nome": x.get("responsavel_nome")}
        except Falha:
            indisponivel = True
    if not todos:
        itens = [i for i in itens if i["situacao"] not in ENCERRADAS]
    return {"ligacao": ligacao, "chamados": itens, "hippo_indisponivel": indisponivel}
