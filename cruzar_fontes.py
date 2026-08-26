# -*- coding: utf-8 -*-
"""cruzar_fontes.py — quem é o mesmo ponto vira UM POI, com várias abas.

É a etapa 7 da mineração, e ela só funciona porque a 6 rodou antes: sem o
logradouro canônico, "Avenida Gen. Flores da Cunha" e "Avenida General Flores
da Cunha" são duas ruas e o par mais forte que existe nunca é visto.

COMO O PAR NASCE

Comparar 8.391 POIs dois a dois são 35 milhões de pares. Em vez disso os POIs
caem numa grade de células de ~110 m e cada um é comparado com a própria célula
e as 8 vizinhas — o que mantém todo par a menos de ~150 m e derruba o custo para
linear na prática. Nenhum par de interesse mora fora disso: o raio das regras é
20 m, e a folga cobre coordenada imprecisa entre fontes.

QUEM DECIDE O QUÊ

    evidencia.py       aplica as regras (endereço > site > telefone) e diz
                       `fundir`, `perguntar` ou `descartar`
    julgar_par_banco   a IA da Spark decide os `perguntar`, com o dado completo
    aqui               escreve o resultado, com a confiança de 1 a 10

O POI ABSORVIDO NÃO É APAGADO. Vira `status='fundido'`, mantém a linha e o
`place_id`, e o vínculo dele passa para o sobrevivente como mais uma aba —
reversível pelo `x` da ficha. Apagar seria mais simples e seria pior: uma junção
errada viraria perda, e a medição do RS mostrou 66,2% de erro nas fusões
automáticas por evidência fraca.

QUEM SOBREVIVE: o que tem mais evidência acumulada (Street View, análise de IA).
A evidência aponta para um `poi_id`; escolher o outro obrigaria a mover trabalho
pago de lugar.

USO
    python cruzar_fontes.py --cidade Cachoeirinha --empresa "Aegea - Corsan"
    python cruzar_fontes.py --cidade Cachoeirinha --empresa "..." --aplicar
    python cruzar_fontes.py --cidade Cachoeirinha --empresa "..." --sem-ia
"""
from __future__ import annotations

import argparse
import json

import config  # noqa: F401
import area_utils as au
import base_comum as bc
import evidencia as ev

# ~110 m. A célula não precisa ser o raio da regra: ela é a rede que pega os
# candidatos, e o `evidencia.avaliar` é quem aplica os 20 m onde eles valem.
CELULA = 0.001

SQL_POIS = """
select p.id, p.nome, p.fonte, p.categoria, p.endereco, p.telefone, p.website,
       p.cnpj, p.razao_social, p.nome_fantasia, p.cnae,
       p.maps_lat, p.maps_lng, p.place_id,
       la.logradouro_marcado, la.logradouro_original, la.numero_canonico, la.tier,
       (select count(*) from streetview_imgs s where s.poi_id = p.id)
     + (select count(*) from analise_ia a where a.poi_id = p.id) as evid
  from pois p
  left join logradouro_ajustado la
         on la.fonte = 'pois' and la.record_id = p.id::text
 where p.maps_lat is not null and p.maps_lng is not null
   and coalesce(p.status, '') <> 'fundido'
   and p.tenant_id = (select nullif(current_setting('app.tenant_id', true), '')::uuid)
   and (%(cidade)s = '' or upper(translate(coalesce(p.cidade, ''), %(ac)s, %(li)s))
                         = upper(translate(%(cidade)s, %(ac)s, %(li)s)))
"""

# O corte pela area entra depois do `where`, e por isso a consulta acima termina
# nele. Ver `_com_margem`: a caixa vai folgada de proposito.
SQL_AREA = (" and p.maps_lat between %(area_s)s and %(area_n)s"
            " and p.maps_lng between %(area_o)s and %(area_l)s")

_ACENTOS = "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ"
_LISOS = "aaaaeeiooouucAAAAEEIOOOUUC"


# A margem vive no `area_utils` (`MARGEM_TRABALHO_M`), e nao mais aqui.
#
# Ela nasceu neste arquivo e por isso ficou so' neste arquivo — o que produziu
# tres recortes diferentes no processo (medido: 80, 85 e 307 POIs nas etapas de
# segmentar, normalizar e cruzar). Uma definicao so' e o conserto.


