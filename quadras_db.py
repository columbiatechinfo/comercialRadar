# -*- coding: utf-8 -*-
"""Banco do processo de quadras — sessão, vias, quadras, faces e pontos.

O processo tem CINCO passos e cada um pode ser rodado sozinho pelo terminal. O
que amarra os passos é a SESSÃO: a área desenhada mais o registro de até onde se
chegou. Se um passo quebra no meio, `retomar` continua do seguinte — por isso
cada passo grava o seu resultado no banco antes de devolver, e nunca depende de
estado em memória do passo anterior.

    analise_sessao   a área desenhada e o último passo concluído
    via_osm          as vias da área, com o NOME CANÔNICO do Google por via
    quadra           o polígono do OSM (eixo das vias) e a borda real
    quadra_face      cada lado da quadra: via canônica e paridade
    quadra_ponto     os endereços coletados, classificados por face

Nada aqui move ponto nem desenha telhado. A posição gravada é sempre a original
do CNEFE.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime

import base_comum as bc

# tabelas do processo ANTIGO (telhados/curadoria), removidas por decisão do
# usuário em 28/07/2026 ao trocar o processo
ANTIGAS = ("telhado_ponto_ia", "telhado_exemplo", "telhado_face_sv", "telhado_face",
           "telhado", "telhado_quadra", "tipo_construcao")

DDL = """
CREATE TABLE IF NOT EXISTS analise_sessao (
    id            text PRIMARY KEY,
    area_wkt      text NOT NULL,
    municipio     text,
    uf            text,
    cod_municipio text,
    passo         integer NOT NULL DEFAULT 1,
    passos        jsonb   NOT NULL DEFAULT '{}'::jsonb,
    erro          text,
    criado_em     timestamptz DEFAULT now(),
    atualizado_em timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS via_osm (
    id            bigserial PRIMARY KEY,
    sessao_id     text NOT NULL REFERENCES analise_sessao(id) ON DELETE CASCADE,
    nome_osm      text,
    tipo          text,
    geom_wkt      text NOT NULL,
    comprimento_m real,
    lat_consulta  double precision,
    lng_consulta  double precision,
    nome_canonico text,
    canonico_em   timestamptz,
    canonico_erro text
);
CREATE INDEX IF NOT EXISTS ix_via_osm_sessao ON via_osm(sessao_id);

CREATE TABLE IF NOT EXISTS quadra (
    id            bigserial PRIMARY KEY,
    sessao_id     text NOT NULL REFERENCES analise_sessao(id) ON DELETE CASCADE,
    geom_osm      text NOT NULL,
    geom_real     text,
    recuo_medio_m real,
    area_osm_m2   real,
    area_real_m2  real,
    lat_centro    double precision,
    lng_centro    double precision,
    vias          jsonb
);
CREATE INDEX IF NOT EXISTS ix_quadra_sessao ON quadra(sessao_id);

CREATE TABLE IF NOT EXISTS quadra_face (
    quadra_id     bigint NOT NULL REFERENCES quadra(id) ON DELETE CASCADE,
    face_idx      integer NOT NULL,
    anel_wkt      text NOT NULL,
    comprimento_m real,
    recuo_m       real,
    via_id        bigint REFERENCES via_osm(id) ON DELETE SET NULL,
    nome_canonico text,
    nome_osm      text,
    dist_via_m    real,
    nome_fonte    text,
    paridade      text,
    paridade_por  text,
    recuo_par_m   real,
    recuo_impar_m real,
    PRIMARY KEY (quadra_id, face_idx)
);

CREATE TABLE IF NOT EXISTS quadra_ponto (
    id            bigserial PRIMARY KEY,
    sessao_id     text NOT NULL REFERENCES analise_sessao(id) ON DELETE CASCADE,
    quadra_id     bigint REFERENCES quadra(id) ON DELETE CASCADE,
    face_idx      integer,
    cod_endereco  text,
    logradouro    text,
    numero        integer,
    cep           text,
    especie       text,
    estabelecimento text,
    nv_geo        text,
    lat           double precision NOT NULL,
    lng           double precision NOT NULL,
    origem        text,
    dist_via_m    real,
    canonico      boolean,
    motivo        text
);
CREATE INDEX IF NOT EXISTS ix_quadra_ponto_sessao ON quadra_ponto(sessao_id);
CREATE INDEX IF NOT EXISTS ix_quadra_ponto_quadra ON quadra_ponto(quadra_id);
"""

_PRONTO = False


def garantir_esquema(con=None) -> None:
    """Cria o esquema uma vez por processo. O guarda existe porque rodar o DDL a
    cada chamada de API já derrubou o servidor com 500 intermitente."""
    global _PRONTO
    if _PRONTO:
        return
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute(DDL)
            # colunas acrescentadas depois da primeira versão
            cur.execute("ALTER TABLE quadra_face ADD COLUMN IF NOT EXISTS nome_fonte text")
            # regra que resgatou o ponto depois de a paridade o ter reprovado
            cur.execute("ALTER TABLE quadra_ponto ADD COLUMN IF NOT EXISTS resgate text")
            # passo 6: posição alinhada na face real (a original NUNCA é alterada)
            for c, t in (("lat_alinhado", "double precision"),
                         ("lng_alinhado", "double precision"),
                         ("ordem_face", "integer"),
                         ("alinhado_por", "text"),
                         ("desloc_m", "real"),
                         # mesmo logradouro + mesmo número = uma porta só
                         ("grupo_id", "text"), ("grupo_n", "integer"),
                         ("grupo_lat", "double precision"),
                         ("grupo_lng", "double precision"),
                         # 'regua' = distribuído pela numeração · 'perpendicular'
                         # = só projetado na testada, porque a régua o levaria longe
                         ("alinhado_modo", "text"),
                         # o número não pertence à série desta face (um 79 no
                         # meio de 641–733): não pode ancorar a régua, e vai ser
                         # realocado para o trecho da rua onde ele se encaixa
                         ("fora_serie", "boolean"),
                         # face de ORIGEM de quem foi realocado — é ela que o
                         # `limpar_passo(6)` devolve, para o passo seguir refazível
                         ("realoc_quadra_id", "bigint"),
                         ("realoc_face_idx", "integer")):
                cur.execute(f"ALTER TABLE quadra_ponto ADD COLUMN IF NOT EXISTS {c} {t}")
            cur.execute("ALTER TABLE quadra_face ADD COLUMN IF NOT EXISTS anel_real_wkt text")
            # a via corre AO LONGO da borda de alguma quadra? Beco, rua projetada
            # e acesso de engenho não fecham quarteirão.
            cur.execute("ALTER TABLE via_osm ADD COLUMN IF NOT EXISTS fecha_quadra boolean")
            # A via que NÃO fecha quadra também tem dois lados, e é neles que os
            # endereços dela moram. Ela vira uma quadra DEGENERADA por lado — um
            # corredor cujo único lado edificável é o eixo da própria via — para
            # que a face dela tenha exatamente as mesmas propriedades de uma face
            # de quarteirão: nome, paridade, recuo, borda real e régua. Antes
            # esses pontos ficavam pendurados na face da quadra VIZINHA (medido:
            # 547 de 1.004 com o logradouro batendo com outra rua).
            for c, t in (("via_aberta_id", "bigint"), ("lado", "text"),
                         ("eixo_wkt", "text")):
                cur.execute(f"ALTER TABLE quadra ADD COLUMN IF NOT EXISTS {c} {t}")
        con.commit()
        _PRONTO = True
    finally:
        if fechar:
            con.close()


def apagar_processo_antigo(con=None) -> list[str]:
    """DROP das tabelas do processo de telhados. Decisão explícita do usuário:
    recomeçar limpo em vez de conviver com o esquema velho."""
    fechar = con is None
    con = con or bc.conectar()
    caidas = []
    try:
        for t in ANTIGAS:
            with con.cursor() as cur:
                cur.execute("SELECT to_regclass(%s)", (t,))
                if cur.fetchone()[0] is None:
                    continue
                cur.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
                caidas.append(t)
        con.commit()
    finally:
        if fechar:
            con.close()
    return caidas


# ── sessão ──────────────────────────────────────────────────────────────────
def _slug(txt: str) -> str:
    t = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_") or "area"


def nova_sessao(area_wkt: str, municipio="", uf="", cod_municipio="", con=None) -> str:
    """Cria a sessão e devolve o id — é ele que o terminal usa para retomar."""
    garantir_esquema(con)
    fechar = con is None
    con = con or bc.conectar()
    try:
        sid = f"{_slug(municipio)}_{datetime.now():%Y%m%d_%H%M%S}"
        with con.cursor() as cur:
            cur.execute("""INSERT INTO analise_sessao
                           (id, area_wkt, municipio, uf, cod_municipio, passo)
                           VALUES (%s,%s,%s,%s,%s,1)""",
                        (sid, area_wkt, municipio, uf, str(cod_municipio or "")))
        con.commit()
        return sid
    finally:
        if fechar:
            con.close()


def sessao(sid: str, con=None) -> dict | None:
    garantir_esquema(con)
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT id, area_wkt, municipio, uf, cod_municipio, passo,
                                  passos, erro, criado_em, atualizado_em
                             FROM analise_sessao WHERE id=%s""", (sid,))
            r = cur.fetchone()
        if not r:
            return None
        k = ("id", "area_wkt", "municipio", "uf", "cod_municipio", "passo",
             "passos", "erro", "criado_em", "atualizado_em")
        return dict(zip(k, r))
    finally:
        if fechar:
            con.close()


