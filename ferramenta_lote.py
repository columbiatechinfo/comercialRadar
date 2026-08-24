# -*- coding: utf-8 -*-
"""Busca vários estabelecimentos ao mesmo tempo, um trabalhador para cada.

POR QUE PARALELO, E POR QUE NÃO TOTALMENTE

Perguntar por dez lojas em sequência custa dez vezes o tempo de uma. Mas os
degraus da escada têm naturezas diferentes:

    Receita, OSM, banco    consulta de rede ou SQL — leves, escalam bem
    busca web              rede, mas com o SearXNG no meio
    Maps, Instagram        abrem CHROMIUM — ~300 MB cada, e é aqui que quebra

Por isso a concorrência é de dois níveis: os degraus baratos rodam soltos, e os
que abrem navegador passam por um portão. Deixar tudo solto derruba a máquina
com dez Chromiums; serializar tudo desperdiça a parte que é só rede.

O TETO É MEDIDO, NÃO CHUTADO

`PESADOS_SIMULTANEOS` segue `MAPS_SESSOES`, o tamanho da piscina de sessões do
Maps. No notebook, 1 a 3 (cada Chromium come ~300 MB); o i9, com 51 GB livres,
comporta dezesseis. Medido com 3: 61 s contra 146 s em fila.
"""
from __future__ import annotations

import asyncio
import time

# Navegadores ao mesmo tempo. ACOMPANHA A PISCINA do `ferramenta_maps`: pedir
# mais trabalhadores que sessões só produz fila, e pedir menos deixa sessão
# ociosa. Um número solto aqui envelhece na primeira vez que a piscina muda.
#
# Era 1 por um motivo que deixou de existir: a sessão do Maps foi singleton, e
# dois trabalhadores na mesma sessão se atropelavam — o lote voltava ora certo,
# ora com `KeyError`. Com a piscina, cada vaga tem sessão e perfil próprios.
import os as _os
PESADOS_SIMULTANEOS = max(1, int(_os.environ.get("MAPS_SESSOES", "1")))
# Teto de itens por pedido: acima disso o usuário espera demais por um bloco só
MAX_ITENS = 20

_PORTAO: asyncio.Semaphore | None = None


def _portao() -> asyncio.Semaphore:
    global _PORTAO
    if _PORTAO is None:
        _PORTAO = asyncio.Semaphore(PESADOS_SIMULTANEOS)
    return _PORTAO


async def _leves(nome: str, cidade: str, uf: str) -> dict:
    """Os degraus baratos, todos ao mesmo tempo — são só rede."""
    import agente_local as A

    laco = asyncio.get_event_loop()

    async def num(fn, *a, **kw):
        # as ferramentas são síncronas; vão para a piscina de threads para não
        # travarem o laço enquanto esperam rede
        return await laco.run_in_executor(None, lambda: fn(*a, **kw))

    receita, lugar, web = await asyncio.gather(
        num(A.consultar_receita, nome=nome, uf=uf, municipio=cidade),
        num(A.buscar_lugar, nome, cidade, uf),
        num(A.buscar_web, f"{nome} {cidade} {uf} endereço telefone", 5),
        return_exceptions=True,
    )
    return {"receita": receita, "lugar": lugar, "web": web}


async def _pesados(nome: str, cidade: str) -> dict:
    """Maps e Instagram — passam pelo portão, um navegador por vez por vaga."""
    import agente_local as A

    laco = asyncio.get_event_loop()
    async with _portao():
        maps = await laco.run_in_executor(
            None, lambda: A.consultar_maps(nome, cidade))
    return {"maps": maps}


