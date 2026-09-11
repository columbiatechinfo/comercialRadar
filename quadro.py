# -*- coding: utf-8 -*-
"""As quatro perguntas do dono do produto sobre o cadastro de Canoas, 10/09/2026.

1. Quantas ligacoes SIM, SIM_COM_ANALISE_HUMANA e NAO existem no cadastro.
2. Quantas SIM e SIM_COM tem vinculo, quantos cruzamentos por status e por
   fonte, e quais ligacoes tem MAIS DE UM POI DA MESMA FONTE (erro).
3. Quantas SIM e SIM_COM por fonte do POI vinculado.
4. Quantas dessas tem foto de rua e foto do Google.

Cinco consultas SIMPLES, uma por tabela, e o cruzamento inteiro em Python.
Nenhum join com o cadastro e nenhum `exists` por linha: foi isso que prendeu
as tentativas anteriores (a ultima ficou 11 min no join com `cadastro_corsan`
pelo `num_ligacao::text`).

O universo das perguntas 2 a 4 e o do pipeline: ligacao RESIDENCIAL e ATIVA,
marcada SIM ou SIM_COM. As marcadas SIM que nao sao residencial ativa sao
contadas a parte.

Imagem segue o criterio de `capturar_evidencia.alvos`: foto de rua
(`poi_evidencia.tipo like 'sv_%'`) e foto do estabelecimento no Google
(`images_urls.url like '%gps-cs-s%'`). Em `poi_evidencia` a presenca se testa
por `bytes_tam`/`storage_path`, nunca por `dados`. A imagem conta para a
LIGACAO quando QUALQUER POI dela tem a foto — regra do dono do produto.
"""
import collections
import json
import time
import unicodedata

import base_comum as bc

T0 = time.time()
ALVO = ("SIM", "SIM_COM_ANALISE_HUMANA")


def log(msg):
    print("[%5.1fs] %s" % (time.time() - T0, msg), flush=True)


def sem_acento(s):
    s = unicodedata.normalize("NFKD", s or "")
    return s.encode("ascii", "ignore").decode().lower().strip()


con = bc.conectar()
cur = con.cursor()
cur.execute("set statement_timeout = '300s'")

# 1. O cadastro: toda ligacao que tem marcacao.
cur.execute("""
    select num_ligacao::text, cidade, qualificacao,
           upper(coalesce(categoria, '')), upper(coalesce(sit_ligacao, '')),
           coalesce(nom_logradouro, '') || ', ' || coalesce(nro, '')
      from resources_root.cadastro_corsan
     where qualificacao is not null""")
q1 = collections.defaultdict(collections.Counter)   # qual -> grupo -> n
cidades = collections.Counter()
qual_de, fora_de, end_de = {}, {}, {}
for lig, cidade, qual, cat, sit, end in cur:
    res_ativa = cat == "RESIDENCIAL" and sit == "ATIVA"
    q1[qual]["residencial ativa" if res_ativa else "outras"] += 1
    cidades[sem_acento(cidade)] += 1
    if qual not in ALVO:
        continue
    if res_ativa:
        qual_de[lig] = qual
        end_de[lig] = end
    else:
        fora_de[lig] = qual
log("cadastro: %d com marcacao; alvo %d, SIM fora do alvo %d"
    % (sum(sum(c.values()) for c in q1.values()), len(qual_de), len(fora_de)))

# 2. Os vinculos vivos.
cur.execute("""select distinct ligacao, poi_id
                 from radar_comercial.ligacao_poi
                where descartado_em is null""")
vinc = cur.fetchall()
log("vinculos vivos: %d pares" % len(vinc))

# 3. Fonte e nome de cada POI vinculado, so os que nao foram fundidos.
ids = sorted({p for _, p in vinc})
cur.execute("""select id, lower(coalesce(fonte, '?')), coalesce(nome, '')
                 from radar_comercial.pois
                where fundido_em is null and id = any(%s)""", (ids,))
fonte_de, nome_de = {}, {}
for pid, fonte, nome in cur:
    fonte_de[pid] = fonte
    nome_de[pid] = nome
log("POIs vinculados e vivos: %d de %d" % (len(fonte_de), len(ids)))

# 4. Foto de rua.
cur.execute("""select distinct poi_id from radar_comercial.poi_evidencia
                where tipo like 'sv_%%'
                  and (bytes_tam is not null or storage_path is not null)""")
com_sv = {r[0] for r in cur.fetchall()}
log("POIs com foto de rua: %d" % len(com_sv))

