# -*- coding: utf-8 -*-
"""fontes_para_poi.py — as fontes que ficaram na gaveta viram ponto.

O BURACO, MEDIDO EM 02/09/2026

Quatro fontes tinham dado no banco e não tinham ponto no mapa:

    base do cliente   102.131 ligações em Canoas, 100% com coordenada,
                      logradouro, número e CEP — e nenhuma no `pois`
    Cadastur          186 prestadores na cidade, 80 viraram POI. Os outros 106
                      foram recusados por `endereco_nao_encontrado` (83) e
                      `ja_existe` (23)
    Airbnb            a coluna `poi_id` existe desde a criação da tabela e
                      nunca foi preenchida — o mesmo que o iFood tinha
    Receita           169.359 estabelecimentos na cidade, 58.947 ativos, com
                      logradouro, número, CEP, CNAE e telefone

POR QUE UM MÓDULO SÓ

As quatro fazem a mesma coisa: ler linhas de uma tabela, montar um endereço no
formato que a etapa 7 lê, achar coordenada quando não houver, inserir em `pois`
e ligar de volta. O que muda entre elas é o SELECT e o de-para de campos — não a
mecânica. Quatro arquivos seriam quatro cópias do mesmo laço, e o defeito
corrigido num não chegaria aos outros.

A RECUSA DO CADASTUR ERA DE OUTRA ÉPOCA

`cadastur.py` diz, no próprio cabeçalho: "não vira POI sem posição". A regra
fazia sentido quando não havia etapa de endereço — hoje há, e ela resolve o
logradouro pelo CEP, offline, para 87% dos pontos. Recusar continua custando os
83 e não protege mais de nada: o endereço deles existe, e é bom
(`Domingos Martins 111/304 Canoas Centro CEP: 92010170 RS`).

Aqui a regra é a do dono do produto: **todos da área escolhida viram POI**. Quem
não tem coordenada recebe a do CNEFE pelo CEP e número; quem não tem nem isso
entra assim mesmo, e a etapa 7 marca para revisão humana. Ponto sem endereço não
é apagado, é marcado.

NÃO HÁ CRUZAMENTO AQUI

Nesta fase nenhuma etapa compara uma base com as outras. Cada fonte insere o que
tem, com `fonte` própria. Se o mesmo estabelecimento existe vindo do estadual e
do Cadastur, passam a existir os dois — quem junta é o cruzamento por ligação,
depois. A única recusa é do banco: o índice `pois_sem_duplicata` proíbe dois
pontos ativos com o MESMO nome e o MESMO endereço.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict

import psycopg2.extras

import base_comum as bc

CELULA = 0.0015
MINUSCULAS = {"de", "do", "da", "dos", "das", "e", "d"}


def _log(m: str) -> None:
    print(m, flush=True)


def nome_de_cidade(s: str) -> str:
    p = (s or "").strip().lower().split()
    return " ".join(x if i and x in MINUSCULAS else x.capitalize()
                    for i, x in enumerate(p))


def montar_endereco(rua, numero, bairro, cidade, uf, cep) -> str:
    """`Rua do Sindicato, 13 - Harmonia, Canoas - RS, 92325-370`.

    É o formato que o libpostal fraciona melhor, e o mesmo que o iFood e o Maps
    já usam. Cada pedaço ausente some com o separador dele: vírgula solta ou
    hífen órfão fazem o parser ver campo vazio onde não há campo.
    """
    rua = (rua or "").strip()
    if not rua:
        return ""
    saida = rua
    n = str(numero or "").strip()
    if n and n.lower() not in ("0", "s/n", "sn", "none"):
        saida += ", %s" % n
    if (bairro or "").strip():
        saida += " - %s" % bairro.strip()
    c = nome_de_cidade(cidade)
    if c:
        saida += ", %s" % c
        if (uf or "").strip():
            saida += " - %s" % uf.strip().upper()
    d = re.sub(r"\D", "", cep or "")
    if len(d) == 8:
        saida += ", %s-%s" % (d[:5], d[5:])
    return saida


def metros(a, b, c, d):
    r = 6371000.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


# ── o nome da via, normalizado ─────────────────────────────────────────────
_ACENTO_VIA = str.maketrans("ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                            "AAAAAEEEEIIIIOOOOOUUUUC")
_TIPOS_VIA = {"AV", "AVE", "AVENIDA", "R", "RU", "RUA", "TV", "TRAV",
              "TRAVESSA", "EST", "ESTR", "ESTRADA", "ROD", "RODOVIA", "PC",
              "PCA", "PRACA", "BC", "BECO", "AL", "ALAMEDA", "LG", "LARGO",
              "VL", "VILA", "PQ", "PARQUE", "JD", "JARDIM"}


def via_normal(txt: str) -> str:
    """O nome da via sem acento, sem tipo e sem pontuação — para comparar.

    O tipo sai porque a mesma via chega como "AV.", "AVENIDA" e às vezes "RUA"
    conforme a fonte, e exigir que o tipo case reprovaria casamentos bons.
    """
    s = (txt or "").upper().translate(_ACENTO_VIA)
    s = s.split(",")[0].split(" - ")[0]
    p = re.sub(r"[^A-Z0-9 ]", " ", s).split()
    if p and p[0] in _TIPOS_VIA:
        p = p[1:]
    return " ".join(p)


class Cnefe:
    """O cadastro do IBGE, para dar coordenada a quem não tem.

    A RUA É PARTE DA CHAVE, e essa é a correção de 04/09/2026.

    Este índice era SÓ POR CEP: dentro de um CEP procurava-se o número mais
    próximo, e a rua nunca entrava na conta. Parece razoável até se lembrar do
    CEP genérico — `92330-000` cobre o bairro Mathias Velho inteiro, com
    centenas de ruas. Ali "o número 43" é o 43 de QUALQUER uma delas, e o
    resultado ainda saía carimbado `cnefe_numero_exato`, que se lê como "casei
    o número exato".

    Medido sobre os 143 POIs da quadra de Canoas: 24 coordenadas caíram em rua
    diferente da que o endereço diz. O POI 91794 — "AVENIDA RIO GRANDE DO SUL,
    43" — recebeu a coordenada do "BECO DEODORO DA FONSECA, 43". Outros três
    endereços da mesma avenida foram parar na "AYRTON SENNA", a 5,1 km. O
    estabelecimento nem estava dentro da área desenhada; entrou nela por um
    casamento que nunca houve.

    A ORDEM DE PREFERÊNCIA agora é:

        1. via + número        `cnefe_via_numero`      exato, e conferido
        2. via + número perto  `cnefe_via_proximo`     mesma rua, outro número
        3. CEP específico      `cnefe_cep_especifico`  só se o CEP não for de
                                                       bairro (não termina em
                                                       000)
        4. nada                `sem_casamento_de_via`

    O passo 3 sobrevive porque CEP de rua identifica a rua sozinho. O que
    sumiu é o passo que usava CEP de bairro como se fosse endereço.
    """

    def __init__(self, cod: str):
        self.cod = cod
        self.por_cep = defaultdict(list)
        self.por_via_num = defaultdict(list)
        self.por_via = defaultdict(list)
        self.n = 0

    def carregar(self, cur) -> float:
        t0 = time.time()
        cur.execute("""
            select replace(coalesce(cep,''),'-','') as cep,
                   coalesce(nom_seglogr,'') as via, num_endereco,
                   latitude::float8, longitude::float8
              from resources_root.ibge_cnefe
             where cod_municipio = %s
               and latitude is not null and longitude is not null
        """, (self.cod,))
        for cep, via, num, lat, lon in cur:
            self.n += 1
            d = re.sub(r"\D", "", str(num or ""))
            n = int(d) if d else None
            v = via_normal(via)
            if cep:
                self.por_cep[cep].append((n, lat, lon))
            if v:
                self.por_via[v].append((n, lat, lon))
                if n is not None:
                    self.por_via_num[(v, n)].append((lat, lon))
        return time.time() - t0

    def coordenada(self, cep, numero, logradouro=None):
        """A coordenada do endereço. `logradouro` é o que faz o casamento valer."""
        alvo = re.sub(r"\D", "", str(numero or ""))
        n = int(alvo) if alvo else None
        via = via_normal(logradouro or "")

        if via and n is not None:
            pts = self.por_via_num.get((via, n))
            if pts:
                return pts[0][0], pts[0][1], "cnefe_via_numero"
            # Mesma rua, número que o IBGE não tem. O ponto mais próximo DAQUELA
            # rua é uma aproximação honesta — e continua sendo a rua certa.
            na_via = [p for p in self.por_via.get(via, ()) if p[0] is not None]
            if na_via:
                m = min(na_via, key=lambda p: abs(p[0] - n))
                return m[1], m[2], "cnefe_via_proximo"
        if via and n is None:
            pts = self.por_via.get(via)
            if pts:
                return pts[0][1], pts[0][2], "cnefe_via_sem_numero"

        # CEP DE RUA AINDA SERVE; CEP DE BAIRRO, NÃO. O sufixo 000 marca o CEP
        # geral da localidade — usá-lo como endereço é o defeito que esta
        # classe acabou de perder.
        d = re.sub(r"\D", "", str(cep or ""))
        if len(d) == 8 and not d.endswith("000"):
            pontos = self.por_cep.get(d)
            if pontos:
                comnum = [p for p in pontos if p[0] is not None]
                if n is not None and comnum:
                    m = min(comnum, key=lambda p: abs(p[0] - n))
                    return m[1], m[2], "cnefe_cep_especifico"
                return pontos[0][1], pontos[0][2], "cnefe_cep_especifico"
        return None, None, "sem_casamento_de_via"


# ══════════════════════════════════════════════ as quatro fontes ════════════
#
# Cada uma diz: de onde ler, como filtrar por cidade, e como virar um POI. O
# resto do arquivo não sabe qual fonte está processando.

def _sql_cidade(coluna: str) -> str:
    return ("translate(upper(coalesce(%s,'')), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', "
            "'AAAAEEIOOOUUC') = translate(upper(%%s), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', "
            "'AAAAEEIOOOUUC')" % coluna)


FONTES = {}


def fonte(nome):
    def registrar(f):
        FONTES[nome] = f
        return f
    return registrar


@fonte("cadastro")
def _cadastro(cur, cidade, limite, extra):
    """A base do cliente. `extra` traz o de-para confirmado no modal."""
    mapa = extra.get("mapa") or {}
    tipos = set(str(t) for t in (extra.get("tipos_comerciais") or []))
    tabela = extra.get("tabela") or "resources_root.cadastro_corsan"
    somente = extra.get("somente_comerciais", True)

    col = lambda k, padrao: mapa.get(k) or padrao          # noqa: E731
    campos = [col("ligacao", "num_ligacao"), "nom_cliente",
              col("endereco", "nom_logradouro"), col("numero", "nro"),
              col("bairro", "nom_bairro"), col("cep", "cod_cep"),
              col("tipo_cliente", "categoria"),
              col("latitude", "cod_latitude"), col("longitude", "cod_longitude")]
    sql = ("select %s from %s where %s"
           % (", ".join('"%s"' % c for c in campos), tabela,
              _sql_cidade('"%s"' % col("cidade", "cidade"))))
    args = [cidade]
    if somente and tipos:
        sql += " and \"%s\" = any(%%s)" % col("tipo_cliente", "categoria")
        args.append(list(tipos))
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, args)

    for lig, cliente, rua, num, bairro, cep, tipo, lat, lon in cur.fetchall():
        yield {
            "chave": str(lig),
            "nome": (cliente or "Ligação %s" % lig).strip(),
            "endereco": montar_endereco(rua, num, bairro, cidade, "RS", cep),
            "categoria": tipo,
            "lat": float(lat) if lat is not None else None,
            "lng": float(lon) if lon is not None else None,
            "cep": cep, "numero": num, "telefone": None, "cnpj": None,
            "id_ligacao_base": str(lig),
            "comercial": (str(tipo) in tipos) if tipos else None,
        }


@fonte("cadastur")
def _cadastur(cur, cidade, limite, extra):
    """TODOS os prestadores da cidade, e não só os que a etapa 3 geocodificou.

    O endereço comercial vem antes do da Receita: é o do estabelecimento que
    atende, e é o que o Radar procura. Quando falta, vale o da RFB.
    """
    sql = """
        select p.id, coalesce(nullif(btrim(p.nome_fantasia),''), p.razao_social),
               p.endereco_comercial, p.endereco_rfb, p.cnpj, p.telefone,
               p.atividade_turistica, p.email
          from resources_root.cadastur_prestador p
         where %s
    """ % _sql_cidade("p.municipio")
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, (cidade,))
    for cid, nome, ec, er, cnpj, tel, atv, email in cur.fetchall():
        # O formato do Cadastur é `Rua Numero/Compl Cidade Bairro CEP: X UF`.
        # Não se tenta desmontar aqui: o texto inteiro vai para a etapa 7, que
        # tem libpostal e o cadastro do IBGE para isso. Desmontar por regra
        # nesta função seria repetir, pior, o que já existe.
        end = (ec or er or "").strip()
        m = re.search(r"CEP:?\s*(\d{5})-?(\d{3})", end)
        yield {
            "chave": str(cid), "nome": (nome or "").strip(),
            "endereco": end, "categoria": atv,
            "lat": None, "lng": None,
            "cep": ("%s%s" % m.groups()) if m else None,
            "numero": None, "telefone": tel,
            "cnpj": re.sub(r"\D", "", cnpj or "") or None,
            "email": email, "id_ligacao_base": None, "comercial": True,
        }


@fonte("airbnb")
def _airbnb(cur, cidade, limite, extra):
    """As hospedagens. Só as de DENTRO do desenho, quando houve desenho."""
    # A CIDADE VEM DA GEOMETRIA, e nao do campo `cidade`.
    #
    # A descoberta por `--cidade` nao preenche esse campo: dos 19 anuncios de
    # Canoas no banco, 18 estao com `cidade` nula. Filtrar por ele deixaria de
    # fora justamente os que a rodada acabou de achar.
    #
    # A malha do IBGE responde sem ambiguidade, e ainda cobre o caso oposto: o
    # anuncio que a caixa delimitadora trouxe de fora do municipio nao entra so
    # porque estava na mesma busca.
    sql = """
        select a.anuncio_id, coalesce(nullif(btrim(a.nome),''), a.titulo),
               a.endereco, a.numero, a.bairro, a.cep, a.lat, a.lng,
               a.tipo_resumo, a.coord_exata
          from radar_comercial.airbnb_anuncio a
         where a.poi_id is null and a.na_area is not false
           and a.lat is not null and a.lng is not null
           and exists (
                 select 1 from resources_root.ibge_malha m
                  where %s
                    and st_contains(m.geom, st_setsrid(st_point(a.lng, a.lat), 4326)))
    """ % _sql_cidade("m.nome")
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, (cidade,))
    for (aid, nome, end, num, bairro, cep, lat, lng, tipo,
         coord_exata) in cur.fetchall():
        endereco = end or ""
        if endereco:
            endereco = montar_endereco(endereco, num, bairro, cidade, "RS", cep)
        yield {
            "chave": str(aid), "nome": (nome or "Hospedagem %s" % aid).strip(),
            # SEM ENDEREÇO NÃO INVENTA UM. O Airbnb não publica rua; quem
            # resolve é o `enderecar_airbnb` pela imagem, ou a etapa 7 pela
            # coordenada — e aí sai marcado como indício, que é a verdade.
            #
            # A CATEGORIA É `hospedagem`, E NÃO `tipo_resumo`. Este campo
            # guarda o SUBTÍTULO do anúncio — "Espaço inteiro: casa de
            # hóspedes em Estância Velha, Brasil" —, que não é categoria e
            # ainda cita um município que não é o do ponto. Medido em
            # 06/09/2026: 26 dos 44 POIs de Canoas tinham subtítulo no lugar
            # da categoria, e sete deles nomeavam outra cidade. O subtítulo
            # continua inteiro em `airbnb_anuncio.tipo_resumo`, que é onde ele
            # sempre foi verdade.
            "endereco": endereco, "categoria": "hospedagem",
            # A COORDENADA DO AIRBNB É EMBARALHADA DE PROPÓSITO, e sem dizer
            # isso o resto do sistema a trata como porta.
            #
            # O site desloca o pino de quem não reservou — `coord_exata` é
            # falso em 34 dos 44 de Canoas. A etapa 7 então geocodifica esse
            # ponto ao contrário e devolve rua e número (27 dos 44 ganharam
            # número assim), e o cruzamento, que lê `logradouro_resolvido`,
            # marca "rua e número batem" e emite confiança 0,95. Catorze
            # vínculos de alta confiança em Canoas nasceram disso.
            #
            # 200 m é o deslocamento que o próprio Airbnb descreve para o
            # pino aproximado. Declarar a incerteza é o que permite a quem lê
            # decidir; escondê-la produz certeza falsa.
            "coord_precisao": "porta" if coord_exata else "aproximada",
            "coord_incerteza_m": None if coord_exata else 200.0,
            "lat": float(lat) if lat is not None else None,
            "lng": float(lng) if lng is not None else None,
            "cep": cep, "numero": num, "telefone": None, "cnpj": None,
            "id_ligacao_base": None, "comercial": True,
        }


@fonte("ibge")
def _ibge(cur, cidade, limite, extra):
    """Os estabelecimentos que o recenseador viu, em 2022, na porta.

    O CNEFE classifica cada endereco em `cod_especie`. As especies 1, 2 e 7 sao
    domicilio e obra; as outras sao ESTABELECIMENTO, e `dsc_estabelecimento`
    traz o nome que o recenseador anotou:

        3  agropecuario        45
        4  ensino             279
        5  saude              325
        6  outras finalidades  16.660
        8  religioso          545

    Sao 17.854 em Canoas, TODOS com nome, endereco e coordenada exata — o
    recenseador esteve la. `COSTUREIRA`, `MECANICA DIESEL CRIATIVA`,
    `LAVAGEM CARRO`: e o comercio pequeno que nao tem site, nao tem CNPJ ativo
    e nao aparece em nenhuma das fontes digitais. Exatamente o que a
    concessionaria nao cobra como comercio.

    Ate agora o CNEFE servia so de autoridade de endereco. Ele tambem e fonte.

    O NOME AS VEZES E O RAMO, e nao o nome do negocio — `MERCADO`, `LOJA`,
    `ACADEMIA`. Isso nao e defeito do dado: e o que estava escrito na fachada
    ou o que o morador respondeu. Vale como ponto e vale como ramo; quem
    precisa do nome proprio cruza com as outras fontes depois.
    """
    ESPECIES = ("3", "4", "5", "6", "8")
    cod = extra.get("cod_municipio")
    sql = """
        select cod_unico_endereco, dsc_estabelecimento, cod_especie,
               btrim(regexp_replace(
                 coalesce(nom_tipo_seglogr,'') || ' ' ||
                 coalesce(nom_titulo_seglogr,'') || ' ' ||
                 coalesce(nom_seglogr,''), '\s+', ' ', 'g')) as via,
               num_endereco, dsc_localidade, cep,
               latitude::float8, longitude::float8
          from resources_root.ibge_cnefe
         where cod_municipio = %s and cod_especie = any(%s)
           and coalesce(dsc_estabelecimento,'') <> ''
    """
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, (cod, list(ESPECIES)))
    RAMO = {"3": "agropecuario", "4": "ensino", "5": "saude",
            "6": "outras finalidades", "8": "religioso"}
    for (chave, nome, especie, via, num, bairro, cep, lat, lon) in cur.fetchall():
        yield {
            "chave": str(chave), "nome": (nome or "").strip(),
            "endereco": montar_endereco(via, num, bairro, cidade, "RS", cep),
            "categoria": RAMO.get(str(especie), "estabelecimento"),
            "lat": float(lat) if lat is not None else None,
            "lng": float(lon) if lon is not None else None,
            "cep": cep, "numero": num, "telefone": None, "cnpj": None,
            "id_ligacao_base": None, "comercial": True,
        }


@fonte("receita")
def _receita(cur, cidade, limite, extra):
    """Os estabelecimentos da Receita. Ativos por padrão.

    Situação `02` é ATIVA. As outras — baixada, suspensa, inapta — dizem que ali
    JÁ houve comércio, o que interessa noutro momento e enche a base agora.
    """
    situacoes = extra.get("situacoes") or ["02"]
    sql = """
        select e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv as cnpj,
               coalesce(nullif(btrim(e.nome_fantasia),''), em.razao_social),
               e.tipo_logradouro, e.logradouro, e.numero, e.bairro, e.cep,
               e.cnae_principal, e.ddd1, e.tel1, e.email
          from resources_root.rf_estabelecimentos e
          left join resources_root.rf_empresas em
                 on em.cnpj_basico = e.cnpj_basico
         where e.municipio = (select codigo from resources_root.rf_municipios
                               where %s limit 1)
           and e.situacao_cadastral = any(%%s)
    """ % _sql_cidade("descricao")
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, (cidade, situacoes))
    for (cnpj, nome, tipo_l, logr, num, bairro, cep, cnae,
         ddd, tel, email) in cur.fetchall():
        rua = " ".join(x for x in ((tipo_l or "").strip(), (logr or "").strip())
                       if x)
        fone = ("(%s) %s" % (ddd, tel)) if ddd and tel else (tel or None)
        yield {
            "chave": cnpj, "nome": (nome or "").strip(),
            "endereco": montar_endereco(rua, num, bairro, cidade, "RS", cep),
            "categoria": cnae, "lat": None, "lng": None,
            "cep": cep, "numero": num, "telefone": fone,
            "cnpj": re.sub(r"\D", "", cnpj or "") or None, "email": email,
            "id_ligacao_base": None, "comercial": True,
        }


# ═══════════════════════════════════════════════════════ o laço comum ═══════
COLUNAS = ("nome", "endereco", "cidade", "uf", "categoria", "telefone", "cnpj",
           "email", "lat_origem", "lng_origem", "fonte", "fonte_dado", "status",
           "endereco_fonte", "coord_fonte", "id_base", "id_ligacao_base")


def do_municipio(qual: str, cidade: str, cod: str, uf: str = "RS",
                 limite: int = 0, aplicar: bool = False,
                 extra: dict | None = None) -> dict:
    if qual not in FONTES:
        _log("   fonte desconhecida: %s (use %s)"
             % (qual, ", ".join(sorted(FONTES))))
        return {"erro": "fonte desconhecida"}
    extra = extra or {}

    con = bc.conectar()
    cur = con.cursor()

    itens = list(FONTES[qual](cur, cidade, limite, extra))
    _log("   %d linhas em %s, fonte '%s'" % (len(itens), cidade, qual))
    if not itens:
        con.close()
        return {"itens": 0}

    precisam = sum(1 for i in itens if i["lat"] is None)
    cnefe = None
    if precisam:
        cnefe = Cnefe(cod)
        dt = cnefe.carregar(cur)
        _log("   %d sem coordenada · cadastro do IBGE carregado (%d, %.1f s)"
             % (precisam, cnefe.n, dt))

    linhas, placar = [], Counter()
    for i in itens:
        lat, lng, origem = i["lat"], i["lng"], "fonte"
        if lat is None and cnefe:
            lat, lng, origem = cnefe.coordenada(
                i.get("cep"), i.get("numero"),
                i.get("logradouro") or i.get("endereco"))
        placar[origem if lat is not None else "sem_coordenada"] += 1
        if not i["nome"] and not i["endereco"]:
            # O gatilho `exigir_comparavel` recusa isto, e com razão: sem nome e
            # sem endereço o ponto não casa com nada e não aparece em busca.
            placar["descartado_sem_nome_e_sem_endereco"] += 1
            continue
        linhas.append((
            i["nome"][:300] or None, i["endereco"][:400] or None,
            nome_de_cidade(cidade), uf.upper(),
            (i.get("categoria") or None), i.get("telefone"), i.get("cnpj"),
            i.get("email"), lat, lng, qual, "%s:%s" % (qual, i["chave"]),
            qual, qual, origem if lat is not None else None,
            extra.get("id_base"), i.get("id_ligacao_base"),
            # A PRECISÃO VIAJA COM O PONTO. Quem não declara nada continua
            # gravando nulo, que é o que todas as fontes faziam até aqui —
            # este par de campos só existe para a fonte que SABE que a
            # coordenada dela é aproximada e precisa dizer.
            i.get("coord_precisao"), i.get("coord_incerteza_m")))

    _log("   de onde veio a coordenada:")
    for k, n in placar.most_common():
        _log("      %-34s %7d" % (k, n))

    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")
        for l in linhas[:4]:
            _log("      %-32s %s" % (str(l[0])[:32], str(l[1])[:60]))
        con.close()
        return {"itens": len(itens), "prontos": len(linhas), "gravados": 0}

    cur.execute("select count(*) from radar_comercial.pois where fonte = %s",
                (qual,))
    antes = cur.fetchone()[0]
    psycopg2.extras.execute_values(cur, """
        insert into radar_comercial.pois
            (nome, endereco, cidade, uf, categoria, telefone, cnpj, email,
             lat_origem, lng_origem, fonte, fonte_dado, status,
             endereco_fonte, coord_fonte, id_base, id_ligacao_base,
             coord_precisao, coord_incerteza_m)
        values %s
        on conflict do nothing
    """, linhas, page_size=1000)
    con.commit()
    cur.execute("select count(*) from radar_comercial.pois where fonte = %s",
                (qual,))
    criados = cur.fetchone()[0] - antes

    ligados = 0
    if qual == "airbnb":
        cur.execute("""
            update radar_comercial.airbnb_anuncio a
               set poi_id = p.id
              from radar_comercial.pois p
             where p.fonte_dado = 'airbnb:' || a.anuncio_id and a.poi_id is null
        """)
        ligados = cur.rowcount
        con.commit()
    elif qual == "cadastur":
        cur.execute("""
            update radar_comercial.cadastur_vinculo v
               set poi_id = p.id, sem_poi_motivo = null
              from radar_comercial.pois p
             where p.fonte_dado = 'cadastur:' || v.cadastur_id::text
               and v.poi_id is null
        """)
        ligados = cur.rowcount
        con.commit()

    _log("   %d pontos criados%s" % (criados,
                                     (" · %d ligados de volta" % ligados)
                                     if ligados else ""))
    if criados < len(linhas):
        _log("   %d não entraram: mesmo nome E mesmo endereço de um ponto que"
             % (len(linhas) - criados))
        _log("   já existe — o índice `pois_sem_duplicata` recusa a cópia.")
    con.close()
    return {"itens": len(itens), "prontos": len(linhas), "gravados": criados,
            "ligados": ligados}


def base_confirmada(cur):
    """A base do cliente, se alguém já confirmou o de-para na tela."""
    cur.execute("""
        select id, tabela_dados, mapa_colunas, tipos_comerciais
          from radar_comercial.base_cliente
         where estado = 'pronta' order by confirmado_em desc limit 1
    """)
    r = cur.fetchone()
    if not r:
        return None
    mapa = r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}")
    tipos = r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]")
    return {"id_base": r[0], "tabela": r[1], "mapa": mapa,
            "tipos_comerciais": tipos}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="As fontes que ficaram na gaveta viram POI.")
    p.add_argument("--fonte", required=True,
                   choices=sorted(FONTES), help="qual fonte trazer")
    p.add_argument("--cidade", required=True)
    p.add_argument("--municipio", required=True, help="código IBGE de 7 dígitos")
    p.add_argument("--uf", default="RS")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--todos-os-tipos", action="store_true",
                   help="base do cliente: traz também os não comerciais "
                        "(em Canoas, 102.131 em vez de 13.364)")
    p.add_argument("--situacoes", default="02",
                   help="Receita: situações cadastrais, separadas por vírgula "
                        "(02 = ativa)")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    extra = {"situacoes": [s.strip() for s in a.situacoes.split(",") if s.strip()],
             "somente_comerciais": not a.todos_os_tipos}
    if a.fonte == "cadastro":
        con = bc.conectar()
        cur = con.cursor()
        b = base_confirmada(cur)
        con.close()
        if not b:
            _log("▶ base do cliente")
            _log("   ⚠️  nenhuma base CONFIRMADA. Sem o de-para de colunas não")
            _log("      há como saber qual coluna é a ligação nem o que é")
            _log("      comércio. Confirme no painel, em 'Bases do cliente'.")
            return 1
        extra.update(b)
        _log("   base #%d · %s · %d tipos comerciais"
             % (b["id_base"], b["tabela"], len(b["tipos_comerciais"])))

    _log("▶ %s → POI · %s (%s)" % (a.fonte, a.cidade, a.municipio))
    extra["cod_municipio"] = a.municipio
    r = do_municipio(a.fonte, a.cidade, a.municipio, a.uf, a.limite,
                     a.aplicar, extra)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