def _um_lado_dentro(par, poligono):
    """Basta UM dos dois estar na area desenhada.

    Exigir os dois perderia exatamente o caso que a margem existe para pegar.
    Nenhum dos dois dentro e vizinhanca de fora do pedido — nao se paga por ela.
    """
    return (au.ponto_no_poligono(par["a"]["lat"], par["a"]["lng"], poligono)
            or au.ponto_no_poligono(par["b"]["lat"], par["b"]["lng"], poligono))


def _empresa(cur, nome: str) -> str:
    cur.execute("select id, nome from tenants where lower(nome)=lower(%s) and ativo",
                (nome.strip(),))
    r = cur.fetchone()
    if not r:
        cur.execute("select nome from tenants where ativo order by nome")
        raise SystemExit(f"empresa {nome!r} não existe. Ativas: "
                         + ", ".join(x[0] for x in cur.fetchall()))
    cur.execute("select set_config('app.tenant_id', %s, false)", (str(r[0]),))
    return r[1]


def carregar(cur, cidade: str, poligono=None) -> list:
    par = {"cidade": cidade, "ac": _ACENTOS, "li": _LISOS}
    sql = SQL_POIS
    if poligono:
        s, n, o, l = au.bbox_com_margem(poligono)
        sql += SQL_AREA
        par.update({"area_s": s, "area_n": n, "area_o": o, "area_l": l})
    cur.execute(sql, par)
    pois = []
    for (pid, nome, fonte, cat, end, tel, site, cnpj, rz, nf, cnae,
         la, lo, place, logr_m, logr_o, num_c, tier, evid) in cur.fetchall():
        pois.append({
            "id": pid, "nome": nome or "", "fonte": fonte or "", "categoria": cat or "",
            "endereco": end or "", "telefone": tel or "", "site": site or "",
            "cnpj": cnpj or "", "razao_social": rz or "", "nome_fantasia": nf or "",
            "cnae": cnae or "", "lat": float(la), "lng": float(lo),
            "place_id": place or "", "logr_marcado": logr_m or "",
            "logr_original": logr_o or "", "numero_canonico": num_c or "",
            "tier": tier or "", "evid": int(evid or 0),
        })
    return pois


def candidatos(pois: list) -> list:
    """Pares vizinhos, sem comparar todo mundo com todo mundo.

    Cada POI entra na célula dele; a comparação varre a célula e as 8 vizinhas,
    e o `id < id` garante que cada par apareça uma vez só.
    """
    grade = {}
    for p in pois:
        grade.setdefault((int(p["lat"] / CELULA), int(p["lng"] / CELULA)), []).append(p)

    vistos, pares = set(), []
    for (cy, cx), aqui in grade.items():
        vizinhos = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                vizinhos.extend(grade.get((cy + dy, cx + dx), ()))
        for a in aqui:
            for b in vizinhos:
                if a["id"] == b["id"]:
                    continue
                chave = (a["id"], b["id"]) if str(a["id"]) < str(b["id"]) \
                    else (b["id"], a["id"])
                if chave in vistos:
                    continue
                vistos.add(chave)
                pares.append((a, b))
    return pares


def _logr_legivel(p: dict) -> str:
    rua, num = ev.logradouro_de(p)
    return f"{rua} {num}".strip()


def avaliar_todos(pares: list) -> dict:
    """Separa os pares em `fundir`, `perguntar` e `descartar`."""
    saida = {"fundir": [], "perguntar": [], "descartar": []}
    for a, b in pares:
        r = ev.avaliar(a, b)
        saida[r["decisao"]].append({"a": a, "b": b, "evidencia": r})
    return saida


def _liga_os_dois(par: dict) -> bool:
    """Existe ALGO ligando os dois além de estarem no mesmo lugar?

    Um token de nome em comum, o mesmo domínio ou o mesmo telefone. Qualquer um
    serve; o julgamento fica com a IA.
    """
    a, b = par["a"], par["b"]
    if ev.semelhanca_nome(a.get("nome", ""), b.get("nome", "")) > 0:
        return True
    da, db = ev.dominio(a.get("site", "")), ev.dominio(b.get("site", ""))
    if da and da == db:
        return True
    ta, tb = ev.so_digitos(a.get("telefone", "")), ev.so_digitos(b.get("telefone", ""))
    return bool(len(ta) >= 8 and ta == tb)


