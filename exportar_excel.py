# -*- coding: utf-8 -*-
"""A planilha para quem vai à porta: aprovadas com certeza e revisão humana.

DUAS ABAS POR VEREDITO, e não uma. `ligacoes` traz uma linha por hidrômetro —
é a lista de visita, e é ela que se ordena e se distribui. `registros` traz uma
linha por POI vinculado, com os links: o mesmo hidrômetro aparece várias vezes
ali de propósito, porque cada fonte é uma testemunha e quem confere precisa ver
todas.

SEM AIRBNB, por pedido do dono do produto em 08/09/2026.
"""
import argparse
import time

import base_comum as bc

FORA = ("airbnb",)

#: A ORDEM DA CERTEZA. `aprovado_exato` é o letreiro com o nome do cadastro;
#: `aprovado_comercial` é comércio sem saber qual. Depois a confiança, depois
#: quantos SISTEMAS independentes concordam — três bases diferentes valem mais
#: que três CNPJs da mesma base —, depois quantos registros e quantas imagens.
#: A ORDEM E O SCORE, desde 10/09/2026. Antes era o veredito e depois a
#: confianca — e o veredito agora e binario, entao ele ordena mal: dividiria a
#: planilha em dois blocos gigantes sem dizer, dentro de cada um, onde estao
#: os casos com mais prova.
#:
#: O score responde exatamente isso, e por soma de evidencia medida: quem tem
#: Street View conclusivo, avaliacao recente, telhado comercial e o ponto em
#: cima do hidrometro sobe. Quem tem so um CNPJ desce.
ORDEM_APROVADO = """
    order by s.total desc nulls last,
             (v.veredito in ('aprovado', 'aprovado_exato')) desc,
             v.fontes desc nulls last,
             v.pois desc nulls last,
             v.ligacao
"""
#: A REVISÃO SE ORDENA AO CONTRÁRIO DA CERTEZA: primeiro a que tem MAIS prova
#: acumulada, porque é a que mais rende o olhar humano — muita testemunha e
#: ainda assim sem decisão é onde a pessoa acrescenta mais.
#: O REPROVADO SE ORDENA PELO SCORE TAMBEM, e do maior para o menor — que
#: parece contraintuitivo e nao e. Reprovado COM score alto e a contradicao
#: que mais rende o olhar humano: havia prova acumulada e a IA disse nao.
ORDEM_REVISAO = """
    order by s.total desc nulls last, v.fontes desc nulls last,
             v.pois desc nulls last, v.ligacao
"""

SQL_LIGACOES = """
select v.ligacao, v.veredito, v.justificativa, v.confianca,
       v.pois, v.fontes, v.imagens,
       v.percepcao->'resposta'->>'estabelecimento',
       v.percepcao->'resposta'->>'sinal_no_imovel',
       v.modelo, v.avaliado_em,
       coalesce(c.nom_logradouro,''), coalesce(c.nro,''),
       coalesce(c.nom_bairro,''), coalesce(c.cod_cep,''),
       coalesce(c.nom_cliente,''), coalesce(c.categoria,''),
       coalesce(c.sit_ligacao,''),
       coalesce(c.qtd_eco_res,0), coalesce(c.qtd_eco_com,0),
       coalesce(c.qtd_eco_ind,0),
       c.cod_latitude::float8, c.cod_longitude::float8,
       coalesce(c.num_medidor,''),
       s.total, s.p_distancia, s.p_telhado, s.p_streetview,
       s.p_avaliacoes, s.p_fotos, s.p_rede,
       s.perto_10m, s.mesmo_telhado, s.telhado_comercial,
       s.sv_exata, s.sv_comercial, s.sv_recente, s.aval_recente,
       s.fotos_validadas, s.rede_recente,
       coalesce(c.qualificacao, ''), coalesce(c.qualificacao_motivo, '')
  from radar_comercial.ligacao_veredito v
  join resources_root.cadastro_corsan c on c.num_ligacao::text = v.ligacao
  left join radar_comercial.ligacao_score s on s.ligacao = v.ligacao
 where v.veredito %(cond)s
   and exists (select 1 from radar_comercial.ligacao_poi lp
                join radar_comercial.pois p on p.id = lp.poi_id
               where lp.ligacao = v.ligacao and lp.descartado_em is null
                 and lower(coalesce(p.fonte,'')) <> all(%(fora)s))
 %(ordem)s
 limit %(lim)s
"""