def _resumir(nome: str, r: dict) -> dict:
    """Junta o que cada degrau trouxe, dizendo DE ONDE veio cada campo.

    A procedência é montada aqui, no código, e não pedida ao modelo — foi
    justamente onde ele inventou "Fonte: Econodata" para um dado de busca.
    """
    saida = {"nome_pedido": nome, "endereco": None, "telefone": None,
             "cnpj": None, "fontes": {}, "alertas": []}

    maps = r.get("maps") or {}
    if isinstance(maps, dict) and maps.get("endereco"):
        saida["endereco"] = maps["endereco"]
        saida["fontes"]["endereco"] = "consultar_maps"
        if maps.get("telefone"):
            saida["telefone"] = maps["telefone"]
            saida["fontes"]["telefone"] = "consultar_maps"
        if maps.get("confere_com_o_nome_pedido") is False:
            saida["alertas"].append(
                "o Maps abriu OUTRO estabelecimento; o endereço pode não ser deste")

    lugar = r.get("lugar") or {}
    if not saida["endereco"] and isinstance(lugar, dict) and lugar.get("lugares"):
        p = lugar["lugares"][0]
        if p.get("rua"):
            saida["endereco"] = f"{p['rua']}, {p.get('numero') or 's/n'}"
            saida["fontes"]["endereco"] = "buscar_lugar (OpenStreetMap)"

    rec = r.get("receita") or {}
    if isinstance(rec, dict):
        empresas = rec.get("empresas") or []
        if len(empresas) == 1:
            saida["cnpj"] = empresas[0]["cnpj"]
            saida["fontes"]["cnpj"] = "consultar_receita"
        elif len(empresas) > 1:
            # ambiguidade se registra, não se resolve no chute
            saida["cnpj"] = [e["cnpj"] for e in empresas[:5]]
            saida["fontes"]["cnpj"] = "consultar_receita (VÁRIOS candidatos)"
            saida["alertas"].append(
                f"{len(empresas)} empresas com esse nome na cidade — escolha humana")
        if rec.get("nota"):
            saida["alertas"].append(rec["nota"])

    if not saida["endereco"]:
        saida["alertas"].append("nenhum degrau trouxe endereço")
    return saida


async def _pela_porta(d: dict, cidade: str, uf: str) -> None:
    """Faltou CNPJ e sobrou endereço? Pergunta à Receita pela PORTA.

    Existe porque o paralelo quebrou um encadeamento que a conversa tinha: o
    endereço só nasce depois do Maps, então a consulta por nome — que roda ao
    mesmo tempo — nunca pôde usá-lo. Num teste, "Atacadao Bela Vista" não casou
    com o nome fantasia "ATACADAO" e o CNPJ ficou vazio, embora estivesse na
    base, no mesmo endereço.
    """
    import agente_local as A

    if d.get("cnpj") or not d.get("endereco"):
        return
    # só o logradouro: a Receita guarda a via sem o tipo e sem complemento
    via = d["endereco"].split(",")[0].strip()
    if len(via) < 5:
        return

    # O NÚMERO VAI JUNTO NA CONSULTA. Filtrar em Python depois era inútil: o
    # `limit 15` já tinha cortado, e numa avenida com centenas de empresas a do
    # número certo quase nunca cai nas quinze que passaram. Assim o banco faz o
    # recorte, e o que volta já é da porta.
    numero = ""
    partes = d["endereco"].split(",")
    if len(partes) > 1:
        numero = "".join(c for c in partes[1] if c.isdigit())
    if not numero:
        d["alertas"].append(
            "endereço do Maps sem número — não dá para casar CNPJ pela porta")
        return

    laco = asyncio.get_event_loop()
    rec = await laco.run_in_executor(None, lambda: A.consultar_receita(
        endereco=via, numero=numero, uf=uf, municipio=cidade))
    if not isinstance(rec, dict):
        return
    empresas = rec.get("empresas") or []
    if not empresas:
        d["alertas"].append(
            f"nenhuma empresa ativa na Receita em '{via}, {numero}' — pode ser "
            f"MEI, ou o número na Receita vir como 'ND'.")
        return

    # O que voltou JÁ é da porta certa — o banco filtrou. Rua em comum não é
    # endereço em comum: sem o número batendo não há cruzamento, e o código
    # antes caía para "qualquer empresa da rua". Isso entregou o Assaí
    # Atacadista (Gonçalo Nunes 1000) como L.A.R ARQUITETURA E CONSTRUÇÃO
    # (Gonçalo Nunes 2131), carimbado de "casado pela porta". Nunca mais.
    mesmos = empresas
    if len(mesmos) == 1:
        d["cnpj"] = mesmos[0]["cnpj"]
        d["fontes"]["cnpj"] = "consultar_receita (pelo ENDEREÇO do Maps)"
        d["alertas"].append(
            f"CNPJ casado pela porta, não pelo nome: na Receita o "
            f"estabelecimento é '{mesmos[0].get('nome_fantasia') or mesmos[0].get('razao_social')}'. "
            f"Confirme que é o mesmo negócio.")
    else:
        # TODOS, não os cinco primeiros. O corte em 5 fazia o aviso dizer
        # "6 CNPJs" e a lista mostrar 5 — quem lê conclui que viu tudo e
        # escolhe entre os errados. Truncar calado é pior que lista longa.
        d["cnpj"] = [e["cnpj"] for e in mesmos]
        d["fontes"]["cnpj"] = "consultar_receita (pelo ENDEREÇO, VÁRIOS)"
        d["alertas"].append(
            f"{len(mesmos)} CNPJs ativos nesse endereço, TODOS listados — "
            f"escolha humana. Nomes: "
            + "; ".join((e.get("nome_fantasia") or e.get("razao_social") or "?")[:28]
                        for e in mesmos))


