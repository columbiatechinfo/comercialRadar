# -*- coding: utf-8 -*-
"""O dossie de UMA ligacao: tudo o que os POIs dela sabem, num texto so.

A UNIDADE E A LIGACAO, e nao o POI. Regra do dono do produto em 08/09/2026:
"o foco e a instalacao, nao os POIs; eles enriquecem a instalacao a que se
fundem, dando confiabilidade a ela".

O que isso muda na pratica: a Madeireira Maravilha existe seis vezes na base, e
ate hoje cada copia era julgada sozinha com o pedaco de evidencia que trouxe.
Resultado medido: 18.249 ligacoes com vereditos que se contradizem, 1.889 delas
com aprovado e reprovado ao mesmo tempo sobre o MESMO hidrometro.

Seis fontes apontando o mesmo medidor nao sao seis problemas — sao seis
testemunhas. Este modulo as poe na mesma sala.
"""
import math

#: Quantas visadas de rua entram. Sao as do POI mais PROXIMO do hidrometro.
VISADAS = 4

#: Ate onde o POI pode estar para as fotos DELE valerem como fotos DESTA
#: ligacao.
#:
#: O DEFEITO QUE ISTO EVITA. Este modulo escolhe as visadas do POI mais
#: proximo do hidrometro — e "mais proximo" nao e "perto". Se todos os POIs de
#: uma ligacao estiverem longe, o mais proximo deles tambem esta, e o dossie
#: mandava para a IA a fachada de outro imovel apresentada como sendo deste.
#:
#: Isso passou a acontecer em 08/09/2026, quando `casar_por_endereco` comecou
#: a vincular o POI que PUBLICA rua, numero e CEP da ligacao mas tem a
#: coordenada no centroide do CEP: sao vinculos bons — a fonte testemunha o
#: endereco — e a mediana de distancia deles e 316 m. Como testemunho de
#: cadastro valem; como fotografia da porta, nao.
#:
#: 60 m e o raio de busca do cruzamento: dentro dele o ponto e o hidrometro
#: podem ser o mesmo imovel com coordenada imprecisa. Fora dele, nao.
RAIO_DA_FOTO_M = 60.0
#: Quantas fotos publicadas no Google, de qualquer POI que as tenha.
#:
#: TRES desde 09/09/2026, por pedido do dono do produto. A ordem e a do
#: Google, que poe na frente a que melhor representa o lugar — nao da para
#: pedir "as mais recentes" porque NENHUMA das 55.649 fotos do banco tem data.
#: Isso e dito ao modelo em vez de silenciado: foto sem data nao sustenta
#: afirmacao sobre o presente.
FOTOS_MAPS = 3

_ORDEM_VISADA = {"sv_frente": 0, "sv_lado_a": 1, "sv_lado_b": 2, "sv_fundo": 3}


def _metros(la1, lo1, la2, lo2):
    """Distancia aproximada. Serve para ORDENAR, nao para medir."""
    if None in (la1, lo1, la2, lo2):
        return 9e9
    dy = (la2 - la1) * 111320.0
    dx = (lo2 - lo1) * 111320.0 * math.cos(math.radians((la1 + la2) / 2))
    return math.hypot(dx, dy)


SQL_LIGACAO = """
select l.num_ligacao::text, coalesce(l.categoria,''),
       coalesce(l.sit_ligacao,''), coalesce(l.nom_logradouro,''),
       coalesce(l.nro,''), coalesce(l.nom_bairro,''),
       l.cod_latitude::float8, l.cod_longitude::float8,
       coalesce(l.qtd_eco_res,0), coalesce(l.qtd_eco_com,0),
       coalesce(l.qtd_eco_ind,0)
  from resources_root.cadastro_corsan l
 where l.num_ligacao = %s::bigint
 limit 1
"""

SQL_POIS = """
select p.id, coalesce(p.nome,''), coalesce(p.fonte,''),
       coalesce(p.categoria,''), coalesce(p.endereco,''),
       coalesce(p.telefone,''), coalesce(p.website,''),
       coalesce(p.instagram,''), coalesce(p.facebook,''),
       coalesce(p.cnpj,''),
       st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
       lp.confianca, lp.metros,
       v.veredito, v.justificativa,
       m.avaliacao, m.total_avaliacoes, m.status_horario, m.resumo_avaliacoes
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  left join radar_comercial.poi_veredito v on v.poi_id = p.id
  left join radar_comercial.maps_data m on m.poi_id = p.id
 where lp.ligacao = %s
   and p.fundido_em is null
   and lp.descartado_em is null
 order by lp.confianca desc nulls last, p.id
"""


