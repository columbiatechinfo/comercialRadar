# -*- coding: utf-8 -*-
"""julgar_par_banco.py — a IA da Spark decide os pares que a evidência não fecha.

DIFERENÇA PARA O `julgar_fusao.py`

Aquele MEDE: lê os pares que a skill de extração já fundiu e diz quantos ela
errou, sobre arquivos parquet, para que a decisão sobre o motor de dedup seja
tomada em cima de número. Ele não muda nada.

Este DECIDE, sobre pares que vêm do banco e que ainda não foram fundidos. O
`evidencia.py` já separou o que é claro — o que chega aqui é o meio-termo que
ele marcou como `perguntar`.

"OS DADOS ENVIADOS PARA AVALIAÇÃO DEVEM SER MAIS COMPLETOS"

Pedido do dono do produto, 25/08/2026, e ele conserta um defeito do julgamento
anterior: lá o par ia com nome, categoria e distância, e só. Um par como
"Óptica Vision" x "Vision Center" é indecidível assim; com o endereço
normalizado, o telefone, o domínio, o CNPJ e a razão social na mesa, ele deixa
de ser. Aqui vai tudo que o banco tem sobre os dois — e a evidência que já foi
apurada vai junto, escrita, porque ela é o contexto da pergunta.

A IA É A DA SPARK. O i9 nunca carrega modelo, e `_conferir_endpoint` recusa o
endereço dele.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import config  # noqa: F401

import evidencia as ev
from segmentar_endereco import SPARK, MODELO, _conferir_endpoint, _extrair_json

# LOTE 4 E 64 THREADS. Os dois números foram MEDIDOS, 26/08/2026, sobre 400
# pares reais de Cachoeirinha — e o resultado contraria a intuição.
#
# Aumentar o lote parecia o caminho óbvio para ir mais rápido. Não é:
#
#   lote  thr    seg   concorda com o lote 4
#      4   16   57,2   (referência)
#      4   16   57,2   99%  <- o modelo discordando de SI MESMO
#     10   32   52,0   94%
#     20   32   83,5   94%
#      4   32   36,4   99%
#      4   64   25,6   99%
#
# DUAS COISAS ESTÃO NESSA TABELA.
#
# A primeira: lote maior NÃO acelera. Com 400 pares, lote 10 produz 40 chamadas
# e lote 4 produz 100 — e cada chamada de 10 pares gera 2,5x mais token de
# saída. O tempo total é ditado pela chamada mais lenta, não pelo número delas,
# então engordar o lote troca muitas chamadas curtas por poucas chamadas longas
# e ainda subutiliza o pool. Em lote 20 o efeito já é francamente negativo.
#
# A segunda, e a que decide: lote maior MUDA A RESPOSTA. A linha de controle
# existe para isso — rodar a mesma configuração duas vezes concorda em 99%, que
# é o não-determinismo próprio do modelo. O lote 10 concorda em 94%: seis vezes
# o ruído, ou seja, 6% dos pares recebem outro veredito por causa do tamanho do
# lote. Com muitos pares numa janela só, a atenção do modelo se dilui e ele
# passa a responder pior sobre cada um.
#
# O ganho estava no PARALELISMO, não no lote: 16 -> 64 threads corta o tempo
# pela metade e concorda em 99%, dentro do ruído. Escala até 128 (18,2 s), mas
# dali para cima são 33% mais threads por 13% de ganho, e a Spark também atende
# a segmentação de endereço.
#
# `SPARK_THREADS` no ambiente sobrepõe.
LOTE = 4
THREADS = int(os.environ.get("SPARK_THREADS", "64"))

VEREDITOS = {"MESMO", "DIFERENTE", "INCERTO"}

PROMPT = """Você decide se dois registros são o MESMO estabelecimento comercial ou DOIS estabelecimentos diferentes.

Eles vieram de fontes diferentes (bases públicas, captura do Google Maps, iFood, cadastro da concessionária) e estão próximos um do outro. A evidência apurada por regra está descrita em cada par; ela é o contexto, não a resposta.

MESMO: são o mesmo negócio escrito de dois jeitos.
  - nome fantasia x razão social ("Rissul" e "Alimentícia Unidasul")
  - abreviação x extenso ("Arco Ens Esc Est Fun Iris" e "Arco Ensino Escola Estadual Fundamental Iris")
  - o mesmo negócio com um qualificador a mais ("M Rolamentos" e "M Peças Automotivas Industriais")
  - a mesma loja com e sem a rede ("Farmácia São João" e "São João Farmácias - Centro")

DIFERENTE: são dois negócios distintos, ainda que dividam telefone, dono, prédio ou endereço.
  - ramos incompatíveis ("Bah Burger" e "Figurati Pizza Napolitana")
  - dois negócios do mesmo dono ("Cabelereira Vera" e "Locação Vera Vestidos")
  - lojas vizinhas numa galeria com o telefone da administração
  - duas salas do mesmo prédio comercial no mesmo número

