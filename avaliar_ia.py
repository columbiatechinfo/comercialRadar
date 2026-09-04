# -*- coding: utf-8 -*-
"""avaliar_ia.py — a IA da Spark julga o POI a partir da evidência capturada.

O QUE ESTE MÓDULO DECIDE, E O QUE ELE NÃO DECIDE

Ele responde uma pergunta só, e ela é comercial: **este imóvel, que a Corsan
fatura como RESIDENCIAL, tem atividade econômica?** Não descobre POI, não
corrige endereço, não mexe em vínculo. Entra com as imagens que o
`capturar_evidencia.py` e o `capturar_pagina.py` gravaram em `poi_evidencia`,
sai com uma linha em `poi_veredito`.

DUAS CHAMADAS, E ELAS NÃO SE MISTURAM — é o padrão que tornou o veredito visual
confiável neste projeto e a razão dele é medida, não teórica:

    1 · PERCEPÇÃO CEGA   só as imagens. O modelo não vê nome, categoria,
                         endereço nem fonte. Se vir, papagaia: pedir "descreva"
                         junto com "o cadastro diz padaria" faz a descrição
                         nascer com padaria dentro, e aí o julgamento está
                         confirmando a si mesmo.
    2 · JULGAMENTO       só texto: a descrição da etapa 1 mais o que o cadastro
                         afirma. Sem imagem. O modelo compara duas afirmações
                         em vez de olhar e opinar ao mesmo tempo.

QUATRO VEREDITOS, e a escala é do dono do produto:

    aprovado_exato       o estabelecimento do cadastro está ali, identificado
    aprovado_comercial   não é aquele, mas o imóvel tem comércio visível
    revisao_humana       há indício, não há prova
    reprovado            nenhuma atividade econômica relevante

DUAS PERGUNTAS DE CLASSIFICAÇÃO, SEPARADAS — também decisão do dono do produto.
A ESPÉCIE do CNEFE (1 a 8) diz o que é a EDIFICAÇÃO; a SEÇÃO da CNAE (A a U)
diz qual é a ATIVIDADE. São eixos diferentes e uma não deriva da outra.

O AIRBNB TEM PROMPT PRÓPRIO. Ele não vai ao Street View — não há endereço exato
para mirar —, e o que prova atividade num anúncio de hospedagem é a página:
calendário com datas, avaliações datadas, anfitrião ativo. A escala de
veredito é a MESMA para não criar duas réguas.

Uso:
    python avaliar_ia.py --area area_atual --limite 10        # ensaio
    python avaliar_ia.py --area area_atual --aplicar
    python avaliar_ia.py --poi 87876 --aplicar --refazer
"""
from __future__ import annotations

import argparse
import base64
import json
import threading
import time

import area_utils
import base_comum as bc

# O CLIENTE DA SPARK É O QUE JÁ EXISTE. `descrever_imagens._chat_local` fala o
# protocolo da OpenAI, lê `VLLM_URL` do ambiente e já carrega a nota sobre por
# que `num_ctx` sumiu. Reescrevê-lo aqui criaria dois clientes para manter.
import descrever_imagens as di

# O MODELO É O QUE A SPARK SERVE HOJE, e o nome vem do `.env` — não do padrão
# histórico do `descrever_imagens`, que ainda aponta para `qwen3vl-moe`. Medido
# em 04/09/2026: `GET /v1/models` devolve `ia-principal` e
# `qwen3.5-35b-a3b`, ambos com raiz `Qwen/Qwen3.5-35B-A3B-FP8` e janela de
# 131.072; `qwen3vl-moe` não está mais no ar. Pedir um modelo que não existe dá
# 404 em toda chamada, e o placar viria zerado sem dizer por quê.
#
# E ELE ENXERGA: sondado no mesmo dia com uma `sv_frente` real, respondeu em
# 1,7 s descrevendo a casa, o telhado e as próprias marcações verdes que o
# `capturar_evidencia` desenha — 577 tokens de prompt para uma imagem de
# 934×621, o que quer dizer que o servidor reamostra antes de olhar.
import os
MODELO_PADRAO = (os.environ.get("SPARK_MODELO")
                 or os.environ.get("MODELO_VISAO") or "ia-principal")
TIMEOUT = 480

