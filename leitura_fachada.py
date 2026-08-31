"""leitura_fachada.py — a leitura de fachada em quatro fases, em produção.

Substitui o motor de `avaliar_fachada.py`, que fazia UMA chamada por POI e
pedia tudo de uma vez. O processo aqui separa em perguntas isoladas:

  1. TRIAGEM   — a foto do Street View presta? (por imagem)
  2. FOTOS     — cada foto do Maps é DO ALVO?  (por foto)
  3. ANÁLISE   — o que a cena mostra e o que fazer (por POI)
  4. CAIXAS    — onde está cada coisa (sob demanda, fora deste módulo)

Isolar não é preciosismo. Enquanto as perguntas vinham juntas, o modelo
respondia a segunda por coerência com a primeira em vez de olhar a imagem; foi
separando que `mostra_atividade_compativel` passou a ser usado e o galpão que
era igreja parou de ser descartado.

O que este módulo NÃO faz, de propósito: corrigir a IA por código. O antecessor
tinha 380 linhas reescrevendo a resposta do modelo — tetos, inferências,
contagens deduzidas. Resposta errada volta para a IA revisar; código sem
discernimento não arbitra.

    .venv\\Scripts\\python leitura_fachada.py --area area_atual [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

import area_utils
import avaliar_fachada as AF
import base_comum as bc
import imagens
import anotar
import prompts_fachada as P
import endpoints

# O endpoint é da Spark rodando vLLM. Ollama saiu: recusava `-np > 1` para as
# arquiteturas `qwen3vl` e `qwen35moe`, o que matava o paralelismo e punha a
# carga inteira em 213 horas.
MODELO_PADRAO = os.environ.get("VLLM_MODELO", "qwen3vl-moe")
BASE_PADRAO = endpoints.VLLM

# 3.0.0 — a pergunta virou "há comércio aqui?" em vez de "este comércio está
# aqui?", e a leitura passou a olhar o GIRO inteiro do panorama, não só a
# visada que encara a fachada.
SCHEMA_VERSAO = "3.0.0"

# Quantas chamadas simultâneas o servidor recebe. Não é o tamanho do lote de
# POIs: um POI com 4 fotos do Maps gera 6 chamadas, e disparar 200 de uma vez
# só enfileira dentro do vLLM com o custo de manter 200 imagens em memória
# deste lado. Medido em 17/08/2026: 115 chamadas em 245 s com este teto.
CONCORRENCIA = int(os.environ.get("LEITURA_CONCORRENCIA", "20"))

# POIs por transação. Define de quanto em quanto o trabalho fica gravado — se o
# processo morrer na fachada 9.000, a retomada perde no máximo este lote.
LOTE = int(os.environ.get("LEITURA_LOTE", "24"))

# Quantas fotos do Maps entram por POI. Acima disso o ganho some: as fotos vêm
# ordenadas e a quarta já costuma ser interior ou prato de comida.
MAX_FOTOS = 4

# Quantas visadas do giro entram por ponto. Seis é o que a captura já produz —
# fachada + três giros do mesmo panorama + dois panoramas deslocados.
MAX_VISADAS = int(os.environ.get("LEITURA_VISADAS", "6"))

# Quantas visadas seguem para a ANÁLISE depois de triadas. A triagem é barata e
# a análise é cara: mandar as seis triplicaria o custo por ponto e já se viu o
# modelo se perder quando o contexto enche de imagem.
VISADAS_NA_ANALISE = 2

# Marcar os objetos nas visadas lidas. Custa uma chamada com imagem por visada
# lida — a fase mais cara depois da análise. Desligável porque nem toda carga
# precisa do desenho: quem só quer o veredito não paga por ele.
COM_CAIXAS = os.environ.get("LEITURA_CAIXAS", "1") not in ("0", "false", "")

# Teto de saída por fase. Sem ele o modelo gera até esgotar o contexto — uma
# requisição chegou a rodar 6 minutos produzindo texto que ninguém leria.
TETO = {"triagem": 400, "foto": 260, "analise": 1500, "decisor": 320, "caixas": 900}

# Ponto de partida da estimativa, medido em 17/08/2026 sobre 48 POIs com este
# lote e esta concorrência, JÁ INCLUINDO baixar as imagens do Storage. Serve só
# enquanto não há histórico no banco — a partir daí `estimar()` usa o que as
# leituras de fato levaram, que é o número que não mente.
SEG_POR_POI_MEDIDO = 6.9


# ---------------------------------------------------------------------------
# Seleção
# ---------------------------------------------------------------------------
def elegiveis(con, poligono, limit: int, refazer: bool, ids: list | None,
              incluir_ja_comerciais: bool) -> list:
    """Quem ainda não foi lido por ESTA versão do processo.

    A seleção de alvos é a de `avaliar_fachada.carregar_alvos` — ela carrega
    lições que custaram caro (o `DISTINCT ON` que cortou 44% de leitura
    repetida, o recorte por área, o filtro de quem já é comercial no cadastro).
    Reescrever seria repetir os erros.

    O que muda aqui é o teste de "já feito": a existência de qualquer anotação
    não basta, porque as 95 linhas antigas vieram de um processo diferente. O
    que conta é anotação com o `schema_versao` corrente.
    """
    alvos = AF.carregar_alvos(poligono, 0, True, con,
                              incluir_ja_comerciais=incluir_ja_comerciais,
                              ids=ids)
    # A EMPRESA DE QUEM DISPAROU limita o que vai ser lido.
    #
    # A conexão usa o papel worker, que tem BYPASSRLS — não há política de linha
    # segurando nada aqui. Sem este corte, um job disparado pela Corsan gastaria
    # a fila lendo fachadas da Columbia e gravaria anotação no tenant delas.
    # Quando o RLS existir, isto vira redundância barata; até lá, é a única
    # coisa entre um cliente e a base do outro.
    tenant = os.environ.get("CR_TENANT_ID")
    if tenant and alvos:
        with con.cursor() as cur:
            cur.execute("SELECT id FROM pois WHERE id = ANY(%s) AND tenant_id = %s",
                        ([a["poi_id"] for a in alvos], tenant))
            meus = {r[0] for r in cur.fetchall()}
        antes, alvos = len(alvos), [a for a in alvos if a["poi_id"] in meus]
        # Zerar TUDO por causa da empresa precisa ser dito, não deduzido.
        # Rodando pelo painel, `CR_TENANT_ID` é a empresa de quem clicou e bate.
        # Rodando pela linha de comando, é o valor do `.env` — que pode ser uma
        # empresa sem POI nenhum, e aí a saída seria um "0 POIs a ler" mudo, que
        # se lê como "não há trabalho" quando o caso é "olhei na empresa errada".
        if antes and not alvos:
            with con.cursor() as cur:
                cur.execute("SELECT nome FROM tenants WHERE id = %s", (tenant,))
                r = cur.fetchone()
            print(f"! os {antes} POIs elegíveis são de OUTRA empresa — "
                  f"CR_TENANT_ID aponta para {r[0] if r else tenant}, "
                  f"que não tem nenhum deles.", flush=True)
    if not refazer and alvos:
        with con.cursor() as cur:
            cur.execute("""SELECT DISTINCT poi_id FROM fachada_anotacao
                            WHERE schema_versao = %s AND poi_id = ANY(%s)""",
                        (SCHEMA_VERSAO, [a["poi_id"] for a in alvos]))
            feitos = {r[0] for r in cur.fetchall()}
        alvos = [a for a in alvos if a["poi_id"] not in feitos]
    return alvos[:limit] if limit else alvos


def contexto(con, alvos: list) -> None:
    """Anexa a cada alvo o que foi MINERADO sobre ele, não só a foto.

    CNPJ, CNAE, nota, comentário e as fotos do Maps com data. É esta camada que
    resolve o caso em que a fachada é um muro cego de 2018 e a foto do cliente
    de 2025 mostra o letreiro — sem ela a leitura pesa só a fonte mais velha.
    """
    ids = [a["poi_id"] for a in alvos]
    por_id = {a["poi_id"]: a for a in alvos}
    with con.cursor() as cur:
        cur.execute("""SELECT id, cnpj, razao_social, situacao_cadastral, cnae,
                              avaliacao, total_avaliacoes, status_horario, tenant_id
                         FROM pois WHERE id = ANY(%s)""", (ids,))
        for (pid, cnpj, razao, sit, cnae, nota, nav, hor, tenant) in cur.fetchall():
            a = por_id[pid]
            a.update(cnpj=cnpj, razao_social=razao, situacao_cadastral=sit,
                     cnae=cnae, total_avaliacoes=nav, status_horario=hor,
                     tenant_id=tenant,
                     # vírgula decimal do scraping quebra o float do lado do modelo
                     avaliacao=str(nota).replace(",", ".") if nota else None)

        # O comentário mais recente, e só ele. O prompt pesa comentário pela
        # idade RELATIVA à data da fachada; dois comentários velhos não somam
        # evidência, só gastam contexto. O ORDER BY joga "há N anos" para o fim.
        cur.execute("""SELECT DISTINCT ON (poi_id) poi_id, texto, data
                         FROM comentarios
                        WHERE poi_id = ANY(%s) AND COALESCE(texto,'') <> ''
                        ORDER BY poi_id, (data ILIKE '%%ano%%'), id DESC""", (ids,))
        for pid, texto, data in cur.fetchall():
            por_id[pid]["comentario_recente"] = f"[{data or 'sem data'}] {texto}"


def carregar_imagens(con, alvos: list) -> None:
    """Traz os BYTES de TODAS as visadas do ponto, não só a que encara a fachada.

    O panorama do Street View é 360°, e a captura já guardava o giro — `g90`,
    `g180`, `g270` (o mesmo panorama girado) e `p1`, `p2` (panoramas deslocados
    ~15 m ao longo da rua, mirando o ponto de outro ângulo). Só que nada disso
    era lido: todo o pipeline filtrava `angulo = 'facade'` e descartava o resto.

    Ler o giro inteiro é o que permite achar o comércio que está do OUTRO LADO
    da via, ou na esquina, ou atrás da câmera — casos em que a visada única
    mostrava um muro e a leitura concluía, corretamente para o que via e
    erradamente para o lugar, que não havia comércio.

    As fotos do Maps vêm por `fotos_do_poi_com_id`, que traz id e data GRUDADOS
    nos bytes. A forma antiga — buscar as datas numa consulta e os bytes noutra,
    casando por posição — trocava a data de uma foto pela da outra assim que
    uma imagem falhasse ao abrir, e a leitura pesa cada foto pela idade dela.
    """
    ids = [a["poi_id"] for a in alvos]
    por_id = {a["poi_id"]: a for a in alvos}
    with con.cursor() as cur:
        # `facade` primeiro: é a visada que encara o endereço, e quando as notas
        # empatam é ela que deve ganhar o desempate.
        cur.execute("""SELECT poi_id, id, angulo, data_captura, heading
                         FROM streetview_imgs
                        WHERE poi_id = ANY(%s)
                        ORDER BY poi_id, (angulo <> 'facade'), id DESC""", (ids,))
        for pid, sv, ang, data, head in cur.fetchall():
            v = por_id[pid].setdefault("visadas", [])
            if len(v) < MAX_VISADAS:
                v.append({"sv_id": sv, "angulo": ang, "data": data,
                          "heading": head})

    for a in alvos:
        for v in a.get("visadas", []):
            b = imagens.streetview_por_id(v["sv_id"], con)
            v["b"] = AF.limpar_interface(b) if b else None
        a["visadas"] = anotar.sem_repetidas(
            [v for v in a.get("visadas", []) if v.get("b")])
        # `img` continua sendo a fachada, porque a mira geométrica e a
        # proveniência ainda se referem a ela.
        a["img"] = next((v["b"] for v in a["visadas"] if v["angulo"] == "facade"), None)
        a["heading_fachada"] = next(
            (v.get("heading") for v in a["visadas"] if v["angulo"] == "facade"), None)

        # A PRANCHA — uma imagem, fotos separadas.
        #
        # Passei por duas tentativas erradas antes desta. Visadas soltas faziam
        # o modelo tratar a mesma esquina como imóveis diferentes. Coladas numa
        # faixa contínua, ele passou a ler a emenda como cena: reprovou pontos
        # com TELENTREGA e Farmácias São João à vista, e o grounding desenhou
        # caixas atravessando costuras. Prancha de contatos resolve os dois —
        # uma imagem, mas com vão, moldura e letra dizendo que são seis fotos.
        # AS VISADAS VÃO SEPARADAS, uma imagem por foto.
        #
        # Medido no POI 166320, mesmo prompt, mesma cena: prancha colada listou
        # os quatro comércios mas devolveu `marcador_google_visivel: False`;
        # seis imagens separadas listaram os mesmos quatro, viram o distintivo
        # do Google, e ainda gastaram MENOS token (10.396 contra 11.283).
        #
        # Compor era eu criando trabalho: a API aceita várias imagens por
        # mensagem, e o modelo trata cada uma como uma cena — que é o que elas
        # são. A prancha continua existindo, para a galeria mostrar ao humano o
        # que foi lido.
        a["quadros"] = [v["b"] for v in sorted(
            a["visadas"], key=lambda v: int(v["angulo"][1:])
            if v["angulo"].startswith("g") and v["angulo"][1:].isdigit() else 999)
            if v["angulo"].startswith("g")]
        a["ordem_prancha"] = [(f"foto {n+1}", int(v["angulo"][1:]))
                              for n, v in enumerate(sorted(
                                  (x for x in a["visadas"] if x["angulo"].startswith("g")),
                                  key=lambda v: int(v["angulo"][1:])))]

        # FOTOS REPETIDAS FORA. A mesma foto reenviada pelo Google chega com
        # outra compressão, então hash de bytes não a pega — o dHash pega. Duas
        # cópias da mesma prova ocupavam duas vagas do contexto.
        a["fotos"] = anotar.sem_repetidas(
            imagens.fotos_do_poi_com_id(a["poi_id"], con, limite=MAX_FOTOS + 2)
        )[:MAX_FOTOS]


# ---------------------------------------------------------------------------
# Chamada
# ---------------------------------------------------------------------------
async def chamar(cli, sem, modelo, sistema, usuario, schema, imgs, teto):
    """Uma pergunta ao modelo, com o portão de concorrência.

    Erro vira valor de retorno em vez de exceção: numa carga de 24 mil, uma
    imagem corrompida não pode derrubar o lote inteiro — ela vira uma linha
    com `erro` e o resto segue.
    """
    imgs = [i for i in (imgs if isinstance(imgs, (list, tuple)) else [imgs]) if i]
    async with sem:
        try:
            r = await cli.chat.completions.create(
                model=modelo, temperature=0, max_tokens=teto,
                messages=[{"role": "system", "content": sistema},
                          {"role": "user", "content":
                              [{"type": "text", "text": usuario}]
                              + [AF._img_openai(b) for b in imgs]}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "r", "strict": True, "schema": schema}})
            u = r.usage
            return (json.loads(r.choices[0].message.content),
                    (u.prompt_tokens, u.completion_tokens) if u else (0, 0))
        except Exception as e:
            return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}, (0, 0)


async def avaliar_lote(cli, modelo, alvos: list, andamento=None) -> dict:
    """As três fases sobre um lote de POIs. Devolve o placar.

    `andamento` é o que faz a linha de cada POI aparecer no terminal do painel
    ASSIM QUE ele é julgado, com o nome do estabelecimento e o veredito. Quem
    acompanha 16 mil leituras precisa auditar por amostragem enquanto elas
    saem — no fim não há o que corrigir, só o que refazer.
    """
    sem = asyncio.Semaphore(CONCORRENCIA)
    gasto = [0, 0]

    def somar(t):
        gasto[0] += t[0]
        gasto[1] += t[1]

    # FASE 1 — quais visadas prestam.
    #
    # Agora é UMA triagem por visada, não uma por ponto: seis imagens do mesmo
    # lugar, e o que interessa é quais delas dão para ler. A triagem é barata
    # (~400 tokens de saída) e é ela que evita mandar seis imagens para a
    # análise, que é a chamada cara.
    # FASE 1 — UMA TRIAGEM POR QUADRO, e o ponto vale pela melhor.
    #
    # Cheguei a julgar um quadro só, o que encara o endereço, e isso derrubava
    # o ponto inteiro quando justamente aquela foto tinha árvore na frente: o
    # POI 178746 perdeu as seis visadas porque a primeira tirou 50. O ponto não
    # é ilegível porque uma das seis pegou o poste.
    #
    # Julgar cada uma também é o que decide QUAIS seguem: mandar para a análise
    # a foto que a triagem acabou de reprovar seria pedir leitura do que não se
    # lê. A triagem é a chamada barata; a análise é a cara.
    pend = [(a, n) for a in alvos for n in range(len(a.get("quadros") or []))]
    res = await asyncio.gather(*[
        chamar(cli, sem, modelo, P.TRIAGEM_SISTEMA, P.TRIAGEM_USUARIO,
               P.TRIAGEM_SCHEMA, a["quadros"][n], TETO["triagem"])
        for a, n in pend])
    notas = {}
    for (a, n), (o, tk) in zip(pend, res):
        somar(tk)
        notas.setdefault(id(a), {})[n] = (
            (0, "recapturar", o) if "erro" in o else (*P.julgar_triagem(o), o))
    for a in alvos:
        d = notas.get(id(a)) or {}
        if not d:
            a["tri"], a["nota"], a["ver"] = None, 0, "recapturar"
            a["quadros_bons"] = []
            continue
        melhor = max(d.values(), key=lambda x: x[0])
        a["nota"], a["ver"], a["tri"] = melhor
        # os que prestam, na ordem original — a primeira é a que encara o alvo
        a["quadros_bons"] = [a["quadros"][n] for n in sorted(d)
                             if d[n][1] in ("aprovada", "revisar")]
        a["ordem_bons"] = [(a.get("ordem_prancha") or [])[n]
                           for n in sorted(d)
                           if d[n][1] in ("aprovada", "revisar")
                           and n < len(a.get("ordem_prancha") or [])]

    # FASE 2 — cada foto do Maps é do alvo?
    pend = [(a, k) for a in alvos for k in range(len(a.get("fotos", [])))]
    res = await asyncio.gather(*[
        chamar(cli, sem, modelo, P.FOTO_MAPS_SISTEMA,
               P.foto_maps_usuario(a, a["fotos"][k]["data"]),
               P.FOTO_MAPS_SCHEMA, a["fotos"][k]["b"], TETO["foto"])
        for a, k in pend])
    for (a, k), (o, t) in zip(pend, res):
        somar(t)
        a["fotos"][k].update(v=o.get("veredito", "erro"),
                             txt=o.get("texto_legivel"),
                             motivo=o.get("motivo", o.get("erro", "")))

    # QUEM SEGUE — e por que a fachada não decide sozinha.
    #
    # A triagem julga se a FACHADA serve, não se o POI serve. Deixá-la barrar
    # tudo foi o erro do `Edifício Bel Vivere`: Street View de 2024-09 com
    # árvore na frente reprovou, e com ele foram embora quatro fotos do Maps de
    # 2025-10 mostrando o prédio nítido — a fonte mais velha vetando as novas.
    passa = []
    for a in alvos:
        boas = [f for f in a.get("fotos", [])
                if f.get("v") in ("mostra_o_alvo", "mostra_atividade_compativel")][:3]
        pan_ok = bool(a.get("quadros_bons"))
        if not pan_ok and not boas:
            a["ana"] = None          # sem evidência aproveitável: não se inventa
            continue
        a["fachada_ok"], a["boas"] = bool(pan_ok), boas
        # Panorâmica reprovada na triagem NÃO entra: mandaria o modelo ler a
        # imagem que a triagem acabou de dizer que não serve.
        a["usadas"] = (a["quadros_bons"] if pan_ok else []) + [f["b"] for f in boas]
        # O manifesto diz a data de CADA fonte, porque o peso é RELATIVO: o que
        # decide não é "a foto é recente", é "a foto é mais nova que a fachada".
        # UMA LINHA POR FOTO, na ordem em que elas são anexadas. É isto que
        # impede o modelo de contar o mesmo comércio duas vezes: ele precisa
        # saber que são visadas do MESMO ponto, não endereços diferentes.
        a["manifesto"] = (
            [f"Street View do mesmo ponto — foto {n + 1} de {len(a['quadros_bons'])}, "
             f"câmera girada {g}° em relação à que encara o endereço"
             + (" (esta encara o endereço avaliado)" if g == 0 else "")
             for n, (_r, g) in enumerate(a.get("ordem_bons") or [])]
            if pan_ok else
            ["(as fotos do Street View foram DESCARTADAS por má qualidade e não "
             "estão anexadas — julgue pelas fotos abaixo)"]) + [
            f"foto do Google Maps enviada por cliente — data: {f['data']}"
            + ("  (mostra a ATIVIDADE do ramo, sem nome legível)"
               if f.get("v") == "mostra_atividade_compativel" else "")
            for f in boas]
        if a.get("comentario_recente"):
            a["manifesto"].append("(o comentário de cliente, com sua data, está "
                                  "no contexto acima)")
        passa.append(a)

    # FASE 3 — o que a cena mostra.
    #
    # Cada POI anuncia o próprio resultado ao terminar, em vez de o lote inteiro
    # aparecer de uma vez no fim: com lote de 24 a 6 s por POI, esperar o
    # fechamento é um silêncio de dois minutos e meio seguido de 24 linhas de
    # enxurrada — que é o oposto de conseguir auditar acompanhando.
    async def uma(a):
        o, t = await chamar(cli, sem, modelo, P.ANALISE_SISTEMA,
                            P.analise_usuario(a, a["manifesto"]),
                            P.ANALISE_SCHEMA, a["usadas"], TETO["analise"])
        somar(t)
        a["ana"] = o
        return a

    await asyncio.gather(*[uma(a) for a in passa])

    # FASE 3b — O DECISOR, sem imagem nenhuma.
    #
    # Quem olhou a cena não escolhe a ação. Medido em 18/08/2026 sobre 358
    # pontos: em 78 de 78 reprovações a mesma chamada tinha LISTADO comércio na
    # cena e ainda assim respondeu "não há comércio" — arrastada pela pergunta
    # "o estabelecimento procurado está aí?", que é a única coisa que a foto na
    # frente insiste em fazer perguntar.
    #
    # Aqui o julgamento recebe só o relato estruturado. Sem imagem, não há como
    # recair na busca pelo nome. É o mesmo padrão que já tinha consertado o
    # veredito visual: percepção cega, julgamento isolado.
    decidir = [a for a in passa if a.get("ana") and "erro" not in a["ana"]]
    res = await asyncio.gather(*[
        chamar(cli, sem, modelo, P.DECISOR_SISTEMA, P.decisor_usuario(a["ana"]),
               P.DECISOR_SCHEMA, [], TETO["decisor"]) for a in decidir])
    for a, (o, t) in zip(decidir, res):
        somar(t)
        # A ação vive onde o resto do código já a procura. O `por_que` do
        # decisor entra como ressalva SÓ quando a análise não deu uma: são
        # coisas diferentes — um explica a cena, o outro explica a escolha.
        c = a["ana"].setdefault("classificacao", {})
        c["acao_recomendada"] = o.get("acao_recomendada")
        c["por_que_a_acao"] = o.get("por_que")
    # O ANDAMENTO SÓ AGORA, depois de decidir. Saindo de dentro da fase 3 ele
    # lia a ação antes de ela existir e imprimia "sem_evidencia" para todo
    # mundo, enquanto o banco recebia a decisão correta — log que desmente o
    # próprio processo é pior que log nenhum.
    if andamento:
        for a in alvos:
            andamento(a)

    # FASE 4 — AS CAIXAS, só nas visadas que a análise leu.
    #
    # Deixou de ser sob demanda porque o pedido mudou: a marcação passa a
    # aparecer na galeria de toda leitura. Roda apenas sobre as visadas LIDAS —
    # marcar objeto numa imagem que a análise descartou seria desenhar sobre
    # prova que ninguém usou.
    if COM_CAIXAS:
        # AS CAIXAS SÓ NO QUADRO QUE ENCARA O ENDEREÇO, nunca na prancha.
        #
        # O grounding devolve `bbox_2d` normalizada sobre a imagem inteira. Numa
        # prancha, isso vira caixa atravessando vão e moldura, pousada em céu e
        # estacionamento — foi o que a galeria mostrou. Marcar objeto só faz
        # sentido sobre a foto onde ele está.
        alvos_cx = [a for a in passa if a.get("fachada_ok") and a.get("img")]
        res = await asyncio.gather(*[
            chamar(cli, sem, modelo, anotar.GROUNDING_SISTEMA,
                   anotar.GROUNDING_USUARIO, anotar.GROUNDING_SCHEMA,
                   a["img"], TETO["caixas"]) for a in alvos_cx])
        for a, (o, tk) in zip(alvos_cx, res):
            somar(tk)
            a["caixas"] = (o or {}).get("deteccoes") or []

    return {"tokens_in": gasto[0], "tokens_out": gasto[1],
            "analisados": len(passa), "sem_evidencia": len(alvos) - len(passa)}


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------
def _divergente(c: dict) -> tuple:
    """O comércio encontrado que NÃO é o procurado — nome e atividade.

    Sai da lista que a IA devolveu, e não de uma dedução do código: quem sabe se
    aquele letreiro é o mesmo estabelecimento com outro nome de fantasia é o
    modelo, olhando a cena. O código só escolhe, entre os divergentes, o
    primeiro com nome legível — se nenhum tiver nome, fica o primeiro mesmo, com
    a atividade, porque "oficina sem placa no número 340" já é achado.
    """
    outros = [x for x in (c.get("comercios_encontrados") or [])
              if not x.get("e_o_procurado")]
    if not outros:
        return None, None
    escolhido = next((x for x in outros if x.get("nome_lido")), outros[0])
    return escolhido.get("nome_lido"), escolhido.get("atividade")


def _status(acao: str | None) -> str:
    """A decisão da IA no vocabulário que as telas de hoje já entendem.

    `acao_recomendada` é o campo autoritativo — só `aprovar`, `reprovar` ou
    `revisar`. Mas `fachada_anotacao.status` já é lido pelos contadores do
    painel, pelos filtros do `server.py` e pelo `app.js`, com quatro valores do
    processo anterior. Gravar um vocabulário novo ali deixaria toda leitura
    nova invisível nas telas existentes sem quebrar nada visivelmente — o pior
    tipo de defeito.

    `revisar` não tem equivalente antigo e vira `pendente`: é justo o balde que
    a fila do supervisor precisa enxergar, e é o filtro que falta no painel.
    """
    return {"aprovar_especifico": "aprovado", "aprovar_divergente": "aprovado",
            "aprovar": "aprovado",          # leitura 2.0.0
            "reprovar": "reprovado", "revisar": "pendente"}.get(acao or "", "inapto")


def gravar_lote(con, alvos: list, modelo: str) -> None:
    """Tudo que foi achado vai para o banco, numa transação por lote.

    Append-only: nenhuma linha antiga é apagada. Reprocessar um POI cria uma
    anotação nova, e a leitura corrente é a mais recente por `criado_em` — o
    histórico de como a IA mudou de ideia é dado, não sujeira.

    O POI SEM EVIDÊNCIA APROVEITÁVEL também vira linha, com `status='inapto'` e
    os campos da análise vazios. Duas razões, e nenhuma é estética: "examinei e
    não havia o que ler" é um achado, e vai para o banco como qualquer outro; e
    sem a linha o `elegiveis` nunca o daria por feito, então toda nova passada
    gastaria a triagem dele de novo, para sempre.
    """
    from psycopg2.extras import Json, execute_values

    # UMA LINHA POR VISADA, não por ponto. A fase 1 agora julga as seis imagens
    # do giro; gravar só a do ponto jogaria fora justamente o que diz QUAL
    # visada prestou — que é o que explica, depois, por que a análise leu a
    # imagem de trás e não a que encara o endereço.
    # UMA linha de triagem por ponto: o que se julga agora é a panorâmica. O
    # `sv_id` gravado é o da fachada, que é a visada que ancora a faixa.
    tri = [(a["visadas"][0]["sv_id"], a["poi_id"], a["tenant_id"], "panorama",
            (a["tri"] or {}).get("tipo_de_foto", "indefinido"),
            Json((a["tri"] or {}).get("notas") or {}), a["nota"], a["ver"],
            (a["tri"] or {}).get("motivo") or (a["tri"] or {}).get("erro"),
            modelo, SCHEMA_VERSAO)
           for a in alvos if a.get("tri") is not None and a.get("visadas")]

    fot = [(f["id"], a["poi_id"], a["tenant_id"], f.get("v", "erro"),
            f.get("txt"), f.get("motivo"), f.get("data"), modelo, SCHEMA_VERSAO)
           for a in alvos for f in a.get("fotos", []) if f.get("v")]

    ana = []
    for a in alvos:
        o = a.get("ana")
        if o and "erro" in o:
            continue                    # falha de chamada: relê na próxima passada
        c = (o or {}).get("classificacao") or {}
        # o mesmo comércio visto em dois quadros do giro é UM comércio
        if c.get("comercios_encontrados"):
            c["comercios_encontrados"] = _sem_achado_repetido(c["comercios_encontrados"])
        e = (o or {}).get("elementos") or {}
        idf = (o or {}).get("identificacao") or {}
        ana.append((
            a["poi_id"], a["tenant_id"], modelo, SCHEMA_VERSAO,
            _status(c.get("acao_recomendada") if o else None),
            (o or {}).get("descricao"), c.get("alvo_encontrado"),
            c.get("posicao_na_imagem"), c.get("marcador_google_visivel"),
            c.get("tipo_cliente"), c.get("tipo_imovel"), c.get("status_ocupacao"),
            c.get("multiplas_unidades"), c.get("limite_ambiguo"),
            c.get("indicio_comercial"), c.get("atividade_economica_aparente"),
            c.get("atividade_no_alvo"), c.get("tipo_via"), c.get("confianca"),
            c.get("acao_recomendada"), c.get("o_que_sustenta"), c.get("ressalva"),
            idf.get("numero_predial"), idf.get("texto_do_letreiro"),
            Json(e), Json(idf),
            Json(c.get("comercios_encontrados") or []), _divergente(c)[0],
            # A PROVENIÊNCIA: quais imagens sustentaram este veredito e de
            # quando. Sem isto não dá para responder "ele viu a foto de 2025 ou
            # só o muro de 2018?", que é a pergunta que decide se o veredito
            # vale alguma coisa.
            Json({"fachada": a["sv_id"] if a.get("fachada_ok") else None,
                  "fachada_data": str(a.get("sv_data") or ""),
                  # QUAIS VISADAS DO GIRO a análise leu, e com que nota. Sem
                  # isto não dá para responder a pergunta que decide se o giro
                  # se paga: quando a leitura acertou, foi olhando para onde?
                  "panoramica": bool(a.get("fachada_ok")),
                  "caixas": a.get("caixas") or [],
                  # As visadas que COMPUSERAM a faixa. Nota e veredito não são
                  # mais de cada uma: a triagem julga a PANORÂMICA, que é o que
                  # a análise lê — por isso ficam ao lado, não dentro.
                  "visadas": [{"sv_id": v["sv_id"], "angulo": v["angulo"],
                               "data": str(v.get("data") or "")}
                              for v in a.get("visadas", [])],
                  "nota_panorama": a.get("nota"),
                  "veredito_panorama": a.get("ver"),
                  "fotos_maps": [{"id": f["id"], "data": f["data"],
                                  "veredito": f.get("v")} for f in a.get("boas", [])]})))

    with con.cursor() as cur:
        if tri:
            execute_values(cur, """INSERT INTO fachada_triagem
                (sv_id, poi_id, tenant_id, angulo, tipo_de_foto, notas,
                 nota_total, veredito, motivo, modelo, schema_versao)
                VALUES %s""", tri)
        if fot:
            execute_values(cur, """INSERT INTO foto_maps_triagem
                (imagem_id, poi_id, tenant_id, veredito, texto_legivel, motivo,
                 data_imagem, modelo, schema_versao) VALUES %s""", fot)
        if ana:
            execute_values(cur, """INSERT INTO fachada_anotacao
                (poi_id, tenant_id, modelo, schema_versao, status,
                 descricao, alvo_encontrado, posicao_na_imagem,
                 marcador_google_visivel, tipo_cliente, tipo_imovel,
                 status_ocupacao, multiplas_unidades, limite_ambiguo,
                 indicio_comercial, atividade_economica_aparente,
                 atividade_no_alvo, tipo_via, confianca, acao_recomendada,
                 veredito_justificativa, ressalva, numero_lido,
                 texto_do_letreiro, elementos, identificacao,
                 comercios_encontrados, comercio_divergente,
                 imagens_usadas) VALUES %s
                RETURNING id, poi_id, tenant_id, acao_recomendada,
                          comercio_divergente, comercios_encontrados""", ana)
            _enfileirar_divergentes(cur, cur.fetchall())
        n = _criar_pois_divergentes(cur, alvos)
        if n:
            print(f"   + {n} estabelecimento(s) novo(s) no mapa (fonte ia_fachada)",
                  flush=True)
    con.commit()


# A coordenada do achado divergente, deslocada conforme ONDE ele apareceu.
#
# O comércio lido na esquerda do quadro não está na coordenada do POI original —
# está ao lado dele. 12 m é a largura típica de uma testada de lote em quarteirão
# comercial; não é medição, é aproximação declarada, e é por isso que estes
# pontos nascem com `fonte='ia_fachada'` e precisam de olho humano antes de
# virar cadastro. Empilhar todos na mesma coordenada faria três achados de uma
# quadra virarem um pino só no mapa.
_DESLOCA = {"achei_na_esquerda": -12.0, "achei_na_direita": 12.0,
            "achei_no_centro": 0.0, "nao_achei": 0.0}


def _coord_do_achado(lat, lng, heading, posicao):
    """Desloca perpendicularmente à mira da câmera, para o lado em que foi visto."""
    import math
    d = _DESLOCA.get(posicao or "", 0.0)
    if not d or lat is None or heading is None:
        return lat, lng
    # perpendicular ao rumo câmera→imóvel: +90° é a direita de quem olha
    rumo = math.radians((heading + 90) % 360)
    dlat = (d * math.cos(rumo)) / 111320
    dlng = (d * math.sin(rumo)) / (111320 * math.cos(math.radians(lat)))
    return lat + dlat, lng + dlng


def _mesmo_comercio(a: dict, b: dict) -> bool:
    """Dois achados da mesma cena são o MESMO estabelecimento?

    A dedupe de imagem não alcança isto: as fotos eram diferentes de verdade —
    dois quadros do giro, dois ângulos da mesma loja. O que se repete é o
    ACHADO, não o arquivo.

    O caso que expôs: `TELENTREGA` saiu duas vezes do mesmo POI de origem
    porque o letreiro grande da farmácia aparecia em dois quadros do giro.
    Duas linhas na fila do supervisor para uma loja só.

    Bate quando o nome normalizado coincide, OU quando ambos estão sem nome e a
    atividade é a mesma — nesse caso são dois "há comércio ali" indistinguíveis,
    e criar dois pontos seria inventar um estabelecimento.
    """
    import unicodedata

    def n(s):
        s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                    if unicodedata.category(c) != "Mn")
        return " ".join(s.split())

    na, nb = n(a.get("nome_lido")), n(b.get("nome_lido"))
    if na and nb:
        return na == nb or na in nb or nb in na
    if not na and not nb:
        return n(a.get("atividade")) == n(b.get("atividade"))
    return False


def _sem_achado_repetido(lista: list) -> list:
    """A lista de comércios da cena, sem o mesmo estabelecimento duas vezes.

    Preserva o primeiro — que costuma ser o de posição mais central, porque a
    ordem vem de como o modelo varreu a imagem.
    """
    saida = []
    for x in lista or []:
        if not any(_mesmo_comercio(x, y) for y in saida):
            saida.append(x)
    return saida


def _criar_pois_divergentes(cur, alvos) -> int:
    """O comércio divergente vira PONTO NOVO no mapa, não só uma anotação.

    Pedido do usuário em 18/08/2026: "deve gerar novos estabelecimentos
    comerciais com coordenada aproximada e dados trazidos da própria imagem".
    Faz sentido — um comércio que existe na rua e não está no cadastro é um
    estabelecimento, não um comentário sobre outro estabelecimento.

    Três cuidados que a origem `ia_fachada` carrega:
      · `match_valido = false` — ele NÃO casou com imóvel do cadastro, e entrar
        como válido o misturaria com o que já foi conferido;
      · a coordenada é APROXIMADA, deslocada pelo lado em que foi visto;
      · `place_id` nulo, porque não veio do Google — veio de uma parede.

    A dedupe é por nome + POI de origem: reler a mesma quadra não deve criar o
    mesmo comércio de novo.
    """
    novos = []
    for a in alvos:
        o = a.get("ana") or {}
        c = o.get("classificacao") or {}
        if c.get("acao_recomendada") != "aprovar_divergente":
            continue
        cam = a.get("heading_fachada")
        for x in _sem_achado_repetido(c.get("comercios_encontrados") or []):
            if x.get("e_o_procurado"):
                continue
            nome = (x.get("nome_lido") or "").strip()
            # SEM NOME TAMBÉM ENTRA, com marca de pendência.
            #
            # Antes eu descartava: "sem nome não há o que cadastrar". Só que o
            # achado sem nome é justamente o que a segunda passada resolve — o
            # `identificar_divergente.py` recaptura o ponto com fov fechado e
            # tenta ler o letreiro de perto. Descartar aqui matava o caso antes
            # de ele chegar lá, e um comércio existente ficava fora da base por
            # não ter placa legível de longe.
            if not nome:
                if not (x.get("atividade") or "").strip():
                    continue    # sem nome E sem atividade não descreve nada
                nome = "sem nome — a identificar"
            la, lo = _coord_do_achado(a["lat"], a["lng"], cam, x.get("posicao"))
            novos.append((nome, x.get("atividade"), la, lo, a.get("endereco"),
                          a["tenant_id"], a["poi_id"]))
    if not novos:
        return 0
    from psycopg2.extras import execute_values
    execute_values(cur, """
        INSERT INTO pois (nome, categoria, maps_lat, maps_lng, lat_origem,
                          lng_origem, endereco, tenant_id, fonte, status,
                          match_valido, descoberto_de)
        SELECT v.nome, v.cat, v.la::float8, v.lo::float8, v.la::float8,
               v.lo::float8, v."end", v.tenant::uuid,
               'ia_fachada', 'novo', false, v.origem::int
          FROM (VALUES %s) AS v(nome, cat, la, lo, "end", tenant, origem)
         WHERE NOT EXISTS (SELECT 1 FROM pois q
                            WHERE q.fonte = 'ia_fachada'
                              AND q.descoberto_de = v.origem::int
                              AND lower(q.nome) = lower(v.nome))""", novos)
    return len(novos)


def _enfileirar_divergentes(cur, gravadas) -> None:
    """Comércio divergente vira item de fila, não fica só anotado.

    A decisão é de outra natureza: no fluxo normal o supervisor CONFIRMA um
    cadastro que já existe; aqui ele decide se um estabelecimento que ninguém
    conhecia entra na base do cliente. Por isso fila própria — e por isso a
    inserção é `ON CONFLICT DO NOTHING`: o índice único deixa um POI ter no
    máximo um item pendente, senão reler a base faria o mesmo endereço voltar
    para a fila a cada passada.

    O nome e a atividade são COPIADOS para a fila em vez de lidos por join. A
    anotação pode ser refeita amanhã, e o que estava sendo julgado não pode
    mudar embaixo de quem julga.
    """
    itens = []
    for (aid, poi, tenant, acao, nome, comercios) in gravadas:
        if acao != "aprovar_divergente":
            continue
        _n, atividade = _divergente({"comercios_encontrados": comercios})
        itens.append((poi, aid, tenant, nome, atividade))
    if itens:
        from psycopg2.extras import execute_values
        execute_values(cur, """INSERT INTO atribuicao_divergente
            (poi_id, anotacao_id, tenant_id, nome_lido, atividade) VALUES %s
            ON CONFLICT DO NOTHING""", itens)


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------
def estimar(n: int, con=None) -> dict:
    """Quanto tempo, ANTES de disparar — medido, não estimado no chute.

    O custo em dólar sumiu da conta porque o modelo é local: o que o painel
    precisa mostrar agora é HORA. E a taxa não é constante fixa no código, e sim
    o que as últimas leituras de fato levaram — a Spark divide GPU com outros
    trabalhos e a taxa muda conforme o que mais estiver rodando lá.

    Sem histórico suficiente, cai no valor medido em 17/08/2026: 48 POIs, lote
    de 24, concorrência 16.
    """
    seg = SEG_POR_POI_MEDIDO
    fonte = "medição de 17/08/2026"
    if con is not None:
        with con.cursor() as cur:
            # Segundos por POI dentro de cada lote já gravado. `count > 3`
            # descarta o lote-relâmpago de um POI só, que distorce para baixo.
            cur.execute("""
                SELECT avg(s) FROM (
                  SELECT extract(epoch from (max(criado_em) - min(criado_em)))
                         / greatest(count(*), 1) AS s
                    FROM fachada_anotacao
                   WHERE schema_versao = %s AND criado_em > now() - interval '30 days'
                   GROUP BY date_trunc('minute', criado_em)
                  HAVING count(*) > 3) t""", (SCHEMA_VERSAO,))
            r = cur.fetchone()
            if r and r[0] and float(r[0]) > 0:
                seg, fonte = float(r[0]), "histórico das últimas leituras"
    return {"pois": n, "modelo": MODELO_PADRAO, "local": True,
            "usd": 0.0, "usd_por_poi": 0.0,
            "seg_por_poi": round(seg, 1), "fonte_da_taxa": fonte,
            "pois_min": round(60 / seg, 1) if seg else 0,
            "horas": round(n * seg / 3600, 1)}


async def rodar(args) -> int:
    from collections import Counter

    from openai import AsyncOpenAI

    con = bc.conectar()
    poligono = area_utils.carregar_area(args.area) if args.area else None
    alvos = elegiveis(con, poligono, args.limit, args.refazer, args.ids,
                      args.incluir_ja_comerciais)
    total = len(alvos)
    # O PAINEL SÓ LIGA A BARRA QUANDO O PROCESSO SE ANUNCIA.
    #
    # `_emitir_progresso` no server.py escolhe de onde tirar os números pela
    # FASE, e a fase vem desta linha. Sem ela a leitura caía no ramo genérico,
    # que conta pelo JSON do watcher — e a leitura não escreve JSON nenhum,
    # grava direto em `fachada_anotacao`. Resultado: o terminal rolava com os
    # nomes e a barra ficava parada em zero, como se nada estivesse andando.
    print("⟦fase⟧ fachada", flush=True)
    print(f"{total} POIs a ler · modelo {args.modelo} · "
          f"concorrência {CONCORRENCIA} · lote {LOTE}", flush=True)
    if not total:
        con.close()
        return 0

    cli = AsyncOpenAI(base_url=args.base, api_key="x", timeout=1800, max_retries=1)
    placar, t0 = Counter(), time.time()
    andado = [0]        # lista porque o fechamento abaixo precisa escrever nele

    def andamento(a):
        """A linha que o painel lê E que a pessoa audita.

        O formato não é livre: `POIs n/total` é o que move a barra de progresso
        (`_RES_PROG` no server.py) e `aprovar N | revisar N | reprovar N | sem
        evidência N` é o que alimenta os quatro cartões ao vivo (`_RE_AV`).
        Meu formato anterior — `24/16621 6.3s/POI` — não casava com nenhum dos
        dois, e por isso o contador em tempo real havia sumido do painel.

        O NOME vem no fim, como na fase do Street View: é ele que deixa auditar
        por amostragem enquanto as 16 mil saem.
        """
        andado[0] += 1
        acao = ((a.get("ana") or {}).get("classificacao") or {}).get(
            "acao_recomendada") or "sem_evidencia"
        placar[acao] += 1
        nome = str(a.get("nome") or "(sem nome)")[:44]
        # `aprovar` no log soma as duas formas: o painel tem quatro cartões e
        # a divisão específico/divergente aparece na lista, não no placar ao
        # vivo. O nome no fim é o que permite auditar por amostragem.
        aprov = placar["aprovar_especifico"] + placar["aprovar_divergente"]
        print(f"🏠 POIs {andado[0]}/{total} | aprovar {aprov} | "
              f"revisar {placar['revisar']} | reprovar {placar['reprovar']} | "
              f"sem evidência {placar['sem_evidencia']} | {nome} → {acao}",
              flush=True)

    for k in range(0, total, LOTE):
        bloco = alvos[k:k + LOTE]
        contexto(con, bloco)
        carregar_imagens(con, bloco)
        try:
            m = await avaliar_lote(cli, args.modelo, bloco, andamento)
        except Exception as e:                      # falha do lote, não da carga
            print(f"   ! lote {k}-{k+len(bloco)} falhou: {type(e).__name__}: {e}",
                  flush=True)
            continue
        gravar_lote(con, bloco, args.modelo)
        placar["tokens_in"] += m["tokens_in"]
        placar["tokens_out"] += m["tokens_out"]
        feitos = andado[0]
        # As imagens do lote saem da memória agora; sem isto a carga inteira
        # ficaria residente e 24 mil fachadas não cabem.
        for a in bloco:
            a.pop("img", None)
            a.pop("usadas", None)
            for f in a.get("fotos", []):
                f.pop("b", None)
        deco = (time.time() - t0) / max(feitos, 1)
        print(f"   ⏱ {deco:.1f}s/POI · restam {(total-feitos)*deco/3600:.1f}h "
              f"· gravado até aqui: {feitos}", flush=True)

    feitos = andado[0]
    con.close()
    print(f"\nfim — {feitos} POIs em {(time.time()-t0)/60:.0f} min · "
          f"{placar['tokens_in']:,} tokens de entrada, "
          f"{placar['tokens_out']:,} de saída", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--area", default=None, help="nome da área; sem isto, base inteira")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--modelo", default=MODELO_PADRAO)
    p.add_argument("--base", default=BASE_PADRAO)
    p.add_argument("--refazer", action="store_true",
                   help="relê quem já tem anotação desta versão")
    p.add_argument("--incluir-ja-comerciais", action="store_true")
    p.add_argument("--ids", type=lambda s: [int(x) for x in s.split(",")],
                   default=None, help="POIs específicos, separados por vírgula")
    args = p.parse_args()
    print(f"[{datetime.now():%H:%M:%S}] leitura_fachada {SCHEMA_VERSAO}", flush=True)
    return asyncio.run(rodar(args))


if __name__ == "__main__":
    sys.exit(main())
