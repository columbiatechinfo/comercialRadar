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

# Teto do grupo "mesmo nome + mesma rua". Vinte POIs dão 190 pares, que é
# barato; quarenta dariam 780, e um nome genérico numa avenida longa produz
# exatamente isso sem que nenhum deles seja duplicado de verdade.
TETO_GRUPO_NOME = 20

SQL_POIS = """
select p.id, p.nome, p.fonte, p.categoria, p.endereco, p.telefone, p.website,
       p.cnpj, p.razao_social, p.nome_fantasia, p.cnae,
       coalesce(p.maps_lat, p.lat_origem), coalesce(p.maps_lng, p.lng_origem),
       p.place_id,
       la.logradouro_marcado, la.logradouro_original, la.numero_canonico, la.tier,
       p.cruzado_em,
       (select count(*) from streetview_imgs s where s.poi_id = p.id)
     + (select count(*) from analise_ia a where a.poi_id = p.id) as evid
  from pois p
  left join logradouro_ajustado la
         on la.fonte = 'pois' and la.record_id = p.id::text
 where coalesce(p.maps_lat, p.lat_origem) is not null
   and coalesce(p.maps_lng, p.lng_origem) is not null
   and coalesce(p.status, '') <> 'fundido'
   and p.tenant_id = (select nullif(current_setting('app.tenant_id', true), '')::uuid)
   and (%(cidade)s = '' or upper(translate(coalesce(p.cidade, ''), %(ac)s, %(li)s))
                         = upper(translate(%(cidade)s, %(ac)s, %(li)s)))
"""

# O corte pela area entra depois do `where`, e por isso a consulta acima termina
# nele. Ver `_com_margem`: a caixa vai folgada de proposito.
# A COORDENADA ORIGINAL VALE AQUI TAMBÉM — e ignorá-la deixava duplicata na
# tela do operador.
#
# Medido em 26/08/2026, Av. Farroupilha, Canoas: "Bico de Pão" aparecia TRÊS
# vezes. Dois dos registros tinham `maps_lat` nulo e só `lat_origem` — e o
# `where p.maps_lat is not null` os tornava invisíveis para a fusão. Um deles
# era idêntico ao que a fusão via: mesmo nome, mesmo telefone (51 3478-4848),
# mesmo site (bicodepaors.com) e a MESMA coordenada. Fundiria com confiança 10.
#
# `maps_lat` é a coordenada que a busca no Maps confirmou; `lat_origem` é a que
# a fonte trouxe. Exigir a primeira descartava tudo que veio de planilha com
# status `descoberto` — justamente o que mais tende a duplicar o que as bases
# públicas já trouxeram.
SQL_AREA = (" and coalesce(p.maps_lat, p.lat_origem) between %(area_s)s and %(area_n)s"
            " and coalesce(p.maps_lng, p.lng_origem) between %(area_o)s and %(area_l)s")

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
         la, lo, place, logr_m, logr_o, num_c, tier, cruzado, evid) in cur.fetchall():
        pois.append({
            "id": pid, "nome": nome or "", "fonte": fonte or "", "categoria": cat or "",
            "endereco": end or "", "telefone": tel or "", "site": site or "",
            "cnpj": cnpj or "", "razao_social": rz or "", "nome_fantasia": nf or "",
            "cnae": cnae or "", "lat": float(la), "lng": float(lo),
            "place_id": place or "", "logr_marcado": logr_m or "",
            "logr_original": logr_o or "", "numero_canonico": num_c or "",
            "tier": tier or "", "cruzado_em": cruzado, "evid": int(evid or 0),
        })
    return pois