# A ORDEM DAS IMAGENS É A ORDEM DA LEITURA, e ela não é arbitrária: o satélite
# primeiro dá o enquadramento (onde fica, quantas construções), a fachada
# depois responde a pergunta, o fundo por último dá o contexto do quarteirão.
ORDEM_RUA = ["satelite", "sv_frente", "sv_fundo"]
ORDEM_PAGINA = ["pagina_airbnb"]

VEREDITOS = ("aprovado_exato", "aprovado_comercial", "revisao_humana", "reprovado")

ESPECIES = """1 domicílio particular · 2 domicílio coletivo (pensão, hotel, \
alojamento) · 3 estabelecimento agropecuário · 4 estabelecimento de ensino · \
5 estabelecimento de saúde · 6 estabelecimento de outras finalidades (comércio, \
serviço, indústria, escritório) · 7 edificação em construção ou reforma · \
8 estabelecimento religioso"""


def _log(m):
    print(m, flush=True)


# ── 1 · a percepção cega ───────────────────────────────────────────────────
#
# NENHUM DADO DO CADASTRO ENTRA AQUI. Nem o nome, nem a categoria, nem a rua.
# O que o modelo devolve tem de poder ser conferido só olhando as imagens.
# ── a percepção, UMA CHAMADA POR IMAGEM ────────────────────────────────────
#
# ISTO JÁ FOI UMA CHAMADA SÓ, COM AS TRÊS IMAGENS, e a razão de ter deixado de
# ser está medida no POI 99207 em 04/09/2026. A loja "Black Style" fica na
# calçada OPOSTA e aparece só na terceira imagem; o modelo a descreveu dentro
# do bloco "fachada", como estabelecimento do imóvel avaliado. O julgamento tem
# uma regra explícita contra isso — "comércio do lado oposto não aprova o
# imóvel" —, e a regra é inútil quando a contaminação acontece ANTES dela: o
# julgamento só vê texto, e o texto já dizia que a loja era da fachada.
#
# Duas tentativas de conter por instrução falharam: separar os blocos no
# esquema, e proibir explicitamente o atravessamento. Instrução não segura o
# que a estrutura permite. Agora cada imagem tem a sua chamada, e o modelo que
# descreve a fachada NUNCA VIU o outro lado da rua — não há o que atravessar.
#
# O custo são duas chamadas a mais por POI: a percepção sobe de ~4 s para ~9 s.
# É o preço de um veredito que não aprova o vizinho no lugar do alvo.

# O QUE SEMPRE SE IGNORA. As três primeiras são marca do Google; a quarta é
# NOSSA — `capturar_evidencia` desenha a mira e escreve "FACHADA AVALIADA" na
# imagem, e o modelo transcreveu essa legenda como letreiro de loja no POI
# 99207. Quem desenha na foto tem de dizer ao leitor o que desenhou.
IGNORAR = """IGNORE, e nunca transcreva como letreiro: a marca d'água do \
Google, placas de trânsito, nomes de rua, e a legenda "FACHADA AVALIADA" com a \
mira verde — essa mira foi desenhada por nós sobre a foto para apontar o alvo, \
e não é nada que exista no local."""

PROMPT_SAT = """Você vê UMA imagem: uma vista de SATÉLITE. Um anel verde marca \
a coordenada exata de um ponto.

Descreva o que está sob o anel e ao redor dele. Não julgue e não adivinhe o uso \
do imóvel. %(ignorar)s

Responda SOMENTE um JSON:
{"construcoes_no_lote": <int|null>,
 "telhado_do_ponto": "<descrição curta do telhado sob o anel>",
 "observacao": "<até 25 palavras sobre o entorno imediato>"}"""

