# -*- coding: utf-8 -*-
"""A duplicata que a fusao entre fontes nao ve: a de DENTRO da mesma fonte.

POR QUE ELA ESCAPA. `cruzar_fontes` existe para reconhecer o mesmo lugar visto
por fontes DIFERENTES — Receita e Maps, IBGE e iFood. Duas linhas da MESMA
fonte no MESMO endereco nunca sao candidatas la, porque a fonte, em tese, ja
publicou cada estabelecimento uma vez.

Na pratica nao publicou. Medido em 10/09/2026, nos vinculos vivos:

    estadual .... 1.832 pares    receita .... 102    ibge .... 76
    ifood ....... 6 pares        maps ....... 5

"M Silveira Marmores e Granitos" e' o POI 174279 E o 175346, com o nome
identico, ligados a mesma ligacao. "Posto Apolo (Ipiranga)" e "Posto Apolo".
"GTI Despachante" duas vezes.

O QUE NAO E DUPLICATA, e por isso o teste e de NOME e nao de contagem: a
ligacao 1988610 tem Forum da Comarca, Juizado Especial Civel e Defensoria
Publica — tres entidades reais no mesmo predio, todas do Maps. A 1994398 e uma
galeria: Atelier da Face e Mix Ferragens. Uma regra de "1 POI por fonte" (a
primeira ideia) colapsaria as tres do forum numa so e cortaria 35.789 dos
94.646 vinculos.

O TESTE E O MESMO `regra_vinculo.parecidos`, com o peso de raridade: soma o
idf dos tokens em comum e exige 9,0. E' o que separa "Posto Apolo (Ipiranga)"
de "Posto Apolo" — que casam — de "Juizado Especial Civel" e "Defensoria
Publica", que nao.
"""
import argparse
import io
import re
import time
from collections import defaultdict

import base_comum as bc
import regra_vinculo as rv

SQL = """
select lp.ligacao, lower(coalesce(p.fonte,'')), p.id, coalesce(p.nome,''),
       coalesce(p.detalhado_em is not null, false)
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
 where lp.descartado_em is null
   and p.fundido_em is null
   and coalesce(p.nome,'') <> ''
   and translate(lower(coalesce(p.cidade,'')),
                 'áàâãäéèêëíìîïóòôõöúùûüçñ',
                 'aaaaaeeeeiiiiooooouuuucn')
     = translate(lower(%s),
                 'áàâãäéèêëíìîïóòôõöúùûüçñ',
                 'aaaaaeeeeiiiiooooouuuucn')
"""


#: CPF ou CNPJ dentro do nome. Oito digitos ou mais, com ou sem pontuacao.
_DOCUMENTO = re.compile(r"(\d[\d.\-/]{7,})")


def tem_documento(nome):
    """A fonte publicou o documento junto do nome?

    QUANDO ELA PUBLICA, O DOCUMENTO E A IDENTIDADE — e nao o nome. A Receita
    registra o MEI e o empresario individual pelo nome da PESSOA, com o CNPJ na
    frente ou o CPF atras:

        32.579.160 PRISCILA DA SILVA MOUSQUER
        CELITA ECKARDT 91714982068

    Dois desses sao dois REGISTROS diferentes, mesmo quando as pessoas dividem
    o sobrenome — e dividem, porque costumam ser da mesma familia no mesmo
    endereco. Pior: a Receita geocodifica todo mundo no centroide do endereco,
    entao os dois POIs ficam a 0,0 m um do outro e a distancia tambem nao ajuda.

    Medido em 10/09/2026, auditando 40 fusoes sorteadas: 11 estavam erradas
    (27,5%) e 10 delas eram exatamente isto. Nenhuma fusao CERTA da amostra
    trazia documento no nome.

    A identidade aqui e' o documento, e ele ja diz que sao dois. Comparar o
    nome nao acrescenta nada — so pode errar.
    """
    return bool(_DOCUMENTO.search(str(nome or "")))


