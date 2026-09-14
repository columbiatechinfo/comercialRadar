# -*- coding: utf-8 -*-
"""Avaliacao ENXUTA da ligacao — o processo do dono do produto de 12/09/2026.

POR QUE EXISTE AO LADO DE `avaliar_ligacao`. O processo anterior mandava o
dossie inteiro, ate sete imagens e a pagina da busca lida por outra chamada da
IA, e pedia um JSON com descricao, numeros lidos, fachadas, medidores, fichas
e registros: ~5.700 tokens de entrada e ~900 gerados, 307 s por avaliacao com
a Spark cheia. Provado em 12/09/2026 em 25 a 60 ligacoes de Canoas:

    processo anterior   307 s por chamada
    enxuto, pagina como imagem   19 avaliacoes/min, 69 s
    enxuto, pagina como texto    33 avaliacoes/min, 36 s (25 ao mesmo tempo)

O DESENHO, do dono do produto:
- no maximo 5 fotos, leves: as quatro visadas de rua e a publicada por outra
  fonte (a do Google Maps nao tem data no banco);
- a pagina da busca web vai como TEXTO, o que o navegador entregou
  (`busca_web.texto`); a pagina antiga, que so tem a leitura da IA, vai como a
  lista do que a leitura achou;
- prompt curto: quais registros sao aderentes ao endereco do cadastro, quais se
  confirmam, quais nao combinam com a maioria; o motivo e o veredito;
- regra de unidade: registro sem complemento, com rua e numero iguais, conta
  como da instalacao; complemento diferente nao combina; so e duvida fundada o
  registro que outra instalacao tambem disputa e que nada prende a esta.

Reaproveita de `avaliar_ligacao` a fila, a gravacao do veredito e o descarte
de quem "nao combina" (que o casamento por endereco depois tenta religar).
Uso: python avaliar_enxuto.py --cidade Canoas --exigir-busca-web --vinculo-novo --trabalhadores 80 --aplicar
"""
import argparse
import base64
import collections
import io
import json
import os
import threading
import time

from PIL import Image

import avaliar_ia as ia
import avaliar_ligacao as al
import base_comum as bc
import checagem_veredito as cv
import descrever_imagens as di
import dossie_ligacao as dl
import imagens

LARGURA_FOTO = 640
TEXTO_MAX = 7000
#: NO MAXIMO 16 LIGACOES GRANDES (mais de `al.GRANDE` POIs) na Spark ao mesmo
#: tempo: sem teto, as 80 vagas viram 80 predios de 4 a 10 min cada (12/09/2026).
MAX_GRANDES = 16
VEREDITOS = ("aprovado", "reprovado", "revisao_humana")
#: O PROCESSO QUE FICA GRAVADO NA PERCEPCAO. Desde 13/09/2026 (noite) a IA diz em
#: campo proprio se as fotos mostram o comercio (`fotos`) e quais resultados da
#: busca usou (`busca`), e o motivo nao tem mais limite de frases — pedido do dono
#: do produto para a tela SEEK so pintar de verde a imagem e a busca que
#: confirmaram. O julgamento anterior a isso e rejulgado (`--prompt-antigo`).
PROCESSO = "enxuto de 13/09/2026 (fotos e busca em campo próprio)"
#: `--saida DIR`: grava cada julgamento (e as fotos) numa pasta, para a galeria
#: de validacao — o lote de conferencia roda sem `--aplicar`.
SAIDA = None