def filtrar_para_ia(perguntar: list, tudo: bool = False) -> tuple:
    """Corta do balde da IA o que é apenas VIZINHANÇA.

    A regra do dono do produto diz que mesma rua a menos de 20 m já é motivo
    para perguntar. Numa rua comercial isso significa perguntar sobre a loja do
    lado — e sobre a do lado da do lado.

    MEDIDO em Cachoeirinha, 25/08/2026: 19.879 pares no balde, e 18.257 deles
    (92%) sem UM token de nome em comum. A amostra diz o que são:

        Mercado Pop Latino  +  Óptica Caelum        19 m
        Mana Modas          +  Igreja Universal     12 m

    São vizinhos, não candidatos. E os 14.556 que entraram por "endereço exato"
    são o mesmo fenômeno com o número igual — galeria e prédio comercial, onde
    dezenas de negócios dividem a porta.

    O corte não é de opinião: exige que exista ALGO ligando os dois além do
    lugar. Sobram 1.659 pares — 415 chamadas em vez de 4.970 — e o que sobra é
    "Farmácia São João" x "Farmácia São João" a 30 m, "La Fiuza Cafe" x
    "La Fiuza Café" a 1 m, "Manga Rosa Modas" x "Manga Rosa" a 8 m.

    `--tudo-para-ia` desliga o corte. Ele existe porque este é um julgamento
    sobre CUSTO, e quem paga a Spark decide.
    """
    if tudo:
        return perguntar, []
    fica = [p for p in perguntar if _liga_os_dois(p)]
    sai = [p for p in perguntar if not _liga_os_dois(p)]
    return fica, sai


def _sobrevivente(a: dict, b: dict) -> tuple:
    """O de MAIS evidência vive. Empate desempata pelo id, para ser estável."""
    if a["evid"] != b["evid"]:
        return (a, b) if a["evid"] > b["evid"] else (b, a)
    return (a, b) if str(a["id"]) < str(b["id"]) else (b, a)


def aplicar(con, decisoes: list) -> int:
    """Grava as fusões. `decisoes` são dicts com `a`, `b`, `evidencia` e
    `confianca` já resolvida.

    A TRANSITIVIDADE É TRATADA: se A absorve B e depois B absorveria C, C tem de
    ir para A. Sem isto o vínculo de C apontaria para um POI já marcado como
    fundido, e a ficha dele ficaria órfã na tela.
    """
    cur = con.cursor()
    destino, n = {}, 0

    def raiz(pid):
        while pid in destino:
            pid = destino[pid]
        return pid

    for d in sorted(decisoes, key=lambda x: -x["confianca"]):
        vive, morre = _sobrevivente(d["a"], d["b"])
        rv, rm = raiz(vive["id"]), raiz(morre["id"])
        if rv == rm:
            continue                      # já estão no mesmo POI
        motivo = (f"{d['origem']}: " + " · ".join(d["evidencia"]["motivos"]))[:400]
        cur.execute("""update vinculo_poi set poi_id = %s, confianca = %s,
                              confianca_origem = %s, motivo = %s
                        where poi_id = %s and estado = 'vinculado'""",
                    (rv, d["confianca"], d["origem"], motivo, rm))
        cur.execute("update pois set status = 'fundido' where id = %s", (rm,))
        destino[rm] = rv
        n += 1
    return n