def _metros(a, b):
    """Distancia entre dois POIs, em metros. Vai na lista de auditoria porque
    e' o unico sinal INDEPENDENTE do nome — e, medido, ele tambem nao separa
    sozinho: um par falso ficou a 15,7 m e um verdadeiro a 16,2 m."""
    import math
    if not a or not b or a[0] is None or b[0] is None:
        return -1.0
    la1, lo1, la2, lo2 = math.radians(a[0]), math.radians(a[1]),         math.radians(b[0]), math.radians(b[1])
    return 6371000.0 * 2 * math.asin(math.sqrt(
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


class Uniao(object):
    """Union-find. Duplicata e' transitiva: se A=B e B=C, os tres sao um."""

    def __init__(self):
        self.pai = {}

    def acha(self, x):
        self.pai.setdefault(x, x)
        while self.pai[x] != x:
            self.pai[x] = self.pai[self.pai[x]]
            x = self.pai[x]
        return x

    def une(self, a, b):
        ra, rb = self.acha(a), self.acha(b)
        if ra != rb:
            self.pai[rb] = ra

    def grupos(self):
        g = defaultdict(list)
        for x in self.pai:
            g[self.acha(x)].append(x)
        return [v for v in g.values() if len(v) > 1]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cidade", default="")
    p.add_argument("--aplicar", action="store_true")
    # A LISTA INTEIRA, para auditoria. A fusao e' destrutiva (reversivel, mas
    # destrutiva) e o teste de nome nao tem taxa de erro conhecida: nenhum
    # sinal isolado — peso, topo, distancia — separou os casos duvidosos nos
    # testes de 10/09/2026. Quem decide aplicar precisa poder ler o que vai
    # acontecer.
    p.add_argument("--listar", default="",
                   help="grava a lista de fusoes neste CSV e nao aplica nada")
    a = p.parse_args(argv)

    # A CIDADE E OBRIGATORIA, e nao e burocracia.
    #
    # `PESO_MINIMO = 9,0` foi calibrado sobre os 117.393 nomes de POI de
    # Canoas: o pior par certo deu 10,06 e o melhor par errado, 6,28. O peso e
    # `log(n / (1 + c))`, entao ele depende de `n` — o tamanho do corpus.
    # Pesar a base inteira aumenta `n`, infla TODO peso, e o corte de 9,0 deixa
    # de cair no vao entre os dois grupos.
    #
    # Medido em 10/09/2026: "SANDRO ROGERIO DOS SANTOS BICCA" x "CARLA MARIA
    # CAMPOS BICCA" — duas pessoas diferentes que dividem o sobrenome — soma
    # 8,91 com os pesos de Canoas e NAO funde; com os pesos da base inteira
    # passou de 9,0 e fundiria.
    if not a.cidade:
        raise SystemExit(
            "--cidade e obrigatorio: o corte de 9,0 foi calibrado por cidade "
            "e nao significa a mesma coisa sobre a base inteira")

    con = bc.conectar()
    cur = con.cursor()
    _log("pesando os nomes de POI de %s..." % a.cidade)
    _log("   %d tokens" % rv.carregar_pesos(con, a.cidade))

    _log("lendo os vínculos vivos...")
    cur.execute(SQL, (a.cidade,))
    grupos = defaultdict(list)
    nome_de, rico_de = {}, {}
    for (lig, fonte, poi, nome, rico) in cur:
        grupos[(lig, fonte)].append(poi)
        nome_de[poi] = nome
        rico_de[poi] = rico
    _log("   %d combinações de ligação × fonte" % len(grupos))

    # OS PARES SAO ACHADOS POR LIGACAO, MAS A UNIAO E GLOBAL. Se A e B sao a
    # mesma loja numa ligacao, sao a mesma loja em todas — a "M Silveira"
    # aparece em seis ligacoes e nao pode virar seis fusoes diferentes.
    u = Uniao()
    vistos = set()
    aresta_direta = set()
    com_documento = 0
    for (lig, fonte), pois in grupos.items():
        if len(pois) < 2:
            continue
        pois = sorted(set(pois))
        for i in range(len(pois)):
            for j in range(i + 1, len(pois)):
                par = (pois[i], pois[j])
                if par in vistos:
                    continue
                vistos.add(par)
                if tem_documento(nome_de[pois[i]]) or                         tem_documento(nome_de[pois[j]]):
                    com_documento += 1
                    continue
                if rv.parecidos(nome_de[pois[i]], nome_de[pois[j]]):
                    aresta_direta.add(par)
                    u.une(*par)

    _log("%d par(es) recusados por trazerem documento no nome" % com_documento)
    gs = u.grupos()
    _log("%d grupo(s) de duplicata, %d POIs envolvidos"
         % (len(gs), sum(len(g) for g in gs)))

    # QUEM SOBREVIVE: o mais detalhado; empate, o de nome mais longo (carrega
    # mais informacao — "Posto Apolo (Ipiranga)" diz mais que "Posto Apolo");
    # empate, o de id menor, so para ser deterministico.
    #
    # SO QUEM CASA DIRETO COM O SOBREVIVENTE. A componente conexa junta por
    # TRANSITIVIDADE, e semelhanca nao e transitiva: A parecer com B e B
    # parecer com C nao faz A parecer com C.
    #
    # O ensaio de 10/09/2026 mostrou os dois casos, e eles sao graves:
    #
    #   '51.203.459 CARLA MARIA CAMPOS' -> '65.981.819 SANDRO ROGERIO DOS
    #   SANTOS'  (dois CNPJs de pessoas diferentes)
    #   'Dra Raquel Machry' -> 'Consultorio Odontologico Dr. Marcelo'
    #
    # Os dois entraram na mesma componente por um terceiro POI que se parecia
    # com ambos. Exigir a aresta DIRETA com o sobrevivente derruba isso; o que
    # sobra na componente sem casar com ele simplesmente nao funde.
    fusoes, sem_aresta = [], 0
    for g in gs:
        vive = sorted(g, key=lambda x: (not rico_de.get(x),
                                        -len(nome_de.get(x) or ""), x))[0]
        for morre in g:
            if morre == vive:
                continue
            if (min(morre, vive), max(morre, vive)) in aresta_direta:
                fusoes.append((morre, vive))
            else:
                sem_aresta += 1
    _log("%d POI(s) a fundir · %d recusados por não casar direto com o "
         "sobrevivente" % (len(fusoes), sem_aresta))
    for morre, vive in fusoes[:8]:
        _log("   %d '%s'  ->  %d '%s'"
             % (morre, nome_de[morre][:34], vive, nome_de[vive][:34]))

    if a.listar:
        import csv
        with io.open(a.listar, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["poi_morre", "nome_morre", "poi_vive", "nome_vive",
                        "peso", "metros"])
            ids = sorted({x for par in fusoes for x in par})
            cur.execute("""select id, st_y(pt_geo::geometry),
                                  st_x(pt_geo::geometry)
                             from radar_comercial.pois where id = any(%s)""",
                        (ids,))
            onde = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            for morre, vive in fusoes:
                w.writerow([morre, nome_de[morre], vive, nome_de[vive],
                            "%.2f" % rv.peso_do_par(nome_de[morre],
                                                    nome_de[vive]),
                            "%.1f" % _metros(onde.get(morre), onde.get(vive))])
        _log("lista em %s" % a.listar)
        con.close()
        return 0

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.close()
        return 0

    from psycopg2.extras import execute_values

    # PRIMEIRO MUDA O VINCULO DE DONO, e so depois marca a fusao. Na ordem
    # inversa, `revisar_vinculo` descartaria o vinculo do absorvido antes de
    # ele ser mudado, e a ligacao que so ele conhecia ficaria sem POI.
    execute_values(cur, """
        update radar_comercial.ligacao_poi lp
           set poi_id = f.vive
          from (values %s) as f(morre, vive)
         where lp.poi_id = f.morre::bigint
           and not exists (select 1 from radar_comercial.ligacao_poi o
                            where o.id_base = lp.id_base
                              and o.ligacao = lp.ligacao
                              and o.poi_id = f.vive::bigint)
    """, fusoes, page_size=500)
    movidos = cur.rowcount
    con.commit()
    _log("vínculos que mudaram de dono: ver contagem abaixo (rowcount: %s)"
         % movidos)

    execute_values(cur, """
        update radar_comercial.pois p
           set fundido_em = now(), fundido_para = f.vive::bigint,
               fundido_por = 'mesma_fonte'
          from (values %s) as f(morre, vive)
         where p.id = f.morre::bigint
           and p.fundido_em is null
    """, fusoes, page_size=500)
    con.commit()
    cur.execute("""select count(*) from radar_comercial.pois
                    where fundido_por = 'mesma_fonte'""")
    _log("%d POIs marcados como fundidos na mesma fonte"
         % int(cur.fetchone()[0] or 0))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