PROMPT = """Você confere se um imóvel cobrado como RESIDENCIAL tem comércio ou serviço funcionando nele.

Você recebe: os dados do cadastro da instalação; os registros candidatos (estabelecimentos que bases independentes situam neste endereço); até 5 fotos (quatro de rua, do mesmo ponto em quatro direções — a mira verde marca a direção da coordenada do registro, que pode ter alguns metros de erro —, e uma publicada por outra fonte); e os resultados de buscas na web pelo endereço, só os que citam a rua e o número desta instalação.

Olhe todos os dados e responda, nesta ordem:
1. Quais registros são aderentes ao endereço do cadastro — rua, número, complemento, bairro — e, destes, quais se confirmam pelas fotos, pela busca ou por outro registro. Em endereço com várias unidades (o cadastro traz complemento, como CASA 02 ou APTO 3): o registro com o mesmo complemento é desta instalação; o registro sem complemento, com rua e número iguais, também conta como desta instalação; o registro com complemento diferente é de outra unidade e não combina. Só é dúvida fundada o registro que também é candidato de outras instalações e que nada — complemento, foto ou busca — prende a esta.
2. Quais registros não combinam com a maioria dos registros e com os dados desta instalação.
3. O motivo do veredito e o veredito:
   - "aprovado" se ao menos um registro de comércio ou serviço pertence a esta instalação;
   - "reprovado" se nenhum pertence;
   - "revisao_humana" só quando a dúvida é a qual instalação o registro pertence — a unidade do número —, e o motivo diz qual é. Dúvida sobre se o negócio funciona não é revisão: decida pelas provas.
4. As fotos: "confirmam" é true só se alguma foto MOSTRA o comércio ou serviço — placa, letreiro, vitrine, porta de loja, fachada com o nome ou a atividade do registro. Casa, portão ou muro sem sinal de comércio é false. "quais" são os números, na lista de fotos, das que mostram; "o_que_mostram" diz o que se vê nelas, ou por que as fotos não provam.
5. A busca: liste os resultados que você usou. "confirma" é true só quando o resultado traz o nome, o telefone, o CNPJ ou a atividade DO REGISTRO junto do endereço desta instalação; resultado de outro negócio no mesmo endereço é false. Resultado que você não usou fica fora; sem nenhum, a lista é vazia.
Templo, igreja, associação e escola não são comércio nem serviço. CNPJ ou MEI com atividade de comércio ou serviço registrada é negócio, mesmo com nome de pessoa. A foto de rua mostra a data em que foi tirada, e não hoje. A ficha do lugar no painel do Google, ou um resultado da busca, com o nome, o endereço desta instalação e horário ou telefone, confirma o registro. Foto de rua sem sinal de comércio NÃO desmente uma confirmação: muito comércio e serviço funciona em casa comum, e a foto não pesa mais que as outras provas. Resultado da busca que fala de outro endereço não conta.

Responda SOMENTE um JSON:
{"aderentes": [{"poi": <número>, "confirmado": true|false, "por": "<o que confirma o registro, ou o que falta>"}],
 "nao_combinam": [{"poi": <número>, "por": "<até 12 palavras>"}],
 "fotos": {"confirmam": true|false, "quais": [<número da foto>], "o_que_mostram": "<o que se vê>"},
 "busca": [{"motor": "DuckDuckGo|Yahoo|Google", "resultado": <número do resultado>, "poi": <número do registro>, "confirma": true|false, "casa": "<o que casa com o registro, ou o que não casa>"}],
 "motivo": "<o motivo do veredito, em detalhe: o que os registros, as fotos e a busca mostraram, o que pesou na decisão e por quê>",
 "veredito": "aprovado|reprovado|revisao_humana"}

────────────────────────────────────────
"""


def _jpeg_leve(b, largura=LARGURA_FOTO, q=70):
    im = Image.open(io.BytesIO(bytes(b))).convert("RGB")
    if im.width > largura:
        im = im.resize((largura, int(im.height * largura / im.width)), Image.LANCZOS)
    s = io.BytesIO()
    im.save(s, "JPEG", quality=q)
    return s.getvalue()


#: OS MOTORES DA BUSCA WEB desde 13/09/2026: DuckDuckGo e Yahoo, e o Google so
#: na reserva (`buscar_web.py`). As buscas feitas DENTRO DO GOOGLE MAPS, de 12 a
#: 13/09, NAO ENTRAM: o Maps devolvia os lugares da regiao, de outras ruas e
#: numeros, e a IA chegou a aprovar por empresa do vizinho. So conta linha com
#: `resultados` (o formato novo, ja filtrado pelo endereco da instalacao).
MOTORES_DA_BUSCA = ("duckduckgo", "yahoo", "google")


def _texto_da_busca(cur, ligacao):
    """(consulta, texto): o que cada motor achou NO ENDERECO da instalacao. O texto
    ja vem filtrado de `buscar_web` — resultado de outro endereco nunca chega aqui."""
    cur.execute("""select distinct on (motor) motor, consulta, texto from radar_comercial.busca_web
                    where ligacao = %s and tipo = 'endereco' and motor = any(%s) and not bloqueado
                      and resultados is not null and texto is not null
                    order by motor, feito_em desc""", (str(ligacao), list(MOTORES_DA_BUSCA)))
    linhas = sorted(cur.fetchall(), key=lambda r: MOTORES_DA_BUSCA.index(r[0]))
    if not linhas:
        return None, None
    return linhas[0][1], "\n\n".join(t for _m, _c, t in linhas)[:TEXTO_MAX]


