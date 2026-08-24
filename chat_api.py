# -*- coding: utf-8 -*-
"""Rotas do chat com conversas e histórico, sobre o agente da Spark.

Separado do `server.py` de propósito: o servidor já é grande, e o chat tem ciclo
de vida próprio — dá para mexer aqui sem tocar no que atende o mapa.

O que cada rota faz:

    GET  /api/chat/conversas          lista, mais recente primeiro
    POST /api/chat/conversas          cria uma
    GET  /api/chat/conversas/{id}     as mensagens dela
    POST /api/chat/conversas/{id}     manda pergunta e recebe por SSE
    PATCH/DELETE /api/chat/conversas/{id}   renomeia / arquiva

A resposta vem por **SSE** e não de uma vez: com ferramentas, a pergunta pode
levar meio minuto entre buscas e consultas, e uma tela parada por meio minuto
parece travada. O fluxo manda cada passo — "chamei buscar_web", "voltou tanto" —
para quem está olhando ver o raciocínio acontecendo.

Para ligar no `server.py`:

    from chat_api import registrar_chat
    registrar_chat(app)
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import queue
import threading
import uuid

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

import agente_local as agente
import anexos as anx
import base_comum as bc

# Os originais ficam fora do banco. Um PDF de 20 MB numa tabela que o painel lê
# a cada troca de conversa é peso puro — o que a conversa precisa é do texto.
PASTA_ANEXOS = pathlib.Path.home() / ".comercialradar" / "anexos_chat"
PASTA_ANEXOS.mkdir(parents=True, exist_ok=True)

rotas = APIRouter(prefix="/api/chat", tags=["chat"])

LISTAR = """
select c.id::text, c.titulo, c.criada_em, c.mexida_em,
       (select count(*) from comercialradar.chat_mensagem m
         where m.conversa_id = c.id and m.papel in ('user','assistant'))
  from comercialradar.chat_conversa c
 where not c.arquivada
 order by c.mexida_em desc
 limit 100
"""

MENSAGENS = """
select papel, conteudo, ferramenta, dados, criada_em
  from comercialradar.chat_mensagem
 where conversa_id = %s
 order by id