def cruzar(cidade: str, empresa: str, aplicar_de_fato: bool, usar_ia: bool,
           tudo_para_ia: bool = False, area: str = "") -> None:
    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    dono = _empresa(cur, empresa)

    poligono = au.carregar_area(area) if area else None
    if area and not poligono:
        raise SystemExit(f"nao ha area desenhada salva com a referencia {area!r}")

    pois = carregar(cur, cidade, poligono)
    escopo = f"area {area!r} + 150 m" if poligono else (cidade or "todas as cidades")
    print(f"  {dono} · {escopo}: {len(pois):,} POIs")
    com_logr = sum(1 for p in pois if p.get("logr_marcado"))
    print(f"  {com_logr:,} com logradouro normalizado "
          f"({com_logr / max(1, len(pois)):.0%}) — é a chave de junção")

    pares = candidatos(pois)
    # SO INTERESSA O PAR QUE TOCA A AREA. A margem trouxe o entorno para que o
    # duplicado da borda fosse visto; par com os DOIS lados fora e vizinhanca de
    # fora do pedido, e julga-la seria pagar pelo que o operador nao desenhou.
    if poligono:
        antes = len(pares)
        pares = [(a, b) for a, b in pares
                 if _um_lado_dentro({"a": a, "b": b}, poligono)]
        print(f"  {antes:,} pares na caixa folgada · {len(pares):,} tocam a area")
    print(f"  {len(pares):,} pares vizinhos a comparar")
    if not pares:
        con.close()
        return

    grupos = avaliar_todos(pares)
    print(f"    fundir direto : {len(grupos['fundir']):,}")
    print(f"    perguntar à IA: {len(grupos['perguntar']):,}")
    print(f"    descartados   : {len(grupos['descartar']):,}")

    decisoes = [dict(g, confianca=g["evidencia"]["confianca"], origem="regra")
                for g in grupos["fundir"]]

    perguntar, vizinhos = filtrar_para_ia(grupos["perguntar"], tudo_para_ia)
    if vizinhos:
        print(f"    dos quais {len(vizinhos):,} são só VIZINHANÇA (nenhum token de "
              "nome, domínio ou telefone em comum) — não são perguntados.")
        print(f"    restam {len(perguntar):,} para a IA. `--tudo-para-ia` desliga o corte.")

    if perguntar and usar_ia:
        import julgar_par_banco as jb
        julgados = jb.julgar(perguntar)
        from collections import Counter
        c = Counter(j["veredito"] for j in julgados)
        print(f"    veredito da IA: {dict(c)}")
        for j in julgados:
            if j["veredito"] != "MESMO":
                continue
            # A IA decidindo vale 7: acima do "evidência parcial" que a mandou
            # perguntar, abaixo do 8 que a regra dá quando ela própria fecha.
            # Medido no RS: 66,2% dos pares de evidência fraca eram negócios
            # distintos — o julgamento dela é bom, não é prova.
            j["evidencia"]["motivos"].append(f"IA: {j.get('motivo_ia') or 'mesmo ponto'}")
            decisoes.append(dict(j, confianca=7, origem="ia"))
        falhas = [j for j in julgados if j["veredito"] == "FALHOU"]
        if falhas:
            print(f"    ⚠️  {len(falhas)} pares a IA não conseguiu julgar — "
                  "ficam SEM fusão, e podem ser repetidos depois.")
    elif perguntar:
        print("    (--sem-ia: os duvidosos ficam como estão)")

    print(f"\n  {len(decisoes):,} fusões a aplicar")
    for d in sorted(decisoes, key=lambda x: -x["confianca"])[:8]:
        a, b = d["a"], d["b"]
        print(f"    [{d['confianca']:2}/10 {d['origem']:5}] "
              f"{a['nome'][:26]:28} + {b['nome'][:26]:28} "
              f"{d['evidencia']['dist_m']:.0f} m")
        print(f"                    {' · '.join(d['evidencia']['motivos'])[:110]}")

    if not aplicar_de_fato:
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        con.close()
        return

    n = aplicar(con, decisoes)
    con.commit()
    print(f"\n  GRAVADO: {n:,} POIs absorvidos — marcados 'fundido', NÃO apagados, "
          "com o vínculo transferido. Reversível pelo `x` da ficha.")
    con.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cidade", default="")
    p.add_argument("--empresa", required=True)
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--sem-ia", dest="sem_ia", action="store_true",
                   help="não chama a Spark; os duvidosos ficam como estão")
    p.add_argument("--area", default="",
                   help="nome da area desenhada: cruza so o que a toca (com 150 m "
                        "de folga). Sem isto, cruza o municipio inteiro.")
    p.add_argument("--tudo-para-ia", dest="tudo_para_ia", action="store_true",
                   help="pergunta também sobre os pares que são só vizinhança "
                        "(12x mais chamadas — veja `filtrar_para_ia`)")
    a = p.parse_args(argv)
    cruzar(a.cidade, a.empresa, a.aplicar, not a.sem_ia, a.tudo_para_ia, a.area)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