def candidatos(pois: list) -> list:
    """Pares candidatos por DOIS caminhos, porque um raio só não dá conta.

    1. VIZINHANÇA. Cada POI entra na célula dele; a comparação varre a célula e
       as 8 vizinhas. Alcance de ~330 m no pior caso, e é o caminho que acha
       duplicado sem nome em comum — "Farmácia" e "Drogaria São João" na mesma
       porta.

    2. MESMO NOME NA MESMA RUA. Uma chave, não um raio.

    O SEGUNDO CAMINHO EXISTE POR UM DEFEITO MEU, achado em 27/08/2026.

    A regra do dono do produto — "mesmo nome e mesmo logradouro é confiança
    máxima, mesmo a 50 metros ou 100" — foi implementada em `evidencia.avaliar`
    com alcance de 1.000 m. Só que a geração de candidatos continuou sendo a
    grade de 111 m. A regra virou letra morta justamente na faixa que ela
    existia para cobrir: o par a 400 m nunca era proposto, então nunca era
    julgado.

    MEDIDO em Canoas, depois de uma rodada completa: 344 grupos de mesmo nome +
    mesmo logradouro continuavam separados, e 334 deles estavam a MAIS de 100 m.
    Só 8 dentro de 20 m. O buraco era quase todo fora do alcance da grade.

    Alargar a grade para 1 km seria o conserto errado: as células de 111 m
    viram 19x19 e os 2,7 milhões de pares de Canoas passariam de 100 milhões,
    para achar algumas centenas. Chave é O(n) com um dicionário.

    O TETO POR GRUPO existe porque nome genérico em rua longa explode: uma rua
    com 40 "Farmácia" daria 780 pares sozinha. Acima do teto o grupo é deixado
    para a vizinhança resolver — e o que ficou de fora é DITO, nunca calado.
    """
    vistos, pares = set(), []

    def _juntar(a, b):
        if a["id"] == b["id"]:
            return
        chave = (a["id"], b["id"]) if str(a["id"]) < str(b["id"]) \
            else (b["id"], a["id"])
        if chave in vistos:
            return
        vistos.add(chave)
        pares.append((a, b))

    # 1. vizinhança
    grade = {}
    for p in pois:
        grade.setdefault((int(p["lat"] / CELULA), int(p["lng"] / CELULA)), []).append(p)
    for (cy, cx), aqui in grade.items():
        vizinhos = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                vizinhos.extend(grade.get((cy + dy, cx + dx), ()))
        for a in aqui:
            for b in vizinhos:
                _juntar(a, b)

    # 2. mesmo nome na mesma rua, a qualquer distância dentro da cidade
    por_chave = {}
    for p in pois:
        nome = ev.nome_util(p.get("nome"))
        rua, _num = ev.logradouro_de(p)
        if not nome or not rua:
            continue
        por_chave.setdefault((ev.norm_nome(nome), rua), []).append(p)

    grandes = 0
    for (_n, _r), grupo in por_chave.items():
        if len(grupo) < 2:
            continue
        if len(grupo) > TETO_GRUPO_NOME:
            grandes += 1
            continue
        for i, a in enumerate(grupo):
            for b in grupo[i + 1:]:
                _juntar(a, b)
    if grandes:
        print(f"  {grandes} grupo(s) de mesmo nome+rua acima de "
              f"{TETO_GRUPO_NOME} POIs ficaram para a vizinhança resolver")
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
    """Quem vive é quem tem o dado MAIS VERIFICÁVEL, não só o mais volumoso.

    A ORDEM, e por que ela é esta:

    1. QUEM TEM `place_id` DO GOOGLE MAPS. Regra do dono do produto,
       27/08/2026: "dado vindo do Google Maps tem peso maior que as demais
       fontes por ser verificável e recente".

       E é uma afirmação sobre a natureza do dado, não preferência de marca. O
       `place_id` é uma ficha pública que qualquer pessoa abre e confere hoje:
       nome como o estabelecimento se anuncia, endereço que o Google
       geocodificou, telefone, horário, foto. As outras bases são extrações
       datadas — a estadual e o cadastro dizem o que era verdade quando foram
       exportados, e nada neles envelhece de forma visível.

       Isto decide o que o mapa MOSTRA: o sobrevivente empresta nome, endereço
       e coordenada ao ponto fundido. O absorvido não some — vira aba, com
       tudo o que trouxe.

    2. MAIS EVIDÊNCIA. Entre dois sem Maps, ou dois com Maps, vence quem tem
       mais fontes sustentando.

    3. O MENOR id. Não é critério de qualidade: é o desempate que torna o
       resultado o mesmo em toda execução. Sem ele, duas rodadas sobre os
       mesmos dados poderiam eleger sobreviventes diferentes, e a ficha do
       ponto mudaria de dono sem nada ter mudado no mundo.
    """
    ma, mb = bool(a.get("place_id")), bool(b.get("place_id"))
    if ma != mb:
        return (a, b) if ma else (b, a)
    if a["evid"] != b["evid"]:
        return (a, b) if a["evid"] > b["evid"] else (b, a)
    return (a, b) if str(a["id"]) < str(b["id"]) else (b, a)


