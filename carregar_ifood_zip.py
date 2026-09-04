# -*- coding: utf-8 -*-
"""Carrega o consolidado do piloto A2L_FOOD (iFood/Canoas) em `ifood_merchant`.

O que este arquivo tem e o que NÃO tem, porque a diferença importa para tudo que
vier depois:

  TEM   — 1.139 lojas com nome, categoria, nota, slug, URL e o ponto de busca
          onde foram vistas. Esse conjunto veio do `home:fallback`, que responde
          200 e nunca foi barrado.
  NÃO TEM — CNPJ, endereço e coordenada de quase todas: o `merchant-info/graphql`
          passou a devolver 403 depois de chamadas repetidas. Fica gravado como
          PENDENTE, jamais como vazio — "não colhi" e "não existe" são coisas
          diferentes e confundi-las contamina todo cruzamento posterior.

Uso:
    python carregar_ifood_zip.py --json <consolidado.json> [--cidade canoas]
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata

import base_comum as bc

# Os bairros de Canoas como o iFood os escreve no slug. Serve para LER o bairro
# do fim do slug com segurança: casar contra uma lista conhecida acerta ou
# devolve nulo, enquanto cortar o slug por posição inventa bairro quando o nome
# da loja termina em palavra parecida.
BAIRROS_SLUG = {
    "centro": "Centro", "igara": "Igara", "niteroi": "Niterói",
    "mathias-velho": "Mathias Velho", "guajuviras": "Guajuviras",
    "olaria": "Olaria", "harmonia": "Harmonia",
    "marechal-rondon": "Marechal Rondon", "fatima": "Fátima",
    "sao-jose": "São José", "rio-branco": "Rio Branco",
    "estancia-velha": "Estância Velha", "mato-grande": "Mato Grande",
    "nossa-senhora-das-gracas": "Nossa Senhora das Graças",
    "sao-luis": "São Luís", "brigadeira": "Brigadeira",
    "industrial": "Industrial", "primavera": "Primavera",
}

# O estado do detail vem do próprio piloto e é preservado como está: ele
# distingue "ainda não fui buscar" de "fui e fui barrado".
ESTADO = {
    "DETAIL_CAPTURED": None,          # colhido: sem pendência
    "DETAIL_BLOCKED": "BLOQUEADO",
    "DETAIL_REQUIRED": "PENDENTE",
}


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def bairro_do_slug(slug: str) -> str | None:
    """Lê o bairro do fim do slug casando contra a lista conhecida.

    Devolve None quando nenhum bairro casa — o que é a resposta honesta, e não
    um palpite pelo último pedaço do slug.
    """
    s = _sem_acento(slug or "")
    achado = None
    for chave, nome in BAIRROS_SLUG.items():
        if s.endswith("-" + chave) and (achado is None or len(chave) > len(achado[0])):
            achado = (chave, nome)
    return achado[1] if achado else None


def _digitos(s) -> str | None:
    d = "".join(c for c in str(s or "") if c.isdigit())
    return d or None


def ler(caminho: str) -> list[dict]:
    with open(caminho, encoding="utf-8") as fh:
        d = json.load(fh)
    itens = d["merchants"] if isinstance(d, dict) else d

    saida = []
    for r in itens:
        # `observations` é a lista de vezes que a loja foi vista, cada uma num
        # ponto de busca. A mais recente é a que vale para nome e nota.
        obs = sorted(r.get("observations") or [],
                     key=lambda o: o.get("captured_at") or "")
        ult = obs[-1] if obs else {}
        slug = ult.get("slug_observed") or ""
        saida.append({
            "merchant_id": r.get("merchant_id"),
            "nome": ult.get("merchant_name_observed"),
            "categoria": r.get("category_observed"),
            "slug": slug,
            "nota": ult.get("rating_observed"),
            "cnpj": _digitos(r.get("cnpj")),
            "telefone": _digitos(r.get("phone")),
            "avaliacoes": r.get("ratings_count"),
            "rua": r.get("street"),
            "numero": r.get("number"),
            # o bairro do detail vale mais que o do slug: um é informado pela
            # plataforma, o outro é lido de um texto de URL
            "bairro": r.get("district") or bairro_do_slug(slug),
            "cep": _digitos(r.get("postal_code")),
            "lat": r.get("latitude"),
            "lng": r.get("longitude"),
            "estado_detalhe": ESTADO.get(r.get("detail_status"), "PENDENTE"),
            "bruto": json.dumps({
                "ponto_busca": r.get("first_seen_point"),
                "url": r.get("public_url"),
                "vezes_visto": len(obs),
                "detail_status": r.get("detail_status"),
                "coord_precision": r.get("coord_precision"),
            }, ensure_ascii=False),
        })
    return saida


COLS = ("merchant_id", "nome", "categoria", "slug", "nota", "cnpj", "telefone",
        "avaliacoes", "rua", "numero", "bairro", "cep", "lat", "lng",
        "estado_detalhe", "bruto")

# `merchant_id` é a chave técnica da plataforma. O ON CONFLICT usa COALESCE para
# que uma recarga NUNCA apague um campo já colhido com um nulo — foi assim que
# uma releitura apagou o estado noutro módulo hoje.
#
# `id_empresa` não aparece no INSERT de propósito: quem o preenche é a trigger
# `preencher_tenant`, a partir do `request.jwt.claim.sub` que a conexão declara. É a
# convenção da casa (ver area_utils.py) e passar o valor à mão aqui abriria a
# porta para gravar no tenant errado.
SQL = f"""
insert into radar_comercial.ifood_merchant
       ({', '.join(COLS)}, visto_em)