"""


def _titulo_de(pergunta: str) -> str:
    """A primeira pergunta vira o nome da conversa, cortada no limite legível."""
    t = " ".join((pergunta or "").split())
    return (t[:57] + "…") if len(t) > 58 else (t or "Nova conversa")


@rotas.get("/conversas")
def listar():
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute(LISTAR)
            return [{"id": i, "titulo": t, "criada_em": c.isoformat(),
                     "mexida_em": m.isoformat(), "mensagens": n}
                    for i, t, c, m, n in k.fetchall()]
    finally:
        con.close()


@rotas.post("/conversas")
def criar(corpo: dict = Body(default={})):
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute("""insert into comercialradar.chat_conversa (titulo)
                         values (%s) returning id::text""",
                      (corpo.get("titulo") or "Nova conversa",))
            novo = k.fetchone()[0]
        con.commit()
        return {"id": novo}
    finally:
        con.close()


@rotas.get("/conversas/{cid}")
def ler(cid: str):
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute(MENSAGENS, (cid,))
            return [{"papel": p, "conteudo": c, "ferramenta": f,
                     "dados": d, "criada_em": t.isoformat()}
                    for p, c, f, d, t in k.fetchall()]
    finally:
        con.close()


@rotas.patch("/conversas/{cid}")
def renomear(cid: str, corpo: dict = Body(...)):
    titulo = (corpo.get("titulo") or "").strip()
    if not titulo:
        raise HTTPException(400, "título vazio")
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute("""update comercialradar.chat_conversa
                            set titulo = %s, mexida_em = now() where id = %s""",
                      (titulo[:120], cid))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@rotas.delete("/conversas/{cid}")
def arquivar(cid: str):
    # arquiva, não apaga: conversa é registro de decisão, e apagar registro de
    # decisão é o tipo de coisa de que alguém sente falta seis meses depois
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute("""update comercialradar.chat_conversa
                            set arquivada = true where id = %s""", (cid,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


def _historico(con, cid: str) -> list:
    """Remonta o histórico no formato que o modelo espera."""
    with con.cursor() as k:
        k.execute("""select papel, conteudo, ferramenta from
                     comercialradar.chat_mensagem where conversa_id = %s
                     order by id""", (cid,))
        linhas = k.fetchall()
    msgs = [{"role": "system", "content": agente.SISTEMA}]
    for papel, conteudo, ferramenta in linhas:
        if papel == "tool":
            # o retorno volta como fala do usuário: é o formato que o servidor
            # aceita quando não tem suporte nativo a ferramentas, e funciona
            # nos dois modos
            msgs.append({"role": "user",
                         "content": f"Resultado de {ferramenta}:\n{conteudo}"})
            continue

        # NENHUM TURNO DE ASSISTENTE VAZIO VOLTA AO MODELO.
        #
        # Chamada de ferramenta é gravada como linha `assistant` com conteúdo
        # "" — o nome vai na coluna `ferramenta`. Devolvidas cruas, viravam
        # exemplos de "aqui o assistente responde nada". Medido na conversa do
        # usuário: 111 mensagens, 28 turnos vazios, e a pergunta seguinte
        # voltava em 0,6 s com ZERO caracteres. O modelo copiava o padrão da
        # própria conversa, e cada vazio novo piorava o próximo.
        # Sai INTEIRO, com ferramenta ou sem. A primeira tentativa punha um
        # texto no lugar — "(chamei a ferramenta X)" — e o modelo copiou isso
        # como resposta ao usuário, literalmente. Encher o vazio com frase
        # minha só troca um padrão ruim por outro.
        #
        # E é seguro tirar: a chamada não se perde, porque o resultado dela vem
        # logo abaixo como "Resultado de {ferramenta}: ...". O que a linha
        # vazia acrescentava era só o exemplo de responder nada.
        if papel == "assistant" and not (conteudo or "").strip():
            continue

        msgs.append({"role": papel, "content": conteudo})
    return msgs


def _gravar(con, cid: str, papel: str, conteudo: str,
            ferramenta: str | None = None, dados=None) -> None:
    with con.cursor() as k:
        k.execute("""insert into comercialradar.chat_mensagem
                     (conversa_id, papel, conteudo, ferramenta, dados)
                     values (%s, %s, %s, %s, %s::jsonb)""",
                  (cid, papel, conteudo or "", ferramenta,
                   json.dumps(dados, ensure_ascii=False) if dados else None))
        k.execute("""update comercialradar.chat_conversa
                        set mexida_em = now() where id = %s""", (cid,))
    con.commit()




GRAVAR_ANEXO = """
insert into comercialradar.chat_anexo
       (conversa_id, nome, tipo, bytes, caminho, extraido, meta)
