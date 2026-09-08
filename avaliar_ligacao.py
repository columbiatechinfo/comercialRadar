# -*- coding: utf-8 -*-
"""avaliar_ligacao.py — um veredito por HIDROMETRO, com todas as testemunhas.

POR QUE ESTE MODULO EXISTE, e por que o `avaliar_ia` nao bastava:

O `avaliar_ia` julga POI. Isso parecia certo ate 08/09/2026, quando o dono do
produto apontou o nivel errado: "o foco e a instalacao, nao os POIs; eles
enriquecem a instalacao a que se fundem, dando confiabilidade a ela".

A medicao deu razao a ele. 91.199 ligacoes tem POI vinculado, 77.564 delas
(85 por cento) tem MAIS DE UM, e 18.249 carregam vereditos que se
contradizem — 11.442 com aprovado e revisao ao mesmo tempo, 1.889 com aprovado
e REPROVADO sobre o mesmo medidor.

O caso que abriu a investigacao mostra por que: a Madeireira Maravilha aparece
seis vezes na base. A Receita traz o CNPJ, o Maps traz nota 4,0 com 162
avaliacoes, o IBGE e o estadual trazem o endereco. NENHUM POI tinha tudo, e
cada um foi julgado com o pedaco que trouxe. Um aprovou, o outro mandou para
revisao, e o mais rico — o do Maps — nem tinha sido julgado.

Aqui as seis testemunhas entram na mesma sala, e a resposta e uma.

O QUE ESTE MODULO NAO FAZ: nao apaga, nao funde e nao escolhe um POI
vencedor. `poi_veredito` continua valendo como o que a IA observou em cada
pedaco, e entra no dossie como evidencia — decisao do dono do produto no mesmo
dia. O que muda e onde mora a RESPOSTA.
"""
import argparse
import json
import threading
import time

import avaliar_ia as ia
import base_comum as bc
import descrever_imagens as di
import dossie_ligacao as dl
import imagens

MODELO_PADRAO = ia.MODELO_PADRAO
TIMEOUT = ia.TIMEOUT
VEREDITOS = ia.VEREDITOS

#: A escala e a MESMA do `avaliar_ia`, de proposito. O painel ja pinta por ela,
#: o operador ja a leu mil vezes, e o que mudou foi o sujeito do julgamento —
#: nao o vocabulario dele.
PROMPT = """Você decide se UMA LIGACAO DE AGUA esta com a tarifa errada.

O contexto: a companhia cobra este hidrometro como RESIDENCIAL. Se houver
comercio no imovel que ele abastece, a tarifa esta errada — e e isso que se
procura.

VOCE RECEBE VARIAS TESTEMUNHAS SOBRE O MESMO ENDERECO, e isso e a novidade
deste julgamento. Cada fonte — a base estadual, o IBGE, a Receita, o Google
Maps, o iFood — registrou o lugar por conta propria, em epocas diferentes e
sem falar com as outras. Elas NAO sao copias a descartar: sao observacoes
independentes, e quando convergem valem mais do que qualquer uma sozinha.

%(dossie)s

AS IMAGENS, nesta ordem:

%(lista)s

AS PRIMEIRAS SAO FOTOS DE RUA do imovel MAIS PROXIMO DO HIDROMETRO, tiradas do
mesmo ponto girando a camera; a primeira tem uma mira no centro marcando o
alvo. Quando houver FOTO PUBLICADA NO GOOGLE, ela e de outra natureza: alguem
que esteve no lugar fotografou o que ele faz.

COMO PESAR AS TESTEMUNHAS

- FONTES QUE CONVERGEM SOMAM. Tres bases dizendo "material de construcao" no
  mesmo numero e mais forte que uma dizendo, e muito mais forte que uma
  fachada muda. Diga na justificativa quantas concordaram.
- FONTES QUE DIVERGEM NAO SE ANULAM — elas descrevem coisas diferentes no
  mesmo lugar. Um hidrometro pode abastecer uma loja E uma casa; nesse caso ha
  comercio, e o veredito e de aprovacao.
- ESTABELECIMENTOS DIFERENTES no mesmo hidrometro sao comuns e nao sao
  contradicao: galeria, sobrado com loja embaixo, casa com salao nos fundos.
- CNPJ ATIVO no endereco e prova de registro, nao de operacao. Vale como
  indicio; quem prova operacao e avaliacao recente, loja no ar em plataforma,
  ou sinal comercial na imagem.
- ECONOMIA COMERCIAL OU INDUSTRIAL ja declarada na propria ligacao significa
  que o cliente JA cobra parte dela como comercio: nao ha o que reclassificar,
  e o veredito e "reprovado" salvo se houver outra atividade alem daquela.
- VARIAS ECONOMIAS RESIDENCIAIS num ponto so indicam uso misto ou varias
  moradias — indicio a favor de investigar, nunca prova sozinho.

%(julgar)s

SEPARE QUEM NAO E DESTE ENDERECO. O vinculo entre POI e hidrometro nasce
tambem por PROXIMIDADE, e proximidade erra: um vizinho a vinte metros entra na
lista sem ser o mesmo lugar. Olhe o numero da porta, o logradouro e o ramo de
cada registro e diga quais claramente NAO pertencem a este endereco.

E "CLARAMENTE" MESMO, e nao "na duvida tire". Numero de porta diferente E ramo
sem relacao e claro; nome diferente sozinho nao e — uma loja e o CNPJ dela
costumam ter nomes distintos, e um sobrado tem a casa e o salao. Na duvida,
mantenha: um registro a mais so dilui, enquanto um registro a menos pode ser a
unica testemunha do comercio.

Responda SOMENTE um JSON:
{"veredito": "<um dos quatro>",
 "pois_de_outro_endereco": [{"poi": <numero do POI>,
                             "porque": "<ate 15 palavras>"}],
 "fontes_que_sustentam": <quantas das fontes listadas sustentam o veredito>,
 "estabelecimento": "<o nome do negocio que justifica a aprovacao, ou null>",
 "especie_cnefe": <1-8|null>, "secao_cnae": "<letra|null>",
 "sinal_no_imovel": "instalacao_fixa|so_oficio|nenhum",
 "justificativa": "<um paragrafo, ate 70 palavras, dizendo QUAIS fontes
sustentam o veredito e o que nas imagens confirma ou contradiz. Cite o que foi
visto, nao o que se supoe.>"}"""