PROMPT_FACHADA = """Você vê UMA imagem: a foto de rua de UM imóvel. Uma mira \
verde aberta está exatamente sobre o imóvel a descrever. É ELE o assunto, e \
nada mais na foto.

DESCREVA O QUE VÊ. Não julgue, não conclua, e não adivinhe o nome de quem \
ocupa o imóvel se não estiver escrito.

REGRAS DE LEITURA
- Transcreva letreiro, placa, toldo, faixa e adesivo de vitrine EXATAMENTE como \
estão escritos. Se estiver ilegível, diga ilegível — não complete.
- %(ignorar)s
- Um mesmo imóvel pode ter mais de um estabelecimento, inclusive em andares de \
cima. Liste todos os que conseguir ler NO IMÓVEL DA MIRA.
- MEDIDORES: conte as caixas de medidor de energia ou de água na fachada e no \
muro. Se não der para contar, use null — nunca zero por desencargo.

Responda SOMENTE um JSON:
{"tipo_imovel": "casa|sobrado|predio|loja_terrea|galpao|terreno_vago|em_obra|\
indefinido", "andares": <int|null>, "letreiros": ["<texto lido>", ...],
 "vitrine": true|false, "porta_comercial": true|false, "toldo": true|false,
 "grade_ou_muro_alto": true|false, "medidores": <int|null>,
 "estabelecimentos": [{"nome": "<lido>", "ramo_aparente": "<o que parece ser>"}],
 "sinais_de_comercio": ["<mercadoria à vista>", "<cliente>", "<estacionamento>"],
 "descricao": "<até 45 palavras do que se vê na mira>"}"""

PROMPT_FUNDO = """Você vê UMA imagem: a vista da MESMA câmera girada 180°, \
mostrando o outro lado de uma rua. O imóvel que interessa NÃO está nesta foto \
— ele fica atrás da câmera. Esta foto serve só para dizer que tipo de \
quarteirão é este.

Descreva o que há do outro lado. %(ignorar)s

Responda SOMENTE um JSON:
{"carater_do_quarteirao": "residencial|misto|comercial|industrial|indefinido",
 "letreiros_do_outro_lado": ["<texto lido na calçada oposta>", ...],
 "observacao": "<até 20 palavras>"}"""

# Cada tipo de evidência tem o seu prompt, o seu bloco no resultado e a sua
# chamada. Esta lista é a percepção de rua inteira.
PERCEPCAO_RUA = [
    ("satelite", "satelite", PROMPT_SAT),
    ("sv_frente", "fachada", PROMPT_FACHADA),
    ("sv_fundo", "lado_oposto", PROMPT_FUNDO),
]


PROMPT_PAGINA = """Você recebe UMA imagem: a página inteira de um anúncio de \
hospedagem, capturada de cima a baixo.

DESCREVA O QUE ESTÁ NA PÁGINA. Não julgue e não invente o que não estiver \
escrito. Onde não houver o dado, use null.

Responda SOMENTE um JSON com estas chaves:
{
 "titulo": "<título do anúncio>",
 "tipo": "<espaço inteiro, quarto, etc., como está escrito>",
 "cidade": "<cidade que a página informa>",
 "nota": <número|null>, "avaliacoes": <int|null>,
 "avaliacao_mais_recente": "<mês e ano da avaliação mais recente visível|null>",
 "calendario_visivel": true|false,
 "mes_do_calendario": "<mês e ano mostrados no calendário|null>",
 "datas_disponiveis": true|false|null,
 "anfitriao": "<nome>", "anos_hospedando": <int|null>, "superhost": true|false,
 "quartos": <int|null>, "hospedes": <int|null>,
 "mapa_visivel": true|false,
 "descricao": "<até 45 palavras do que o anúncio oferece>"
}"""


