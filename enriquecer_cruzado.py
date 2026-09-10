# -*- coding: utf-8 -*-
"""O que uma foto ensinou, servindo aos outros pontos da mesma via.

A IDEIA, ditada pelo dono do produto em 10/09/2026: "numerações lidas na
imagem, na via que já sabemos qual é, podem ajudar outros pontos a ser
identificados; crie esse fluxo de ajuda entre eles e enriquecimento cruzado".

POR QUE UM NÚMERO LIDO VALE TANTO. A foto de rua é tirada de um ponto com
coordenada conhecida e olha uma via que já sabemos qual é — a do cadastro, não
um palpite do modelo. Quando a IA lê "952" num muro daquela cena, o que se
aprendeu não é sobre o POI fotografado: é que o número 952 DAQUELA VIA fica a
poucos metros da câmera. E isso é exatamente o que falta a 10.048 POIs da fila
de alocação, cujo endereço publicado bate com uma ligação e cuja coordenada
está a 228 m de mediana dela.

QUATRO COISAS SAEM DAQUI, e são independentes:

  1. CONFIRMAÇÃO — a IA leu, na casa julgada, o mesmo número da ligação. É a
     única prova de endereço que não vem de cadastro nenhum: alguém escreveu
     aquele número na parede e a câmera fotografou.

  2. CONTRADIÇÃO — leu um número DIFERENTE. Aqui o veredito daquela ligação se
     apoiou na fachada errada, e isso é grave: é o defeito que este projeto
     passou dois dias corrigindo, aparecendo agora com prova visual.

  3. ENDEREÇO LOCALIZADO — o número lido pertence a OUTRA ligação da mesma
     via. Ela ganha uma posição observada, e os POIs que a fila de alocação
     não conseguiu casar por distância passam a ter para onde ir.

  4. FACHADA SEM DONO — letreiro que a IA leu e que não corresponde a POI
     nenhum daquela ligação. É comércio que existe na quadra e que nenhuma
     base registrou; é a única fonte deste projeto que descobre
     estabelecimento OLHANDO, em vez de cruzar cadastro.

ESTE MÓDULO NÃO MOVE COORDENADA E NÃO CRIA VÍNCULO. Ele mede e relata; o que
fazer com cada achado é decisão de quem responde pelo produto, e as três
primeiras listas saem prontas para virar ação num passo separado.
"""
import argparse
import math
import time

import base_comum as bc
import regra_vinculo as rv
from cruzar_ligacao import _via


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def _metros(la1, lo1, la2, lo2):
    if None in (la1, lo1, la2, lo2):
        return None
    dy = (la2 - la1) * 111320.0
    dx = (lo2 - lo1) * 111320.0 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot(dx, dy)


SQL_LEITURA = """
select lv.ligacao, lv.numero_na_fachada, c.nom_logradouro, c.nro, c.cidade,
       c.cod_latitude::float8, c.cod_longitude::float8,
       lv.medidores_agua, lv.medidores_energia, lv.tampa_esgoto
  from radar_comercial.leitura_visual lv
  join resources_root.cadastro_corsan c on c.num_ligacao::text = lv.ligacao
 where lv.numero_na_fachada is not null
"""

SQL_NUMEROS = """
select nl.logradouro, nl.numero, nl.cidade, nl.cam_lat, nl.cam_lng,
       nl.ligacao, nl.certeza
  from radar_comercial.numero_lido nl
 where nl.cam_lat is not null
"""

