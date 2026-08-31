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

O POI ABSORVIDO NÃO É APAGADO. Ganha `fundido_em` e `fundido_para`, mantém a
linha e o
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
   and p.fundido_em is null
   and p.id_empresa = core.empresa_atual()
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
    cur.execute("select id, name from core.tb_empresas where lower(name)=lower(%s) and ativa",
                (nome.strip(),))
    return bc.assumir_empresa(cur, nome)[1]


def carregar(cur, cidade: str, poligono=None,
             com_fundidos: bool = False) -> list:
    par = {"cidade": cidade, "ac": _ACENTOS, "li": _LISOS}
    sql = SQL_POIS
    if com_fundidos:
        # SO O `--desfundir` PEDE ISTO, e precisa: para saber se a fusao
        # ainda se sustenta e preciso comparar os dois lados dela, e um deles
        # esta fundido — fora da consulta normal por definicao.
        sql = sql.replace("and p.fundido_em is null",
                          "and (p.fundido_em is null or p.fundido_para is not null)")
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
    _marcar_multiloja(pois)
    return pois


_CELULA_MULTILOJA = 0.001      # ~110 m; a mesma ordem da grade de vizinhanca


def _marcar_multiloja(pois: list) -> int:
    """Marca quem esta numa porta com mais de `TETO_MULTILOJA` nomes distintos.

    A regra e do par, mas o DADO nao e: saber que a Avenida Farroupilha 4545
    abriga 91 estabelecimentos exige olhar a cidade inteira, e `evidencia.
    avaliar` recebe so dois POIs. Entao a contagem acontece uma vez, aqui, e
    viaja no proprio POI -- `avaliar` continua puro.

    A CHAVE E A MESMA QUE A FUSAO USA. `logradouro_de` normaliza o logradouro e
    tira tudo que nao e digito do numero; contar por `endereco` cru separaria
    "Av. Farroupilha, 4545" de "AVENIDA FARROUPILHA, 4545 - LUC 3003" e o
    shopping deixaria de parecer shopping.

    So conta porta COM numero: "mesma rua" sem numero nao e o mesmo lugar, e
    uma avenida inteira teria centenas de nomes sem ser galeria nenhuma.
    """
    from collections import defaultdict
    nomes = defaultdict(set)
    for p in pois:
        rua, num = ev.logradouro_de(p)
        if rua and num:
            n = ev.norm_nome(p.get("nome") or "")
            if n:
                nomes[(rua, num)].add(n)
    portas = {k for k, v in nomes.items() if len(v) > ev.TETO_MULTILOJA}

    # A MARCA CONTAGIA O ENTORNO, e nao so quem tem a porta escrita certa.
    #
    # A loja de dentro do shopping costuma NAO ter o numero: o `Pista de
    # Patinacao (Iceland)` tem logradouro "PARKSHOPPINGCANOAS" e numero vazio,
    # enquanto o proprio shopping tem "AVENIDA FARROUPILHA 4545". Marcar so
    # quem casa a porta deixaria de fora exatamente quem a regra existe para
    # separar.
    #
    # Entao quem esta a menos de `RAIO_M` de um POI de porta-multiloja herda a
    # marca. A grade e a mesma ideia da vizinhanca em `candidatos`: celula de
    # ~0,001 grau, varre as 8 vizinhas.
    from math import floor
    grade = {}
    for p in pois:
        rua, num = ev.logradouro_de(p)
        if rua and num and (rua, num) in portas:
            c = (floor(p["lat"] / _CELULA_MULTILOJA),
                 floor(p["lng"] / _CELULA_MULTILOJA))
            grade.setdefault(c, []).append(p)

    marcados = 0
    for p in pois:
        rua, num = ev.logradouro_de(p)
        propria = bool(rua and num and (rua, num) in portas)
        if not propria and grade:
            cx = floor(p["lat"] / _CELULA_MULTILOJA)
            cy = floor(p["lng"] / _CELULA_MULTILOJA)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for q in grade.get((cx + dx, cy + dy), ()):
                        if ev.distancia_m(p["lat"], p["lng"],
                                          q["lat"], q["lng"]) <= ev.RAIO_M:
                            propria = True
                            break
                    if propria:
                        break
                if propria:
                    break
        p["multiloja"] = propria
        marcados += propria
    return marcados


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

    A TRANSITIVIDADE TEM DOIS LADOS, e o lote só acertava um deles até
    27/08/2026. Quem MORRE já era resolvido no laço: se A absorve B e depois B
    absorveria C, C vai para A. Quem SOBREVIVE não era: se A absorve B e depois
    C absorve A, o vínculo de B ficava apontando para A, que acabara de virar
    `fundido`. Por isso o destino de cada fusão é resolvido OUTRA VEZ depois do
    laço, com `destino` completo — veja o passo 1b.

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

    # 1b. O DESTINO TEM DE SER O FINAL, e nao o de quem sobrevivia na hora.
    #
    # DEFEITO INTRODUZIDO PELO PROPRIO LOTE, medido em 27/08/2026: 6 vinculos
    # ativos apontando para POI ja marcado `fundido` -- ficha orfa na tela.
    #
    # A versao de antes gravava DENTRO do laco, uma fusao por vez. Quando uma
    # fusao posterior movia os vinculos de `rv` para `X`, os que ja tinham sido
    # movidos para `rv` estavam la e iam junto: a cadeia se resolvia pela ordem
    # de execucao, sem ninguem ter escrito codigo para isso.
    #
    # No lote nao se resolve: `UPDATE ... FROM (VALUES)` casa cada linha contra
    # o estado ANTERIOR ao comando. A linha movida de `rm` para `rv` nao e
    # recasada com `f.morre = rv`, entao fica em `rv` -- que o comando seguinte
    # marca como fundido.
    #
    #     A absorve B   ->  vinculo de B vai para A
    #     C absorve A   ->  vinculo de B FICA em A, que morreu
    #
    # Aqui `destino` ja esta completo, entao `raiz(rv)` anda a cadeia inteira e
    # todo vinculo vai direto para o sobrevivente final. E o unico ponto do
    # arquivo em que isso pode ser feito: antes do laco terminar, a cadeia
    # ainda nao existe.
    fusoes = [(rm, raiz(rv), conf, origem, motivo)
              for rm, rv, conf, origem, motivo in fusoes]

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

        # O DESTINO VAI JUNTO, e e' a unica hora em que ele existe.
        #
        # Ate 27/08/2026 a fusao so marcava o absorvido e a cadeia morria com o
        # processo. Quando 6 vinculos ficaram apontando para POIs fundidos, nao
        # havia como saber para onde cada um deveria ir -- foi preciso rastrear
        # pelo nome, e 11,7% dos casos dariam mais de um destino.
        #
        # E `fundido_em` no lugar de `status = 'fundido'`: `status` guarda a
        # ORIGEM do ponto, aparece na ficha e e' contado no painel. Sobrescreve-lo
        # apagava esse dado para sempre e tornava a fusao IRREVERSIVEL -- ver a
        # migracao 0036.
        # E QUEM DECIDIU VAI JUNTO -- e a unica hora em que se sabe.
        #
        # Uma fusao que a REGRA fez e que a regra nao faria mais esta obsoleta:
        # desfaze-la e a correcao que o `--desfundir` existe para aplicar. Uma
        # que a IA decidiu nao esta -- a regra nunca a faria, e por isso a IA
        # foi consultada. Sem distinguir as duas, desfazer gira em falso: ver
        # a migracao 0038.
        psycopg2.extras.execute_values(cur, """
            update pois p
               set fundido_em = now(), fundido_para = f.vive,
                   fundido_por = f.quem
              from (values %s) as f(morre, vive, quem)
             where p.id = f.morre""",
            [(m, v, o) for m, v, _, o, _ in bloco],
            template="(%s::bigint, %s::bigint, %s::text)")
        if len(fusoes) > LOTE:
            log(f"    gravadas {min(i + LOTE, len(fusoes)):,} de {len(fusoes):,}")
    return len(fusoes)


