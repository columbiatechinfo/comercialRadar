# -*- coding: utf-8 -*-
"""Ponte entre as lojas do iFood e o pipeline de busca que já existe.

O iFood entrega nome, categoria e bairro — e para no endereço, porque o
`merchant-info/graphql` devolve 403 para automação (medido, cinco caminhos).
Mas endereço a partir de nome é problema que este projeto já resolve, e resolve
para os POIs todo dia: `search_from_sheet.py` busca no Google Maps com a
metodologia v2, e o que o Maps não achar cai para o `minerar_web.py`.

Este módulo faz os dois lados da ponte:

    --exportar   grava a planilha que o `search_from_sheet.py` consome
    --importar   lê o `_db.json` que ele produz e devolve ao `ifood_merchant`

O bairro entra como dica de endereço de propósito. "Dogão do Rei" sozinho acha
qualquer coisa no Brasil; "Dogão do Rei, Mathias Velho, Canoas - RS" acha o
certo, e é a mesma dica que uma pessoa daria.

Uso:
    python ifood_para_busca.py --exportar ifood_canoas.csv
    py search_from_sheet.py ifood_canoas.csv --cidade "Canoas RS"
    python ifood_para_busca.py --importar ifood_canoas_db.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys

from psycopg2.extras import execute_values

import unicodedata

import base_comum as bc


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")

SELECIONAR = """
select merchant_id, nome, categoria, bairro, cidade, uf
  from comercialradar.ifood_merchant
 where nome is not null and coalesce(rua, '') = ''
   and (%(cidade)s is null or cidade = %(cidade)s)
 order by nome
"""

_RE_CEP = re.compile(r"\b(\d{5})-?(\d{3})\b")
_RE_NUM = re.compile(r",\s*(\d{1,6})\b")


def _digitos(s) -> str | None:
    d = "".join(c for c in str(s or "") if c.isdigit())
    return d or None


def _cidade_bonita(c: str | None, uf: str | None) -> str:
    """`porto-alegre` + `RS` -> `Porto Alegre - RS`."""
    nome = " ".join(w.capitalize() if w not in {"de", "da", "do", "dos", "das"}
                    else w for w in (c or "").split("-"))
    return f"{nome} - {uf}" if uf else nome


def exportar(caminho: str, cidade: str, limite: int) -> int:
    con = bc.conectar()
    with con.cursor() as k:
        k.execute(SELECIONAR, {"cidade": cidade or None})
        linhas = k.fetchall()
    con.close()
    if limite:
        linhas = linhas[:limite]

    with open(caminho, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        # `merchant_id` viaja junto para o reencontro ser por CHAVE e não por
        # nome. Reencontrar por nome depois de o Maps ter normalizado o nome é
        # como perder o dado duas vezes.
        w.writerow(["nome", "endereco", "merchant_id"])
        for mid, nome, categoria, bairro, cid, uf in linhas:
            # A cidade da DICA é a da LOJA, lida do slug do iFood — não a da
            # busca. O feed cobre 14 km e traz Gravataí, Porto Alegre e
            # Cachoeirinha junto; mandar "Canoas" para todas fazia o Maps
            # procurar no lugar errado e depois o resultado certo ser
            # descartado como "outra cidade".
            local = _cidade_bonita(cid, uf)
            dica = f"{bairro}, {local}" if bairro else local
            w.writerow([nome, dica, mid])
    print(f"{len(linhas)} lojas em {caminho}", flush=True)
    print(f"agora:  py search_from_sheet.py {caminho}", flush=True)
    return 0


def _partes(endereco: str | None) -> tuple:
    """(rua, número, CEP) do endereço em texto que o Maps devolveu."""
    if not endereco:
        return (None, None, None)
    cep = None
    m = _RE_CEP.search(endereco)
    if m:
        cep = m.group(1) + m.group(2)
    num = None
    mn = _RE_NUM.search(endereco)
    if mn:
        num = mn.group(1)
    return (endereco.split(",")[0].strip() or None, num, cep)


GRAVAR = """
update comercialradar.ifood_merchant as m set
  rua      = coalesce(v.rua, m.rua),
  numero   = coalesce(v.numero, m.numero),
  cep      = coalesce(v.cep, m.cep),
  lat      = coalesce(v.lat, m.lat),
  lng      = coalesce(v.lng, m.lng),
  telefone = coalesce(v.telefone, m.telefone),
  -- OK só quando de fato veio endereço. Marcar como colhido sem dado faria a
  -- loja nunca mais ser tentada, e "não achei" viraria "não existe".
  estado_detalhe = case when v.rua is not null then 'OK' else 'SEM_RETORNO' end,
  bruto    = coalesce(m.bruto, '{}'::jsonb) || v.extra::jsonb,
  visto_em = now()