async def _um(nome: str, cidade: str, uf: str) -> dict:
    t0 = time.time()
    try:
        leves, pesados = await asyncio.gather(_leves(nome, cidade, uf),
                                              _pesados(nome, cidade))
        r = {**leves, **pesados}
        d = _resumir(nome, r)
        # segundo passo, agora que o endereço existe: o paralelo não podia
        # encadear isto, e sem ele o CNPJ ficava vazio mesmo estando na base
        await _pela_porta(d, cidade, uf)
    except Exception as e:
        d = {"nome_pedido": nome, "erro": f"{type(e).__name__}: {str(e)[:160]}"}
    d["segundos"] = round(time.time() - t0)
    return d


async def _todos(nomes: list, cidade: str, uf: str) -> list:
    return list(await asyncio.gather(*[_um(n, cidade, uf) for n in nomes]))


def buscar_varios(nomes: list, cidade: str = "", uf: str = "") -> dict:
    """Busca vários estabelecimentos ao mesmo tempo.

    Cada nome ganha um trabalhador. Os degraus de rede correm soltos; os que
    abrem navegador passam por um portão de `PESADOS_SIMULTANEOS` vagas.
    """
    if isinstance(nomes, str):
        nomes = [n.strip() for n in nomes.split(";") if n.strip()]
    nomes = [n for n in (nomes or []) if str(n).strip()][:MAX_ITENS]
    if not nomes:
        return {"erro": "informe ao menos um nome"}

    from ferramenta_maps import _rodar   # o laço vivo, que a sessão do Maps usa

    t0 = time.time()
    try:
        itens = _rodar(_todos(nomes, cidade, uf), 60 * len(nomes) + 120)
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:200]}"}

    total = round(time.time() - t0)
    achou = sum(1 for i in itens if i.get("endereco"))
    return {
        "pedidos": len(nomes), "com_endereco": achou,
        "segundos_total": total,
        "segundos_se_fosse_em_fila": sum(i.get("segundos", 0) for i in itens),
        "resultados": itens,
    }


ESQUEMA = {
    "type": "function", "function": {
        "name": "buscar_varios",
        "description": (
            "Busca VARIOS estabelecimentos ao mesmo tempo, um trabalhador para "
            "cada. Use quando o usuario pedir dados de mais de um ponto — e "
            "prefira esta a chamar as ferramentas uma por uma, porque ela roda "
            "em paralelo. Devolve endereco, telefone e CNPJ de cada um, com a "
            "FONTE de cada campo e alertas de ambiguidade. Ate 20 por pedido."),
        "parameters": {"type": "object", "properties": {
            "nomes": {"type": "array", "items": {"type": "string"}},
            "cidade": {"type": "string"},
            "uf": {"type": "string"}}, "required": ["nomes"]}},
}