def _gravar_multiloja(cur, pois: list) -> int:
    """Leva a marca de multiloja para o banco — como DADO, nao como decisao.

    Ela ja foi um corte na `evidencia`, e o corte errava 32% das vezes (ver a
    migracao 0037). Hoje quem decide o par e a IA; a marca fica porque saber que
    um ponto esta num predio de varias lojas vale na tela e na revisao humana.

    E DERIVADA e recalculada a cada passada: o numero de nomes numa porta muda
    quando POIs entram, saem ou se fundem. Grava os DOIS lados (`true` e
    `false`) sobre todos os POIs carregados — so escrever os `true` deixaria
    para tras quem deixou de ser multiloja.

    FICA FORA DO BLOCO DE CARIMBO, e isso e conserto de 28/08/2026. Ela morava
    junto do `cruzado_em`, depois do `if not pares: return` — entao numa run que
    nao encontrava par nenhum a marca simplesmente nao era atualizada. E essa e
    a run COMUM: a rodada de 08:19 daquele dia carregou 32.361 POIs, comparou
    zero pares (todos ja carimbados) e saiu sem tocar na marca. O dado envelhecia
    exatamente nas passadas baratas.
    """
    ids = [p["id"] for p in pois]
    if not ids:
        return 0
    marcados = [p["id"] for p in pois if p.get("multiloja")]
    cur.execute("update pois set multiloja = (id = any(%s)) where id = any(%s)",
                (marcados, ids))
    return len(marcados)