def _datas_das_visadas(cur, poi_id):
    """`{tipo: "2024-05"}` para as fotos de rua deste POI.

    O DEFEITO QUE ISTO CORRIGE, achado em 08/09/2026: as regras de julgar
    dizem "A FOTO DE RUA TEM IDADE, e o cadastro diz qual" e mandam o modelo
    pesar foto de dois anos contra fonte recente — mas o dossie da ligacao
    nunca mandava data nenhuma. A regra existia e nao tinha como ser aplicada.

    A data existe e e boa: 83.096 visadas de 2025, 63.023 de 2024, 657 de 2026
    e apenas 95 sem data, contadas na `poi_evidencia` no mesmo dia.
    """
    cur.execute("""select tipo, data_imagem
                     from radar_comercial.poi_evidencia
                    where poi_id = %s and data_imagem is not null""",
                (poi_id,))
    return {tp: str(d)[:7] for tp, d in cur.fetchall() if d}


def _dist_do_poi(pois, poi_id, la, lo):
    """Metros do hidrometro ate este POI, lendo a lista ja carregada."""
    for r in pois:
        if r[0] == poi_id:
            return _metros(la, lo, r[10], r[11])
    return 9e9


#: A LOJA NO AR E PROVA DATADA DE OPERACAO, e por isso entra separada.
#:
#: O POI diz que o estabelecimento existe; o merchant do iFood diz que ele
#: estava ACEITANDO PEDIDO quando foi visto. Sao coisas diferentes, e a segunda
#: e a que responde "esta funcionando hoje" — a pergunta do produto.
SQL_IFOOD = """
select p.id, coalesce(m.nome,''), coalesce(m.categoria,''),
       m.nota, m.avaliacoes,
       coalesce(m.rua,''), coalesce(m.numero,''), coalesce(m.bairro,''),
       coalesce(m.cnpj,'')
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  join radar_comercial.ifood_merchant m on m.poi_id = p.id
 where lp.ligacao = %s and lp.descartado_em is null and p.fundido_em is null
"""

#: O ANUNCIO DE HOSPEDAGEM NAO TRAZ ENDERECO, e e do desenho da plataforma.
#: Por isso ele entra com o que TEM: capacidade, nota e quantas avaliacoes —
#: hospedagem remunerada com hospede recente e consumo comercial numa ligacao
#: residencial, e a capacidade diz o tamanho desse consumo.
SQL_AIRBNB = """
select p.id, coalesce(a.titulo, a.nome, ''), coalesce(a.tipo_resumo,''),
       a.hospedes, a.quartos, a.camas, a.banheiros,
       a.nota, a.avaliacoes_qtd, coalesce(a.bairro,''),
       coalesce(a.anfitriao,'')
  from radar_comercial.ligacao_poi lp
  join radar_comercial.pois p on p.id = lp.poi_id
  join radar_comercial.airbnb_anuncio a on a.poi_id = p.id
 where lp.ligacao = %s and lp.descartado_em is null and p.fundido_em is null
"""


def _linha_da_ligacao(cur, ligacao):
    cur.execute(SQL_LIGACAO, (ligacao,))
    return cur.fetchone()