# ── 2 · o julgamento, sem imagem ───────────────────────────────────────────
PROMPT_JULGAR = """Você decide se um imóvel tem ATIVIDADE ECONÔMICA.

O contexto: a companhia de água fatura este imóvel como RESIDENCIAL, mas alguma \
base indica que existe um estabelecimento nele. Se houver comércio, a ligação \
está com a tarifa errada — é isso que se procura.

Você recebe (a) a DESCRIÇÃO das imagens, feita por outro observador que NÃO \
sabia nada do cadastro, e (b) o que o CADASTRO afirma. Compare as duas coisas.

DESCRIÇÃO DAS IMAGENS:
%(percepcao)s

O QUE O CADASTRO AFIRMA:
%(cadastro)s

ESCOLHA UM VEREDITO:
- "aprovado_exato": a descrição identifica O estabelecimento do cadastro — o \
letreiro traz o nome, ou o ramo visto é inequivocamente o mesmo do cadastro.
- "aprovado_comercial": não dá para dizer que é AQUELE, mas o imóvel tem \
atividade comercial visível (vitrine, letreiro de outro negócio, mercadoria, \
porta de loja). A visita se justifica do mesmo jeito.
- "revisao_humana": há indício e não há prova. Ex.: porta que pode ser de loja \
mas está fechada; quarteirão comercial e imóvel ambíguo; imagem antiga demais \
para o que o cadastro afirma; mais de um medidor numa casa aparentemente comum.
- "reprovado": nada indica atividade econômica. Casa residencial sem qualquer \
sinal, terreno vago, obra.

REGRAS QUE NÃO SE NEGOCIAM
- Comércio do LADO OPOSTO da rua NÃO aprova o imóvel. Ele só informa o caráter \
do quarteirão, e caráter de quarteirão sozinho é, no máximo, revisao_humana. \
Tudo que estiver em "lado_oposto" — inclusive "letreiros_do_outro_lado" — é da \
calçada de frente, não do imóvel avaliado: se o único letreiro do conjunto \
estiver ali, o imóvel continua sem letreiro.
- Só aprova o que estiver em "fachada".
- Nome parecido não é nome igual. "Silva Alimentos" não confirma "Mercado Silva".
- Ausência de letreiro não reprova por si só: muitos negócios de bairro operam \
sem fachada. O que reprova é a ausência de QUALQUER sinal.
- Na dúvida entre aprovar e reprovar, escolha revisao_humana. Reprovar apaga o \
caso; mandar para revisão custa um olhar.

CLASSIFIQUE TAMBÉM, e são duas perguntas independentes:
- ESPÉCIE DA EDIFICAÇÃO (código do CNEFE): %(especies)s
- SEÇÃO DA ATIVIDADE (letra da CNAE): %(secoes)s
  Use null na seção quando o veredito for "reprovado" ou quando não houver \
atividade identificável.

Responda SOMENTE um JSON:
{"veredito": "<um dos quatro>", "especie_cnefe": <1-8|null>,
 "secao_cnae": "<letra|null>", "medidores": <int|null>,
 "justificativa": "<um parágrafo, até 60 palavras, dizendo o que na descrição \
sustenta o veredito. Cite o que foi visto, não o que se supõe.>"}"""


PROMPT_JULGAR_HOSPEDAGEM = """Você decide se um imóvel tem ATIVIDADE ECONÔMICA.

O contexto: a companhia de água fatura este imóvel como RESIDENCIAL, e ele está \
anunciado como hospedagem por temporada. Hospedagem remunerada É atividade \
econômica — um apartamento alugado por diária consome como comércio, não como \
moradia.

NÃO HÁ FOTO DA FACHADA, e isso é do desenho: a plataforma não publica o \
endereço exato. A prova aqui é a PRÓPRIA PÁGINA — o anúncio no ar, o calendário \
respondendo, as avaliações datadas.

DESCRIÇÃO DA PÁGINA:
%(percepcao)s

O QUE O CADASTRO AFIRMA:
%(cadastro)s

ESCOLHA UM VEREDITO, na mesma escala dos demais:
- "aprovado_exato": o anúncio está ativo E tem prova de uso recente — avaliação \
nos últimos 12 meses, ou calendário com datas disponíveis e anfitrião ativo.
- "aprovado_comercial": o anúncio existe e é de hospedagem remunerada, mas sem \
sinal de uso recente (sem avaliação datada, sem calendário legível).
- "revisao_humana": a página não deixa claro se é hospedagem remunerada, ou os \
dados estão incompletos a ponto de não sustentar conclusão.
- "reprovado": a página não é de hospedagem, ou o anúncio está claramente \
desativado.

CLASSIFICAÇÃO: hospedagem é espécie 2 do CNEFE (domicílio coletivo) quando o \
imóvel é operado como hospedagem, e a seção da CNAE é I (alojamento e \
alimentação). Use esses valores salvo se a página disser outra coisa.

Responda SOMENTE um JSON:
{"veredito": "<um dos quatro>", "especie_cnefe": <1-8|null>,
 "secao_cnae": "<letra|null>", "medidores": null,
 "justificativa": "<um parágrafo, até 60 palavras, citando o que na página \
sustenta o veredito — data da avaliação, mês do calendário, nota.>"}"""