SQL_REGISTROS = """
select lp.ligacao, p.id, coalesce(p.fonte,''), coalesce(p.nome,''),
       coalesce(p.categoria,''), coalesce(p.endereco,''),
       coalesce(lr.logradouro,''), coalesce(lr.numero,''),
       coalesce(p.telefone,''), coalesce(p.website,''),
       coalesce(p.instagram,''), coalesce(p.facebook,''),
       coalesce(p.cnpj,''), coalesce(p.situacao_cadastral,''),
       coalesce(p.place_id,''), coalesce(m.maps_url,''),
       m.avaliacao, m.total_avaliacoes, coalesce(m.status_horario,''),
       coalesce(m.resumo_avaliacoes,''),
       p.presente_no_ifood, coalesce(p.preco_medio,''),
       lp.metros, lp.confianca, coalesce(lp.origem,''),
       vp.veredito, vp.justificativa,
       st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry)
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  left join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id
  left join radar_comercial.maps_data m on m.poi_id = p.id
  left join radar_comercial.poi_veredito vp on vp.poi_id = p.id
 where lp.ligacao = any(%s)
   and lp.descartado_em is null
   and p.fundido_em is null
   and lower(coalesce(p.fonte,'')) <> all(%s)
 order by lp.ligacao, lp.confianca desc nulls last, p.id
"""


def _link_maps(place_id, maps_url, lat, lng, nome):
    """O link que ABRE a ficha, e não só o ponto.

    `place_id` é o identificador estável do Google e é o primeiro a ser usado:
    `?q=place_id:` abre a ficha do estabelecimento. Sem ele, cai na coordenada
    com o nome como busca — que abre o lugar certo na maioria das vezes e o
    bairro certo sempre.
    """
    if place_id:
        return "https://www.google.com/maps/search/?api=1&query=%s&query_place_id=%s" % (
            (nome or "").replace(" ", "+")[:60] or "poi", place_id)
    if maps_url:
        return maps_url
    if lat and lng:
        return "https://www.google.com/maps/search/?api=1&query=%.6f,%.6f" % (lat, lng)
    return ""


def _link_rede(v):
    """Instagram e Facebook chegam ora como @nome, ora como URL inteira."""
    v = (v or "").strip()
    if not v:
        return ""
    if v.startswith("http"):
        return v
    return v


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--saida", default="/app/logs/radar_canoas.xlsx")
    p.add_argument("--quantos", type=int, default=2000)
    a = p.parse_args(argv)

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    con = bc.conectar()
    cur = con.cursor()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # O VEREDITO E BINARIO DESDE 10/09/2026: nao existe mais `revisao_humana`
    # neste julgamento. A segunda aba passa a ser a dos REPROVADOS — e ela
    # continua valendo, ordenada por score: reprovado com muita prova e onde a
    # pessoa acrescenta mais.
    for rot, cond, ordem in (
            ("aprovadas", "like 'aprovado%%'", ORDEM_APROVADO),
            ("reprovadas", "= 'reprovado'", ORDEM_REVISAO)):
        cur.execute(SQL_LIGACOES % {"cond": cond, "ordem": ordem,
                                    "fora": "%s", "lim": "%s"},
                    (list(FORA), a.quantos))
        ligs = cur.fetchall()
        print("%-16s %d ligação(ões)" % (rot, len(ligs)))
        if not ligs:
            continue
        nums = [r[0] for r in ligs]
        cur.execute(SQL_REGISTROS, (nums, list(FORA)))
        regs = cur.fetchall()
        print("%-16s %d registro(s)" % ("", len(regs)))

        _aba_ligacoes(wb, rot, ligs, openpyxl, Font, PatternFill, Alignment,
                      get_column_letter)
        _aba_registros(wb, rot, regs, openpyxl, Font, PatternFill, Alignment,
                       get_column_letter)

    con.close()
    wb.save(a.saida)
    import os
    print("\nescrito em %s (%.2f MB)"
          % (a.saida, os.path.getsize(a.saida) / 1e6))


CAB_LIG = ["Ligação", "Veredito", "SCORE", "Distância", "Telhado",
           "Street View", "Avaliações", "Fotos", "Rede social",
           "< 10 m", "Mesmo telhado", "Telhado comercial",
           "SV mostra ESTE negócio", "SV mostra comércio", "Foto do último ano",
           "Avaliação do último ano", "Fotos validadas", "Post recente",
           "Qualificação", "Motivo da qualificação",
           "Confiança", "Estabelecimento (IA)",
           "Sinal no imóvel", "Registros", "Sistemas", "Imagens",
           "Justificativa da IA", "Logradouro", "Número", "Bairro", "CEP",
           "Cliente da água", "Categoria", "Situação", "Econ. res.",
           "Econ. com.", "Econ. ind.", "Medidor", "Mapa da ligação",
           "Decidido por", "Quando"]

CAB_REG = ["Ligação", "POI", "Fonte", "Nome", "Atividade",
           "Endereço que a fonte publica", "Logradouro (resolvido)",
           "Número (resolvido)", "Telefone", "Site", "Instagram", "Facebook",
           "CNPJ", "Situação do CNPJ", "Link do Maps", "Nota",
           "Nº avaliações", "Horário", "O que os clientes dizem",
           "No iFood", "Preço médio", "Metros até o hidrômetro",
           "Confiança do vínculo", "Como entrou", "Veredito do POI",
           "Justificativa do POI", "Mapa do POI"]