# 5. Foto do estabelecimento no Google.
cur.execute("""select distinct poi_id from radar_comercial.images_urls
                where url like '%%gps-cs-s%%'
                  and (dados is not null or storage_path is not null)""")
com_foto = {r[0] for r in cur.fetchall()}
log("POIs com foto do Google: %d" % len(com_foto))
con.close()

# Por ligacao do alvo: os POIs de cada fonte.
L = collections.defaultdict(lambda: collections.defaultdict(set))
fora_com_poi = collections.defaultdict(set)
for lig, poi in vinc:
    if poi not in fonte_de:
        continue
    if lig in qual_de:
        L[lig][fonte_de[poi]].add(poi)
    elif lig in fora_de:
        fora_com_poi[fora_de[lig]].add(lig)

sv_lig = {g for g, fs in L.items() if any(p in com_sv for ps in fs.values() for p in ps)}
foto_lig = {g for g, fs in L.items() if any(p in com_foto for ps in fs.values() for p in ps)}


def imagem(ligs):
    ligs = set(ligs)
    sv, ft = ligs & sv_lig, ligs & foto_lig
    return {"com_sv": len(sv), "com_foto": len(ft), "as_duas": len(sv & ft),
            "nenhuma": len(ligs - sv - ft)}


res = {"q1": {q: dict(c) for q, c in q1.items()}, "cidades": dict(cidades)}

# Pergunta 2 e 4, por status.
por_qual = {}
for q in ALVO:
    ligs = [g for g in L if qual_de[g] == q]
    pares = sum(len(ps) for g in ligs for ps in L[g].values())
    pois = {p for g in ligs for ps in L[g].values() for p in ps}
    por_qual[q] = dict({"universo": sum(1 for v in qual_de.values() if v == q),
                        "com_vinculo": len(ligs), "cruzamentos": pares,
                        "pois": len(pois)}, **imagem(ligs))
res["por_qual"] = por_qual
res["fora_alvo_com_poi"] = {q: len(s) for q, s in fora_com_poi.items()}

# Perguntas 2, 3 e 4, por fonte.
por_fonte = {}
exemplos = []
for f in sorted({f for fs in L.values() for f in fs},
                key=lambda f: -sum(1 for fs in L.values() if f in fs)):
    ligs = [g for g, fs in L.items() if f in fs]
    multi = [g for g in ligs if len(L[g][f]) > 1]
    dist = collections.Counter()
    for g in multi:
        n = len(L[g][f])
        dist["2" if n == 2 else "3-5" if n <= 5 else "6-10" if n <= 10
             else "mais de 10"] += 1
        exemplos.append((n, g, f))
    por_fonte[f] = dict({
        "ligacoes": len(ligs),
        "sim": sum(1 for g in ligs if qual_de[g] == "SIM"),
        "sim_com": sum(1 for g in ligs if qual_de[g] != "SIM"),
        "cruzamentos": sum(len(L[g][f]) for g in ligs),
        "pois": len({p for g in ligs for p in L[g][f]}),
        "pois_sim": len({p for g in ligs if qual_de[g] == "SIM" for p in L[g][f]}),
        "pois_sim_com": len({p for g in ligs if qual_de[g] != "SIM" for p in L[g][f]}),
        "lig_multi": len(multi),
        "lig_multi_sim": sum(1 for g in multi if qual_de[g] == "SIM"),
        "cruz_excesso": sum(len(L[g][f]) - 1 for g in multi),
        "max": max((len(L[g][f]) for g in ligs), default=0),
        "dist_multi": dict(dist),
    }, **imagem(ligs))
res["por_fonte"] = por_fonte

todas_multi = {g for g, fs in L.items() if any(len(ps) > 1 for ps in fs.values())}
res["lig_multi_qualquer_fonte"] = {
    "n": len(todas_multi),
    "sim": sum(1 for g in todas_multi if qual_de[g] == "SIM")}

# Exemplos: as ligacoes com mais POIs da mesma fonte, e mais alguns casos de 2.
exemplos.sort(reverse=True)
amostra = exemplos[:12] + [e for e in exemplos if e[0] == 2][:8]
res["exemplos"] = [{"ligacao": g, "endereco": end_de.get(g, ""),
                    "status": qual_de[g], "fonte": f, "n": n,
                    "nomes": sorted(nome_de[p] for p in L[g][f])[:6]}
                   for n, g, f in amostra]

log("pronto: %d ligacoes do alvo com ao menos 1 POI" % len(L))
print("JSON:" + json.dumps(res, ensure_ascii=False), flush=True)