def sessoes(limite: int = 50, con=None) -> list[dict]:
    garantir_esquema(con)
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT s.id, s.municipio, s.uf, s.passo, s.erro, s.criado_em,
                                  (SELECT count(*) FROM quadra q WHERE q.sessao_id=s.id),
                                  (SELECT count(*) FROM via_osm v WHERE v.sessao_id=s.id),
                                  (SELECT count(*) FROM quadra_ponto p WHERE p.sessao_id=s.id)
                             FROM analise_sessao s
                         ORDER BY s.criado_em DESC LIMIT %s""", (limite,))
            rows = cur.fetchall()
        return [{"id": r[0], "municipio": r[1], "uf": r[2], "passo": r[3], "erro": r[4],
                 "criado_em": r[5].isoformat(timespec="seconds") if r[5] else None,
                 "quadras": r[6], "vias": r[7], "pontos": r[8]} for r in rows]
    finally:
        if fechar:
            con.close()


def marcar_passo(sid: str, passo: int, resumo: dict | None = None,
                 erro: str | None = None, con=None) -> None:
    """Registra que o passo terminou. `passo` só AVANÇA — refazer um passo antigo
    não pode fazer a sessão retroceder e reexecutar o que já estava pronto."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""UPDATE analise_sessao
                              SET passo = GREATEST(passo, %s),
                                  passos = passos || %s::jsonb,
                                  erro = %s,
                                  atualizado_em = now()
                            WHERE id = %s""",
                        (passo, json.dumps({str(passo): resumo or {}}), erro, sid))
        con.commit()
    finally:
        if fechar:
            con.close()


def limpar_passo(sid: str, passo: int, con=None) -> None:
    """Apaga o que um passo produziu, para poder refazê-lo do zero.

    Em cascata para a frente: refazer o passo 2 (vias/quadras) invalida faces e
    pontos, que foram derivados delas."""
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            if passo <= 2:
                cur.execute("DELETE FROM quadra_ponto WHERE sessao_id=%s", (sid,))
                cur.execute("DELETE FROM quadra WHERE sessao_id=%s", (sid,))
                cur.execute("DELETE FROM via_osm WHERE sessao_id=%s", (sid,))
            elif passo == 3:
                cur.execute("""UPDATE quadra SET geom_real=NULL, recuo_medio_m=NULL,
                                      area_real_m2=NULL WHERE sessao_id=%s""", (sid,))
                cur.execute("""DELETE FROM quadra_face WHERE quadra_id IN
                               (SELECT id FROM quadra WHERE sessao_id=%s)""", (sid,))
                cur.execute("DELETE FROM quadra_ponto WHERE sessao_id=%s", (sid,))
            elif passo == 4:
                cur.execute("DELETE FROM quadra_ponto WHERE sessao_id=%s", (sid,))
            elif passo == 6:
                # quem foi realocado trocou de face: devolve antes de zerar o
                # resto, senão refazer o passo 6 parte de um dono trocado
                cur.execute("""UPDATE quadra_ponto
                                  SET quadra_id=realoc_quadra_id,
                                      face_idx=realoc_face_idx,
                                      realoc_quadra_id=NULL, realoc_face_idx=NULL
                                WHERE sessao_id=%s AND realoc_quadra_id IS NOT NULL""",
                            (sid,))
                cur.execute("""UPDATE quadra_ponto SET lat_alinhado=NULL,
                                      lng_alinhado=NULL, ordem_face=NULL,
                                      alinhado_por=NULL, desloc_m=NULL, grupo_id=NULL,
                                      grupo_n=NULL, grupo_lat=NULL, grupo_lng=NULL,
                                      alinhado_modo=NULL, fora_serie=NULL
                                WHERE sessao_id=%s""", (sid,))
            elif passo == 5:
                cur.execute("""UPDATE quadra_face SET via_id=NULL, nome_canonico=NULL,
                                      paridade=NULL, paridade_por=NULL
                                WHERE quadra_id IN (SELECT id FROM quadra WHERE sessao_id=%s)""",
                            (sid,))
                cur.execute("""UPDATE quadra_ponto SET canonico=NULL, motivo=NULL,
                                      face_idx=NULL WHERE sessao_id=%s""", (sid,))
        con.commit()
    finally:
        if fechar:
            con.close()


# ── leitura para o mapa ─────────────────────────────────────────────────────
def vias(sid: str, con=None) -> list[dict]:
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT id, nome_osm, tipo, geom_wkt, comprimento_m,
                                  nome_canonico, lat_consulta, lng_consulta, canonico_erro
                             FROM via_osm WHERE sessao_id=%s ORDER BY comprimento_m DESC""",
                        (sid,))
            rows = cur.fetchall()
        k = ("id", "nome_osm", "tipo", "geom_wkt", "comprimento_m",
             "nome_canonico", "lat_consulta", "lng_consulta", "canonico_erro")
        return [dict(zip(k, r)) for r in rows]
    finally:
        if fechar:
            con.close()