SQL_FACHADAS = """
select fv.id, fv.ligacao, fv.texto, fv.ramo, fv.numero, fv.e_o_alvo,
       fv.cam_lat, fv.cam_lng, c.nom_logradouro, c.cidade
  from radar_comercial.fachada_vista fv
  left join resources_root.cadastro_corsan c
         on c.num_ligacao::text = fv.ligacao
 where not fv.e_o_alvo
"""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default="")
    p.add_argument("--aplicar", action="store_true",
                   help="grava as confirmacoes e contradicoes em ligacao_poi")
    a = p.parse_args(argv)

    con = bc.conectar()
    cur = con.cursor()

    # ── 1 e 2 · o número do próprio alvo ─────────────────────────────────
    cur.execute(SQL_LEITURA)
    confirma, contradiz = [], []
    for (lig, lido, logr, nro, cid, la, lo, ma, me, te) in cur:
        a_lido, a_cad = rv.numero_limpo(lido), rv.numero_limpo(nro)
        if not a_lido or not a_cad:
            continue
        (confirma if a_lido == a_cad else contradiz).append(
            (lig, a_lido, a_cad, logr))
    _log("o número do alvo foi lido em %d ligação(ões)"
         % (len(confirma) + len(contradiz)))
    _log("   confirma o cadastro ... %d" % len(confirma))
    _log("   CONTRADIZ ............. %d" % len(contradiz))
    for (lig, lido, cad, logr) in contradiz[:10]:
        _log("      ligação %s (%s): cadastro diz %s, a foto mostra %s"
             % (lig, (logr or "")[:24], cad, lido))

    # ── 3 · o número lido que pertence a OUTRA ligação ───────────────────
    #
    # A CHAVE É (via normalizada, número limpo), a mesma de `casar_por_endereco`
    # — e é de propósito: se as duas usassem chaves diferentes, um endereço
    # casaria num módulo e não no outro, e ninguém saberia por quê.
    cur.execute(SQL_NUMEROS)
    lidos = cur.fetchall()
    _log("")
    _log("%d número(s) lidos na via, com câmera" % len(lidos))

    # O ÍNDICE DE ENDEREÇOS É MONTADO SEMPRE — as seções 3 e 5 dependem dele.
    if True:
        filtro = ""
        par = []
        if a.cidade:
            filtro = (" where translate(lower(coalesce(cidade,'')),"
                      "'áàâãäéèêëíìîïóòôõöúùûüçñ','aaaaaeeeeiiiiooooouuuucn')"
                      " = translate(lower(%s),"
                      "'áàâãäéèêëíìîïóòôõöúùûüçñ','aaaaaeeeeiiiiooooouuuucn')")
            par = [a.cidade]
        cur.execute("""
            select num_ligacao::text, coalesce(nom_logradouro,''),
                   coalesce(nro,''), cod_latitude::float8,
                   cod_longitude::float8
              from resources_root.cadastro_corsan %s""" % filtro, tuple(par))
        porta = {}
        for (num, logr, nro, la, lo) in cur:
            v, n = _via(logr), rv.numero_limpo(nro)
            if v and n:
                porta.setdefault((v, n), []).append((num, la, lo))

        achou, longe, perto = 0, 0, 0
        exemplos = []
        for (logr, numero, cid, cla, clo, de_lig, cert) in lidos:
            alvos = porta.get((_via(logr), rv.numero_limpo(numero) or ""), [])
            for (num, la, lo) in alvos:
                if num == de_lig:
                    continue          # e ela mesma; ja contado em 1 e 2
                achou += 1
                d = _metros(cla, clo, la, lo)
                if d is None:
                    continue
                if d > 60:
                    longe += 1
                    if len(exemplos) < 8:
                        exemplos.append((num, logr, numero, d, de_lig))
                else:
                    perto += 1
        _log("   %d apontam para OUTRA ligação da mesma via" % achou)
        _log("      a menos de 60 m do cadastro dela ... %d" % perto)
        _log("      a MAIS de 60 m — posição observada . %d" % longe)
        for (num, logr, numero, d, de_lig) in exemplos:
            _log("         ligação %s (%s, %s) está a %.0f m de onde a foto "
                 "de %s leu o número" % (num, (logr or "")[:22], numero, d,
                                         de_lig))

    # ── 4 · fachada que nenhum POI explica ───────────────────────────────
    cur.execute(SQL_FACHADAS)
    fachadas = cur.fetchall()
    _log("")
    _log("%d fachada(s) comerciais lidas que NÃO eram o alvo" % len(fachadas))

    # ── 5 · a fachada com NOME e NÚMERO procura a instalação dela ────────
    #
    # Regra do dono do produto em 10/09/2026: "se o número tiver sido
    # identificado e o nome, busca nas instalações um par pra eles".
    #
    # A VIA É A DA CENA, e não do letreiro. A foto olha uma rua conhecida — a
    # da ligação que estava sendo julgada —, então um letreiro lido ali com o
    # número 512 é um comércio no 512 DAQUELA via. É a mesma chave de
    # `casar_por_endereco`: via normalizada mais número limpo, de propósito,
    # para que um endereço não case num módulo e falhe no outro.
    #
    # O PAR É CANDIDATO, E O CÓDIGO DIZ ISSO NO NOME DA COLUNA. Ninguém
    # confirmou que aquele letreiro pertence àquela ligação: a cena tem várias
    # portas, e o número pode estar sobre a porta ao lado do toldo. O que se
    # sabe é que existe comércio com este nome nesta via e neste número, e que
    # o cadastro tem uma instalação ali.
    pares, sem_numero, sem_ligacao = [], 0, 0
    for (fid, lig, txt, ramo, num, _alvo, cla, clo, logr, cid) in fachadas:
        if not txt or not num or not logr:
            sem_numero += 1
            continue
        v, n = _via(logr), rv.numero_limpo(num)
        if not v or not n:
            sem_numero += 1
            continue
        # A MESMA CHAVE DA SEÇÃO 3 e de `casar_por_endereco`. Uma segunda
        # normalização aqui — uma consulta SQL com `translate`, por exemplo —
        # casaria endereços que o outro módulo recusa e recusaria os que ele
        # casa, e ninguém saberia por quê.
        alvos = porta.get((v, n), [])
        casou = None
        for (numlig, la, lo) in alvos:
            if numlig != lig:
                casou = numlig
                break
        if casou:
            pares.append((fid, casou, txt, ramo, num, logr))
        else:
            sem_ligacao += 1

    _log("   com nome E número, e instalação encontrada ... %d" % len(pares))
    _log("   com nome mas SEM número legível ............. %d" % sem_numero)
    _log("   com número que não achou instalação ......... %d" % sem_ligacao)
    for (fid, numlig, txt, ramo, num, logr) in pares[:10]:
        _log("      \"%s\"%s — %s, %s  ->  ligação %s"
             % ((txt or "?")[:30], (" (" + ramo[:18] + ")") if ramo else "",
                (logr or "")[:22], num, numlig))
    for (fid, lig, txt, ramo, num, _a, cla, clo, logr, cid) in fachadas[:8]:
        if not num:
            _log("      sem número: \"%s\"%s [visto julgando %s]"
                 % ((txt or "?")[:32],
                    (" · " + ramo[:20]) if ramo else "", lig))

    if not a.aplicar:
        _log("")
        _log("(ensaio: nada gravado. Use --aplicar para gravar a leitura do "
             "número e os pares de fachada)")
        con.close()
        return 0

    # A CONTRADIÇÃO VIRA MARCA NO VÍNCULO, e não descarte automático.
    #
    # Uma leitura de número contra o cadastro é forte, mas é UMA leitura, de
    # uma foto, por um modelo. Descartar o vínculo por causa dela seria dar à
    # imagem o poder de juiz que o próprio prompt lhe nega — e o número
    # pregado no muro erra também: casa reformada que manteve a placa antiga,
    # número do lote em vez do da porta, dois imóveis com a mesma plaquinha.
    #
    # Marcar deixa o caso visível e reversível: quem olha a fila decide.
    #
    # A COLUNA E DA `leitura_visual`, e nao de `ligacao_poi.suspeita_motivo`.
    # Aquela tem dono: `telhados.py` guarda ali a medida que sustentou a
    # suspeita, em 67 vinculos. Escrever por cima apagaria o registro de outro
    # modulo — ver a migracao 0096b.
    from psycopg2.extras import execute_values
    tudo = ([(l, n, c, True) for (l, n, c, _v) in confirma]
            + [(l, n, c, False) for (l, n, c, _v) in contradiz])
    if tudo:
        execute_values(cur, """
            update radar_comercial.leitura_visual lv
               set confere_com_cadastro = v.confere::boolean,
                   numero_no_cadastro = v.cad
              from (values %s) as v(ligacao, lido, cad, confere)
             where lv.ligacao = v.ligacao
               and lv.id_empresa = (select core.empresa_atual())
        """, tudo, page_size=500)
        con.commit()
        # O PLACAR DA TABELA NAO E O PLACAR DA CORRIDA. A consulta conta a
        # tabela inteira, e ela guarda o achado de execucoes anteriores: uma
        # contradicao lida ontem continua la se o numero nao foi relido hoje.
        # Imprimir os dois evita a leitura errada de "esta corrida achou 1
        # contradicao" quando ela achou zero.
        cur.execute("""select count(*) filter (where confere_com_cadastro),
                              count(*) filter (where confere_com_cadastro
                                                     is false)
                         from radar_comercial.leitura_visual""")
        ok, nao = cur.fetchone()
        _log("nesta corrida: %d confirmação(ões), %d contradição(ões)"
             % (len(confirma), len(contradiz)))
        _log("na tabela, somando as corridas: %d e %d" % (ok, nao))

    if pares:
        execute_values(cur, """
            update radar_comercial.fachada_vista fv
               set ligacao_par = v.lig, casado_em = now()
              from (values %s) as v(id, lig)
             where fv.id = v.id::bigint
        """, [(fid, numlig) for (fid, numlig, _t, _r, _n, _l) in pares],
            page_size=500)
        con.commit()
        _log("%d fachada(s) receberam instalação candidata" % len(pares))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
