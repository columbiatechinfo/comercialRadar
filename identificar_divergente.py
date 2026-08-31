"""identificar_divergente.py — segunda tentativa de nomear o comércio achado.

A leitura de fachada encontra comércio que o cadastro não conhece, mas às vezes
sem conseguir ler o nome: o letreiro está longe, de lado, coberto por árvore, ou
o que denunciou o estabelecimento foi o distintivo do Google e não uma placa.
Esses achados entram no banco com `nome_lido` nulo — existem, mas não têm como
virar cadastro.

Este módulo faz a passada de resgate. Para cada um:

  1. calcula o RUMO da câmera até a coordenada aproximada do achado — que a
     leitura deslocou para o lado em que ele apareceu;
  2. captura o Street View mirando ALI, com `fov` fechado (zoom), em vez da
     panorâmica que serve para enxergar a cena e não para ler letra pequena;
  3. pergunta à IA UMA coisa só: que nome está escrito e que tipo de comércio é.

Por que separado da leitura: são perguntas de enquadramento oposto. A leitura
precisa de campo largo para achar o que existe; nomear precisa de campo estreito
para ler o que está escrito. A mesma imagem não serve para as duas, e foi por
isso que o nome faltou na primeira passada.

    .venv\\Scripts\\python identificar_divergente.py [--limit N] [--ids 1,2]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time

import anotar
import avaliar_fachada as AF
import base_comum as bc
import imagens
import prompts_fachada as P
import streetview_capture as SV
import endpoints

MODELO = os.environ.get("VLLM_MODELO", "qwen3vl-moe")
BASE = endpoints.VLLM

# `fov` fechado: aqui a missão é LER, não enxergar. 30° sobre uma testada de
# loja a 15 m enche o quadro com a fachada — que é o oposto do que a panorâmica
# faz, e é justamente por isso que esta passada existe.
FOV_LEITURA = 30
CONCORRENCIA = int(os.environ.get("LEITURA_CONCORRENCIA", "12"))

NOME_SISTEMA = """\
Você olha uma foto de rua ENQUADRADA sobre um estabelecimento e responde duas \
coisas: que nome está escrito nele e que tipo de comércio é.

O estabelecimento de interesse está no CENTRO do quadro. Ignore o que estiver \
nas bordas — vizinho não é o alvo.

`nome_lido` é o que você CONSEGUE LER, letra por letra, em letreiro, toldo, \
vitrine, adesivo de porta ou fachada pintada. Não complete palavra cortada, não \
corrija grafia estranha, não deduza o nome pela atividade. Se você lê apenas \
"UT...DADES", devolva "UT...DADES". Se não lê nada, devolva `null` — nome \
inventado num cadastro comercial é pior que campo vazio, porque ninguém depois \
sabe que foi inventado.

`tipo` é a atividade que a cena mostra, em duas ou três palavras: "padaria", \
"oficina mecânica", "loja de roupas". Vitrine com produto, mercadoria na porta, \
equipamento, mobiliário — tudo conta. Se a cena não permite dizer, `null`.

