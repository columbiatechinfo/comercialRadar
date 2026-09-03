# -*- coding: utf-8 -*-
"""base_api.py — as rotas do cadastro de bases do cliente.

O QUE ESTA TELA DECIDE, E POR QUE ELA VEM ANTES DE TUDO

A base do cliente é o eixo da fase 1: cada linha dela é uma instalação, e o
Radar pendura nela os POIs das outras fontes. Só que cada cliente entrega a base
no formato dele — a latitude pode ser `lat`, `LATITUDE`, `y` ou `cod_latitude`;
o tipo de cliente pode estar em `categoria`, `tipo_lig` ou `classe`.

Enquanto ninguém DECLARA o que é o quê, escolher cidade ou desenhar área é
trabalhar no escuro: adivinhar errado a coluna de tipo faz o produto inteiro
classificar comércio como residência. Por isso a base precisa estar `pronta`
antes, e é o banco que impõe — a `check` da migração 0045 recusa `pronta` sem as
seis declaradas.

A IA SUGERE, A PESSOA DECIDE

`base_cliente_mapear.py` manda os títulos reais e uma amostra de linhas reais ao
modelo da Spark e grava a sugestão em `sugerido_por_ia`, deixando a base em
`rascunho`. Estas rotas servem essa sugestão à tela junto com a amostra, para
que a pessoa veja o mesmo que a IA viu, e confirmem ou corrijam.

`POST /confirmar` é o único caminho para `pronta`. Ele não confia no que chega:
recusa coluna que não existe na base e recusa tipo comercial que não está entre
os valores da coluna de tipo. Cliente pode mandar qualquer coisa; a rota é onde
isso para.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException

import auth as _auth
import base_comum as bc

rotas = APIRouter(prefix="/api/base-cliente", tags=["base do cliente"])

OBRIGATORIAS = ("latitude", "longitude", "ligacao", "endereco", "tipo_cliente")
COMPLEMENTARES = ("numero", "bairro", "cep", "cidade")


def _linhas(cur):
    nomes = [d[0] for d in cur.description]
    return [dict(zip(nomes, linha)) for linha in cur.fetchall()]


@rotas.get("")
def listar():
    """As bases cadastradas, com o estado de cada uma.

    `pronta` é o que a tela usa para liberar a escolha de área. Devolvê-lo aqui
    evita que o front precise adivinhar por ausência de campo.
    """
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""
        select id, nome, cliente, tabela_dados, linhas, estado,
               mapa_colunas, tipos_comerciais, confirmado_por, confirmado_em,
               criado_em
          from radar_comercial.base_cliente
         order by criado_em desc
    """)
    bases = _linhas(cur)
    con.close()
    return {"bases": bases,
            "alguma_pronta": any(b["estado"] == "pronta" for b in bases)}