def aplicar(con, decisoes: list, log=print) -> int:
    """Grava as fusões EM LOTE. Devolve quantos POIs foram absorvidos.

    POR QUE EM LOTE, e o número que obrigou a mudança

    A primeira versão mandava dois `UPDATE` por fusão, um a um. Em Canoas foram
    13.271 fusões = **26.542 idas e voltas** ao Postgres do i9, e a etapa levou
    ~15 minutos — quase tudo em latência de rede, não em trabalho de banco.
    Numa capital isso escala mal.

    Aqui a decisão de QUEM absorve QUEM continua sendo tomada em Python, uma a
    uma (a transitividade exige ordem), mas a ESCRITA vira dois comandos:

        um `UPDATE ... FROM (VALUES ...)` para reapontar os vínculos
        um `UPDATE ... WHERE id = ANY(...)` para marcar os absorvidos

    A TRANSITIVIDADE CONTINUA TRATADA, e ela é o motivo de a decisão não poder
    ser feita em SQL puro: se A absorve B e depois B absorveria C, C tem de ir
    para A — não para B, que já é um POI fundido. `raiz()` resolve a cadeia
    ANTES de qualquer escrita, então o lote já sai com o destino final.

    O TAMANHO DO LOTE É 1.000 de propósito. `execute_values` monta um comando
    com todos os valores embutidos; com 13 mil linhas o texto do comando passa
    de megabytes e o parser do Postgres vira o gargalo no lugar da rede.
    """
    import psycopg2.extras

    cur = con.cursor()
    destino = {}

    def raiz(pid):
        while pid in destino:
            pid = destino[pid]
        return pid

    # 1. RESOLVE AS CADEIAS PRIMEIRO, em memória. Nada é escrito aqui.
    fusoes = []
    for d in sorted(decisoes, key=lambda x: -x["confianca"]):
        vive, morre = _sobrevivente(d["a"], d["b"])
        rv, rm = raiz(vive["id"]), raiz(morre["id"])
        if rv == rm:
            continue                      # já estão no mesmo POI
        motivo = (f"{d['origem']}: " + " · ".join(d["evidencia"]["motivos"]))[:400]
        fusoes.append((rm, rv, d["confianca"], d["origem"], motivo))
        destino[rm] = rv

    if not fusoes:
        return 0

    # 2. ESCREVE EM LOTE. Dois comandos por bloco, não dois por fusão.
    LOTE = 1000
    for i in range(0, len(fusoes), LOTE):
        bloco = fusoes[i:i + LOTE]
        psycopg2.extras.execute_values(cur, """
            update vinculo_poi v
               set poi_id = f.vive, confianca = f.conf,
                   confianca_origem = f.origem, motivo = f.motivo
              from (values %s) as f(morre, vive, conf, origem, motivo)
             where v.poi_id = f.morre and v.estado = 'vinculado'""",
            bloco, template="(%s::bigint, %s::bigint, %s::int, %s::text, %s::text)")

        cur.execute("update pois set status = 'fundido' where id = any(%s)",
                    ([m for m, *_ in bloco],))
        if len(fusoes) > LOTE:
            log(f"    gravadas {min(i + LOTE, len(fusoes)):,} de {len(fusoes):,}")
    return len(fusoes)