`legivel` diz se havia texto legível no alvo. É diferente de ter nome: pode \
haver texto ilegível, e isso é informação (a recaptura não vai resolver)."""

NOME_SCHEMA = {
    "type": "object", "additionalProperties": False,
    # `observacao` saiu. Ninguém a lia, e era ela que estourava o teto de
    # tokens no meio da string — o JSON chegava truncado e as cinco leituras
    # falhavam com "Expecting ',' delimiter". Campo que não se usa não é
    # inofensivo: ele custa o orçamento dos campos que se usam.
    "required": ["nome_lido", "tipo", "legivel"],
    "properties": {
        "nome_lido": {"type": ["string", "null"], "maxLength": 80},
        "tipo": {"type": ["string", "null"], "maxLength": 40},
        "legivel": {"type": "boolean"},
    },
}


def pendentes(con, limite: int, ids: list | None) -> list:
    """Os achados divergentes SEM nome, com a coordenada que a leitura deu.

    Vêm da tabela de POIs criados pela leitura (`fonte='ia_fachada'`) porque é
    lá que o achado ganhou coordenada própria — e é a coordenada que diz para
    onde mirar. Quem já tem nome não entra: nomear de novo o que já foi lido só
    gastaria GPU para confirmar o que se sabe.
    """
    with con.cursor() as cur:
        cur.execute(f"""
            SELECT n.id, n.nome, n.categoria,
                   COALESCE(n.maps_lat, n.lat_origem), COALESCE(n.maps_lng, n.lng_origem),
                   n.descoberto_de, s.pano_id, s.cam_lat, s.cam_lng, s.data_captura
              FROM pois n
              JOIN streetview_imgs s ON s.poi_id = n.descoberto_de
                                    AND s.angulo = 'facade'
             WHERE n.fonte = 'ia_fachada'
               AND (n.nome IS NULL OR n.nome = '' OR n.nome ILIKE 'sem nome%%')
               {"AND n.id = ANY(%s)" if ids else ""}
               AND s.pano_id IS NOT NULL AND s.cam_lat IS NOT NULL
             ORDER BY n.id {"LIMIT %s" if limite else ""}""",
                    tuple(x for x in ([list(ids)] if ids else []) + ([limite] if limite else [])))
        campos = ("id nome categoria lat lng origem pano cam_lat cam_lng data").split()
        return [dict(zip(campos, r)) for r in cur.fetchall()]


async def capturar(alvos: list) -> None:
    """Um print por achado, mirando a coordenada dele com o zoom fechado."""
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        br = await pw.chromium.launch(headless=True)
        # O MESMO user-agent da captura principal. Sem ele o Maps não entra em
        # modo panorama: `_abrir` fica esperando o `,3a,` na URL até estourar e
        # devolve None — foi por isso que cinco de cinco resgates saíram sem
        # imagem, enquanto meu log dizia "sem texto legível", que era falso.
        ctx = await br.new_context(viewport={"width": 1280, "height": 800},
                                   locale="pt-BR", user_agent=SV.UA)
        page = await ctx.new_page()
        for a in alvos:
            rumo = SV._bearing(a["cam_lat"], a["cam_lng"], a["lat"], a["lng"])
            ok = await SV._abrir_por_id(page, a["pano"], rumo, FOV_LEITURA)
            if not ok:
                a["_motivo"] = "nao_abriu"
                continue
            await page.wait_for_timeout(2500)
            a["img"] = await page.screenshot(
                type="jpeg", quality=80,
                clip={"x": 0, "y": 64, "width": 1280, "height": 656})
            a["heading"] = rumo
        await br.close()


async def nomear(alvos: list) -> None:
    from openai import AsyncOpenAI
    cli = AsyncOpenAI(base_url=BASE, api_key="x", timeout=900, max_retries=1)
    sem = asyncio.Semaphore(CONCORRENCIA)

    async def uma(a):
        async with sem:
            try:
                r = await cli.chat.completions.create(
                    model=MODELO, temperature=0, max_tokens=220,
                    messages=[{"role": "system", "content": NOME_SISTEMA},
                              {"role": "user", "content": [
                                  {"type": "text", "text":
                                   "Que nome está escrito neste estabelecimento, e "
                                   "que tipo de comércio é?"},
                                  AF._img_openai(AF.limpar_interface(a["img"]))]}],
                    response_format={"type": "json_schema", "json_schema": {
                        "name": "n", "strict": True, "schema": NOME_SCHEMA}})
                a["leitura"] = json.loads(r.choices[0].message.content)
            except Exception as e:
                a["leitura"] = {"erro": f"{type(e).__name__}: {str(e)[:120]}"}

    await asyncio.gather(*[uma(a) for a in alvos if a.get("img")])


def gravar(con, alvos: list) -> int:
    """O nome lido entra no POI que a leitura criou. A imagem vai junto.

    `match_valido` continua falso: nomear não confirma cadastro, só melhora o
    achado. Quem decide se ele entra na base do cliente é o supervisor, na fila
    de divergentes.
    """
    n = 0
    with con.cursor() as cur:
        for a in alvos:
            r = a.get("leitura") or {}
            if "erro" in r:
                continue
            nome, tipo = (r.get("nome_lido") or "").strip(), r.get("tipo")
            if a.get("img"):
                # a captura fechada fica gravada: é a prova do nome, e sem ela
                # ninguém depois sabe com base em que a IA leu aquilo
                imagens.gravar_streetview(
                    a["id"], a["img"], a["cam_lat"], a["cam_lng"], con,
                    angulo="divergente_zoom", cam_lat=a["cam_lat"],
                    cam_lng=a["cam_lng"], heading=a.get("heading"),
                    fov=FOV_LEITURA, pano_id=a["pano"], data_captura=a.get("data"))
            if not nome and not tipo:
                continue
            cur.execute("""UPDATE pois
                              SET nome = COALESCE(NULLIF(%s, ''), nome),
                                  categoria = COALESCE(%s, categoria)
                            WHERE id = %s""", (nome or None, tipo, a["id"]))
            # a fila do supervisor mostra o que foi lido: atualizar lá também,
            # senão ele decide sobre o nome antigo enquanto o POI já tem o novo
            cur.execute("""UPDATE atribuicao_divergente
                              SET nome_lido = COALESCE(NULLIF(%s, ''), nome_lido),
                                  atividade = COALESCE(%s, atividade)
                            WHERE poi_id = %s AND status = 'pendente'""",
                        (nome or None, tipo, a["id"]))
            n += 1
        fundidos = _fundir_homonimos(cur, {a["origem"] for a in alvos})
    con.commit()
    if fundidos:
        print(f"   ({fundidos} achado(s) fundido(s): mesmo comércio visto em "
              f"mais de um quadro do giro)", flush=True)
    return n


def _fundir_homonimos(cur, origens: set) -> int:
    """Funde achados que o resgate revelou serem o MESMO estabelecimento.

    A dedupe da leitura não alcança este caso, e o motivo é temporal: no momento
    da leitura os dois achados eram `nome_lido: null` com atividades diferentes
    — "farmácia" e "telecomunicações" —, então eram legitimamente distintos. Só
    depois que o resgate leu `TELENTREGA` nos dois é que ficou claro serem o
    letreiro da mesma loja, visto em dois quadros do giro. Por isso a fusão mora
    aqui: é aqui que o nome passa a existir.

    A normalização é feita em Python, não em SQL. O Postgres deste banco não tem
    `unaccent` instalada, e escrever um `translate` de 50 caracteres dentro da
    consulta troca uma dependência por uma linha ilegível que ninguém revisa.

    Fica o de MENOR id — o primeiro que a leitura registrou. Os itens de fila
    dos fundidos são apagados junto, senão o supervisor decidiria duas vezes
    sobre a mesma loja.
    """
    if not origens:
        return 0
    import unicodedata

    def chave(s):
        s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                    if unicodedata.category(c) != "Mn")
        return " ".join(s.split())

    # O AGRUPAMENTO É POR NOME E PROXIMIDADE, não por POI de origem.
    #
    # Agrupar só dentro da mesma origem resolvia o caso pequeno — o mesmo
    # letreiro em dois quadros do giro — e deixava passar o grande: num
    # quarteirão denso, a MESMA loja aparece na panorâmica de vinte vizinhos.
    # `CASA DO PAPEL` foi criada 20 vezes, `Farmácias São João` outras tantas.
    # Sem isto o mapa recebe 250 pontos onde há 60 lojas, e a fila do supervisor
    # pede a mesma decisão vinte vezes.
    #
    # 45 m é a folga: a coordenada do achado é aproximada por construção
    # (deslocada 12 m pelo lado em que apareceu) e cada vizinho a estima de um
    # ponto de vista diferente. Duas lojas homônimas de verdade a menos de 45 m
    # seriam duas filiais na mesma quadra — caso raro que o supervisor desfaz.
    cur.execute("""SELECT id, nome, COALESCE(maps_lat, lat_origem),
                          COALESCE(maps_lng, lng_origem)
                     FROM pois
                    WHERE fonte = 'ia_fachada' AND tenant_id IS NOT DISTINCT FROM
                          (SELECT tenant_id FROM pois WHERE id = %s)
                      AND nome IS NOT NULL AND nome NOT ILIKE 'sem nome%%'
                    ORDER BY id""", (list(origens)[0],))
    linhas = [(i, chave(nome), la, lo) for i, nome, la, lo in cur.fetchall()]

    import math

    def perto(a, b, metros=45.0):
        dy = (a[2] - b[2]) * 111320
        dx = (a[3] - b[3]) * 111320 * math.cos(math.radians(a[2]))
        return math.hypot(dx, dy) <= metros

    donos, n = [], 0
    apagar = []
    for reg in linhas:
        alvo = next((d for d in donos
                     if d[1] == reg[1] and perto(d, reg)), None)
        if alvo:
            apagar.append(reg[0])
        else:
            donos.append(reg)
    for k in range(0, len(apagar), 500):
        lote = apagar[k:k + 500]
        cur.execute("DELETE FROM atribuicao_divergente WHERE poi_id = ANY(%s)", (lote,))
        cur.execute("DELETE FROM pois WHERE id = ANY(%s)", (lote,))
        n += len(lote)
    return n


async def rodar(args) -> int:
    con = bc.conectar()
    alvos = pendentes(con, args.limit, args.ids)
    print(f"⟦fase⟧ fachada", flush=True)
    print(f"{len(alvos)} achados divergentes sem nome · fov {FOV_LEITURA}", flush=True)
    if not alvos:
        con.close()
        return 0
    t0 = time.time()
    for k in range(0, len(alvos), 24):
        bloco = alvos[k:k + 24]
        await capturar(bloco)
        await nomear(bloco)
        n = gravar(con, bloco)
        for a in bloco:
            r = a.get("leitura") or {}
            # TRÊS estados diferentes, e confundi-los foi o que me fez ler
            # "ilegível" onde não havia foto nenhuma.
            if not a.get("img"):
                situacao = f"NÃO FOTOGRAFOU ({a.get('_motivo', 'sem motivo')})"
            elif "erro" in r:
                situacao = f"falha na leitura: {r['erro'][:40]}"
            elif not r.get("legivel"):
                situacao = "fotografou, sem texto legível"
            else:
                situacao = ""
            print(f"   {a['id']:<8}{str(r.get('nome_lido') or '—')[:32]:<34}"
                  f"{str(r.get('tipo') or '')[:22]:<24}{situacao}", flush=True)
        print(f"   POIs {min(k+24, len(alvos))}/{len(alvos)} | nomeados {n} | "
              f"{(time.time()-t0)/max(min(k+24, len(alvos)),1):.1f}s cada", flush=True)
        for a in bloco:
            a.pop("img", None)
    con.close()
    print(f"\nfim — {len(alvos)} em {(time.time()-t0)/60:.0f} min", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--ids", type=lambda s: [int(x) for x in s.split(",")], default=None)
    return asyncio.run(rodar(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
