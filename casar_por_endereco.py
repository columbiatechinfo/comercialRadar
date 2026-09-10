# -*- coding: utf-8 -*-
"""O POI órfão que PUBLICA rua e número: procura a ligação por endereço, não por distância.

POR QUE ISTO EXISTE. O cruzamento só olha 60 m ao redor do hidrômetro. Isso
resolve o POI cuja coordenada está mais ou menos certa — e perde exatamente o
caso oposto: a fonte publicou "Rua Tal, 350", e o geocodificador jogou o ponto
no eixo da via, no centroide do bairro ou no CEP. Esse POI é bom e está longe.

A ORDEM É DELIBERADA: só entra aqui quem já não achou ligação nenhuma pela
geometria. Rodar isto antes faria o endereço publicado competir com a
proximidade, e não é disso que se trata — é do que sobrou.

Decisão do dono do produto em 08/09/2026: "primeiro tentar casar pelo endereço
publicado", antes de mandar 47.275 POIs para uma fila humana.
"""
import argparse
import time
from collections import Counter, defaultdict

import base_comum as bc
import regra_vinculo as rv
from cruzar_ligacao import _via


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def _cep(s):
    """Oito digitos, ou nada. CEP pela metade nao desempata coisa nenhuma."""
    d = "".join(c for c in str(s or "") if c.isdigit())
    return d if len(d) == 8 else None