@rotas.get("/{base_id}")
def detalhe(base_id: int):
    """A base com o que a tela precisa para montar a planilha: os títulos, a
    amostra de linhas REAIS, a sugestão da IA e os valores da coluna de tipo."""
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""
        select id, nome, cliente, tabela_dados, linhas, estado,
               colunas_brutas, mapa_colunas, tipos_comerciais, sugerido_por_ia
          from radar_comercial.base_cliente where id = %s
    """, (base_id,))
    linhas = _linhas(cur)
    if not linhas:
        con.close()
        raise HTTPException(404, "base não encontrada")
    b = linhas[0]

    sug = b.get("sugerido_por_ia") or {}
    if isinstance(sug, str):
        sug = json.loads(sug)

    # A AMOSTRA VEM DA TABELA, e não do que a IA guardou. O que a pessoa
    # confere na tela tem de ser o dado de hoje: se a base foi recarregada
    # depois da sugestão, mostrar a amostra velha seria confirmar sobre um
    # retrato que não existe mais.
    amostra, colunas = [], b.get("colunas_brutas") or []
    if isinstance(colunas, str):
        colunas = json.loads(colunas)
    tabela = b.get("tabela_dados") or ""
    if "." in tabela and colunas:
        schema, nome = tabela.split(".", 1)
        lista = ", ".join('"%s"' % c for c in colunas)
        try:
            cur.execute("select %s from %s.%s limit 15" % (lista, schema, nome))
            amostra = [[None if v is None else str(v)[:80] for v in linha]
                       for linha in cur.fetchall()]
        except Exception:                                      # noqa: BLE001
            con.rollback()

    # Os valores da coluna de tipo, com contagem: é o que responde a sexta
    # pergunta, e sem a contagem a pessoa não sabe o que pesa.
    valores = (sug.get("tipos") or {}).get("valores") or []
    mapa = b.get("mapa_colunas") or {}
    if isinstance(mapa, str):
        mapa = json.loads(mapa)
    if not valores and mapa.get("tipo_cliente") and "." in tabela:
        schema, nome = tabela.split(".", 1)
        try:
            cur.execute('''select "%s"::text, count(*) from %s.%s
                            where "%s" is not null group by 1
                            order by 2 desc limit 40'''
                        % (mapa["tipo_cliente"], schema, nome,
                           mapa["tipo_cliente"]))
            valores = [[str(v), n] for v, n in cur.fetchall()]
        except Exception:                                      # noqa: BLE001
            con.rollback()
    con.close()

    b["colunas_brutas"] = colunas
    b["mapa_colunas"] = mapa
    return {"base": b, "amostra": amostra, "valores_tipo": valores,
            "sugestao": sug,
            "obrigatorias": list(OBRIGATORIAS),
            "complementares": list(COMPLEMENTARES)}


@rotas.post("/{base_id}/confirmar")
def confirmar(base_id: int, corpo: dict = Body(...),
              usuario: _auth.Usuario = Depends(_auth.usuario_atual)):
    """O único caminho para `pronta`.

    NÃO CONFIA NO QUE CHEGA. Uma coluna que não existe na base passaria aqui e
    quebraria a importação lá na frente, longe daqui e sem dizer por quê; um
    tipo comercial que não está entre os valores da coluna faria o produto
    procurar um comércio que a base nunca vai declarar. As duas coisas param
    nesta função.
    """
    mapa = corpo.get("mapa_colunas") or {}
    tipos = corpo.get("tipos_comerciais") or []
    # A ASSINATURA VEM DO TOKEN, E NÃO DO CORPO.
    #
    # Antes era `corpo.get("confirmado_por")` — e o front nunca mandava esse
    # campo, então `confirmado_por` ficava NULL mesmo com a base virando
    # `pronta`. É pior que cosmético: um campo que diz "quem assinou" não pode
    # depender de o cliente lembrar de se identificar, nem aceitar o nome que
    # ele digitar. Quem confirma é quem está logado, e o servidor já sabe disso
    # pelo JWT — é o mesmo princípio que o resto da identidade segue aqui.
    quem = usuario.nome or usuario.email or usuario.id

    faltam = [c for c in OBRIGATORIAS if not mapa.get(c)]
    if faltam:
        raise HTTPException(400, "faltam colunas obrigatórias: %s"
                                 % ", ".join(faltam))
    if not tipos:
        raise HTTPException(
            400, "escolha ao menos um tipo que indique atividade comercial — "
                 "é o que o Radar vai procurar")

    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select tabela_dados, colunas_brutas
                     from radar_comercial.base_cliente where id = %s""",
                (base_id,))
    r = cur.fetchone()
    if not r:
        con.close()
        raise HTTPException(404, "base não encontrada")
    tabela, colunas = r[0] or "", r[1] or []
    if isinstance(colunas, str):
        colunas = json.loads(colunas)

    inexistentes = [(k, v) for k, v in mapa.items()
                    if v and colunas and v not in colunas]
    if inexistentes:
        con.close()
        raise HTTPException(
            400, "coluna inexistente na base: %s"
                 % ", ".join("%s=%s" % (k, v) for k, v in inexistentes))

    if "." in tabela and mapa.get("tipo_cliente"):
        schema, nome = tabela.split(".", 1)
        try:
            cur.execute('select distinct "%s"::text from %s.%s where "%s" is not null'
                        % (mapa["tipo_cliente"], schema, nome, mapa["tipo_cliente"]))
            reais = {str(v[0]) for v in cur.fetchall()}
            fora = [t for t in tipos if str(t) not in reais]
            if fora:
                con.close()
                raise HTTPException(
                    400, "estes tipos não existem na coluna %s: %s"
                         % (mapa["tipo_cliente"], ", ".join(map(str, fora))))
        except HTTPException:
            raise
        except Exception:                                      # noqa: BLE001
            con.rollback()

    cur.execute("""
        update radar_comercial.base_cliente
           set mapa_colunas = %s, tipos_comerciais = %s, estado = 'pronta',
               confirmado_por = %s, confirmado_em = now()
         where id = %s
        returning id, estado
    """, (json.dumps(mapa), json.dumps(tipos), quem, base_id))
    saida = cur.fetchone()
    con.commit()
    con.close()
    return {"id": saida[0], "estado": saida[1]}


def registrar_bases(app) -> None:
    """Chamado pelo `server.py`, como o `registrar_chat`."""
    app.include_router(rotas)