def montar(con, ligacao):
    """(dados_do_prompt, fotos_jpeg, rotulos, ids, n_fontes) ou (None, ...) sem POI."""
    return montar_com_refs(con, ligacao)[:5]


def montar_com_refs(con, ligacao):
    """O mesmo que `montar`, e mais DE QUEM E CADA FOTO: [{"poi", "tipo"}], na ordem
    em que foram para a IA. A tela SEEK marca no carrossel a foto que a IA disse
    que mostra o comercio (dono do produto, 13/09/2026)."""
    cur = con.cursor()
    cur.execute("""select coalesce(end_ligacao,''), coalesce(categoria,''), coalesce(nom_bairro,'')
                     from resources_root.cadastro_corsan where num_ligacao::text = %s""", (str(ligacao),))
    cad = cur.fetchone()
    if not cad:
        return None, [], [], [], 0, []
    end_l, cat, bairro = cad
    cur.execute("""select p.id, lower(coalesce(p.fonte,'')), coalesce(p.nome,''), coalesce(p.categoria,''),
                          coalesce(p.endereco,''), coalesce(p.telefone,''), coalesce(p.cnpj,''),
                          m.total_avaliacoes, rd.bruto->>'data_inicio', rd.complemento,
                          (select count(distinct l2.ligacao) from radar_comercial.ligacao_poi l2
                            where l2.poi_id = p.id and l2.descartado_em is null and l2.ligacao <> %s)
                     from radar_comercial.ligacao_poi lp
                     join radar_comercial.pois p on p.id = lp.poi_id
                     left join radar_comercial.maps_data m on m.poi_id = p.id
                     left join radar_comercial.receita_data rd on rd.poi_id = p.id
                    where lp.ligacao = %s and lp.descartado_em is null and p.fundido_em is null""",
                (str(ligacao), str(ligacao)))
    regs, ids, fontes = [], [], set()
    for pid, fonte, nome, catp, endp, tel, cnpj, aval, abertura, compl, outras in cur.fetchall():
        ids.append(pid)
        fontes.add(fonte)
        partes = [x for x in (catp, endp, ("complemento " + compl) if compl else "sem complemento",
                              ("tel " + tel) if tel else "", ("CNPJ " + cnpj) if cnpj else "",
                              ("aberto em %s/%s" % (abertura[4:6], abertura[:4])) if abertura and len(abertura) >= 6 else "",
                              ("%s avaliações no Google" % aval) if aval else "",
                              ("candidato também de %d outra(s) instalação(ões)" % outras) if outras else "") if x]
        regs.append("#%s [%s] %s · %s" % (pid, fonte, nome, " · ".join(partes)))
    if not ids:
        return None, [], [], [], 0, []
    # AS FOTOS: as quatro de rua do POI mais proximo do medidor e a primeira
    # publicada — as mesmas que o dossie escolhe, sem o texto dele.
    _t, imgs, tipos, _r = dl.montar(con, ligacao, ia, imagens, busca_web=None)
    sv = [(b, t) for b, t in zip(imgs, tipos) if t.startswith("sv_")][:4]
    pub = [(b, t) for b, t in zip(imgs, tipos) if not t.startswith("sv_")][:1]
    fotos = [_jpeg_leve(b) for b, t in sv + pub]
    rot = [t if t.startswith("sv_") else "foto publicada no Google (sem data)" for b, t in sv + pub]
    _r = _r or {}
    refs = [{"poi": _r.get("fonte_das_visadas"), "tipo": t.split(" ")[0]} for _b, t in sv] \
        + [{"poi": _r.get("fonte_das_fotos"), "tipo": "foto publicada"} for _b, _t2 in pub]
    consulta, texto = _texto_da_busca(cur, ligacao)
    dados = ("INSTALAÇÃO: %s · categoria %s · bairro %s\n\nREGISTROS CANDIDATOS:\n%s\n\nFOTOS, nesta ordem:\n%s\n\n"
             "TEXTO DA BUSCA NA WEB%s:\n%s"
             % (end_l, cat, bairro, "\n".join(regs), "\n".join("%d. %s" % (i + 1, r) for i, r in enumerate(rot))
                or "(nenhuma foto)", (" (consulta \"%s\", DuckDuckGo e Yahoo)" % consulta) if consulta else "",
                texto or "(não houve busca na web para esta instalação)"))
    return dados, fotos, rot, ids, len(fontes), refs


