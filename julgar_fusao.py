# -*- coding: utf-8 -*-
"""julgar_fusao.py — a IA da Spark julga as fusões que o dedup marcou como suspeitas.

POR QUE A IA CABE AQUI

A `extracao-poi-estadual` funde dois registros quando tem prova: mesmo telefone,
mesmo site, nome parecido dentro do raio. Quando funde dois registros da MESMA
fonte com nomes divergentes, ela grava o par em `poi_fusao_suspeita_*` — não
porque errou, mas porque não tem como saber.

E ela não tem mesmo. `loterica schneider` e `bazar mega`, a 147 m, com o mesmo
telefone: pode ser uma loja com dois nomes no cadastro, pode ser o mesmo dono com
dois negócios. Nenhuma regra mecânica separa os dois casos — nem a distância
(medido no RS: a menos de 10 m, 73% dos pares fundidos por telefone já têm nomes
sem um token em comum), nem o "núcleos disjuntos", que é heurística tão frágil
quanto o telefone que ela critica.

**Isto é julgamento semântico sobre nomes de comércio, e é onde a IA é o
instrumento certo** — pela mesma régua que decidiu o resto da cadeia de endereço:
segmentar é leitura (IA), canonizar é prova (skill), e julgar identidade quando
NÃO HÁ prova disponível é leitura de novo.

O QUE ELE FAZ, E O QUE NÃO FAZ

Ele MEDE. Não desfaz fusão, não reescreve entrega, não muda parâmetro de dedup.
Produz um veredito por par, com o motivo escrito, para que a decisão sobre o
motor de dedup seja tomada em cima de número medido em vez de estimativa.

`INCERTO` é resposta legítima e precisa existir: forçar binário faz o modelo
chutar, e chute contado como medição é pior que medição faltando.

USO
    python julgar_fusao.py --saida dados_externos/estadual/RS/saida --amostra 200
    python julgar_fusao.py --saida dados_externos/estadual/RS/saida --tudo --gravar
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random
import urllib.error
import urllib.request
from pathlib import Path

import config  # noqa: F401

from segmentar_endereco import SPARK, MODELO, _conferir_endpoint, _extrair_json

LOTE = 10          # pares por chamada; o julgamento pede mais atenção por item
THREADS = int(os.environ.get("SPARK_THREADS", "16"))

PROMPT = """Você decide se dois registros são o MESMO estabelecimento comercial ou DOIS estabelecimentos diferentes.

Os pares abaixo foram unidos automaticamente por evidência fraca — em geral telefone ou site iguais — apesar de terem nomes diferentes. Sua tarefa é dizer se a união faz sentido.

MESMO: são o mesmo negócio escrito de dois jeitos.
  - nome fantasia x razão social ("Rissul" e "Alimentícia Unidasul")
  - abreviação x extenso ("Arco Ens Esc Est Fun Iris" e "Arco Ensino Escola Estadual Fundamental Iris")
  - o mesmo negócio com um qualificador a mais ("M Rolamentos" e "M Peças Automotivas Industriais")

DIFERENTE: são dois negócios distintos, ainda que dividam telefone, dono ou endereço.
  - ramos incompatíveis ("Bah Burger" e "Figurati Pizza Napolitana")
  - dois negócios do mesmo dono ("Cabelereira Vera" e "Locação Vera Vestidos")
  - lojas vizinhas numa galeria com o telefone da administração

Telefone igual NÃO é prova de mesmo estabelecimento: no varejo brasileiro é comum
o mesmo número atender dois negócios do mesmo dono, ou ser o número da galeria.

INCERTO: quando não dá para decidir com o que está escrito. É resposta legítima — não chute.

Responda APENAS um array JSON, um objeto por par, na MESMA ORDEM, com as chaves:
  "veredito": "MESMO" | "DIFERENTE" | "INCERTO"
  "motivo": uma frase curta dizendo o que decidiu

