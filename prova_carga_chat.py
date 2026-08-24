# -*- coding: utf-8 -*-
"""Cinco buscas ao mesmo tempo, um chat para cada — taxa de acerto e tempo.

O QUE ISTO MEDE, e por que medir importa

Até agora cada melhoria foi verificada num caso por vez. Um caso não diz taxa:
diz que aquele caso funcionou. Cinco estabelecimentos diferentes, rodando ao
mesmo tempo em conversas separadas, respondem outras perguntas:

    quantos devolvem endereço, telefone, horário, avaliações, rede social e CNPJ
    quanto custa cada um, e quanto custa o conjunto
    o paralelo degrada a qualidade, ou só divide o tempo

CADA UM EM SEU CHAT, e não cinco perguntas num chat só, porque conversa longa
comprovadamente contamina: numa de 131 mensagens o modelo copiou o formato das
respostas antigas e omitiu campos que as ferramentas passaram a devolver.
Conversa nova é a condição limpa.

O QUE CONTA COMO ACERTO

Não é "o modelo escreveu algo". É o CAMPO ter valor, conferido no texto da
resposta contra o que as ferramentas devolveram. Uma resposta que diz "não
encontrado" para tudo é rápida e inútil — sem esta separação, ela contaria como
sucesso e o número mentiria a favor.

Uso:
    python prova_carga_chat.py            # os cinco padrão
    python prova_carga_chat.py 3          # só os três primeiros
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time

# Cinco pontos reais, escolhidos para NÃO serem todos fáceis: rede grande com
# muitos CNPJs, loja de bairro, ponto em shopping, um que o Maps nomeia
# diferente do pedido, e um que já se sabe fechado. Cinco lojas simples dariam
# 100% e não informariam nada.
ALVOS = [
    ("Supermercado Vancosty", "Canoas", "RS"),
    ("Espetao Vancosty", "Canoas", "RS"),
    ("PKC Fusion", "Canoas", "RS"),
    ("Bussbier Cerveja Artesanal e Bebidas", "Canoas", "RS"),
    ("Los Chiapas", "Canoas", "RS"),
    # Do 6 ao 10, tirados da propria base de POIs de Canoas — variedade de ramo
    # de proposito: comercio de rua, servico profissional, oficina. Cinco
    # restaurantes dariam um numero que so vale para restaurante.
    ("Zanardi Padaria & Cafe", "Canoas", "RS"),
    ("Livraria Auxiliadora", "Canoas", "RS"),
    ("Mecanica Trentim", "Canoas", "RS"),
    ("Frutos de Goias Canoas", "Canoas", "RS"),
    ("Bratz Advogados Associados", "Canoas", "RS"),
]

PERGUNTA = ("ache do estabelecimento {nome} em {cidade} {uf}: endereco com "
            "numero, telefone, horario de funcionamento, avaliacoes com data, "
            "redes sociais com a data da ultima publicacao, e o CNPJ "
            "confirmado")

# Como se reconhece que o campo veio. Deliberadamente exigente: "não
# encontrado" na mesma linha derruba o campo, senão a ausência contaria como
# presença e a taxa mentiria a favor.
CAMPOS = {
    "endereco": r"\b(rua|av\.?|avenida|r\.)\s+\w+.{0,60}?\d",
    "telefone": r"\(\d{2}\)\s*\d{4,5}-?\d{4}",
    "horario": r"\d{1,2}:\d{2}\s*(a|às|as|-|–)\s*\d{1,2}:\d{2}",
    "avaliacoes": r"(\d[,.]\d|\d)\s*(estrela|★)|nota:?\s*\d|avalia",
    "rede_social": r"@[\w.]{3,}|instagram\.com/[\w.]{3,}",
    "data_publicacao": r"20\d\d-\d\d-\d\d|\d{1,2} de \w+ de 20\d\d|há \d+ (dias|meses|semanas)",
    "cnpj": r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}",
}


def _tem(texto: str, campo: str) -> bool:
    """O campo aparece COM valor? Linha que nega o campo não conta."""
    alvo = texto.lower()
    for m in re.finditer(CAMPOS[campo], alvo, re.I):
        # a vizinhança da ocorrência não pode ser uma negativa
        volta = alvo[max(0, m.start() - 90):m.start()]
        # `confirmad` faltava aqui, e "O CNPJ nao foi confirmado. 00.890.225/
        # 0005-94 aparece na Receita mas em outro endereco" contava como CNPJ
        # ACHADO. O instrumento inflava a propria nota — o teste pegou.
        if re.search(r"n[aã]o (foi |e |é |ha |há )?(possível|possivel)?\s*"
                     r"(encontrad|localizad|dispon|confirmad|vinculad|"
                     r"identificad|conclus)", volta):
            continue
        return True
    return False


def _uma(nome, cidade, uf, saida, indice):
    """Uma busca completa, numa conversa própria, do começo ao fim."""
    import agente_local as A

    t0 = time.time()
    passos = []
    registro = {"alvo": nome, "ferramentas": passos}
    try:
        # historico VAZIO de proposito: e o chat novo de cada um
        texto, _ = A.conversar(
            PERGUNTA.format(nome=nome, cidade=cidade, uf=uf), [],
            lambda tipo, d: passos.append(d["nome"])
            if tipo == "ferramenta" else None)
        registro["campos"] = {c: _tem(texto, c) for c in CAMPOS}
        registro["resposta"] = texto
    except Exception as e:
        registro["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
        registro["campos"] = {c: False for c in CAMPOS}
    registro["segundos"] = round(time.time() - t0, 1)
    saida[indice] = registro


def rodar(quantos: int = 5) -> dict:
    alvos = ALVOS[:max(1, min(quantos, len(ALVOS)))]
    saida = [None] * len(alvos)

    # Threads, e nao asyncio: `conversar` e sincrono do comeco ao fim, e cada um
    # e um chat independente — nao ha estado compartilhado a proteger. O que
    # eles disputam e a piscina de sessoes do Maps, que ja tem portao proprio.
    t0 = time.time()
    fios = [threading.Thread(target=_uma, args=(n, c, u, saida, i))
            for i, (n, c, u) in enumerate(alvos)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    total = round(time.time() - t0, 1)

    # FECHAR O QUE FOI ABERTO. Sem isto o processo terminava e deixava DOZE
    # relays vivos — cada um um Python inteiro — porque `encerrar_maps` nunca
    # era chamado. O servidor faz isso no `shutdown`; um script solto tem de
    # fazer explicitamente, senão cada prova de carga custa 600 MB permanentes.
    try:
        import ferramenta_maps as M
        M.encerrar_maps()
    except Exception as e:
        print(f"   aviso: nao consegui fechar as sessoes ({type(e).__name__})")

    fila = sum(r["segundos"] for r in saida if r)
    por_campo = {c: sum(1 for r in saida if r and r["campos"].get(c))
                 for c in CAMPOS}
    return {"pedidos": len(alvos), "segundos_total": total,
            "segundos_se_fosse_em_fila": round(fila, 1),
            "ganho": round(fila / total, 1) if total else None,
            "por_campo": por_campo, "resultados": saida}


if __name__ == "__main__":
    quantos = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    r = rodar(quantos)

    print(f"\n{'='*72}")
    print(f"{r['pedidos']} buscas em paralelo · {r['segundos_total']}s "
          f"(em fila seriam {r['segundos_se_fosse_em_fila']}s · "
          f"ganho {r['ganho']}x)")
    print(f"{'='*72}\n")

    largura = max(len(c) for c in CAMPOS)
    print(f"{'ALVO':38} {'s':>5}  " +
          " ".join(c[:4].upper() for c in CAMPOS))
    for reg in r["resultados"]:
        if not reg:
            continue
        marcas = " ".join(("  ok " if reg["campos"].get(c) else "  -  ")[:4]
                          for c in CAMPOS)
        print(f"{reg['alvo'][:38]:38} {reg['segundos']:>5}  {marcas}"
              + ("   ERRO: " + reg["erro"][:40] if reg.get("erro") else ""))

    print(f"\n{'TAXA POR CAMPO':38}")
    for c, n in r["por_campo"].items():
        pct = round(100 * n / r["pedidos"])
        print(f"  {c:{largura}}  {n}/{r['pedidos']}  {pct:>3}%  "
              + "█" * (pct // 10))

    with open("prova_carga.json", "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    print("\ndetalhe completo em prova_carga.json")