# ── quem entra ─────────────────────────────────────────────────────────────
SQL_ALVO = """
    select distinct p.id, coalesce(p.nome,''), coalesce(p.fonte,''),
           coalesce(p.categoria,''), coalesce(p.endereco,''),
           coalesce(p.cidade,''), coalesce(p.uf,''),
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry)
      from radar_comercial.pois p
      join radar_comercial.ligacao_poi lp on lp.poi_id = p.id
      join resources_root.cadastro_corsan l on l.num_ligacao::text = lp.ligacao
      join radar_comercial.categoria_catalogo cc
            on cc.fonte = p.fonte and cc.valor = btrim(p.categoria)
      join radar_comercial.poi_evidencia e
            on e.poi_id = p.id and e.dados is not null
     where cc.avaliar
       and upper(l.categoria) = 'RESIDENCIAL'
       and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA'
       %(filtro)s
     order by p.id
"""

SEM_VEREDITO = """
       and not exists (select 1 from radar_comercial.poi_veredito v
                        where v.poi_id = p.id)
"""


def alvos(con, poligono, limite, pois, refazer):
    cur = con.cursor()
    if pois:
        cur.execute(
            SQL_ALVO % {"filtro": "and p.id = any(%s)"} , (list(pois),))
    else:
        cur.execute(SQL_ALVO % {"filtro": "" if refazer else SEM_VEREDITO})
    saida, fora = [], 0
    for (pid, nome, fonte, cat, endereco, cidade, uf, la, lo) in cur.fetchall():
        if poligono and la is not None \
                and not area_utils.ponto_no_poligono(la, lo, poligono):
            fora += 1
            continue
        saida.append({"id": pid, "nome": nome, "fonte": fonte, "categoria": cat,
                      "endereco": endereco, "cidade": cidade, "uf": uf})
        if limite and len(saida) >= limite:
            break
    return saida, fora


def evidencia(con, poi_id):
    """As imagens do POI, na ordem em que a IA deve lê-las.

    Devolve `(forma, imagens, tipos)` — `tipos` na mesma ordem das imagens, e é
    dela que sai a lista que o prompt anuncia.
    """
    cur = con.cursor()
    cur.execute("""select tipo, dados from radar_comercial.poi_evidencia
                    where poi_id = %s and dados is not null""", (poi_id,))
    por_tipo = {t: bytes(d) for t, d in cur.fetchall()}
    if por_tipo.get("pagina_airbnb"):
        return "pagina", [por_tipo["pagina_airbnb"]], ["pagina_airbnb"]
    tipos = [t for t in ORDEM_RUA if t in por_tipo]
    if not tipos:
        return None, [], []
    return "rua", [por_tipo[t] for t in tipos], tipos


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _cadastro_texto(con, alvo) -> str:
    """O que o cadastro afirma, em texto — inclusive o que o iFood já sabe.

    A LOJA DO IFOOD ENTRA AQUI, e não como imagem: a página da loja é protegida
    por desafio anti-robô, mas o endpoint `/extra` já entregou, de graça, o que
    interessa — se a loja estava disponível, quantas avaliações tem e quando foi
    vista. É prova de atividade mais forte que um print do cardápio, porque diz
    QUANDO.
    """
    linhas = [
        "- nome: %s" % (alvo["nome"] or "(sem nome)"),
        "- categoria declarada: %s" % (alvo["categoria"] or "(sem categoria)"),
        "- fonte do dado: %s" % alvo["fonte"],
        "- endereço: %s — %s/%s" % (alvo["endereco"] or "(sem endereço)",
                                    alvo["cidade"], alvo["uf"]),
    ]
    cur = con.cursor()
    if alvo["fonte"] == "ifood":
        cur.execute("""select nota, avaliacoes, cnpj, telefone, visto_em,
                              bruto->>'disponivel'
                         from radar_comercial.ifood_merchant
                        where poi_id = %s limit 1""", (alvo["id"],))
        r = cur.fetchone()
        if r:
            linhas.append(
                "- iFood em %s: loja %s, nota %s, %s avaliação(ões)%s%s"
                % (r[4].date() if r[4] else "?",
                   "DISPONÍVEL" if r[5] == "true" else "não disponível",
                   r[0] if r[0] is not None else "?",
                   r[1] if r[1] is not None else "?",
                   ", CNPJ %s" % r[2] if r[2] else "",
                   ", telefone %s" % r[3] if r[3] else ""))
    elif alvo["fonte"] == "airbnb":
        cur.execute("""select nota, avaliacoes_qtd, anfitriao, hospedes, quartos
                         from radar_comercial.airbnb_anuncio
                        where poi_id = %s limit 1""", (alvo["id"],))
        r = cur.fetchone()
        if r:
            linhas.append("- Airbnb: nota %s, %s avaliação(ões), anfitrião %s, "
                          "%s hóspede(s), %s quarto(s)"
                          % tuple("?" if x is None else x for x in r))
    cur.execute("""select url from radar_comercial.poi_link
                    where poi_id = %s and ativo order by fonte limit 4""",
                (alvo["id"],))
    for (u,) in cur.fetchall():
        linhas.append("- link: %s" % u)
    return "\n".join(linhas)


