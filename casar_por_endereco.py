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

A REGRA DE 11/09/2026, e o que ela mudou aqui. "O limite de distância passa a
não decidir": rua, número, BAIRRO e cidade batendo, com a ligação marcada SIM
ou SIM_COM_ANALISE_HUMANA, é vínculo válido — e da Receita só o ativo conta.
Três consequências neste módulo:

- ele olha TODOS os POIs, e não só os órfãos. Um POI que o cruzamento
  geométrico prendeu na ligação vizinha, a 40 m, também tem direito à ligação
  do endereço que publicou. Quem decide, quando são várias, é
  a IA, ligação por ligação: a ligação é o foco, e o mesmo POI pode ser
  testemunha de mais de uma. `--so-orfaos` devolve o comportamento antigo.
- o teto sai. O que ele barrava eram justamente os POIs de coordenada errada,
  que é o caso para o qual este módulo existe.
- o CEP deixa de vetar. Ele servia para derrubar a rua homônima de outro
  bairro, e agora quem faz isso é o próprio bairro. O CEP divergente continua
  contado no placar.
"""
import argparse
import time
from collections import Counter, defaultdict

import base_comum as bc
import regra_vinculo as rv
import bairro as bz
from cruzar_ligacao import _via
# A GRAFIA NAO SEPARA A RUA (dono do produto, 12/09/2026): a Corsan escreve
# 'VINTE E DOIS DE OUTUBRO', o iFood e o Google '22 de Outubro'. A chave vem
# depois da normalizacao da skill e so junta variantes do mesmo nome.
import via_chave as vc


#: CIDADE COMPARADA SEM ACENTO, DOS DOIS LADOS.
#:
#: Mesmo defeito que `cruzar_ligacao` levou em 03/09/2026 e este modulo nao:
#: a Corsan grava a cidade SEM acento ("GRAVATAI") e a malha do IBGE devolve
#: COM ("Gravatai"), entao `upper(cidade) = upper(%s)` nunca casa cidade
#: acentuada — que e quase toda cidade do RS. So passou despercebido porque
#: "CANOAS" nao tem acento.
#:
#: Medido em 10/09/2026: `pois.cidade` tem 18.274 orfaos em "GRAVATAÍ" e 393
#: em "GRAVATAI". Sem isto, qualquer chamada acerta um dos dois e perde o
#: outro, em silencio — o log diria "0 orfaos" e pareceria fila vazia.
_SEM_ACENTO_DE = "'áàâãäéèêëíìîïóòôõöúùûüçñ'"
_SEM_ACENTO_PARA = "'aaaaaeeeeiiiiooooouuuucn'"


def _sa(expr):
    """`expr` em minuscula e sem acento, para comparar cidade."""
    return "translate(lower(%s), %s, %s)" % (expr, _SEM_ACENTO_DE,
                                             _SEM_ACENTO_PARA)


#: Os descartes que a regra pode desfazer. O que a IA descartou (`ia-...`),
#: o que foi fundido e o que alguem descartou a mao NAO voltam por aqui:
#: reviver um par que o modelo recusou lendo as fotos apagaria a unica
#: explicacao que existe para ele.
DESCARTE_DA_REGRA = ("regra_vinculo", "teto_distancia")


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
    p.add_argument("--so-orfaos", dest="so_orfaos", action="store_true",
                   help="so os POIs sem vinculo vivo (o comportamento de antes "
                        "de 11/09/2026)")
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
               coalesce(cod_cep,''), coalesce(nom_bairro,''),
               coalesce(qualificacao,'') like 'SIM%%'
          from resources_root.cadastro_corsan
         where """ + _sa("coalesce(cidade,'')") + " = " + _sa("%s"),
                (a.cidade,))
    porta = defaultdict(list)
    n_lig = 0
    for (lig, logr, nro, la, lo, cep, bai, apta) in cur:
        v, n = _via(logr), rv.numero_limpo(nro)
        if not v or not n:
            continue
        n_lig += 1
        porta[(vc.chave(v), n)].append((lig, la, lo, _cep(cep), bai, bool(apta)))
    _log("   %d ligações com rua e número · %d endereços distintos"
         % (n_lig, len(porta)))

    _log("lendo os POIs%s..." % (" órfãos" if a.so_orfaos else ""))
    orfao = ("""
           and not exists (select 1 from radar_comercial.ligacao_poi lp
                            where lp.poi_id = p.id
                              and lp.descartado_em is null)"""
             if a.so_orfaos else "")
    cur.execute("""
        select p.id, coalesce(lr.logradouro,''), coalesce(lr.numero,''),
               st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
               coalesce(p.fonte,''), coalesce(p.nome,''), coalesce(lr.cep,''),
               coalesce(lr.bairro,''),
               lower(coalesce(p.fonte,'')) = 'receita'
                 and ltrim(coalesce(rd.situacao_cadastral,''),'0') <> '2'
          from radar_comercial.pois p
          join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id
          left join radar_comercial.receita_data rd on rd.poi_id = p.id
         where p.fundido_em is null
           and """ + _sa("coalesce(p.cidade,'')") + " = " + _sa("%s") + """
           and lr.forca = 'prova'""" + orfao, (a.cidade,))
    pois = cur.fetchall()
    _log("   %d POIs com endereço publicado" % len(pois))

    # O BAIRRO DA LIGACAO, quando a Corsan nao o escreveu: 1.244 ligacoes de
    # Canoas vem com "BAIRRO NAO INFORMADO". Vale a mesma regra do POI — na
    # duvida, a coordenada. Uma consulta por ligacao, so das que casarem.
    bairro_lig = {}

    def _bairro_da_ligacao(lig, bai, la, lo):
        if rv.bairro_util(bai):
            return bai
        if lig not in bairro_lig:
            bairro_lig[lig] = bz.bairro_reverso(la, lo, a.cidade) or ""
        return bairro_lig[lig]

    placar, distancias, achados = Counter(), [], []
    recusa = {}
    pois_dentro = set()
    quantas_ligacoes = Counter()
    for (pid, logr, nro, pla, plo, fonte, nome, cep_p, bai_p, inativa) in pois:
        v, n = _via(logr), rv.numero_limpo(nro)
        if not v or not n:
            placar["sem rua ou numero utilizavel"] += 1
            continue
        alvos = porta.get((vc.chave(v), n))
        if not alvos:
            placar["nenhuma ligacao neste endereco"] += 1
            continue
        if rv.declara_vazio(nome):
            placar["o nome diz que a unidade esta vazia"] += 1
            continue
        if fonte.strip().lower() in rv.SEM_ENDERECO_EXATO:
            placar["airbnb: nao casa por endereco"] += 1
            continue
        cp = _cep(cep_p)
        bons = []
        for (lig, la, lo, cl, bai_l, apta) in alvos:
            if not apta:
                recusa[(lig, pid)] = ("a ligacao nao esta marcada SIM nem SIM "
                                      "com analise humana")
                placar["par com ligacao que nao e SIM"] += 1
                continue
            if inativa:
                recusa[(lig, pid)] = "o estabelecimento da Receita nao esta ativo"
                placar["par com Receita inativa"] += 1
                continue
            bl = _bairro_da_ligacao(lig, bai_l, la, lo)
            if not rv._bairro_bate({"bairro_lig": bl, "bairro_poi": bai_p}):
                recusa[(lig, pid)] = ("endereco exato, mas o bairro diverge: "
                                      "POI %s, ligacao %s" % (bai_p, bl))
                placar["par com bairro divergente"] += 1
                continue
            if cp and cl and cp != cl:
                placar["CEP diverge (nao veta mais)"] += 1
            bons.append((lig, la, lo))
        if not bons:
            continue
        quantas_ligacoes[min(len(bons), 9)] += 1
        placar["CASOU"] += 1
        for (lig, la, lo) in bons:
            d = _metros(la, lo, pla, plo)
            if d is not None:
                distancias.append(d)
            pois_dentro.add(pid)
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
        print("      acima de 200 m: %d · acima de 1 km: %d · de %d vínculos"
              % (sum(1 for d in distancias if d > 200),
                 sum(1 for d in distancias if d > 1000), len(distancias)))
    _log("%d vínculos pela regra de 11/09 · %d POIs · %d pares recusados"
         % (len(achados), len(pois_dentro), len(recusa)))
    _log("%d ligações sem bairro na Corsan consultadas na coordenada"
         % len(bairro_lig))

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
               1, 0, fonte, "endereco_publicado", "endereco_exato")
              for (lig, pid, d, fonte, nome, quantos) in achados]
    execute_values(cur, """
        insert into radar_comercial.ligacao_poi
            (id_base, ligacao, poi_id, mesmo_endereco, mesmo_numero, ate_20m,
             mesmo_telhado, telhado_comercial, metros, criterios_ok, confianca,
             fontes_aderentes, fontes_no_momento, fonte_poi, origem,
             aceito_por)
        values %s
        on conflict (id_base, ligacao, poi_id) do update set
            -- O ENDERECO PUBLICADO MANDA NO ENDERECO. Se o par ja existia e
            -- estava descartado, ele volta — e volta com as colunas de
            -- endereco corrigidas.
            --
            -- POR QUE ISSO E' NECESSARIO, medido em 10/09/2026: o `do nothing`
            -- gravou 269 dos 2.279 pares que este modulo tinha encontrado. Os
            -- outros 2.010 ja existiam, inseridos pelo cruzamento geometrico e
            -- descartados pela revisao — 365 deles por "numero diferente",
            -- quando os numeros publicados BATEM.
            --
            -- A causa e' que `mesmo_numero` foi gravado por uma versao antiga
            -- de `_num`, que concatenava todos os grupos de digitos: "350 sala
            -- 2" virava "3502" e nunca casava com a porta 350. A funcao foi
            -- corrigida, a COLUNA nao — e `revisar_vinculo` le a coluna.
            --
            -- Sem esta clausula o POI fica orfao para sempre: o casamento por
            -- endereco o encontra toda vez e toda vez o `do nothing` o joga
            -- fora, em silencio.
            mesmo_endereco = true,
            mesmo_numero = true,
            metros = coalesce(excluded.metros, radar_comercial.ligacao_poi.metros),
            ate_20m = excluded.ate_20m,
            origem = 'endereco_publicado',
            aceito_por = 'endereco_exato',
            descartado_em = null,
            descartado_motivo = null,
            descartado_por = null
          -- SO O DESCARTE DA REGRA VOLTA. Ver `DESCARTE_DA_REGRA`.
          where radar_comercial.ligacao_poi.descartado_em is null
             or radar_comercial.ligacao_poi.descartado_por in
                ('regra_vinculo', 'teto_distancia')
    """, linhas, page_size=1000)
    con.commit()
    cur.execute("""select count(*) from radar_comercial.ligacao_poi
                    where origem = 'endereco_publicado'""")
    _log("o banco gravou ou reviveu %d vínculos (pedidos: %d)"
         % (int(cur.fetchone()[0] or 0) - antes, len(linhas)))

    # ── A FILA DO TETO ACABOU ────────────────────────────────────────────
    #
    # Ate 11/09/2026 o par com rua e numero batendo e ponto longe entrava
    # descartado, com `descartado_por = 'teto_distancia'`, e alimentava a Fila
    # de alocacao do painel. Sem teto, esses pares ou entraram agora, ou caem
    # por outro motivo — e a linha passa a dizer QUAL, em vez de citar um teto
    # que nao existe mais. Nada e inserido aqui: so se reescreve a linha que ja
    # estava descartada.
    if recusa:
        execute_values(cur, """
            update radar_comercial.ligacao_poi lp
               set descartado_motivo = v.motivo,
                   descartado_por = 'regra_vinculo'
              from (values %s) as v(ligacao, poi_id, motivo)
             where lp.ligacao = v.ligacao and lp.poi_id = v.poi_id::bigint
               and lp.descartado_por = 'teto_distancia'
        """, [(l, p, m) for (l, p), m in recusa.items()], page_size=1000)
        con.commit()
    cur.execute("""
        update radar_comercial.ligacao_poi
           set descartado_motivo = 'o teto de distancia saiu em 11/09/2026, e o '
                                   'par nao passa na regra nova (rua, numero, '
                                   'bairro, ligacao SIM, Receita ativa)',
               descartado_por = 'regra_vinculo'
         where descartado_por = 'teto_distancia'
           and ligacao in (select num_ligacao::text
                             from resources_root.cadastro_corsan
                            where """ + _sa("coalesce(cidade,'')") + " = "
                + _sa("%s") + """)""", (a.cidade,))
    _log("%d pares da antiga fila do teto sem motivo específico, relabelados"
         % cur.rowcount)
    con.commit()

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