O QUE PESA, e nesta ordem:
  1. ENDEREÇO. Mesma rua e mesmo número é o indício mais forte — mas prédio comercial tem muitas portas no mesmo número.
  2. SITE. Domínio próprio pertence a um negócio. Domínio de rede social não identifica nada.
  3. TELEFONE. NÃO é prova: no varejo brasileiro o mesmo número atende dois negócios do mesmo dono, ou é o número da galeria.
  4. CNPJ diferente entre os dois é indício FORTE de DIFERENTE.

INCERTO: quando não dá para decidir com o que está escrito. É resposta legítima — não chute.

Responda APENAS um array JSON, um objeto por par, na MESMA ORDEM, com as chaves:
  "veredito": "MESMO" | "DIFERENTE" | "INCERTO"
  "motivo": no maximo 14 palavras

PARES:
"""


def _lado(p: dict, letra: str) -> str:
    """Tudo que o banco tem sobre um lado do par, sem campo vazio.

    Campo vazio impresso como "?" gasta token e ensina o modelo a ver ausência
    onde ela não importa. O que não existe simplesmente não aparece.

    E VAZIO INCLUI A PALAVRA "nan". Medido 25/08/2026: 28.394 POIs têm
    `website = 'nan'` e 17.193 têm `telefone = 'nan'` — um NaN do pandas que
    virou string numa ingestão. O `evidencia.py` já os trata como ausência; se
    aqui eles chegassem à IA como texto, ela leria "os dois têm o mesmo
    telefone: nan" e decidiria por um dado que não existe.
    """
    linhas = [f'{letra}: "{p.get("nome") or "(sem nome)"}"']
    for rot, val in (
            ("fonte", p.get("fonte")),
            ("categoria", p.get("categoria")),
            ("endereço", p.get("endereco")),
            ("logradouro normalizado", p.get("logr_norm")),
            ("telefone", p.get("telefone")),
            ("site", p.get("site")),
            ("CNPJ", p.get("cnpj")),
            ("razão social", p.get("razao_social")),
            ("nome fantasia", p.get("nome_fantasia")),
            ("CNAE", p.get("cnae")),
    ):
        v = str(val or "").strip()
        if v and ev._sem_acento(v) not in ev._VAZIO:
            linhas.append(f"   {rot}: {v}")
    return "\n".join(linhas)


def _descrever(par: dict) -> str:
    # `e`, e nao `ev`: o modulo `evidencia` esta importado como `ev` no topo,
    # e uma local com esse nome o sombreia. Hoje nao quebra — `_lado` e outro
    # escopo — mas e armadilha armada para quem vier acrescentar um campo.
    e = par.get("evidencia") or {}
    motivos = " · ".join(e.get("motivos") or []) or "nenhuma evidência forte"
    return (f'{_lado(par["a"], "A")}\n'
            f'{_lado(par["b"], "B")}\n'
            f'   distância: {e.get("dist_m", 0):.0f} m\n'
            f'   evidência apurada: {motivos}')


def _chamar(lote: list) -> list:
    _conferir_endpoint(SPARK)
    texto = "\n\n".join(f"{i + 1}.\n{_descrever(p)}" for i, p in enumerate(lote))
    corpo = {"model": MODELO,
             "messages": [{"role": "user", "content": PROMPT + texto}],
             "temperature": 0,
             "max_tokens": 260 * len(lote) + 400}
    req = urllib.request.Request(
        f"{SPARK}/chat/completions", method="POST",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    txt = (d["choices"][0]["message"].get("content") or "").strip()
    return _extrair_json(txt)


def julgar(pares: list, threads: int = THREADS, verboso: bool = True) -> list:
    """Devolve os pares com `veredito` e `motivo_ia`.

    Lote que falhou é MARCADO, não descartado: sumir com o par faria a fusão
    passar por cima dele em silêncio, e o operador nunca saberia que aquele
    ponto ficou sem decisão.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not pares:
        return []
    lotes = [pares[k:k + LOTE] for k in range(0, len(pares), LOTE)]

    def _um(lote):
        try:
            lidos = _chamar(lote)
        except (urllib.error.URLError, ValueError, json.JSONDecodeError,
                OSError, KeyError, IndexError) as erro:
            return [dict(p, veredito="FALHOU",
                         motivo_ia=f"{type(erro).__name__}: {erro}") for p in lote]
        saida = []
        for i, p in enumerate(lote):
            d = lidos[i] if i < len(lidos) and isinstance(lidos[i], dict) else {}
            v = str(d.get("veredito") or "").strip().upper()
            # Veredito fora do vocabulário vira INCERTO em vez de virar decisão
            # inventada: o modelo às vezes responde "PROVAVELMENTE DIFERENTE",
            # e tratar isso como MESMO funde por engano de parsing.
            if v not in VEREDITOS:
                v = "INCERTO"
            saida.append(dict(p, veredito=v, motivo_ia=str(d.get("motivo") or "")[:200]))
        return saida

    resultado, feitos = [], 0
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for lote in pool.map(_um, lotes):
            resultado.extend(lote)
            feitos += 1
            if verboso and (feitos % 5 == 0 or feitos == len(lotes)):
                print(f"    IA: lote {feitos}/{len(lotes)} · {len(resultado)} pares",
                      flush=True)
    return resultado