def _secoes_texto(con) -> str:
    cur = con.cursor()
    cur.execute("select letra, nome from radar_comercial.cnae_secao order by letra")
    return " · ".join("%s %s" % (l, n) for l, n in cur.fetchall())


def um_poi(con, alvo, modelo, secoes, placar, trava, aplicar) -> None:
    t0 = time.time()
    forma, imgs, tipos = evidencia(con, alvo["id"])
    if not imgs:
        with trava:
            placar["sem_evidencia"] += 1
        return

    prompt_jul = (PROMPT_JULGAR_HOSPEDAGEM if forma == "pagina"
                  else PROMPT_JULGAR)

    # 1 · percepção cega — uma chamada POR IMAGEM (ver a nota nos prompts)
    por_tipo = dict(zip(tipos, imgs))
    percepcao = {}
    try:
        if forma == "pagina":
            percepcao = di._chat_local(modelo, PROMPT_PAGINA, [_b64(imgs[0])],
                                       max_tokens=900, timeout=TIMEOUT)
        else:
            for tipo, bloco, prompt in PERCEPCAO_RUA:
                if tipo not in por_tipo:
                    # SEM IMAGEM NÃO HÁ CHAMADA, e o bloco fica nulo. É a
                    # diferença entre "não havia foto" e "o modelo não soube
                    # descrever" — dois erros de natureza oposta.
                    percepcao[bloco] = None
                    continue
                percepcao[bloco] = di._chat_local(
                    modelo, prompt % {"ignorar": IGNORAR},
                    [_b64(por_tipo[tipo])], max_tokens=520, timeout=TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha_percepcao"] += 1
            _log("   %8d percepção FALHOU %s: %s"
                 % (alvo["id"], type(e).__name__, str(e)[:70]))
        return

    # QUAIS IMAGENS ENTRARAM, gravado junto da percepção. Sem isto, ler depois
    # que a fachada veio nula não distingue "não havia foto" de "o modelo não
    # soube descrever" — e são erros de natureza oposta.
    if isinstance(percepcao, dict):
        percepcao["_imagens"] = tipos

    # 2 · julgamento, só texto
    try:
        veredito = di._chat_local(
            modelo,
            prompt_jul % {"percepcao": json.dumps(percepcao, ensure_ascii=False,
                                                  indent=1),
                          "cadastro": _cadastro_texto(con, alvo),
                          "especies": ESPECIES, "secoes": secoes},
            None, max_tokens=400, timeout=TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha_julgamento"] += 1
            _log("   %8d julgamento FALHOU %s: %s"
                 % (alvo["id"], type(e).__name__, str(e)[:70]))
        return

    v = (veredito.get("veredito") or "").strip()
    if v not in VEREDITOS:
        # VEREDITO FORA DA ESCALA NÃO VIRA "reprovado" NEM SOME. Ele vira
        # revisão humana e a resposta crua fica guardada: transformar resposta
        # inválida em reprovação inventaria uma decisão que o modelo não tomou.
        with trava:
            placar["fora_da_escala"] += 1
        percepcao["_veredito_cru"] = veredito
        v = "revisao_humana"

    dt = time.time() - t0
    if aplicar:
        gravar(con, alvo["id"], v, veredito, percepcao, modelo, len(imgs), dt)
    with trava:
        placar[v] += 1
        _log("   %8d %-26s %-19s %s"
             % (alvo["id"], alvo["nome"][:26], v,
                (veredito.get("justificativa") or "")[:64]))


def gravar(con, poi_id, v, veredito, percepcao, modelo, n_imgs, dt):
    esp = veredito.get("especie_cnefe")
    sec = (veredito.get("secao_cnae") or "").strip().upper()[:1] or None
    med = veredito.get("medidores")
    try:
        esp = int(esp) if esp is not None else None
        if esp is not None and not (1 <= esp <= 8):
            esp = None
    except (TypeError, ValueError):
        esp = None
    try:
        med = int(med) if med is not None else None
    except (TypeError, ValueError):
        med = None
    with con.cursor() as k:
        k.execute("""
            insert into radar_comercial.poi_veredito
                (poi_id, veredito, justificativa, especie_cnefe, secao_cnae,
                 medidores, percepcao, modelo, imagens, segundos)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (id_empresa, poi_id) do update set
                veredito = excluded.veredito,
                justificativa = excluded.justificativa,
                especie_cnefe = excluded.especie_cnefe,
                secao_cnae = excluded.secao_cnae,
                medidores = excluded.medidores,
                percepcao = excluded.percepcao,
                modelo = excluded.modelo,
                imagens = excluded.imagens,
                segundos = excluded.segundos,
                avaliado_em = now()
        """, (poi_id, v, veredito.get("justificativa"), esp, sec, med,
              json.dumps(percepcao, ensure_ascii=False), modelo, n_imgs,
              round(dt, 2)))
    con.commit()


def rodar(area, limite, aplicar, trabalhadores, modelo, pois, refazer):
    poligono = None if pois else (area_utils.carregar_area(area) if area else None)
    con = bc.conectar()
    lista, fora = alvos(con, poligono, limite, pois, refazer)
    _log("   %d POI(s) com evidência na fila" % len(lista))
    if fora:
        _log("   %d fora do desenho" % fora)
    if not lista:
        _log("   nada a avaliar. Capture evidência antes "
             "(capturar_evidencia.py / capturar_pagina.py).")
        con.close()
        return {"alvos": 0}
    for a in lista[:5]:
        _log("      %8d %-34s %s" % (a["id"], a["nome"][:34], a["fonte"]))
    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")
        con.close()
        return {"alvos": len(lista), "avaliados": 0}
    secoes = _secoes_texto(con)
    con.close()

    placar = {k: 0 for k in VEREDITOS}
    placar.update({"sem_evidencia": 0, "falha_percepcao": 0,
                   "falha_julgamento": 0, "fora_da_escala": 0})
    trava = threading.Lock()
    t0 = time.time()

    import concurrent.futures
    fila = list(lista)

    def obreiro(_n):
        c = bc.conectar()
        try:
            while True:
                with trava:
                    if not fila:
                        return
                    a = fila.pop(0)
                try:
                    um_poi(c, a, modelo, secoes, placar, trava, aplicar)
                except Exception as e:                         # noqa: BLE001
                    with trava:
                        _log("   %8d FALHOU %s: %s"
                             % (a["id"], type(e).__name__, str(e)[:70]))
        finally:
            c.close()

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, trabalhadores)) as piscina:
        list(piscina.map(obreiro, range(max(1, trabalhadores))))

    dt = time.time() - t0
    _log("")
    for k in VEREDITOS:
        _log("   %-20s %5d" % (k, placar[k]))
    for k in ("sem_evidencia", "falha_percepcao", "falha_julgamento",
              "fora_da_escala"):
        if placar[k]:
            _log("   %-20s %5d" % (k, placar[k]))
    _log("   %d POI(s) em %.1f min · %.1f s por POI"
         % (len(lista), dt / 60, dt / max(len(lista), 1)))
    return {"alvos": len(lista), **placar, "segundos": dt}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=3)
    p.add_argument("--modelo", default=MODELO_PADRAO)
    p.add_argument("--poi", action="append", type=int,
                   help="repetível; avalia estes POIs ignorando a fila")
    p.add_argument("--refazer", action="store_true",
                   help="reavalia quem já tem veredito")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ veredito da IA — percepção cega, julgamento isolado (%s)" % a.modelo)
    r = rodar(a.area, a.limite, a.aplicar, a.trabalhadores, a.modelo,
              a.poi, a.refazer)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