values ({', '.join(['%s'] * len(COLS))}, now())
-- POR EMPRESA, e nao so pelo id da fonte (migracao 0058).
-- O mesmo estabelecimento existe uma vez em CADA empresa que o extraiu ou
-- reaproveitou; um indice global impediria isso. `id_empresa` nao aparece na
-- lista de colunas do insert porque o gatilho `preencher_empresa` a carimba
-- antes — e o gatilho BEFORE INSERT roda antes da checagem de conflito, entao
-- a inferencia pelo indice funciona.
on conflict (id_empresa, merchant_id) do update set
  nome       = coalesce(excluded.nome,       ifood_merchant.nome),
  categoria  = coalesce(excluded.categoria,  ifood_merchant.categoria),
  slug       = coalesce(excluded.slug,       ifood_merchant.slug),
  nota       = coalesce(excluded.nota,       ifood_merchant.nota),
  cnpj       = coalesce(excluded.cnpj,       ifood_merchant.cnpj),
  telefone   = coalesce(excluded.telefone,   ifood_merchant.telefone),
  avaliacoes = coalesce(excluded.avaliacoes, ifood_merchant.avaliacoes),
  rua        = coalesce(excluded.rua,        ifood_merchant.rua),
  numero     = coalesce(excluded.numero,     ifood_merchant.numero),
  bairro     = coalesce(excluded.bairro,     ifood_merchant.bairro),
  cep        = coalesce(excluded.cep,        ifood_merchant.cep),
  lat        = coalesce(excluded.lat,        ifood_merchant.lat),
  lng        = coalesce(excluded.lng,        ifood_merchant.lng),
  bruto      = excluded.bruto,
  visto_em   = now()
"""

# O estado do detail é DERIVADO do que a linha tem, nunca herdado de quem gravou
# por último. Sem isto, restos de execuções antigas sobrevivem à recarga: cinco
# linhas sem CNPJ e sem endereço estavam marcadas como colhidas, e a única loja
# realmente capturada aparecia como bloqueada. "Não colhi" e "não existe" são
# coisas diferentes, e um rótulo errado aqui contamina todo cruzamento adiante.
RECONCILIAR = """
update radar_comercial.ifood_merchant set estado_detalhe = case
    when cnpj is not null or rua is not null then 'OK'
    when estado_detalhe = 'BLOQUEADO'             then 'BLOQUEADO'
    else 'PENDENTE' end
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", required=True)
    p.add_argument("--cidade", default="canoas")
    args = p.parse_args()

    itens = ler(args.json)
    print(f"lidos {len(itens)} do consolidado", flush=True)

    con = bc.conectar()
    try:
        with con.cursor() as k:
            for it in itens:
                k.execute(SQL, tuple(it[c] for c in COLS))
            k.execute(RECONCILIAR)
            con.commit()

            k.execute("""select estado_detalhe, count(*)
                           from radar_comercial.ifood_merchant
                          group by 1 order by 2 desc""")
            print("\nestado do detail:", flush=True)
            for e, n in k.fetchall():
                print(f"  {str(e or 'colhido'):<12}{n}", flush=True)

            k.execute("""select count(*) total, count(nome) nome,
                                count(bairro) bairro, count(cnpj) cnpj,
                                count(lat) geo
                           from radar_comercial.ifood_merchant""")
            t, nm, ba, cn, ge = k.fetchone()
            print(f"\ntotal {t} · com nome {nm} · com bairro {ba} · "
                  f"com CNPJ {cn} · com coordenada {ge}", flush=True)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