def uma(poco, ligacao, modelo, placar, trava, aplicar):
    t0 = time.time()
    with poco.pegar() as con:
        dados, fotos, rot, ids, n_fontes, refs = montar_com_refs(con, ligacao)
    if dados is None:
        with trava:
            placar["sem_poi"] += 1
        return
    if al.PULAR_SEM_IMAGEM and not fotos:
        with trava:
            placar["sem_imagem_fica_para_o_fim"] += 1
        return
    # O TETO CRESCE COM OS REGISTROS: a lista de aderentes de um predio grande
    # passa dos 900 tokens. A base subiu para 1.600 em 13/09/2026: o motivo
    # deixou de ter limite de frases, e as fotos e a busca ganharam campo.
    # 14/09/2026: com o "por" de cada aderente sem limite, 33 respostas vieram
    # cortadas no meio do JSON (4.683 caracteres com 1.600 + 80 por POI).
    teto = min(16000, 3000 + 200 * len(ids))  # predio de 140 POIs cortava o JSON em 3.000 (12/09/2026)
    # O CONTEXTO DA SPARK E DE 32.768 TOKENS (prompt + resposta): passar disso o vLLM
    # recusa com 400. 2 CARACTERES POR TOKEN, e nao 2,8: a lista de registros e densa
    # de CNPJ, telefone e numero, e com 2,8 os predios de 130 a 177 POIs (27 a 40 mil
    # caracteres so de registros) passavam do contexto (14/09/2026). 1.000 por foto.
    teto = max(1500, min(teto, 32768 - int(len(PROMPT + dados) / 2.0) - 1000 * len(fotos) - 500))
    try:
        # O TEMPO SEGUE O TAMANHO DA RESPOSTA (14/09/2026): com 120 julgamentos ao
        # mesmo tempo a Spark gera ~3 tokens/s para cada um, e com o motivo sem
        # limite o predio passava dos 40 s por POI e morria em "timed out".
        r = di._chat_local(modelo, PROMPT + dados, [base64.b64encode(b).decode() for b in fotos],
                           max_tokens=teto, timeout=max(al.TIMEOUT, min(7200, max(40 * len(ids), int(teto / 2.5)))))
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha"] += 1
            al._log("   %-10s FALHOU: %s" % (ligacao, str(e)[:70]))
        return
    r = dict(r or {})
    v = str(r.get("veredito") or "").strip().lower()
    if v not in VEREDITOS:
        with trava:
            placar["fora_da_escala"] += 1
        v = "reprovado"
    # QUEM NAO COMBINA vira "de outro endereco": o descarte e o revinculo sao os
    # mesmos do processo anterior (`marcar_intrusos`, que nunca descarta todos).
    r["pois_de_outro_endereco"] = [{"poi": x.get("poi"), "porque": x.get("por")}
                                   for x in (r.get("nao_combinam") or []) if isinstance(x, dict)]
    r["justificativa"] = r.get("motivo")
    # A CHECAGEM DO CODIGO (dono do produto, 12/09/2026): registro que nao vale
    # nao aprova, e a aprovada so por MEI vai para revisao humana. A regra de
    # um POI por instalacao roda no fim da rodada, sobre todas as aprovadas.
    checagem = None
    if v == "aprovado":
        with poco.pegar() as con:
            v, checagem = cv.checar_uma(con, ligacao, v, r, ids)
        if checagem:
            checagem["justificativa_ia"] = r.get("justificativa")
            if v != "aprovado":
                r["justificativa"] = "[checagem: %s] %s" % (checagem.get("porque"), r.get("motivo") or "")
    percepcao = {"processo": PROCESSO, "dados": dados, "fotos": rot, "fotos_ref": refs, "resposta": r,
                 "ids": ids}
    if checagem:
        percepcao["checagem"] = checagem
    resumo = {"pois": len(ids), "fontes": n_fontes, "ids": ids}
    fora = 0
    if SAIDA:
        with trava:
            with open(os.path.join(SAIDA, "resultado.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ligacao": ligacao, "veredito": v, "resposta": r, "dados": dados,
                                    "fotos": rot, "fotos_ref": refs, "segundos": round(time.time() - t0, 1)},
                                   ensure_ascii=False) + "\n")
            for i, b in enumerate(fotos):
                open(os.path.join(SAIDA, "%s_%d.jpg" % (ligacao, i + 1)), "wb").write(b)
    if aplicar:
        with poco.pegar() as con:
            al.gravar(con, ligacao, v, r, percepcao, resumo, modelo, len(fotos), time.time() - t0)
            fora = al.marcar_intrusos(con, ligacao, r, set(ids), modelo)
    with trava:
        placar[v] += 1
        placar["poi_de_outro_endereco"] += fora
        al._log("   %-10s %-15s %d POIs · %d fotos · %.0fs · %s"
                % (ligacao, v, len(ids), len(fotos), time.time() - t0, str(r.get("motivo") or "")[:70]))