def _metros(la1, lo1, la2, lo2):
    import math
    if None in (la1, lo1, la2, lo2):
        return None
    dy = (la2 - la1) * 111320.0
    dx = (lo2 - lo1) * 111320.0 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot(dx, dy)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cidade", required=True)
    p.add_argument("--base", type=int, default=1)
    p.add_argument("--aplicar", action="store_true")
    # MOVER A COORDENADA NAO E MAIS O PADRAO, e a inversao e deliberada.
    #
    # Decisao do dono do produto em 09/09/2026: "corrige as coordenadas pra
    # seus locais originais". A coordenada que a fonte publicou E UM DADO DA
    # FONTE — dizer onde ela achou que o lugar fica. Sobrescreve-la com a
    # coordenada do hidrometro faz o ponto parar de responder "onde a Receita
    # disse que isto fica" e passar a responder "onde nos decidimos por-lo".
    # As duas sao uteis, mas so uma e observacao.
    #
    # A coordenada antiga continuava guardada em `coord_anterior_*`, entao nada
    # se perderia — mas o padrao de um comando nao deve ser a acao que altera
    # dado de origem. Quem quiser, pede.
    p.add_argument("--mover", action="store_true",
                   help="ALEM de vincular, move a coordenada do POI casado "
                        "para a porta da ligacao (nao e o padrao)")
    a = p.parse_args(argv)

    con = bc.conectar()
    cur = con.cursor()

    _log("lendo as ligações de %s..." % a.cidade)
    cur.execute("""
        select num_ligacao::text, coalesce(nom_logradouro,''),
               coalesce(nro,''), cod_latitude::float8, cod_longitude::float8,
               coalesce(cod_cep,'')
          from resources_root.cadastro_corsan
         where upper(coalesce(cidade,'')) = upper(%s)""", (a.cidade,))
    porta = defaultdict(list)
    n_lig = 0
    for (lig, logr, nro, la, lo, cep) in cur:
        v, n = _via(logr), rv.numero_limpo(nro)
        if not v or not n:
            continue
        n_lig += 1
        porta[(v, n)].append((lig, la, lo, _cep(cep)))
    _log("   %d ligações com rua e número · %d endereços distintos"
         % (n_lig, len(porta)))

    _log("lendo os POIs órfãos...")
    cur.execute("""
        select p.id, coalesce(lr.logradouro,''), coalesce(lr.numero,''),
               st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
               coalesce(p.fonte,''), coalesce(p.nome,''), coalesce(lr.cep,'')
          from radar_comercial.pois p
          join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id
         where p.fundido_em is null
           and upper(coalesce(p.cidade,'')) = upper(%s)
           and lr.forca = 'prova'
           and not exists (select 1 from radar_comercial.ligacao_poi lp
                            where lp.poi_id = p.id
                              and lp.descartado_em is null)""", (a.cidade,))
    orfaos = cur.fetchall()
    _log("   %d órfãos com endereço publicado" % len(orfaos))

    placar, distancias, achados = Counter(), [], []
    quantas_ligacoes = Counter()
    for (pid, logr, nro, pla, plo, fonte, nome, cep_p) in orfaos:
        v, n = _via(logr), rv.numero_limpo(nro)
        if not v or not n:
            placar["sem rua ou numero utilizavel"] += 1
            continue
        alvos = porta.get((v, n))
        if not alvos:
            placar["nenhuma ligacao neste endereco"] += 1
            continue
        # O CEP EXCLUI, e nao confirma. Medido em 08/09/2026: com rua, numero
        # E CEP batendo, a mediana de distancia continua em 316 m — porque a
        # populacao aqui e, por construcao, a dos POIs cuja coordenada falhou.
        # Distancia grande e a assinatura esperada desses, e nao sinal contra o
        # casamento. O que o CEP faz e derrubar o homonimo: dos pares com CEP
        # dos dois lados, 3.339 divergiam — rua de mesmo nome em outro bairro.
        cp = _cep(cep_p)
        bons = [x for x in alvos if not (cp and x[3]) or cp == x[3]]
        if not bons:
            placar["so casou com CEP divergente"] += 1
            continue
        quantas_ligacoes[min(len(bons), 9)] += 1
        placar["CASOU"] += 1
        for (lig, la, lo, cl) in bons:
            d = _metros(la, lo, pla, plo)
            if d is not None:
                distancias.append(d)
            achados.append((lig, pid, d, fonte, nome, len(bons)))

    print()
    for k in sorted(placar):
        print("   %-40s %8d" % (k, placar[k]))
    print("\n   quantas ligações cada POI casou:")
    for k in sorted(quantas_ligacoes):
        print("      %s ligação(ões) %s %6d POIs"
              % (k if k < 9 else "9+", "." * (10 - len(str(k))),
                 quantas_ligacoes[k]))

    if distancias:
        distancias.sort()
        def q(f):
            return distancias[min(int(len(distancias) * f), len(distancias) - 1)]
        print("\n   distância do POI à ligação que casou pelo endereço:")
        for rot, f in (("mediana", .5), ("75%", .75), ("90%", .9),
                       ("99%", .99)):
            print("      %-8s %8.0f m" % (rot, q(f)))
        print("      acima de 1 km: %d de %d vínculos"
              % (sum(1 for d in distancias if d > 1000), len(distancias)))
    _log("%d vínculos novos a gravar" % len(achados))

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.close()
        return 0

    from psycopg2.extras import execute_values
    cur.execute("""select count(*) from radar_comercial.ligacao_poi
                    where origem = 'endereco_publicado'""")
    antes = int(cur.fetchone()[0] or 0)
    # A CONFIANÇA É A DA PORTA IDENTIFICADA — rua e número publicados batem —,
    # e não a de proximidade: aqui a distância não foi usada e não deve entrar
    # na régua. `ate_20m` fica pelo que a medida diz, para não mentir na coluna.
    linhas = [(a.base, lig, pid, True, True,
               bool(d is not None and d <= 20.0), False, False, d, 2, 0.70,
               1, 0, fonte, "endereco_publicado")
              for (lig, pid, d, fonte, nome, quantos) in achados]
    execute_values(cur, """
        insert into radar_comercial.ligacao_poi
            (id_base, ligacao, poi_id, mesmo_endereco, mesmo_numero, ate_20m,
             mesmo_telhado, telhado_comercial, metros, criterios_ok, confianca,
             fontes_aderentes, fontes_no_momento, fonte_poi, origem)
        values %s
        on conflict (id_base, ligacao, poi_id) do nothing
    """, linhas, page_size=1000)
    con.commit()
    cur.execute("""select count(*) from radar_comercial.ligacao_poi
                    where origem = 'endereco_publicado'""")
    _log("o banco gravou %d vínculos novos"
         % (int(cur.fetchone()[0] or 0) - antes))

    cur.execute("""
        update radar_comercial.pois p
           set id_ligacao_base = v.ligacao, id_base = v.id_base
          from (select distinct on (poi_id) poi_id, id_base, ligacao
                  from radar_comercial.ligacao_poi
                 where descartado_em is null
                 order by poi_id, confianca desc, metros nulls last) v
         where p.id = v.poi_id
           and p.id_ligacao_base is distinct from v.ligacao""")
    _log("%d POIs receberam ligação principal" % cur.rowcount)
    con.commit()

    # ── A COORDENADA TAMBÉM ESTAVA ERRADA, e agora dá para consertá-la ────
    #
    # Este POI publicou rua, número e CEP da ligação, e mesmo assim ficou a
    # 316 m dela (a mediana medida). Isso não é o endereço que está errado —
    # é a coordenada: o geocodificador da fonte jogou o ponto no eixo da via
    # ou no centroide do CEP. A ligação sabe onde fica a porta, porque o
    # hidrômetro está nela.
    #
    # SÓ QUANDO NÃO HÁ DÚVIDA: um POI que casou com VÁRIAS ligações do mesmo
    # endereço não diz qual é a dele — é prédio com vários medidores —, e
    # mover o ponto para uma delas seria escolher por sorteio.
    #
    # `pt_geo` é coluna gerada a partir de `coalesce(maps_lat, lat_origem)`:
    # escrever `maps_lat/maps_lng` move o ponto no mapa e em toda consulta
    # espacial. A coordenada antiga vai para `coord_anterior_*` e não se
    # perde — é a convenção que `corrigir_coordenada.py` já usa.
    if not a.mover:
        _log("as coordenadas dos POIs ficam como estão "
             "(use --mover para levá-las à porta da ligação)")
        con.close()
        return 0

    sozinhos = {}
    for (lig, pid, d, fonte, nome, quantos) in achados:
        if quantos == 1:
            sozinhos[pid] = lig
    alvos = [(pid, lig) for pid, lig in sozinhos.items()]
    _log("%d POIs casaram com UMA ligação só — coordenada corrigível"
         % len(alvos))
    if alvos:
        cur.execute("""select count(*) from radar_comercial.pois
                        where coord_fonte = 'ligacao_endereco'""")
        antes_c = int(cur.fetchone()[0] or 0)
        execute_values(cur, """
            update radar_comercial.pois p
               set coord_anterior_lat = coalesce(p.coord_anterior_lat,
                                                 coalesce(p.maps_lat, p.lat_origem)),
                   coord_anterior_lng = coalesce(p.coord_anterior_lng,
                                                 coalesce(p.maps_lng, p.lng_origem)),
                   maps_lat = c.cod_latitude::float8,
                   maps_lng = c.cod_longitude::float8,
                   coord_fonte = 'ligacao_endereco',
                   coord_precisao = 'porta',
                   coord_incerteza_m = 20,
                   coord_motivo = 'rua, numero e CEP publicados batem com a '
                                  'ligacao ' || v.ligacao
              from (values %s) as v(poi_id, ligacao)
              join resources_root.cadastro_corsan c
                on c.num_ligacao::text = v.ligacao
             where p.id = v.poi_id::bigint
               and c.cod_latitude is not null
        """, alvos, template="(%s::bigint, %s::text)", page_size=1000)
        con.commit()
        cur.execute("""select count(*) from radar_comercial.pois
                        where coord_fonte = 'ligacao_endereco'""")
        _log("%d POIs tiveram a coordenada movida para a porta"
             % (int(cur.fetchone()[0] or 0) - antes_c))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