def quadras(sid: str, con=None) -> list[dict]:
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT id, geom_osm, geom_real, recuo_medio_m, area_osm_m2,
                                  area_real_m2, lat_centro, lng_centro, vias,
                                  via_aberta_id, lado, eixo_wkt
                             FROM quadra WHERE sessao_id=%s ORDER BY id""", (sid,))
            rows = cur.fetchall()
        k = ("id", "geom_osm", "geom_real", "recuo_medio_m", "area_osm_m2",
             "area_real_m2", "lat_centro", "lng_centro", "vias",
             "via_aberta_id", "lado", "eixo_wkt")
        return [dict(zip(k, r)) for r in rows]
    finally:
        if fechar:
            con.close()


def lista_quadras(limite: int = 300, busca: str = "", con=None) -> list[dict]:
    """Todas as quadras já tratadas, de todas as sessões — é a lista do painel.

    Traz as vias de cada uma (o nome que as FACES adotaram, não o do OSM) para
    dar para achar a quadra pelo nome da rua."""
    garantir_esquema(con)
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""
                SELECT q.id, q.sessao_id, s.municipio, s.uf, q.lat_centro, q.lng_centro,
                       round(q.area_real_m2::numeric, 0), s.criado_em,
                       (SELECT count(*) FROM quadra_ponto p WHERE p.quadra_id=q.id),
                       (SELECT count(*) FROM quadra_ponto p
                         WHERE p.quadra_id=q.id AND p.canonico),
                       (SELECT string_agg(DISTINCT f.nome_canonico, ' · ')
                          FROM quadra_face f WHERE f.quadra_id=q.id
                           AND f.nome_canonico IS NOT NULL),
                       (SELECT count(*) FROM quadra_face f
                         WHERE f.quadra_id=q.id AND f.paridade IS NOT NULL),
                       (SELECT count(*) FROM quadra_face f WHERE f.quadra_id=q.id)
                  FROM quadra q JOIN analise_sessao s ON s.id=q.sessao_id
              ORDER BY s.criado_em DESC, q.id LIMIT %s""", (limite,))
            rows = cur.fetchall()
        k = ("id", "sessao_id", "municipio", "uf", "lat", "lng", "area_m2", "criado_em",
             "pontos", "canonicos", "vias", "faces_com_paridade", "faces")
        out = [dict(zip(k, r)) for r in rows]
        for o in out:
            o["criado_em"] = (o["criado_em"].isoformat(timespec="seconds")
                              if o["criado_em"] else None)
            o["area_m2"] = float(o["area_m2"]) if o["area_m2"] is not None else None
        if busca:
            b = busca.strip().lower()
            out = [o for o in out
                   if b in str(o["id"]) or b in (o["vias"] or "").lower()
                   or b in (o["municipio"] or "").lower()]
        return out
    finally:
        if fechar:
            con.close()