#: QUANTAS CONEXOES, independentemente de quantos trabalhadores.
#:
#: O DEFEITO QUE ISTO CORRIGE, medido em 08/09/2026 na primeira corrida deste
#: modulo: ele abria UMA CONEXAO POR TRABALHADOR e foi disparado com 80. O
#: pooler da porta 7100 aceita 20 sessoes NO TOTAL — entre todas as maquinas, a
#: API, o painel e as capturas —, e 64 threads morreram com
#: `(EMAXCONNSESSION) max clients reached in session mode`.
#:
#: Pior: o comentario que eu tinha escrito ali dizia "o teto de trabalhadores e
#: quem protege o pooler", e isso e falso. O teto de trabalhadores protege o
#: MODELO; o pooler tem 20 e nao sabe quantos trabalhadores existem. O
#: `avaliar_ia` ja tinha aprendido isso — `CONEXOES = 4` e a mesma nota no
#: cabecalho — e eu nao segui.
#:
#: SEIS BASTAM porque o dossie usa o banco em rajadas curtas e a chamada ao
#: modelo, que e o grosso do tempo, nao usa banco NENHUM. Ver `Poco.pegar`.
CONEXOES = 6


class Poco:
    """Emprestimo de conexao. Quem nao pega, espera — nao abre outra.

    A CONEXAO E DEVOLVIDA ANTES DA CHAMADA AO MODELO, e e isso que faz seis
    atenderem oitenta. Montar o dossie sao dezenas de consultas de
    milissegundos; julgar sao dez segundos de espera pela Spark. Segurar a
    conexao durante a espera seria deixar 74 trabalhadores parados na fila do
    banco enquanto o banco esta ocioso.
    """

    def __init__(self, n):
        import queue
        self.fila = queue.Queue()
        for _ in range(max(1, n)):
            self.fila.put(bc.conectar())

    def pegar(self):
        import contextlib

        @contextlib.contextmanager
        def _emprestar():
            con = self.fila.get()
            try:
                yield con
            except Exception:
                # CONEXAO QUE VIU ERRO VOLTA LIMPA. Sem o rollback, a proxima
                # a peg&-la herda a transacao abortada e morre com "current
                # transaction is aborted" — defeito que ja custou uma rodada
                # inteira no `minerar_placeid`.
                try:
                    con.rollback()
                except Exception:                              # noqa: BLE001
                    pass
                raise
            finally:
                self.fila.put(con)
        return _emprestar()

    def fechar(self):
        while not self.fila.empty():
            try:
                self.fila.get_nowait().close()
            except Exception:                                  # noqa: BLE001
                pass


