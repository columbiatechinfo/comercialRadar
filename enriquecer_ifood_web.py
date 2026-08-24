# -*- coding: utf-8 -*-
"""CNPJ e endereço das lojas do iFood pela camada web que já existe.

Por que por aqui e não pelo iFood:

O `extrair_ifood.py` entrega a LISTA — nome, categoria, nota, bairro, slug — e
isso funciona. O que ele não entrega é CNPJ e endereço: eles vivem no
`merchant-info/graphql`, que responde página de captcha do PerimeterX. Medido
nesta sessão: IP virgem, perfil virgem, desafio na primeira loja.

Mas o dado não é exclusivo do iFood. `Croc Frangos Canoas` no buscador devolve
`Av. das Canoas, 142 - Mato Grande, Canoas - RS, 92323-270` e o telefone, no
painel do próprio buscador. É público, e o `minerar_web.py` já sabe colher isso:
busca no Yahoo (escolhido justamente por NÃO apresentar desafio, ao contrário
do Google), lê snippets e as páginas de topo, filtra CNPJ/CEP/telefone por
regex, estrutura com LLM e confirma o CNPJ na BrasilAPI — dados abertos da
Receita.

Este módulo é só a ponte: lê as lojas PENDENTES do banco, passa cada uma pelo
`_processar_poi` que já existe, e grava de volta o que voltou.

Uso:
    python enriquecer_ifood_web.py --cidade canoas --limit 10     # amostra
    python enriquecer_ifood_web.py --cidade canoas                # tudo
    python enriquecer_ifood_web.py --cidade canoas --no-proxy     # teste
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time

import aiohttp
from psycopg2.extras import execute_values

import base_comum as bc
import minerar_web as MW

# O que o `_processar_poi` devolve e nos interessa. `endereco_fonte` é o
# endereço em texto que ele conseguiu confirmar; `cnpj_conf` é a confiança que
# ele mesmo atribuiu, e é ela que decide o que vai para revisão humana.
CAMPOS = ("cnpj", "cnpj_conf", "endereco_fonte", "telefone", "instagram")

_RE_CEP = re.compile(r"\b(\d{5})-?(\d{3})\b")
# "Av. das Canoas, 142" — o número vem depois da última vírgula ou do último
# espaço, e só conta se for realmente número de porta (até 6 dígitos).
_RE_NUM = re.compile(r",\s*(\d{1,6})\b")


def _partes(endereco: str | None) -> tuple:
    """Extrai (rua, número, CEP) do endereço em texto que a web devolveu.

    Devolve None em cada posição que não der para afirmar. Chutar o número aqui
    contamina o casamento por CEP+número, que é a chave forte contra a Receita.
    """
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
    rua = endereco.split(",")[0].strip() or None
    return (rua, num, cep)


SELECIONAR = """
select merchant_id, nome, bairro
  from comercialradar.ifood_merchant
 where coalesce(cnpj, '') = ''
   and coalesce(estado_detalhe, 'PENDENTE') <> 'OK'
 order by nome
"""

GRAVAR = """
update comercialradar.ifood_merchant as m set
  cnpj      = coalesce(v.cnpj, m.cnpj),
  telefone  = coalesce(v.telefone, m.telefone),
  rua       = coalesce(v.rua, m.rua),
  numero    = coalesce(v.numero, m.numero),
  cep       = coalesce(v.cep, m.cep),
  -- OK só quando veio dado. Sem isso, uma busca infrutífera marcaria a loja
  -- como colhida e ela nunca mais seria tentada.
  estado_detalhe = case when v.cnpj is not null or v.rua is not null
                        then 'OK' else 'SEM_RETORNO' end,
  visto_em  = now()
from (values %s) as v(merchant_id, cnpj, telefone, rua, numero, cep)
where m.merchant_id = v.merchant_id
"""


def _digitos(s) -> str | None:
    d = "".join(c for c in str(s or "") if c.isdigit())
    return d or None


async def rodar(args) -> int:
    con = bc.conectar()
    with con.cursor() as k:
        k.execute(SELECIONAR)
        alvo = [{"merchant_id": mid, "nome": nome, "bairro": bairro}
                for mid, nome, bairro in k.fetchall()]
    if args.limit:
        alvo = alvo[:args.limit]

    print("⟦fase⟧ ifood-web", flush=True)
    print(f"{len(alvo)} lojas sem CNPJ · Yahoo → pré-filtro → "
          f"{MW._LLM_NOME} → BrasilAPI", flush=True)
    if not alvo:
        print("nada a fazer.", flush=True)
        con.close()
        return 0

    cidade = args.cidade.title()
    uf = args.uf.upper()
    t0 = time.time()
    achados = []
    sem = asyncio.Semaphore(args.workers)

    serp = await MW.SerpPool(min(args.workers, 6),
                             usar_proxy=not args.no_proxy).start()
    conn = aiohttp.TCPConnector(limit=args.workers * 4, ssl=False)
    async with aiohttp.ClientSession(connector=conn) as sessao:
        async def um(reg):
            try:
                await MW._processar_poi(sessao, serp, reg, cidade, uf, sem)
            except Exception as e:
                reg["_erro"] = type(e).__name__
            rua, num, cep = _partes(reg.get("endereco_fonte"))
            achados.append((reg["merchant_id"], _digitos(reg.get("cnpj")),
                            _digitos(reg.get("telefone")), rua, num, cep))
            n = len(achados)
            marca = "✓" if reg.get("cnpj") else ("·" if rua else " ")
            print(f"  {n:>4}/{len(alvo)} {marca} {str(reg['nome'])[:32]:<34}"
                  f"{reg.get('cnpj') or '—':<20}"
                  f"{str(reg.get('endereco_fonte') or '')[:44]}", flush=True)

        await asyncio.gather(*[um(r) for r in alvo])

    if not args.simular and achados:
        with con.cursor() as k:
            execute_values(k, GRAVAR, achados, page_size=500)
        con.commit()

    com_cnpj = sum(1 for a in achados if a[1])
    com_end = sum(1 for a in achados if a[3])
    print(f"\n{len(achados)} processadas · {com_cnpj} com CNPJ "
          f"({100*com_cnpj//max(1,len(achados))}%) · {com_end} com endereço "
          f"· {(time.time()-t0)/60:.1f} min"
          + (" (simulação)" if args.simular else ""), flush=True)
    con.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default="canoas")
    p.add_argument("--uf", default="RS")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--no-proxy", action="store_true")
    p.add_argument("--simular", action="store_true", help="não grava")
    return asyncio.run(rodar(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