def faces(sid: str, con=None) -> list[dict]:
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT f.quadra_id, f.face_idx, f.anel_wkt, f.comprimento_m,
                                  f.recuo_m, f.via_id, f.nome_canonico, f.nome_osm,
                                  f.dist_via_m, f.paridade, f.paridade_por,
                                  f.recuo_par_m, f.recuo_impar_m, f.nome_fonte,
                                  f.anel_real_wkt
                             FROM quadra_face f JOIN quadra q ON q.id=f.quadra_id
                            WHERE q.sessao_id=%s ORDER BY f.quadra_id, f.face_idx""",
                        (sid,))
            rows = cur.fetchall()
        k = ("quadra_id", "face_idx", "anel_wkt", "comprimento_m", "recuo_m", "via_id",
             "nome_canonico", "nome_osm", "dist_via_m", "paridade", "paridade_por",
             "recuo_par_m", "recuo_impar_m", "nome_fonte", "anel_real_wkt")
        return [dict(zip(k, r)) for r in rows]
    finally:
        if fechar:
            con.close()


def pontos(sid: str, con=None) -> list[dict]:
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT id, quadra_id, face_idx, cod_endereco, logradouro,
                                  numero, cep, especie, estabelecimento, nv_geo,
                                  lat, lng, origem, dist_via_m, canonico, motivo, resgate,
                                  lat_alinhado, lng_alinhado, ordem_face, alinhado_por, desloc_m,
                                  grupo_id, grupo_n, grupo_lat, grupo_lng,
                                  alinhado_modo, fora_serie
                             FROM quadra_ponto WHERE sessao_id=%s ORDER BY id""", (sid,))
            rows = cur.fetchall()
        k = ("id", "quadra_id", "face_idx", "cod_endereco", "logradouro", "numero",
             "cep", "especie", "estabelecimento", "nv_geo", "lat", "lng", "origem",
             "dist_via_m", "canonico", "motivo", "resgate",
             "lat_alinhado", "lng_alinhado", "ordem_face", "alinhado_por", "desloc_m",
             "grupo_id", "grupo_n", "grupo_lat", "grupo_lng", "alinhado_modo",
             "fora_serie")
        return [dict(zip(k, r)) for r in rows]
    finally:
        if fechar:
            con.close()