def cruzar(cidade: str, empresa: str, aplicar_de_fato: bool, usar_ia: bool,
           tudo_para_ia: bool = False, area: str = "",
           recruzar: bool = False) -> None:
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

    # SÓ O PAR QUE TOCA ALGUÉM AINDA NÃO CRUZADO.
    #
    # Dois POIs que já foram comparados entre si na rodada passada não podem
    # produzir resultado novo agora: as regras não mudaram e o dado deles
    # também não. Refazer é pagar de novo pela mesma resposta.
    #
    # MEDIDO em Canoas, 27/08/2026, numa rodada que trouxe UM POI novo:
    #
    #     todos contra todos ............ 1.698.100 pares
    #     os que tocam o POI novo ...........  304 pares      99,98% a menos
    #
    # E não é só tempo de CPU: os pares que sobram do corte de vizinhança viram
    # pergunta para a IA. Naquela rodada foram 3.236 chamadas à Spark para zero
    # POI novo em Canoas — todas repetindo veredito já dado.
    #
    # QUANDO AS REGRAS MUDAM, ISTO PRECISA SER DESLIGADO. Hoje mesmo a regra do
    # número da porta e o segundo caminho de candidatos entraram: pares antigos
    # que ninguém reavaliasse ficariam com o veredito velho para sempre. É o
    # que `--recruzar` faz — limpa o carimbo e força a passada inteira.
    if not recruzar:
        antes = len(pares)
        pares = [(a, b) for a, b in pares
                 if not a.get("cruzado_em") or not b.get("cruzado_em")]
        if antes != len(pares):
            print(f"  {antes:,} pares no total · {len(pares):,} tocam POI ainda "
                  f"não cruzado  (--recruzar refaz tudo)")

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

    n = aplicar(con, decisoes, print)

    # O CARIMBO, e ele é o que fecha o ciclo.
    #
    # Sem gravar quem já foi cruzado, o filtro lá em cima nunca teria o que
    # filtrar e a rodada seguinte refaria os mesmos 1,7 milhão de pares. Ele
    # entra DEPOIS do `aplicar` e ANTES do commit: se a gravação falhar, a
    # transação inteira volta e ninguém fica marcado como cruzado sem ter sido.
    #
    # Marca só quem ENTROU nesta comparação. Um POI que a consulta não trouxe —
    # de outra cidade, fora da caixa da área — não foi cruzado e não pode
    # receber o carimbo.
    ids = [p["id"] for p in pois]
    if ids:
        cur.execute("update pois set cruzado_em = now() where id = any(%s)", (ids,))
        print(f"  {cur.rowcount:,} POIs marcados como cruzados "
              f"(a próxima rodada só compara os novos)")

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
    p.add_argument("--recruzar", action="store_true",
                   help="refaz TODOS os pares, inclusive os ja cruzados. Necessario "
                        "quando as regras de fusao mudam — sem isto o par antigo "
                        "fica com o veredito velho para sempre.")
    p.add_argument("--sem-ia", dest="sem_ia", action="store_true",
                   help="não chama a Spark; os duvidosos ficam como estão")
    p.add_argument("--area", default="",
                   help="nome da area desenhada: cruza so o que a toca (com 150 m "
                        "de folga). Sem isto, cruza o municipio inteiro.")
    p.add_argument("--tudo-para-ia", dest="tudo_para_ia", action="store_true",
                   help="pergunta também sobre os pares que são só vizinhança "
                        "(12x mais chamadas — veja `filtrar_para_ia`)")
    a = p.parse_args(argv)
    cruzar(a.cidade, a.empresa, a.aplicar, not a.sem_ia, a.tudo_para_ia,
           a.area, a.recruzar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