def feitas_na_rodada(placar):
    return sum(placar[v] for v in VEREDITOS) > 0


def rodar(limite, aplicar, trabalhadores, modelo, ligacoes, cidade, exigir_busca, vinculo_novo,
          adiar_grandes=0, so_grandes=0, julgar_sem_foto=False):
    # JULGAR SEM FOTO (dono do produto, 14/09/2026): a ligacao cujo registro mais
    # perto fica a mais de `dossie_ligacao.RAIO_DA_FOTO_M` do hidrometro nunca tera
    # foto no dossie — capturar a foto do POI nao resolve. Com a flag, ela e julgada
    # so com registros e busca, em vez de ficar para o fim para sempre.
    al.PULAR_SEM_IMAGEM = bool(exigir_busca) and not julgar_sem_foto
    con = bc.conectar()
    alvos = al.fila(con, 0 if so_grandes else limite, False, ligacoes, sem_catalogo=True,
                    exigir_busca=exigir_busca, vinculo_novo=vinculo_novo, cidade=cidade,
                    adiar_grandes=adiar_grandes)
    if so_grandes:
        # O LACO DOS PREDIOS: so as ligacoes com mais de `so_grandes` POIs.
        cur = con.cursor()
        cur.execute("""select lp.ligacao, count(*) from radar_comercial.ligacao_poi lp
                         join radar_comercial.pois p on p.id = lp.poi_id
                        where lp.ligacao = any(%s) and lp.descartado_em is null and p.fundido_em is null
                        group by 1 having count(*) > %s""", ([str(x) for x in alvos], so_grandes))
        grandes_ = {str(r[0]) for r in cur.fetchall()}
        alvos = [x for x in alvos if str(x) in grandes_]
        if limite:
            alvos = alvos[:limite]
    con.close()
    al._log("▶ avaliação ENXUTA — até 5 fotos, a página da busca em texto, prompt curto")
    al._log("   %d ligação(ões) na fila" % len(alvos))
    if not alvos:
        return {"alvos": 0}
    placar = collections.Counter()
    trava = threading.Lock()
    poco = al.Poco(al.CONEXOES)
    fila_ = list(alvos)
    trava_fila = threading.Lock()
    # QUANTOS POIS CADA UMA TEM, de uma vez: e o que separa a grande da pequena.
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select lp.ligacao, count(*) from radar_comercial.ligacao_poi lp
                     join radar_comercial.pois p on p.id = lp.poi_id
                    where lp.ligacao = any(%s) and lp.descartado_em is null and p.fundido_em is null
                    group by 1""", ([str(x) for x in fila_],))
    npoi = {str(l): n for l, n in cur.fetchall()}
    con.close()
    grandes = [0]
    t0 = time.time()

    def trabalhador():
        while True:
            with trava_fila:
                if not fila_:
                    return
                i = 0
                if grandes[0] >= MAX_GRANDES:
                    i = next((k for k, x in enumerate(fila_) if npoi.get(str(x), 0) <= al.GRANDE), 0)
                lig = fila_.pop(i)
                grande = npoi.get(str(lig), 0) > al.GRANDE
                if grande:
                    grandes[0] += 1
            try:
                uma(poco, lig, modelo, placar, trava, aplicar)
            except Exception as e:                             # noqa: BLE001
                with trava:
                    placar["erro"] += 1
                    al._log("   %-10s ERRO: %s: %s" % (lig, type(e).__name__, str(e)[:80]))
            finally:
                if grande:
                    with trava_fila:
                        grandes[0] -= 1

    ts = [threading.Thread(target=trabalhador, daemon=True) for _ in range(max(1, trabalhadores))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    dt = time.time() - t0
    if aplicar and feitas_na_rodada(placar):
        # UM POI, UMA INSTALACAO: precisa de todas as aprovadas, entao roda aqui.
        con = bc.conectar()
        try:
            cv.revisar(con, aplicar=True, log=al._log)
        finally:
            con.close()
    al._log("")
    for k, n in sorted(placar.items()):
        al._log("   %-26s %d" % (k, n))
    feitas = sum(placar[v] for v in VEREDITOS)
    al._log("   %d ligação(ões) em %.1f min · %.1f por minuto" % (feitas, dt / 60, feitas * 60 / max(dt, 1)))
    return dict(placar)


def ligacoes_do_prompt_antigo(cidade, limite=0):
    """As ligacoes com veredito de OUTRO processo que nao `PROCESSO`, na cidade.
    Dois conjuntos cruzados em Python, e nao `exists` por linha."""
    con = bc.conectar()
    try:
        cur = con.cursor()
        cur.execute("""select ligacao, id_empresa from radar_comercial.ligacao_veredito
                        where coalesce(percepcao::jsonb->>'processo', '') <> %s""", (PROCESSO,))
        velhas = {}
        for l, emp in cur.fetchall():
            velhas.setdefault(str(emp), set()).add(str(l))
        ligs = []
        for emp, conj in velhas.items():
            cur.execute("""select num_ligacao::text from resources_root.cadastro_corsan
                            where id_empresa = %s and num_ligacao = any(%s::bigint[])"""
                        + (" and upper(cidade) = upper(%s)" if cidade else ""),
                        (emp, [int(x) for x in conj if x.isdigit()]) + ((cidade,) if cidade else ()))
            ligs += [r[0] for r in cur.fetchall()]
    finally:
        con.close()
    ligs.sort(key=int)
    al._log("   %d ligação(ões) julgadas por outro processo%s" % (len(ligs), " em " + cidade if cidade else ""))
    return ligs[:limite] if limite else ligs


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=80)
    p.add_argument("--modelo", default=al.MODELO_PADRAO)
    p.add_argument("--ligacao", action="append")
    p.add_argument("--cidade", default=None)
    p.add_argument("--exigir-busca-web", dest="exigir_busca", action="store_true")
    p.add_argument("--vinculo-novo", dest="vinculo_novo", action="store_true")
    p.add_argument("--saida", default=None, help="pasta para gravar cada julgamento e as fotos")
    p.add_argument("--adiar-grandes", dest="adiar_grandes", type=int, default=0,
                   help="a fila tira as ligacoes com mais de N POIs (elas vao para o laco dos predios)")
    p.add_argument("--so-grandes", dest="so_grandes", type=int, default=0,
                   help="o laco dos predios: so as ligacoes com mais de N POIs")
    p.add_argument("--ligacoes-arquivo", dest="ligacoes_arquivo", default=None,
                   help="arquivo com uma ligacao por linha (a reavaliacao das 22 mil nao cabe na linha de comando)")
    p.add_argument("--julgar-sem-foto", dest="julgar_sem_foto", action="store_true",
                   help="julga tambem a ligacao sem foto a ate 60 m do hidrometro, so com registros e busca")
    p.add_argument("--prompt-antigo", dest="prompt_antigo", action="store_true",
                   help="rejulga as ligacoes (da --cidade) cujo veredito veio de outro processo que nao o atual")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    global SAIDA
    if a.saida:
        os.makedirs(a.saida, exist_ok=True)
        SAIDA = a.saida
    ligs = list(a.ligacao or [])
    if a.ligacoes_arquivo:
        ligs += [x.strip() for x in open(a.ligacoes_arquivo) if x.strip()]
    if a.prompt_antigo:
        ligs += ligacoes_do_prompt_antigo(a.cidade, a.limite)
        if not ligs:
            al._log("nenhuma ligação julgada por outro processo")
            return 0
    r = rodar(a.limite, a.aplicar, a.trabalhadores, a.modelo, ligs or None, a.cidade, a.exigir_busca,
              a.vinculo_novo, a.adiar_grandes, a.so_grandes, a.julgar_sem_foto)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