from (values %s) as v(merchant_id, rua, numero, cep, lat, lng, telefone, extra)
where m.merchant_id = v.merchant_id
"""


def _fora_da_cidade(endereco: str, cidade: str) -> bool:
    """O endereço menciona outra cidade que não a procurada?"""
    alvo = _sem_acento(cidade.split("-")[0])
    txt = _sem_acento(endereco)
    return alvo not in txt


def importar(caminho: str, planilha: str, cidade: str, simular: bool) -> int:
    """Reencontra cada resultado com a loja de origem.

    O `search_from_sheet.py` só carrega adiante as colunas que conhece — `nome`,
    `endereco`, `lat`, `lng`, `uf` —, então o `merchant_id` que eu pus no CSV
    NÃO volta. Mas ele preserva `nome_planilha`, o nome exatamente como estava
    na planilha, antes de o Maps normalizar. É por esse que se reencontra.

    Casar pelo nome que o MAPS devolveu seria errado: ele corrige grafia e
    acrescenta rede ("Burger King - Canoas" vira "Burger King"), e aí o
    resultado voltaria para a loja errada ou para nenhuma.
    """
    with open(caminho, encoding="utf-8") as fh:
        dados = json.load(fh)
    regs = dados if isinstance(dados, list) else dados.get("pois", dados)

    con0 = bc.conectar()
    with con0.cursor() as k0:
        k0.execute("select merchant_id, cidade, uf from comercialradar.ifood_merchant")
        de_cidade = {m: _cidade_bonita(c, u) for m, c, u in k0.fetchall()}
    con0.close()

    de_nome = {}
    with open(planilha, encoding="utf-8-sig") as fh:
        for linha in csv.DictReader(fh):
            # o buscador APARA o nome ao ler a planilha; sem aparar aqui,
            # toda loja com espaço sobrando vira órfã na volta
            de_nome.setdefault((linha["nome"] or "").strip(), []).append(
                linha["merchant_id"])

    linhas, achou, sem, orfaos, fora = [], 0, 0, 0, 0
    for r in regs:
        if not isinstance(r, dict):
            continue
        # consome em ordem quando o mesmo nome aparece mais de uma vez na
        # planilha — duas lojas homônimas em bairros diferentes existem
        fila = de_nome.get((r.get("nome_planilha") or "").strip(), [])
        mid = fila.pop(0) if fila else None
        if not mid:
            orfaos += 1
            continue
        endereco = r.get("endereco") or r.get("endereco_original")
        # GUARDA DE CIDADE. O Maps devolve o melhor palpite dele, e sem isso
        # aceita endereço de outra cidade: "A Casa do Yakissoba" voltou como
        # Av. Benjamin Constant, PORTO ALEGRE, para uma loja de Canoas. Um
        # endereço fora da cidade não é dado fraco — é dado errado, e no mapa
        # vira um ponto que ninguém encontra.
        if endereco and _fora_da_cidade(endereco, de_cidade.get(mid, cidade)):
            fora += 1
            endereco = None
        rua, num, cep = _partes(endereco)
        if rua:
            achou += 1
        else:
            sem += 1
        linhas.append((
            mid, rua, num, cep,
            r.get("maps_lat") or r.get("lat"),
            r.get("maps_lng") or r.get("lng"),
            _digitos(r.get("telefone")),
            json.dumps({"origem_endereco": "google_maps",
                        "place_id": r.get("place_id"),
                        "nome_maps": r.get("nome"),
                        "status_maps": r.get("status")}, ensure_ascii=False)))

    print(f"{len(linhas)} registros · {achou} com endereço · {sem} sem"
          + (f" · {orfaos} sem loja de origem" if orfaos else "")
          + (f" · {fora} descartados por serem de outra cidade" if fora else ""),
          flush=True)
    if not linhas or simular:
        if simular:
            print("(simulação — nada gravado)", flush=True)
        return 0
    con = bc.conectar()
    with con.cursor() as k:
        execute_values(k, GRAVAR, linhas, page_size=500)
        n = k.rowcount
    con.commit()
    con.close()
    print(f"gravadas {n}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exportar", metavar="CSV")
    p.add_argument("--importar", metavar="JSON")
    p.add_argument("--planilha", metavar="CSV",
                   help="o CSV exportado, para reencontrar o merchant_id")
    p.add_argument("--cidade", default="Canoas - RS")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--simular", action="store_true")
    a = p.parse_args()
    if a.exportar:
        return exportar(a.exportar, a.cidade, a.limit)
    if a.importar:
        if not a.planilha:
            print("! --importar precisa de --planilha (o CSV exportado)",
                  flush=True)
            return 1
        return importar(a.importar, a.planilha, a.cidade, a.simular)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
