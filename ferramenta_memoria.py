# -*- coding: utf-8 -*-
"""Memória do agente entre conversas — o que ficou valendo, não o que foi dito.

A DIFERENÇA QUE JUSTIFICA UMA TABELA À PARTE

`chat_mensagem` guarda a conversa. Memória guarda a CONCLUSÃO. Numa conversa de
quarenta mensagens sobre o iFood, o que precisa sobreviver é uma linha — "o
detalhe da loja é bloqueado, só a listagem passa" — e não as quarenta.

Sem essa separação, lembrar exigiria reler conversas inteiras, e o que foi
decidido ficaria indistinguível do que foi só tentado e descartado.

COMO É USADA, e a ordem importa

  `recordar` no COMEÇO, quando o assunto aparece — devolve resumos curtos
  `lembrar`  no FIM, quando algo foi concluído

O índice é feito de frases curtas de propósito: o agente lê a lista inteira
barato e pede detalhe só do que interessa. É a ideia de divulgação progressiva
do claude-mem, com o banco que já existe aqui em vez de mais um serviço.

QUATRO TIPOS, porque a natureza muda o peso:

  fato         verdade sobre o sistema ou os dados
  decisao      o que se escolheu, e por quê
  preferencia  como o usuário quer que se trabalhe
  limite       o que NÃO funciona — evita repetir tentativa cara
"""
from __future__ import annotations

import re

import base_comum as bc

MAX_RESUMO = 200
MAX_INDICE = 40          # quantas memórias o `recordar` devolve

TIPOS = ("fato", "decisao", "preferencia", "limite")

_PALAVRA = re.compile(r"[a-zà-ú0-9]{4,}", re.I)
_COMUNS = {"para", "como", "quando", "porque", "sobre", "muito", "esse",
           "essa", "isso", "pelo", "pela", "mais", "menos", "todo", "toda",
           "cada", "ainda", "entao", "então", "sempre", "nunca", "aqui"}


def _chaves(texto: str, extra: list | None = None) -> list:
    """Palavras pelas quais essa memória vai ser achada depois."""
    achadas = {p.lower() for p in _PALAVRA.findall(texto or "")}
    achadas -= _COMUNS
    for e in (extra or []):
        achadas.update(p.lower() for p in _PALAVRA.findall(str(e)))
    return sorted(achadas)[:25]


def lembrar(resumo: str, detalhe: str = "", tipo: str = "fato",
            conversa_id: str = "") -> dict:
    """Guarda algo que deve sobreviver a esta conversa.

    Guarde CONCLUSÃO, não narrativa: "o detalhe da loja no iFood é bloqueado,
    só a listagem passa" e não "tentamos abrir a página e deu erro".
    """
    resumo = " ".join((resumo or "").split())
    if not resumo:
        return {"erro": "resumo vazio"}
    if tipo not in TIPOS:
        tipo = "fato"

    con = bc.conectar()
    try:
        with con.cursor() as k:
            # o mesmo aprendizado dito duas vezes não vira duas memórias: quem
            # lê o índice depois não saberia qual das duas está atualizada
            k.execute("""select id from comercialradar.memoria
                          where lower(resumo) = lower(%s) limit 1""",
                      (resumo[:MAX_RESUMO],))
            ja = k.fetchone()
            if ja:
                k.execute("""update comercialradar.memoria
                                set detalhe = coalesce(nullif(%s,''), detalhe),
                                    criado_em = now()
                              where id = %s""", (detalhe, ja[0]))
                con.commit()
                return {"ok": True, "id": ja[0], "acao": "atualizada"}

            k.execute("""insert into comercialradar.memoria
                         (resumo, detalhe, tipo, conversa_id, chaves)
                         values (%s, %s, %s, %s, %s) returning id""",
                      (resumo[:MAX_RESUMO], detalhe or None, tipo,
                       conversa_id or None, _chaves(resumo + " " + detalhe)))
            novo = k.fetchone()[0]
        con.commit()
        return {"ok": True, "id": novo, "acao": "guardada", "tipo": tipo}
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        con.close()


def recordar(assunto: str = "", detalhado: bool = False) -> dict:
    """O que já se sabe sobre um assunto — resumos curtos por padrão.

    Sem `assunto`, devolve as mais recentes: serve para o agente saber com o
    que está lidando no começo de uma conversa.
    """
    termos = [t for t in _PALAVRA.findall(assunto or "") if t.lower() not in _COMUNS]
    con = bc.conectar()
    try:
        with con.cursor() as k:
            if termos:
                # casa por palavra-chave OU por texto: a chave acha o que foi
                # indexado, o texto acha o que o usuário escreveu diferente
                k.execute("""select id, resumo, detalhe, tipo, criado_em
                               from comercialradar.memoria
                              where chaves && %s
                                 or resumo ilike any(%s)
                              order by criado_em desc limit %s""",
                          ([t.lower() for t in termos],
                           [f"%{t}%" for t in termos], MAX_INDICE))
            else:
                k.execute("""select id, resumo, detalhe, tipo, criado_em
                               from comercialradar.memoria
                              order by criado_em desc limit %s""", (MAX_INDICE,))
            linhas = k.fetchall()

            if linhas:
                k.execute("""update comercialradar.memoria
                                set usado_em = now(),
                                    vezes_usada = vezes_usada + 1
                              where id = any(%s)""", ([l[0] for l in linhas],))
                con.commit()

        itens = []
        for i, resumo, detalhe, tipo, quando in linhas:
            d = {"id": i, "tipo": tipo, "resumo": resumo,
                 "quando": quando.strftime("%d/%m/%Y")}
            if detalhado and detalhe:
                d["detalhe"] = detalhe
            elif detalhe:
                # o índice diz que HÁ detalhe sem despejá-lo — quem quiser
                # chama de novo com `detalhado`
                d["tem_detalhe"] = True
            itens.append(d)
        return {"assunto": assunto or "(recentes)", "encontradas": len(itens),
                "memorias": itens,
                "nota": ("nada guardado sobre isso ainda" if not itens else None)}
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        con.close()


ESQUEMA = [
    {"type": "function", "function": {
        "name": "recordar",
        "description": (
            "O que ja se sabe sobre um assunto, de conversas ANTERIORES. "
            "Chame ANTES de responder sobre iFood, Receita, cruzamentos, "
            "extracao ou qualquer decisao do projeto — evita repetir tentativa "
            "que ja falhou e contradizer o que foi decidido. Sem assunto, "
            "devolve as memorias mais recentes."),
        "parameters": {"type": "object", "properties": {
            "assunto": {"type": "string"},
            "detalhado": {"type": "boolean",
                          "description": "traz o texto completo, nao so o resumo"}}}}},
    {"type": "function", "function": {
        "name": "lembrar",
        "description": (
            "Guarda algo que deve sobreviver a esta conversa. Use quando algo "
            "for CONCLUIDO: um fato descoberto, uma decisao tomada, uma "
            "preferencia do usuario, ou um limite (o que nao funciona). "
            "Guarde a conclusao, nao a narrativa."),
        "parameters": {"type": "object", "properties": {
            "resumo": {"type": "string",
                       "description": "uma frase, como se diria a um colega"},
            "detalhe": {"type": "string"},
            "tipo": {"type": "string",
                     "enum": ["fato", "decisao", "preferencia", "limite"]}},
            "required": ["resumo"]}}},
]