values (%s, %s, %s, %s, %s, %s, %s::jsonb)
returning id
"""


@rotas.post("/conversas/{cid}/anexos")
async def subir_anexos(cid: str, arquivos: list[UploadFile] = File(...)):
    """Recebe os arquivos, extrai o conteúdo e devolve o que cada um virou.

    A extração acontece AQUI e não na hora de perguntar: assim o usuário vê na
    tela se o PDF era escaneado ou se o ZIP veio vazio antes de gastar uma
    pergunta com ele.
    """
    con = bc.conectar()
    saida = []
    try:
        for arq in arquivos:
            dados = await arq.read()
            lido = anx.ler(dados, arq.filename or "arquivo")

            caminho = None
            if lido.get("tipo") != "erro":
                caminho = str(PASTA_ANEXOS / f"{uuid.uuid4().hex}_{arq.filename}")
                pathlib.Path(caminho).write_bytes(dados)

            meta = {k: v for k, v in lido.items()
                    if k not in ("texto", "b64", "nome", "tipo")}
            with con.cursor() as k:
                k.execute(GRAVAR_ANEXO, (
                    cid, lido.get("nome"), lido.get("tipo", "erro"),
                    len(dados), caminho, lido.get("texto"),
                    json.dumps(meta, ensure_ascii=False)))
                anexo_id = k.fetchone()[0]
            con.commit()

            saida.append({
                "id": anexo_id, "nome": lido.get("nome"),
                "tipo": lido.get("tipo"), "bytes": len(dados),
                "erro": lido.get("erro"),
                # o que a pessoa precisa saber antes de perguntar
                "aviso": ("PDF sem texto extraível (parece digitalizado)"
                          if lido.get("paginas_sem_texto") else
                          "conteúdo truncado" if lido.get("cortado") else None),
            })
    finally:
        con.close()
    return saida


def _carregar_anexos(con, ids: list) -> list:
    """Remonta os anexos no formato que o `anexos.para_mensagem` espera."""
    if not ids:
        return []
    with con.cursor() as k:
        k.execute("""select id, nome, tipo, caminho, extraido, meta
                       from comercialradar.chat_anexo
                      where id = any(%s) order by id""", (ids,))
        linhas = k.fetchall()
    saida = []
    for _, nome, tipo, caminho, extraido, meta in linhas:
        d = {"nome": nome, "tipo": tipo, "texto": extraido}
        d.update(meta or {})
        if tipo == "imagem" and caminho and pathlib.Path(caminho).exists():
            # a imagem é reconstruída do original a cada uso: guardar o base64
            # no banco dobraria o peso do anexo sem ganhar nada
            d = anx.preparar_imagem(pathlib.Path(caminho).read_bytes(), nome)
        saida.append(d)
    return saida


@rotas.post("/conversas/{cid}")
async def perguntar(cid: str, corpo: dict = Body(...)):
    pergunta = (corpo.get("pergunta") or "").strip()
    ids_anexo = corpo.get("anexos") or []
    if not pergunta and not ids_anexo:
        raise HTTPException(400, "pergunta vazia")

    fila: queue.Queue = queue.Queue()

    def trabalhar():
        """O agente é síncrono; roda em thread e publica os passos na fila.

        Sem a thread, uma pergunta de 30 s com quatro ferramentas bloquearia o
        laço de eventos e derrubaria as outras requisições do servidor junto.
        """
        con = bc.conectar()
        try:
            _gravar(con, cid, "user", pergunta)
            with con.cursor() as k:
                k.execute("""select titulo from comercialradar.chat_conversa
                              where id = %s""", (cid,))
                atual = (k.fetchone() or [""])[0]
            if atual in ("", "Nova conversa"):
                with con.cursor() as k:
                    k.execute("""update comercialradar.chat_conversa
                                    set titulo = %s where id = %s""",
                              (_titulo_de(pergunta), cid))
                con.commit()
                fila.put(("titulo", _titulo_de(pergunta)))

            def passo(tipo, dado):
                fila.put((tipo, dado))
                if tipo == "ferramenta":
                    _gravar(con, cid, "assistant", "", dado["nome"],
                            {"argumentos": dado["args"]})
                elif tipo == "retorno":
                    _gravar(con, cid, "tool",
                            json.dumps(dado["saida"], ensure_ascii=False)[:8000],
                            dado["nome"])

            hist = _historico(con, cid)
            # Com anexo, a pergunta que vai ao modelo é a mensagem multimodal:
            # documentos viram texto ANTES do pedido, imagens vão inteiras.
            if ids_anexo:
                lidos = _carregar_anexos(con, ids_anexo)
                msg = anx.para_mensagem(lidos, pergunta)
                resposta, _ = agente.conversar(msg["content"], hist, passo)
            else:
                resposta, _ = agente.conversar(pergunta, hist, passo)
            _gravar(con, cid, "assistant", resposta)
            fila.put(("fim", resposta))
        except Exception as e:
            fila.put(("erro", f"{type(e).__name__}: {str(e)[:300]}"))
        finally:
            con.close()
            fila.put((None, None))

    threading.Thread(target=trabalhar, daemon=True).start()

    async def fluxo():
        laco = asyncio.get_event_loop()
        while True:
            tipo, dado = await laco.run_in_executor(None, fila.get)
            if tipo is None:
                break
            yield ("data: " + json.dumps({"tipo": tipo, "dado": dado},
                                         ensure_ascii=False) + "\n\n")

    return StreamingResponse(fluxo(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def registrar_chat(app) -> None:
    app.include_router(rotas)