def _desfundir(cur, cidade: str, poligono=None,
               so_o_que_mudou: bool = True,
               incluir_ia: bool = False) -> int:
    """Devolve ao mapa os POIs absorvidos, para que TUDO volte a ser comparado.

    POR QUE ISTO PRECISOU EXISTIR

    `--recruzar` limpa o carimbo `cruzado_em` e refaz todos os pares — mas só
    entre POIs ATIVOS. Quem já foi absorvido está fora do `carregar`, e por isso
    uma fusão errada era definitiva: nenhuma regra nova a alcançava.

    Foi o caso do `ParkShoppingCanoas`, absorvido pela `Pista de Patinação
    (Iceland)` de dentro dele. A regra de multiloja, escrita depois, teria
    impedido a fusão — e não tinha como desfazê-la.

    ISTO SÓ É POSSÍVEL DESDE A MIGRAÇÃO 0036. Antes, absorver sobrescrevia
    `pois.status` — que guarda a ORIGEM do ponto — com a palavra `fundido`, e o
    valor anterior se perdia. Desfazer devolveria o POI ao mapa com a origem
    errada. Hoje a fusão mora em `fundido_em`/`fundido_para` e o `status` não é
    tocado, então desfazer não perde nada.

    O VÍNCULO VOLTA JUNTO. O que a fusão fez foi mover os vínculos do absorvido
    para o sobrevivente; desfazer sem devolvê-los deixaria um ponto no mapa sem
    evidência nenhuma — pior que a fusão errada. `fundido_para` é o que torna
    isso possível, e é por isso que ele passou a ser gravado.
    """
    # SEM DESTINO NÃO SE DESFAZ, e a medição obrigou esta regra.
    #
    # O primeiro ensaio devolveu 15.385 POIs ao mapa e **nenhum vínculo junto**:
    # os absorvidos antes da migração 0036 têm `fundido_para` vazio, e sem saber
    # em quem cada um entrou não há como recuperar a evidência dele. Eram 15.388
    # pontos ativos sem vínculo — invisíveis no mapa, que exige vínculo ativo, e
    # sem telefone, site ou endereço na ficha.
    #
    # Ponto absorvido é melhor que ponto oco. Quem não tem destino gravado fica
    # como está; `backfill_fundido_para.py` recupera o que for rastreável.
    onde, par = ["fundido_em is not null", "fundido_para is not null"], {}

    # A DECISAO DA IA NAO REABRE SOZINHA, e sem isto o desfazer gira em falso.
    #
    # O alvo pergunta "a regra de hoje refaria?". Para o par que a IA decidiu a
    # resposta e SEMPRE nao — a regra nunca o faria, e e exatamente por isso que
    # a IA foi consultada. Entao ele e elegivel toda vez: desfeito, perguntado,
    # refundido, elegivel de novo.
    #
    # MEDIDO em 28/08/2026: a execucao real desfez 2.483 e o cruzamento refez
    # 1.913; a passada seguinte encontraria mais 1.824. Nao era resto, era laco.
    #
    # `--desfundir-ia` reabre tambem as dela, para quando o que mudou foi o
    # modelo ou o prompt — ai a pergunta e outra e vale refaze-la.
    if not incluir_ia:
        onde.append("coalesce(fundido_por, '') <> 'ia'")
    if cidade:
        onde.append("upper(translate(coalesce(cidade, ''), %(ac)s, %(li)s)) "
                    "like upper(translate(%(cidade)s, %(ac)s, %(li)s)) || '%%'")
        par.update({"cidade": cidade, "ac": _ACENTOS, "li": _LISOS})
    if poligono:
        s, n, o, l = au.bbox_com_margem(poligono)
        onde.append("coalesce(maps_lat, lat_origem) between %(area_s)s and %(area_n)s")
        onde.append("coalesce(maps_lng, lng_origem) between %(area_o)s and %(area_l)s")
        par.update({"area_s": s, "area_n": n, "area_o": o, "area_l": l})

    # DUPLICATA LITERAL NAO VOLTA, e o indice unico e quem manda nisso.
    #
    # `pois_sem_duplicata` proibe dois POIs ATIVOS com o mesmo nome e o mesmo
    # endereco. Devolver ao mapa quem foi absorvido por ser exatamente o mesmo
    # registro recria a duplicata que o indice existe para impedir -- e a
    # primeira versao desta funcao morreu assim:
    #
    #     duplicate key ... (IGREJA NOSSA SENHORA DO ROSARIO, RUA DUQUE DE CAXIAS)
    #
    # A recusa esta certa, e delimita a regra: so ha o que reavaliar em quem
    # NAO e' copia literal. Quem e' voltaria a fundir na mesma passada, pela
    # mesma evidencia. Os casos que uma regra nova alcanca sao os outros --
    # nome diferente, endereco diferente -- e e' o caso do
    # `ParkShoppingCanoas`, absorvido pela `Pista de Patinacao` com nome E
    # endereco distintos.
    onde.append("""not exists (select 1 from pois q
                                 where q.fundido_em is null
                                   and upper(trim(q.nome)) = upper(trim(pois.nome))
                                   and upper(trim(q.endereco)) = upper(trim(pois.endereco)))""")

    # E O LOTE TAMBEM DESDUPLICA CONTRA SI MESMO.
    #
    # O `not exists` acima olha os POIs ATIVOS. Dois absorvidos que sejam copia
    # um do outro passam os dois -- nenhum esta ativo -- e colidem entre si na
    # hora de voltar. Foi o segundo `duplicate key` desta funcao:
    #
    #     (LABORATORIO DE ANATOMIA, ULBRA CAMPUS CANOAS, CANOAS, 92425-900)
    #
    # Volta um por chave. Os outros seguem absorvidos, que e' o que eles sao.
    cur.execute(f"""
        select id, fundido_para from (
            select id, fundido_para,
                   row_number() over (partition by upper(trim(nome)),
                                                   upper(trim(endereco))
                                          order by id) as ordem
              from pois where {' and '.join(onde)}) t
         where ordem = 1""", par)
    voltam = cur.fetchall()
    if not voltam:
        return 0

    # NÃO SE DESFAZ O QUE O PROCESSO REFARIA NA MESMA PASSADA.
    #
    # Sem este corte, `--desfundir` devolvia 13.252 pontos ao mapa e o
    # cruzamento em seguida refundia 10.775 deles — 81% de trabalho ida e
    # volta, com o mapa duplicado no meio do caminho. Os exemplos dizem o que
    # são:
    #
    #     Primos fratelli      Avenida das Canoas, nº 264
    #     Primos fratelli      Avenida das Canoas, 264 - Canoas - RS      0 m
    #
    # É o MESMO estabelecimento com o endereço escrito de duas formas. Desfazer
    # isso não revê nada.
    #
    # O que vale desfazer é o que a regra de HOJE não refaria — e é onde as
    # regras novas alcançam o passado. Medido em Canoas, 28/08/2026:
    #
    #     refaria .............. 10.775   não se toca
    #     a IA julgaria ......... 2.302   inclui o ParkShoppingCanoas
    #     não fundiria ............ 145
    #     separaria sem julgar ..... 41   o `filtrar_para_ia` corta o par
    if so_o_que_mudou:
        antes = len(voltam)
        idx = {p["id"]: p for p in
               carregar(cur, cidade, poligono, com_fundidos=True)}
        voltam = [(m, v) for m, v in voltam
                  if m in idx and v in idx
                  and ev.avaliar(idx[m], idx[v])["decisao"] != "fundir"]
        print(f"    {antes - len(voltam):,} fusões que o processo refaria ficam "
              f"como estão; {len(voltam):,} vão à reavaliação")
        if not voltam:
            return 0

    # 1. O VÍNCULO VOLTA PARA CASA — e VOLTA O DELE, não um qualquer.
    #
    #    A primeira versão desta função movia `min(id)` dos vínculos do
    #    sobrevivente. Isso é escolher por ordem de chegada: o ponto
    #    ressuscitado podia receber a evidência de OUTRO POI que o sobrevivente
    #    também tinha absorvido, e a ficha passaria a mostrar telefone, site e
    #    endereço de um terceiro estabelecimento. Dado errado com aparência de
    #    dado certo, que é o pior resultado possível aqui.
    #
    #    O vínculo do absorvido é reconhecível pelo NOME: foi o nome dele que a
    #    fusão carregou para o sobrevivente. É o mesmo rastreio que reparou os
    #    6 vínculos órfãos de 27/08/2026, e acertou os 4 casos que restavam.
    #
    #    Quem não casar pelo nome NÃO recebe vínculo nenhum. O ponto volta sem
    #    evidência e fica fora do mapa até o `povoar_vinculo` lhe dar a dele —
    #    uma ausência que se conserta sozinha, contra um erro que ninguém veria.
    #    O PAREAMENTO ACONTECE NO PYTHON, com `norm_nome` — o mesmo
    #    normalizador que gravou os destinos. Uma versão anterior comparava
    #    `upper(trim())` no SQL, que é mais fraco: acento e pontuação separam o
    #    que a normalização junta. Resultado medido: 13.252 fusões desfeitas e
    #    só ~2.000 vínculos devolvidos — 11.190 pontos voltaram ocos. Duas
    #    normalizações diferentes para a mesma pergunta sempre divergem; a
    #    única defesa é usar uma só.
    import psycopg2.extras
    com_destino = [(m, v) for m, v in voltam if v]
    if com_destino:
        mortos = [m for m, _ in com_destino]
        cur.execute("select id, nome from pois where id = any(%s)", (mortos,))
        nome_do_morto = {i: ev.norm_nome(n or "") for i, n in cur.fetchall()}

        vivos = sorted({v for _, v in com_destino})
        cur.execute("""select id, poi_id, nome from vinculo_poi
                        where poi_id = any(%s) and estado = 'vinculado'""",
                    (vivos,))
        do_vivo = {}
        for vid, pid, nome in cur.fetchall():
            do_vivo.setdefault((pid, ev.norm_nome(nome or "")), []).append(vid)

        # UM VÍNCULO POR RESSUSCITADO, E NUNCA O ÚLTIMO DO SOBREVIVENTE.
        #
        # Quando os dois POIs tinham o mesmo nome — que é o caso comum, porque
        # é o nome igual que os fez fundir — o sobrevivente fica com DOIS
        # vínculos daquele nome: o dele e o que veio do absorvido. Devolver os
        # dois deixa o sobrevivente sem evidência nenhuma.
        #
        # MEDIDO antes deste conserto: 13.252 fusões desfeitas e 11.314 pontos
        # ocos — e 10.690 deles eram SOBREVIVENTES, não os ressuscitados. O
        # desfazer estava esvaziando quem ficou.
        cur.execute("""select poi_id, count(*) from vinculo_poi
                        where poi_id = any(%s) and estado = 'vinculado'
                        group by 1""", (vivos,))
        restam = dict(cur.fetchall())

        devolver = []
        for morre, vive in com_destino:
            fila = do_vivo.get((vive, nome_do_morto.get(morre, "")), [])
            if fila and restam.get(vive, 0) > 1:
                devolver.append((fila.pop(), morre))
                restam[vive] -= 1
        if devolver:
            psycopg2.extras.execute_values(cur, """
                update vinculo_poi w set poi_id = f.morre
                  from (values %s) as f(vid, morre)
                 where w.id = f.vid""",
                devolver, template="(%s::bigint, %s::bigint)")

    # 2. A MARCA SAI, e com ela o carimbo de cruzado dos dois lados: o par
    #    precisa ser reavaliado, não pulado por já ter sido visto.
    ids = [m for m, _ in voltam] + [v for _, v in voltam if v]
    cur.execute("""update pois set fundido_em = null, fundido_para = null,
                          cruzado_em = null
                    where id = any(%s)""", (ids,))
    return len(voltam)


