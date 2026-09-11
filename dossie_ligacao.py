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

import setor

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


#: Os sites que so republicam o cadastro da Receita. Contar um deles como
#: segunda fonte era contar a Receita duas vezes.
DIRETORIOS_DE_CNPJ = ("cnpj", "econodata", "solutudo", "casadosdados", "empresaqui",
                      "informecadastral", "ondefica", "guiamais", "apontador",
                      "telelistas", "listamais", "consultasocio", "empresas")
#: As plataformas em que o negocio opera de fato: pedido, reserva, venda,
#: agenda. Estar nelas e prova de atividade, e nao copia de cadastro.
PLATAFORMAS = ("ifood.", "airbnb.", "booking.", "mercadolivre.", "olx.", "elo7.",
               "shopee.", "getninjas.", "doctoralia.", "rappi.", "aiqfome.",
               "tripadvisor.", "trivago.", "99app.", "keeta.")
REDES_SOCIAIS = ("instagram.", "facebook.", "tiktok.", "linkedin.", "wa.me", "whatsapp.")


def _de_onde_vem(x):
    """O tipo da prova como o modelo deve le-lo — o dominio corrige a leitura."""
    f = familia_da_prova(dict(x, status=None, mesmo_endereco=None))
    if f == "receita":
        return "site de consulta de CNPJ (fonte não oficial)"
    return x.get("tipo_da_prova")


def familia_da_prova(x):
    """A familia de fonte que um resultado da busca web acrescenta — ou ''.

    O TIPO QUE A LEITURA ESCREVEU NAO BASTA: ela chamou diretorio de CNPJ de
    "plataforma". O dominio decide; o tipo so vale para o painel do Google,
    que nao tem dominio.
    """
    t = str(x.get("tipo_da_prova") or "")
    d = str(x.get("dominio") or "").lower()
    if x.get("status") == "fechado_permanente" or x.get("mesmo_endereco") is False:
        return ""
    if d and any(k in d for k in DIRETORIOS_DE_CNPJ):
        return "receita"
    if t == "perfil_google" or x.get("onde_na_pagina") in ("painel", "mapa"):
        return "google"
    if d and any(k in d for k in PLATAFORMAS):
        return "plataforma"
    if d and any(k in d for k in REDES_SOCIAIS):
        return "web"
    if t in ("site_proprio", "rede_social") and d:
        return "web"
    if t == "cadastro_cnpj":
        return "receita"
    return ""