PARES:
"""


def _brutos(saida: Path) -> dict:
    """`id_fonte -> (nome, categoria, endereco, telefone)`, das três fontes.

    O par vem do arquivo de fusão suspeita só com o NÚCLEO DISCRIMINANTE — o
    nome sem os tokens de contexto. Julgar por ele seria julgar por menos do que
    existe: o contexto removido (o nome do shopping, o bairro) é às vezes o que
    diz que os dois estão na mesma galeria.
    """
    import pandas as pd
    mapa = {}
    for arq in sorted(glob.glob(str(saida / "poi_bruto_*.parquet"))):
        df = pd.read_parquet(arq, columns=["id_fonte", "nome", "categoria_orig",
                                           "endereco_raw", "telefone"])
        for t in df.itertuples(index=False):
            mapa[str(t.id_fonte)] = (
                str(t.nome or ""), str(t.categoria_orig or ""),
                str(t.endereco_raw or ""), str(t.telefone or ""))
    return mapa


def _pares(saida: Path) -> list:
    arqs = glob.glob(str(saida / "poi_fusao_suspeita_*.csv"))
    if not arqs:
        raise SystemExit(f"não achei poi_fusao_suspeita_* em {saida}")
    with open(arqs[0], encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f)]


def _descrever(par: dict, mapa: dict) -> str:
    a = mapa.get(par["id_a"], ("", "", "", ""))
    b = mapa.get(par["id_b"], ("", "", "", ""))
    # Cai para o núcleo quando o bruto não tem o id — melhor julgar com menos do
    # que descartar o par e mentir na taxa.
    na = a[0] or par.get("nucleo_a", "")
    nb = b[0] or par.get("nucleo_b", "")
    return (f'A: "{na}" · categoria: {a[1] or "?"}\n'
            f'B: "{nb}" · categoria: {b[1] or "?"}\n'
            f'distância: {par.get("dist_m", "?")} m · unidos por: {par.get("motivo", "?")}')


def _chamar(pares: list, mapa: dict) -> list:
    _conferir_endpoint(SPARK)
    corpo_texto = "\n\n".join(f"{i + 1}.\n{_descrever(p, mapa)}"
                              for i, p in enumerate(pares))
    corpo = {"model": MODELO,
             "messages": [{"role": "user", "content": PROMPT + corpo_texto}],
             "temperature": 0,
             "max_tokens": 130 * len(pares) + 200}
    req = urllib.request.Request(
        f"{SPARK}/chat/completions", method="POST",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    txt = (d["choices"][0]["message"].get("content") or "").strip()
    if not txt:
        raise ValueError("a Spark devolveu conteúdo vazio")
    return _extrair_json(txt)


VEREDITOS = ("MESMO", "DIFERENTE", "INCERTO")


def julgar(pares: list, mapa: dict, threads: int = THREADS, verboso: bool = True) -> list:
    from concurrent.futures import ThreadPoolExecutor

    lotes = [pares[k:k + LOTE] for k in range(0, len(pares), LOTE)]

    def _um(lote):
        try:
            lidos = _chamar(lote, mapa)
        except (urllib.error.URLError, ValueError, json.JSONDecodeError,
                OSError, KeyError, IndexError) as erro:
            return [dict(p, veredito="FALHOU", motivo=f"{type(erro).__name__}: {erro}")
                    for p in lote]
        saida = []
        for i, p in enumerate(lote):
            d = lidos[i] if i < len(lidos) and isinstance(lidos[i], dict) else {}
            v = str(d.get("veredito") or "").strip().upper()
            # Veredito fora do vocabulário vira INCERTO em vez de virar contagem
            # inventada: o modelo às vezes responde "PROVAVELMENTE DIFERENTE".
            if v not in VEREDITOS:
                v = "INCERTO"
            saida.append(dict(p, veredito=v, motivo=str(d.get("motivo") or "")[:200]))
        return saida

    resultado, feitos = [], 0
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for lote in pool.map(_um, lotes):
            resultado.extend(lote)
            feitos += 1
            if verboso and (feitos % 5 == 0 or feitos == len(lotes)):
                print(f"  lote {feitos}/{len(lotes)} · {len(resultado)} pares", flush=True)
    return resultado


def _resumo(julgados: list) -> None:
    from collections import Counter
    c = Counter(j["veredito"] for j in julgados)
    t = sum(c.values())
    print(f"\n  {t:,} pares julgados")
    for v in ("DIFERENTE", "MESMO", "INCERTO", "FALHOU"):
        if c.get(v):
            print(f"    {v:<10} {c[v]:>6,}  {c[v] / t:>6.1%}")

    por_motivo = {}
    for j in julgados:
        k = j.get("motivo_uniao") or j.get("motivo_dedup") or "?"
        por_motivo.setdefault(k, Counter())[j["veredito"]] += 1
    if len(por_motivo) > 1:
        print("\n  por evidência que uniu:")
        for k, cc in sorted(por_motivo.items(), key=lambda x: -sum(x[1].values())):
            tt = sum(cc.values())
            print(f"    {k:<10} {tt:>6,} pares · DIFERENTE {cc.get('DIFERENTE', 0) / tt:>5.1%}")

    print("\n  amostra do que ele julgou DIFERENTE:")
    for j in [x for x in julgados if x["veredito"] == "DIFERENTE"][:6]:
        print(f"    {j.get('dist_m', '?'):>7}m  {j.get('nucleo_a', '')[:32]:34}"
              f"| {j.get('nucleo_b', '')[:32]}")
        print(f"             {j['motivo'][:88]}")
    print("\n  amostra do que ele julgou MESMO:")
    for j in [x for x in julgados if x["veredito"] == "MESMO"][:4]:
        print(f"    {j.get('dist_m', '?'):>7}m  {j.get('nucleo_a', '')[:32]:34}"
              f"| {j.get('nucleo_b', '')[:32]}")
        print(f"             {j['motivo'][:88]}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", required=True, help="pasta saida/ de uma extração")
    p.add_argument("--amostra", type=int, default=200)
    p.add_argument("--tudo", action="store_true", help="julga todos os pares")
    p.add_argument("--semente", type=int, default=42)
    p.add_argument("--gravar", action="store_true",
                   help="grava julgamento_fusao.csv na pasta da extração")
    a = p.parse_args(argv)

    saida = Path(a.saida)
    todos = _pares(saida)
    print(f"  {len(todos):,} fusões suspeitas em {saida.name}")

    # Amostra ALEATÓRIA com semente fixa: a taxa medida precisa ser reproduzível,
    # e pegar "os primeiros N" mediria a ordem do arquivo, não a base.
    if a.tudo:
        alvo = todos
    else:
        random.Random(a.semente).shuffle(todos)
        alvo = todos[:a.amostra]
    print(f"  julgando {len(alvo):,} · modelo {MODELO}")

    print("  lendo os nomes completos das fontes brutas...")
    mapa = _brutos(saida)
    print(f"  {len(mapa):,} registros de origem indexados")

    julgados = julgar(alvo, mapa)
    for j in julgados:
        j["motivo_uniao"] = j.get("motivo", "")
    # `motivo` do CSV de origem é a evidência que uniu; `motivo` do modelo é o
    # parecer. Guardar os dois com o mesmo nome perderia um deles.
    for j, orig in zip(julgados, alvo):
        j["motivo_uniao"] = orig.get("motivo", "")
    _resumo(julgados)

    if a.gravar:
        destino = saida / "julgamento_fusao.csv"
        campos = ["id_a", "id_b", "motivo_uniao", "dist_m", "nucleo_a", "nucleo_b",
                  "veredito", "motivo"]
        with open(destino, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            w.writerows(julgados)
        print(f"\n  gravado: {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