def _log(m):
    print(m, flush=True)


SQL_FILA = """
select distinct lp.ligacao
  from radar_comercial.ligacao_poi lp
  join resources_root.cadastro_corsan l on l.num_ligacao::text = lp.ligacao
  join radar_comercial.pois p on p.id = lp.poi_id
 where upper(l.categoria) = 'RESIDENCIAL'
   and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA'
   and p.fundido_em is null
   and exists (select 1 from radar_comercial.categoria_catalogo cc
                where cc.fonte = p.fonte and cc.valor = btrim(p.categoria)
                  and cc.avaliar)
   %(filtro)s
 order by 1
"""

SEM_VEREDITO = """
   and not exists (select 1 from radar_comercial.ligacao_veredito v
                    where v.ligacao = lp.ligacao)
"""


def fila(con, limite, refazer, ligacoes=None):
    if ligacoes:
        return [str(x) for x in ligacoes]
    cur = con.cursor()
    cur.execute(SQL_FILA % {"filtro": "" if refazer else SEM_VEREDITO})
    saida = [r[0] for r in cur.fetchall()]
    return saida[:limite] if limite else saida


def gravar(con, ligacao, v, resposta, percepcao, resumo, modelo, n_img, dt):
    cur = con.cursor()
    cur.execute("""
        insert into radar_comercial.ligacao_veredito
            (id_empresa, ligacao, veredito, justificativa, confianca,
             pois, fontes, imagens, percepcao, modelo, segundos)
        values ((select core.empresa_atual()), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (id_empresa, ligacao) do update set
            veredito = excluded.veredito,
            justificativa = excluded.justificativa,
            confianca = excluded.confianca,
            pois = excluded.pois, fontes = excluded.fontes,
            imagens = excluded.imagens, percepcao = excluded.percepcao,
            modelo = excluded.modelo, segundos = excluded.segundos,
            avaliado_em = now()
    """, (ligacao, v, resposta.get("justificativa"),
          resposta.get("confianca", ia.CONF_VEREDITO.get(v, 0.5)),
          resumo.get("pois", 0), resumo.get("fontes", 0), n_img,
          json.dumps(percepcao, ensure_ascii=False), modelo, round(dt, 2)))
    con.commit()


def marcar_intrusos(con, ligacao, resposta, ids_validos, modelo):
    """Grava quem a IA disse nao pertencer a este hidrometro. Devolve quantos.

    DUAS GUARDAS, e as duas ja evitaram estrago em outros pontos do sistema:

    1. SO POI QUE ESTAVA NO DOSSIE. O modelo pode inventar um numero, ou
       repetir um id que leu noutro lugar do texto. Marcar um vinculo que nao
       fazia parte deste julgamento seria agir sobre o que ninguem examinou.

    2. NUNCA TODOS. Se a resposta manda descartar o dossie inteiro, alguma
       coisa saiu errada — nao ha julgamento possivel sobre uma ligacao sem
       testemunha, e o resultado seria uma ligacao muda e sem vinculo nenhum.
       Nesse caso nao se marca nada e o caso fica para gente.
    """
    pedidos = (resposta or {}).get("pois_de_outro_endereco") or []
    if not isinstance(pedidos, list):
        return 0
    alvos = []
    for p in pedidos:
        try:
            pid = int(p.get("poi") if isinstance(p, dict) else p)
        except (TypeError, ValueError):
            continue
        if pid in ids_validos:
            alvos.append((pid, str((p or {}).get("porque", ""))[:200]
                          if isinstance(p, dict) else ""))
    if not alvos or len(alvos) >= len(ids_validos):
        return 0
    with con.cursor() as k:
        for pid, porque in alvos:
            k.execute("""
                update radar_comercial.ligacao_poi
                   set descartado_em = now(),
                       descartado_motivo = %s,
                       descartado_por = %s
                 where ligacao = %s and poi_id = %s
                   and descartado_em is null""",
                      (porque or "a IA leu o dossie e concluiu que e outro "
                                 "endereco", modelo, ligacao, pid))
    con.commit()
    return len(alvos)