def _cabecalho(ws, cab, Font, PatternFill, Alignment, get_column_letter,
               larguras):
    ws.append(cab)
    f = PatternFill("solid", fgColor="1F3A5F")
    for i, _ in enumerate(cab, 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.fill = f
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = larguras.get(i, 16)
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 30


def _aba_ligacoes(wb, rot, ligs, openpyxl, Font, PatternFill, Alignment,
                  get_column_letter):
    ws = wb.create_sheet("%s · ligações" % rot[:18])
    _cabecalho(ws, CAB_LIG, Font, PatternFill, Alignment, get_column_letter,
               {1: 12, 2: 19, 3: 8, 19: 22, 20: 24, 22: 28, 27: 70,
                28: 26, 32: 26, 39: 22, 41: 17})
    for r in ligs:
        (lig, ver, just, conf, pois, fontes, imgs, estab, sinal, modelo,
         quando, logr, nro, bairro, cep, cliente, cat, sit, eres, ecom,
         eind, la, lo, medidor,
         total, pd, pt, psv, pav, pf, prd,
         f10, ftel, ftelc, fexa, fcom, frec, faval, ffot, frede,
         qualif, qmot) = r
        sim = lambda v: "sim" if v else ""
        ws.append([
            lig, ver, total, pd, pt, psv, pav, pf, prd,
            sim(f10), sim(ftel), sim(ftelc), sim(fexa), sim(fcom), sim(frec),
            sim(faval), sim(ffot), sim(frede), qualif, qmot,
            float(conf) if conf is not None else None, estab or "",
            sinal or "", pois, fontes, imgs, just or "", logr, nro, bairro,
            cep, cliente, cat, sit, eres, ecom, eind, medidor,
            ("https://www.google.com/maps/search/?api=1&query=%.6f,%.6f"
             % (la, lo)) if la and lo else "",
            "IA" if modelo != "regra" else "regra",
            quando.strftime("%d/%m/%Y %H:%M") if quando else ""])
        i = ws.max_row
        # A JUSTIFICATIVA E O MAPA ANDARAM 18 COLUNAS com a entrada do score.
        # Deixar os indices antigos aqui poria a quebra de linha num numero e
        # o hyperlink no nome do bairro — erro que so aparece ao abrir.
        ws.cell(row=i, column=27).alignment = Alignment(wrap_text=True,
                                                        vertical="top")
        if ws.cell(row=i, column=39).value:
            ws.cell(row=i, column=39).hyperlink = ws.cell(row=i, column=39).value
            ws.cell(row=i, column=39).font = Font(color="0563C1",
                                                  underline="single")
    ws.auto_filter.ref = ws.dimensions


def _aba_registros(wb, rot, regs, openpyxl, Font, PatternFill, Alignment,
                   get_column_letter):
    ws = wb.create_sheet("%s · registros" % rot[:17])
    _cabecalho(ws, CAB_REG, Font, PatternFill, Alignment, get_column_letter,
               {1: 12, 4: 30, 5: 24, 6: 34, 15: 30, 19: 60, 26: 50, 27: 22})
    LINKS = (15, 27)
    for r in regs:
        (lig, pid, fonte, nome, categ, end, logr, num, tel, site, insta, face,
         cnpj, sit_cnpj, place_id, maps_url, nota, n_aval, horario, resumo,
         ifood, preco, metros, conf, origem, vpoi, vjust, pla, plo) = r
        ws.append([
            lig, pid, fonte, nome, categ, end, logr, num, tel, site,
            _link_rede(insta), _link_rede(face), cnpj, sit_cnpj,
            _link_maps(place_id, maps_url, pla, plo, nome),
            float(nota) if nota is not None else None, n_aval, horario,
            (resumo or "")[:900],
            "sim" if ifood else "", preco or "",
            round(metros, 1) if metros is not None else None,
            float(conf) if conf is not None else None, origem,
            vpoi or "", (vjust or "")[:400],
            ("https://www.google.com/maps/search/?api=1&query=%.6f,%.6f"
             % (pla, plo)) if pla and plo else ""])
        i = ws.max_row
        for col in LINKS:
            c = ws.cell(row=i, column=col)
            if c.value and str(c.value).startswith("http"):
                c.hyperlink = c.value
                c.font = Font(color="0563C1", underline="single")
        for col in (19, 26):
            ws.cell(row=i, column=col).alignment = Alignment(wrap_text=True,
                                                             vertical="top")
    ws.auto_filter.ref = ws.dimensions


if __name__ == "__main__":
    raise SystemExit(main())