def montar(con, ligacao, ia, imagens_mod):
    """Devolve (texto_do_dossie, imagens_b64, tipos, resumo).

    `ia` e o modulo `avaliar_ia` — usado para `_sinal_do_maps` e `_dias_atras`,
    que ja sabem converter "ha 2 meses" em mes e ano ancorados na data em que o
    POI foi detalhado. Reaproveitar em vez de reescrever: sao as mesmas contas.
    """
    cur = con.cursor()
    lig = _linha_da_ligacao(cur, ligacao)
    cur.execute(SQL_POIS, (ligacao,))
    pois = cur.fetchall()
    if not pois:
        return None, [], [], {}

    linhas = []
    # ── 1 · a ligacao, que e o sujeito ───────────────────────────────────
    if lig:
        (num, cat, sit, logr, nro, bairro, la, lo, eres, ecom, eind) = lig
        linhas.append("A LIGACAO DE AGUA — e ELA que esta sendo julgada:")
        linhas.append("- numero: %s" % num)
        linhas.append("- endereco do hidrometro: %s, %s — bairro %s"
                      % (logr or "(sem rua)", nro or "s/n", bairro or "?"))
        linhas.append("- categoria cobrada hoje: %s (%s)" % (cat or "?",
                                                             sit or "?"))
        # OS MEDIDORES SAO INDICIO FORTE, e para companhia de agua sao dos
        # melhores que existem: uma casa com seis economias nao e uma casa.
        linhas.append("- economias: %d residencial(is), %d comercial(is), "
                      "%d industrial(is)" % (eres, ecom, eind))
        if ecom or eind:
            linhas.append("  ATENCAO: esta ligacao JA tem economia comercial "
                          "ou industrial declarada.")
        if eres > 1:
            linhas.append("  ATENCAO: %d economias residenciais num mesmo "
                          "ponto — varias moradias ou uso misto." % eres)
    else:
        la = lo = None
        linhas.append("A LIGACAO DE AGUA %s — a base do cliente nao devolveu "
                      "a ficha dela." % ligacao)

    # ── 2 · quem aponta para ela ─────────────────────────────────────────
    linhas.append("")
    linhas.append("O QUE AS FONTES DIZEM SOBRE ESTE ENDERECO — sao %d "
                  "registro(s) independentes:" % len(pois))
    fontes = set()
    melhor_sv = None       # POI mais proximo do hidrometro
    melhor_foto = None     # POI com mais avaliacoes, para as fotos publicadas
    for r in pois:
        (pid, nome, fonte, categoria, endereco, tel, site, insta, face, cnpj,
         pla, plo, conf, metros, ver, just, nota, n_aval, horario,
         resumo) = r
        fontes.add(fonte)
        d = _metros(la, lo, pla, plo)
        if melhor_sv is None or d < melhor_sv[1]:
            melhor_sv = (pid, d)
        if n_aval and (melhor_foto is None or n_aval > melhor_foto[1]):
            melhor_foto = (pid, n_aval)

        linhas.append("")
        # O NUMERO DO POI VAI NO TEXTO porque o modelo precisa poder
        # APONTAR quem nao pertence aqui. Sem identificador ele so poderia
        # descrever ("aquele da escola"), e nenhum codigo agiria sobre isso.
        linhas.append("  POI #%s [%s] %s"
                      % (pid, fonte, nome or "(sem nome)"))
        linhas.append("    atividade declarada: %s" % (categoria or "?"))
        if endereco:
            linhas.append("    endereco que esta fonte traz: %s" % endereco)
        if d < 9e8:
            linhas.append("    fica a %.0f m do hidrometro" % d)
        for rot, val in (("telefone", tel), ("site", site),
                         ("Instagram", insta), ("Facebook", face),
                         ("CNPJ", cnpj)):
            if val and str(val).strip():
                linhas.append("    %s: %s" % (rot, str(val).strip()[:120]))
        if nota is not None or n_aval:
            linhas.append("    Google: nota %s de 5, com %s avaliacao(oes)"
                          % (nota if nota is not None else "?",
                             n_aval if n_aval else "?"))
        if horario:
            linhas.append("    horario declarado: %s" % str(horario)[:110])
        if resumo:
            linhas.append("    o que o Google resume: %s" % str(resumo)[:300])
        # O VEREDITO ANTERIOR ENTRA COMO EVIDENCIA, e nao como resposta.
        # Decisao do dono do produto: os 24 mil julgamentos por POI seguem
        # valendo como o que a IA ja observou naquele pedaco.
        if ver:
            linhas.append("    a IA ja olhou ESTE registro sozinho e disse "
                          "'%s': %s" % (ver, (just or "")[:220]))

    # ── 2b · a loja no ar, que e prova DATADA de operacao ────────────────
    try:
        cur.execute(SQL_IFOOD, (ligacao,))
        ifood = cur.fetchall()
    except Exception:                                          # noqa: BLE001
        ifood = []
    if ifood:
        linhas.append("")
        linhas.append("NO IFOOD — loja no ar, ou seja, aceitando pedido "
                      "quando foi vista:")
        for (pid, nome, categ, nota, aval, rua, nro, bairro, cnpj) in ifood:
            linhas.append("  POI #%s · %s" % (pid, nome or "(sem nome)"))
            if categ:
                linhas.append("    cozinha: %s" % categ)
            if nota is not None or aval:
                linhas.append("    nota %s com %s avaliacao(oes)"
                              % (nota if nota is not None else "?", aval or 0))
            if rua or nro:
                linhas.append("    endereco que o iFood publica: %s, %s%s"
                              % (rua or "?", nro or "s/n",
                                 (" — " + bairro) if bairro else ""))
            if cnpj:
                linhas.append("    CNPJ: %s" % cnpj)

    # ── 2c · hospedagem por temporada ────────────────────────────────────
    try:
        cur.execute(SQL_AIRBNB, (ligacao,))
        airbnb = cur.fetchall()
    except Exception:                                          # noqa: BLE001
        airbnb = []
    if airbnb:
        linhas.append("")
        linhas.append("NO AIRBNB — hospedagem remunerada, que e consumo "
                      "comercial numa ligacao residencial:")
        for (pid, titulo, tipo, hosp, qua, camas, banh, nota, aval,
             bairro, anfitriao) in airbnb:
            linhas.append("  POI #%s · %s" % (pid, titulo or "(sem titulo)"))
            if tipo:
                linhas.append("    tipo: %s" % tipo)
            cap = [x for x in (
                ("%d hospede(s)" % hosp) if hosp else None,
                ("%d quarto(s)" % qua) if qua else None,
                ("%d cama(s)" % camas) if camas else None,
                ("%d banheiro(s)" % banh) if banh else None) if x]
            if cap:
                linhas.append("    capacidade: %s" % " · ".join(cap))
            if nota is not None or aval:
                linhas.append("    nota %s com %s avaliacao(oes)"
                              % (nota if nota is not None else "?", aval or 0))
            if anfitriao:
                linhas.append("    anfitriao: %s" % anfitriao)
            # O ENDERECO NAO VEM, e dize-lo evita que o modelo o procure.
            linhas.append("    o Airbnb NAO publica o endereco exato — a "
                          "plataforma mostra so um circulo aproximado.")

    # ── 3 · os comentarios, de todos os POIs juntos ──────────────────────
    for r in pois:
        pid, nome, fonte = r[0], r[1], r[2]
        alvo = {"id": pid}
        try:
            sinais = ia._sinal_do_maps(con, alvo)
        except Exception:                                      # noqa: BLE001
            sinais = []
        coments = [s for s in sinais if s.strip().startswith("·")
                   or "avaliações de clientes" in s]
        if coments:
            linhas.append("")
            linhas.append("  avaliacoes de clientes ligadas a [%s] %s:"
                          % (fonte, nome[:40]))
            linhas.extend("  " + c for c in coments)

    # ── 4 · as imagens ───────────────────────────────────────────────────
    imgs, tipos = [], []
    datas = {}
    # O MAIS PROXIMO SO SERVE SE ESTIVER PERTO. Ver `RAIO_DA_FOTO_M`.
    if melhor_sv and melhor_sv[1] > RAIO_DA_FOTO_M:
        linhas.append("")
        linhas.append("SEM FOTO DA FACHADA DESTA LIGACAO. O registro mais "
                      "proximo esta a %.0f m do hidrometro — longe demais para "
                      "que a foto dele seja deste imovel. Julgue pelas fontes."
                      % melhor_sv[1])
        melhor_sv = None
    if melhor_sv:
        forma, bb, tt = ia.evidencia(con, melhor_sv[0])
        try:
            with con.cursor() as k:
                datas = _datas_das_visadas(k, melhor_sv[0])
        except Exception:                                      # noqa: BLE001
            datas = {}
        pares = sorted(zip(tt, bb),
                       key=lambda x: _ORDEM_VISADA.get(x[0], 9))
        for tp, b in pares:
            if tp.startswith("sv_") and len(imgs) < VISADAS:
                imgs.append(b)
                tipos.append(tp)
    # AS FOTOS VEM DE QUEM TEM MAIS AVALIACOES, e nao do POI mais proximo.
    # Sao coisas diferentes: a visada de rua tem de ser do imovel cobrado, mas
    # a foto publicada e do NEGOCIO, e o registro com mais avaliacoes e o que
    # o Google considera a ficha principal daquele lugar.
    #
    # `_fotos_do_maps` devolve uma lista de BYTES, nao pares — o tipo e sempre
    # foto publicada, e e este codigo que o nomeia.
    if melhor_foto and _dist_do_poi(pois, melhor_foto[0], la, lo) > RAIO_DA_FOTO_M:
        melhor_foto = None
    alvo_foto = (melhor_foto or melhor_sv or (None,))[0]
    if alvo_foto:
        try:
            with con.cursor() as k:
                for n, b in enumerate(ia._fotos_do_maps(k, alvo_foto)[:FOTOS_MAPS]):
                    imgs.append(b)
                    tipos.append("foto_maps_%d" % (n + 1))
        except Exception:                                      # noqa: BLE001
            pass

    # A IDADE DA FOTO VAI NO TEXTO, e nao so no rotulo da imagem. E dela que
    # sai a decisao de quanto a fachada ainda vale: uma casa fotografada em
    # 2024 nao desmente uma loja que abriu em 2025, e sem a data o modelo
    # tratava toda foto como se fosse de hoje.
    anos = sorted({d[:4] for tp, d in datas.items() if tp.startswith("sv_")})
    if anos:
        linhas.append("")
        if len(anos) == 1:
            linhas.append("QUANDO AS FOTOS DE RUA FORAM TIRADAS: %s (%s)."
                          % (anos[0], _mes_ano_das(datas)))
        else:
            linhas.append("QUANDO AS FOTOS DE RUA FORAM TIRADAS: entre %s e "
                          "%s (%s)." % (anos[0], anos[-1],
                                        _mes_ano_das(datas)))
        linhas.append("Pese a idade delas: o que a fachada mostra vale para "
                      "AQUELA data, e nao para hoje.")
    elif imgs:
        linhas.append("")
        linhas.append("A DATA DAS FOTOS DE RUA NAO ESTA NO CADASTRO — trate-as "
                      "como possivelmente antigas.")
    else:
        # O SILENCIO NAO AVISA. Auditadas as 200 primeiras julgadas pelo modelo
        # novo em 08/09/2026: 20 dossies nao falavam de foto nenhuma, e 17
        # deles porque nao HAVIA foto. O modelo recebia so texto e nao era
        # informado disso — e um prompt que anuncia "AS PRIMEIRAS IMAGENS SAO
        # FOTOS DE RUA" com zero imagem anexada convida a inventar o que elas
        # mostrariam.
        linhas.append("")
        linhas.append("NENHUMA IMAGEM ACOMPANHA ESTE DOSSIE. Nao ha foto de rua "
                      "nem foto publicada para este endereco: nada foi "
                      "capturado ainda. Julgue SO pelas fontes, e nao comente "
                      "fachada — voce nao viu nenhuma.")

    resumo = {"pois": len(pois), "fontes": len(fontes),
              "ifood": len(ifood), "airbnb": len(airbnb),
              "ano_das_visadas": anos[-1] if anos else None,
              "ids": [r[0] for r in pois],
              "fonte_das_visadas": melhor_sv[0] if melhor_sv else None,
              "metros_ate_o_hidrometro": (round(melhor_sv[1], 1)
                                          if melhor_sv and melhor_sv[1] < 9e8
                                          else None),
              "fonte_das_fotos": alvo_foto,
              "vereditos_por_poi": [r[14] for r in pois if r[14]]}
    # O ROTULO DE CADA IMAGEM TAMBEM DIZ A DATA. O prompt anuncia a lista
    # numerada de imagens, e e ali que o modelo olha ao citar "a foto 2".
    tipos = [("%s (%s)" % (tp, datas[tp])) if tp in datas else tp
             for tp in tipos]
    return "\n".join(linhas), imgs, tipos, resumo


def _mes_ano_das(datas):
    """"maio/2024" a partir do `{tipo: "2024-05"}` das visadas."""
    MES = ("", "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
           "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    vistos = []
    for tp, d in sorted(datas.items()):
        if not tp.startswith("sv_") or len(d) < 7:
            continue
        try:
            rot = "%s/%s" % (MES[int(d[5:7])], d[:4])
        except (ValueError, IndexError):
            rot = d
        if rot not in vistos:
            vistos.append(rot)
    return ", ".join(vistos) if vistos else "data desconhecida"