def uma(poco, ligacao, modelo, secoes, placar, trava, aplicar):
    t0 = time.time()
    # O BANCO SO ENQUANTO SE MONTA O DOSSIE. Depois a conexao volta ao poco e
    # a espera pela Spark acontece sem segurar nada.
    with poco.pegar() as con:
        texto, imgs, tipos, resumo = dl.montar(con, ligacao, ia, imagens)
    if texto is None:
        with trava:
            placar["sem_poi"] += 1
        return
    lista = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(tipos))
    prompt = PROMPT % {"dossie": texto, "lista": lista,
                       "julgar": ia._regras_de_julgar(secoes)}
    try:
        resposta = di._chat_local(modelo, prompt, [ia._b64(b) for b in imgs],
                                  max_tokens=700, timeout=TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha"] += 1
            _log("   %-10s FALHOU: %s" % (ligacao, str(e)[:70]))
        return
    v = (resposta or {}).get("veredito", "").strip()
    if v not in VEREDITOS:
        with trava:
            placar["fora_da_escala"] += 1
        resumo["_veredito_cru"] = resposta
        v = "revisao_humana"
    percepcao = {"dossie": texto, "resposta": resposta, **resumo}
    dt = time.time() - t0
    fora = 0
    if aplicar:
        with poco.pegar() as con:
            gravar(con, ligacao, v, resposta or {}, percepcao, resumo, modelo,
                   len(imgs), dt)
            fora = marcar_intrusos(con, ligacao, resposta,
                                   set(resumo.get("ids") or []), modelo)
        if fora:
            with trava:
                placar["poi_de_outro_endereco"] += fora
    with trava:
        placar[v] += 1
        _log("   %-10s %-19s %d POIs/%d fontes · %s"
             % (ligacao, v, resumo.get("pois", 0), resumo.get("fontes", 0),
                (resposta or {}).get("justificativa", "")[:66]))


def rodar(limite, aplicar, trabalhadores, modelo, ligacoes, refazer):
    con = bc.conectar()
    alvos = fila(con, limite, refazer, ligacoes)
    _log("▶ veredito por LIGACAO — o dossiê de todas as fontes numa chamada")
    _log("   %d ligação(ões) na fila" % len(alvos))
    if not alvos:
        return {"alvos": 0}
    secoes = ia._secoes_texto(con)
    placar = {k: 0 for k in VEREDITOS}
    placar.update({"sem_poi": 0, "falha": 0, "fora_da_escala": 0,
                   "poi_de_outro_endereco": 0})
    trava = threading.Lock()
    t0 = time.time()

    # UMA CONEXAO POR TRABALHADOR aqui, e nao emprestimo: o dossie faz muitas
    # consultas curtas em sequencia e a disputa por uma conexao unica seria o
    # gargalo. O teto de trabalhadores e quem protege o pooler.
    poco = Poco(CONEXOES)

    def worker(fatia):
        for lig in fatia:
            try:
                uma(poco, lig, modelo, secoes, placar, trava, aplicar)
            except Exception as e:                             # noqa: BLE001
                # A FALHA DE UMA LIGACAO NAO DERRUBA O TRABALHADOR. Na primeira
                # corrida a excecao subia ate o `threading` e matava a thread
                # inteira: 64 delas morreram e a fila parou de andar sem que o
                # placar acusasse — o log dizia "Exception in thread" e mais
                # nada.
                with trava:
                    placar["falha"] += 1
                    _log("   %-10s ERRO %s: %s"
                         % (lig, type(e).__name__, str(e)[:70]))

    n = max(1, trabalhadores)
    fatias = [alvos[i::n] for i in range(n)]
    threads = [threading.Thread(target=worker, args=(f,)) for f in fatias]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    poco.fechar()

    dt = time.time() - t0
    _log("")
    for k in placar:
        if placar[k]:
            _log("   %-20s %5d" % (k, placar[k]))
    _log("   %d ligação(ões) em %.1f min · %.1f s cada"
         % (len(alvos), dt / 60, dt / max(len(alvos), 1)))
    return {"alvos": len(alvos), **placar}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=8)
    p.add_argument("--modelo", default=MODELO_PADRAO)
    p.add_argument("--ligacao", action="append",
                   help="repetível; avalia estas ligações ignorando a fila")
    p.add_argument("--refazer", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    r = rodar(a.limite, a.aplicar, a.trabalhadores, a.modelo, a.ligacao,
              a.refazer)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