def cruzar(cidade: str, empresa: str, aplicar_de_fato: bool, usar_ia: bool,
           tudo_para_ia: bool = False, area: str = "",
           recruzar: bool = False, desfundir: bool = False,
           so_o_que_mudou: bool = True,
           incluir_ia: bool = False) -> None:
    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    dono = _empresa(cur, empresa)

    poligono = au.carregar_area(area) if area else None
    if area and not poligono:
        raise SystemExit(f"nao ha area desenhada salva com a referencia {area!r}")

    if desfundir:
        # SEM COMMIT AQUI, e isso é deliberado.
        #
        # A primeira versão gravava o desfazer antes de cruzar. Se o
        # cruzamento morresse depois — a IA fora do ar, o SSH caindo, um erro
        # meu —, o banco ficaria com os 15.385 pontos absorvidos de volta no
        # mapa e nada os reunindo: o operador abriria o painel e veria a
        # duplicação que o sistema existe para eliminar.
        #
        # Deixando na mesma transação, desfazer e refazer viram um passo só:
        # ou o mapa fica com a fusão nova, ou continua com a antiga.
        n = _desfundir(cur, cidade, poligono, so_o_que_mudou, incluir_ia)
        print(f"  {n:,} POIs devolvidos ao mapa — a fusão deles foi desfeita, "
              f"e agora TUDO volta a ser comparado")
        print("  (desfazer e refazer estão na MESMA transação: se o cruzamento "
              "falhar, nada disto é gravado)")

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
        # A MARCA AINDA PRECISA SER GRAVADA. Sem par para comparar nao ha fusao,
        # mas o conjunto de POIs pode ter mudado — e a marca e derivada dele.
        if aplicar_de_fato:
            n_multi = _gravar_multiloja(cur, pois)
            con.commit()
            print(f"  {n_multi:,} POIs em endereço de multiloja "
                  f"(mais de {ev.TETO_MULTILOJA} nomes na mesma porta) — "
                  f"é marca, não decide fusão")
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

    n_multi = _gravar_multiloja(cur, pois)
    print(f"  {n_multi:,} POIs em endereço de multiloja "
          f"(mais de {ev.TETO_MULTILOJA} nomes na mesma porta) — é marca, "
          f"não decide fusão")


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
    p.add_argument("--desfundir", action="store_true",
                   help="desfaz as fusoes que a regra de HOJE nao refaria, "
                        "antes de recruzar — o unico jeito de uma regra nova "
                        "alcancar o que ja foi fundido")
    p.add_argument("--desfundir-ia", dest="desfundir_ia",
                   action="store_true",
                   help="reabre tambem as fusoes que a IA decidiu. So faz "
                        "sentido quando o que mudou foi o modelo ou o prompt")
    p.add_argument("--desfundir-tudo", dest="desfundir_tudo",
                   action="store_true",
                   help="desfaz TODAS, inclusive as que serao refeitas "
                        "identicas. Custa a passada inteira e o mapa fica "
                        "duplicado no meio do caminho")
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
           a.area,
           a.recruzar or a.desfundir or a.desfundir_tudo or a.desfundir_ia,
           a.desfundir or a.desfundir_tudo or a.desfundir_ia,
           not a.desfundir_tudo, a.desfundir_ia)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