def montar(con, ligacao, ia, imagens_mod, busca_web=None):
    """Devolve (texto_do_dossie, imagens_b64, tipos, resumo).

    `ia` e o modulo `avaliar_ia` — usado para `_sinal_do_maps` e `_dias_atras`,
    que ja sabem converter "ha 2 meses" em mes e ano ancorados na data em que o
    POI foi detalhado. Reaproveitar em vez de reescrever: sao as mesmas contas.
    """
    # AS PALAVRAS DO SETOR, e nao "hidrometro" escrito no texto. O mesmo
    # dossie serve agua, energia e gas; ver `setor.py` e a migracao 0095.
    # VOCABULARIO NEUTRO no que a IA le. Pedido do dono do produto em
    # 11/09/2026: o servico atende agua, energia e gas, e o prompt nao pode
    # empurrar o modelo para um setor. `setor` continua valendo para a tela.
    pal = dict(setor.palavras(con), ligacao_mai="INSTALAÇÃO", medidor="medidor",
               consumo="consumo")
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
        # ACENTO E SEMANTICA, e este titulo era a prova.
        #
        # "A LIGACAO DE AGUA — e ELA que esta sendo julgada" tem um "e" que
        # deveria ser "é": sem o acento a frase vira uma conjuncao solta e
        # perde o verbo. Apontado pelo dono do produto em 10/09/2026 com o
        # exemplo exato: "casa 1 E ligacao 20" e "casa 1 É ligacao 20" dizem
        # coisas diferentes, e quem le isto e um modelo de linguagem.
        linhas.append("A %s — é ELA que está sendo julgada:"
                      % pal["ligacao_mai"])
        linhas.append("- número: %s" % num)
        linhas.append("- endereço do %s: %s, %s — bairro %s"
                      % (pal["medidor"], logr or "(sem rua)", nro or "s/n",
                         bairro or "?"))
        linhas.append("- categoria cobrada hoje: %s (%s)" % (cat or "?",
                                                             sit or "?"))
        # AS ECONOMIAS SAO INDICIO FORTE em qualquer utility: uma casa com
        # seis economias nao e uma casa.
        linhas.append("- economias: %d residencial(is), %d comercial(is), "
                      "%d industrial(is)" % (eres, ecom, eind))
        if eres > 1:
            linhas.append("  ATENÇÃO: %d economias residenciais num mesmo "
                          "ponto — várias moradias ou uso misto." % eres)
    else:
        la = lo = None
        linhas.append("A %s %s — a base do cliente não devolveu a ficha "
                      "dela." % (pal["ligacao_mai"], ligacao))

    # ── 2 · quem aponta para ela ─────────────────────────────────────────
    # O ENDERECO COMPLETO COMO A CORSAN ESCREVE: e nele que mora a unidade
    # ("CASA 02", "ESQ SALAO", "APT. 03"), que desempata o condominio.
    ids_pois = [r[0] for r in pois]
    outras, compl, aberto = {}, {}, {}
    try:
        cur.execute("""select coalesce(end_ligacao,'') from resources_root.cadastro_corsan
                        where num_ligacao::text = %s limit 1""", (ligacao,))
        k = cur.fetchone()
        if k and k[0]:
            linhas.append("- endereço completo no cadastro: %s" % k[0])
        # QUEM MAIS DISPUTA CADA REGISTRO. Ver a regra "o registro que tambem
        # e candidato de outras ligacoes" no prompt.
        cur.execute("""select lp.poi_id, lp.ligacao, coalesce(c.end_ligacao,'')
                         from radar_comercial.ligacao_poi lp
                         join resources_root.cadastro_corsan c
                           on c.num_ligacao::text = lp.ligacao
                        where lp.descartado_em is null and lp.poi_id = any(%s)
                          and lp.ligacao <> %s""", (ids_pois, ligacao))
        for pid, lig2, end2 in cur.fetchall():
            outras.setdefault(pid, []).append((lig2, end2))
        cur.execute("""select poi_id, complemento from radar_comercial.receita_data
                        where poi_id = any(%s) and coalesce(complemento,'') <> ''""",
                    (ids_pois,))
        compl = dict(cur.fetchall())
        # A ABERTURA DO CNPJ E UMA DATA, e a foto de rua tambem: uma casa
        # fotografada antes do negocio abrir nao o desmente.
        cur.execute("""select poi_id, bruto->>'data_inicio' from radar_comercial.receita_data
                        where poi_id = any(%s) and bruto ? 'data_inicio'""", (ids_pois,))
        aberto = {p: v for p, v in cur.fetchall() if v and len(v) >= 6}
    except Exception:                                          # noqa: BLE001
        con.rollback()
    # A DATA DA FOTO DE RUA ANTES DOS REGISTROS: e contra ela que se pesa a
    # abertura de cada CNPJ. Mesmo POI que a secao das imagens escolhe — o
    # mais proximo do medidor, se estiver dentro do raio.
    ym_foto = None
    prox = min(((r[0], _metros(la, lo, r[10], r[11])) for r in pois), key=lambda x: x[1])
    if prox[1] <= RAIO_DA_FOTO_M:
        try:
            with con.cursor() as k:
                dd = _datas_das_visadas(k, prox[0])
            ym_foto = max((v[:7] for tp, v in dd.items()
                           if tp.startswith("sv_") and len(v) >= 7), default=None)
        except Exception:                                      # noqa: BLE001
            con.rollback()
    linhas.append("")
    linhas.append("O QUE AS FONTES DIZEM SOBRE ESTE ENDEREÇO — são %d "
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
            linhas.append("    endereço que esta fonte traz: %s" % endereco)
        if compl.get(pid):
            linhas.append("    complemento na Receita: %s" % compl[pid])
        if aberto.get(pid):
            ab = aberto[pid]
            txt = "    CNPJ aberto em: %s/%s" % (ab[4:6], ab[:4])
            if ym_foto:
                try:
                    m_ab = int(ab[:4]) * 12 + int(ab[4:6])
                    m_ft = int(ym_foto[:4]) * 12 + int(ym_foto[5:7])
                    foto = "%s/%s" % (ym_foto[5:7], ym_foto[:4])
                    if m_ab > m_ft:
                        txt += (" — DEPOIS da foto de rua (%s): a foto é de antes "
                                "deste negócio existir" % foto)
                    elif m_ab < m_ft:
                        txt += " — %d mês(es) ANTES da foto de rua (%s)" % (m_ft - m_ab, foto)
                    else:
                        txt += " — no mesmo mês da foto de rua (%s)" % foto
                except ValueError:
                    pass
            linhas.append(txt)
        if outras.get(pid):
            os_ = outras[pid]
            amostra = "; ".join("%s (%s)" % (l2, "-".join(e2.split("-")[1:2]) or "?")
                                for l2, e2 in os_[:6])
            linhas.append("    TAMBÉM é candidato de %d outra(s) instalação(ões): %s%s"
                          % (len(os_), amostra, " ..." if len(os_) > 6 else ""))
        if d < 9e8:
            linhas.append("    fica a %.0f m do %s" % (d, pal["medidor"]))
        for rot, val in (("telefone", tel), ("site", site),
                         ("Instagram", insta), ("Facebook", face),
                         ("CNPJ", cnpj)):
            if val and str(val).strip():
                linhas.append("    %s: %s" % (rot, str(val).strip()[:120]))
        if nota is not None or n_aval:
            linhas.append("    Google: nota %s de 5, com %s avaliação(ões)"
                          % (nota if nota is not None else "?",
                             n_aval if n_aval else "?"))
        if horario:
            linhas.append("    horário declarado: %s" % str(horario)[:110])
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
                linhas.append("    nota %s com %s avaliação(ões)"
                              % (nota if nota is not None else "?", aval or 0))
            if rua or nro:
                linhas.append("    endereço que o iFood publica: %s, %s%s"
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
        linhas.append("NO AIRBNB — hospedagem remunerada, que é %s "
                      "comercial numa instalação residencial:" % pal["consumo"])
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
                linhas.append("    anfitrião: %s" % anfitriao)
            # O ENDERECO NAO VEM, e dize-lo evita que o modelo o procure.
            linhas.append("    o Airbnb NAO publica o endereco exato — a "
                          "plataforma mostra so um circulo aproximado.")

    # ── 3 · os comentarios, de todos os POIs juntos ──────────────────────
    if busca_web:
        linhas.append("")
        linhas.append("O QUE A BUSCA NA WEB ACHOU — Google, e o Bing só quando "
                      "o Google falhou:")
        for b in busca_web:
            linhas.append("  busca: \"%s\" (%s)" % (b.get("consulta"), b.get("motor")))
            # OUTRO ENDERECO FICA DE FORA; o resto entra INTEIRO, de qualquer
            # fonte e sem corte. Fonte nao oficial e dado obtido, e quem julga
            # se casa com algum registro e o modelo (dono do produto, 11/09/2026).
            ests = [x for x in (b.get("estabelecimentos") or [])
                    if x.get("mesmo_endereco") is not False]
            if not ests:
                linhas.append("    nenhum estabelecimento neste endereço")
            for x in ests:
                campos = [("endereço", x.get("endereco")), ("telefone", x.get("telefone")),
                          ("site", x.get("site")), ("Instagram", x.get("instagram")),
                          ("horário", x.get("horario")), ("nota", x.get("nota")),
                          ("avaliações", x.get("avaliacoes")),
                          ("avaliação mais recente",
                           (x.get("avaliacao_mais_recente") or {}).get("quando")),
                          ("CNPJ", x.get("cnpj")), ("situação", x.get("status")),
                          ("atividade", x.get("categoria")), ("e-mail", x.get("email")),
                          ("aberto em", x.get("data_abertura")),
                          ("o que a página diz", x.get("descricao")),
                          ("onde na página", x.get("onde_na_pagina")),
                          ("de onde vem", _de_onde_vem(x)),
                          ("domínio", x.get("dominio"))]
                linhas.append("    %s · %s" % (x.get("nome") or "(sem nome)",
                              " · ".join("%s: %s" % (k2, v2) for k2, v2 in campos
                                         if v2 not in (None, "", [], {}))))
            conf = b.get("pois_confirmados") or (
                [b["poi_confirmado"]] if b.get("poi_confirmado") else [])
            if conf:
                linhas.append("    a leitura da página diz que ela comprova: %s"
                              % ", ".join("POI #%s" % c for c in conf))
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
            linhas.append("  avaliações de clientes ligadas a [%s] %s:"
                          % (fonte, nome[:40]))
            linhas.extend("  " + c for c in coments)

    # ── 4 · as imagens ───────────────────────────────────────────────────
    imgs, tipos = [], []
    datas = {}
    # O MAIS PROXIMO SO SERVE SE ESTIVER PERTO. Ver `RAIO_DA_FOTO_M`.
    if melhor_sv and melhor_sv[1] > RAIO_DA_FOTO_M:
        linhas.append("")
        linhas.append("SEM FOTO DA FACHADA DESTA INSTALAÇÃO. O registro mais "
                      "próximo está a %.0f m do %s — longe demais para que a "
                      "foto dele seja deste imóvel. Julgue pelas fontes."
                      % (melhor_sv[1], pal["medidor"]))
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
                      "AQUELA data, e não para hoje.")
    elif imgs:
        linhas.append("")
        linhas.append("A DATA DAS FOTOS DE RUA NÃO ESTÁ NO CADASTRO — "
                      "trate-as como possivelmente antigas.")
    else:
        # O SILENCIO NAO AVISA. Auditadas as 200 primeiras julgadas pelo modelo
        # novo em 08/09/2026: 20 dossies nao falavam de foto nenhuma, e 17
        # deles porque nao HAVIA foto. O modelo recebia so texto e nao era
        # informado disso — e um prompt que anuncia "AS PRIMEIRAS IMAGENS SAO
        # FOTOS DE RUA" com zero imagem anexada convida a inventar o que elas
        # mostrariam.
        linhas.append("")
        linhas.append("NENHUMA IMAGEM ACOMPANHA ESTE DOSSIÊ. Não há foto de "
                      "rua nem foto publicada para este endereço: nada foi "
                      "capturado ainda. Julgue SÓ pelas fontes, e não comente "
                      "fachada — você não viu nenhuma.")

    # O QUE A REGRA DO CODIGO PRECISA: as economias ja cobradas como
    # comercio, a fonte de cada POI, e as familias de fonte que a busca web
    # acrescenta a cada POI que ela comprovou. Ver `avaliar_ligacao.aplicar_regra`.
    eco_com = eco_ind = 0
    if lig:
        eco_com, eco_ind = (lig[9] or 0), (lig[10] or 0)
    web_familias = {}
    for b in (busca_web or []):
        # NAO SE CHAMA `tipos`: esse nome e a lista dos rotulos das imagens,
        # e reusa-lo aqui mandava o prompt com a lista de imagens vazia.
        fams = {familia_da_prova(x) for x in (b.get("estabelecimentos") or [])}
        fams.discard("")
        for c in (b.get("pois_confirmados") or []):
            web_familias.setdefault(str(c), set()).update(fams)
    resumo = {"pois": len(pois), "fontes": len(fontes),
              "eco_com": eco_com, "eco_ind": eco_ind,
              "fonte_de": {str(r[0]): str(r[2] or "").lower() for r in pois},
              "web_familias": {k: sorted(v) for k, v in web_familias.items()},
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
