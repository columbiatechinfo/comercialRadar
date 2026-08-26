# -*- coding: utf-8 -*-
"""enriquecer_por_ifood.py — o iFood ao contrário: do POI para a loja.

A INVERSÃO, e por que ela resolve mais do que o bloqueio

Até aqui o iFood era FONTE DE DESCOBERTA: um navegador abria o feed de cada
bairro, colhia a lista de lojas, e só então o CNPJ era buscado. Essa metade
morreu em 26/08/2026 — o Cloudflare passou de desafio automático para Turnstile
INTERATIVO ("Confirme que é humano"), e navegador automatizado não clica.
Medido: 1.598 lojas em 25/08, zero em 26/08, no notebook e no i9, com proxy e
sem proxy.

Aqui o sentido é o contrário, e a descoberta deixa de ser necessária:

    POI que já existe   →  busca web "nome cidade uf ifood"
    link do iFood       →  o id está DENTRO da URL
    /v1/merchants/{id}/extra  →  CNPJ, rua, número, CEP, coordenada

Nenhum passo abre o iFood. O `/extra` responde 200 sem navegador — medido:
1.598 respostas, zero falhas, CNPJ em 99,7%.

O GANHO NÃO É SÓ CONTORNAR O BLOQUEIO. Antes o iFood só enriquecia o que ele
mesmo tinha descoberto; agora alcança qualquer POI da base, venha do Overture,
do OSM ou da captura do Maps.

O QUE ISTO NÃO É

Não é descoberta. Um POI que não está no iFood continua não estando, e isso
aparece como `sem_link` — que quer dizer "não achei", nunca "não existe".

A CONFERÊNCIA É OBRIGATÓRIA, e é o que separa isto de inventar dado

"Farmácia São João" existe em dezenas de cidades. O buscador devolve alguma, e
gravar o CNPJ dela no POI errado seria pior que não gravar nada — vira dado
falso com aparência de verificado. Por isso todo par passa por `_confere`: a
coordenada que o `/extra` devolve tem de estar perto da do POI. Não estando, o
par é DESCARTADO e contado como `longe`.

USO
    python enriquecer_por_ifood.py --area area_atual --limite 10
    python enriquecer_por_ifood.py --cidade Canoas --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import urllib.error
import urllib.request

import config  # noqa: F401
import area_utils as au
import base_comum as bc
import evidencia as ev

# O id da loja vive na própria URL: /delivery/<cidade-uf>/<slug>/<uuid>
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
EXTRA = "https://marketplace.ifood.com.br/v1/merchants/%s/extra"
CABECALHO = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36")}

# 250 m. Não é o raio de fusão (20 m) e não deveria ser.
#
# Aqui não se está decidindo se dois registros são o mesmo ponto — isso já está
# decidido pelo nome que foi buscado. Está-se conferindo se o buscador devolveu
# a loja da cidade certa ou a homônima de outro estado. O iFood publica a
# coordenada do estabelecimento e o POI tem a do Maps ou da base pública; entre
# elas há divergência honesta de dezenas de metros. Apertar para 20 m
# descartaria par legítimo; afrouxar para 2 km deixaria passar o bairro errado.
RAIO_CONFERE_M = 250.0


# ─── quem pode estar no iFood ───────────────────────────────────────────────
#
# FILTRO PERIGOSO, E O DONO DO PRODUTO DISSE ISSO ANTES DE MIM: o iFood deixou
# de ser só comida. Hoje tem Mercados, Bebidas, Farmácias, Pets e Shopping.
#
# Então a lista abaixo é generosa de propósito, e erra para o lado de INCLUIR.
# Uma busca a mais custa uma consulta; uma loja de fora do filtro custa um CNPJ
# que nunca será colhido, e ninguém saberá que faltou.
#
# O que fica de fora é o que não vende ao consumidor por entrega: oficina,
# imobiliária, escritório, indústria, escola, igreja, estacionamento, via.
RAMOS = (
    # comida preparada
    "restaurante", "lanchonete", "pizzaria", "hamburgue", "burger", "lanche",
    "churrascaria", "cafeteria", "café", "cafe", "bar", "pub", "sorveteria",
    "açaí", "acai", "doceria", "confeitaria", "padaria", "panificadora",
    "pastelaria", "esfiha", "sushi", "japones", "japonês", "chines", "chinês",
    "italian", "mexican", "árabe", "arabe", "marmit", "buffet", "delivery",
    "food", "comida", "pizza", "salgad", "espetinho", "xis", "cachorro quente",
    "hot dog", "dogão", "dogao", "creperia", "tapioca", "temaki", "yakisoba",
    "self service", "restaurant",
    # mercado e bebida
    "mercado", "mercearia", "supermercado", "minimercado", "hortifruti",
    "quitanda", "empório", "emporio", "adega", "distribuidora de bebidas",
    "bebidas", "conveniência", "conveniencia", "açougue", "acougue",
    "peixaria", "frios", "laticínio", "laticinio",
    # farmácia e pet
    "farmácia", "farmacia", "drogaria", "manipulaç", "pet shop", "petshop",
    "pet", "veterinár", "veterinar", "agropecuária", "agropecuaria",
    # o "shopping" do iFood
    "floricultura", "flores", "presente", "tabacaria", "loja de conveniência",
    "suplemento", "produtos naturais", "natural", "cosmétic", "cosmetic",
    "perfumaria",
)


def _pode_estar(categoria: str, nome: str) -> bool:
    """O filtro olha categoria E nome.

    A categoria da base pública é grosseira: "compras/comércio" cobre de
    joalheria a hortifruti. O nome costuma dizer mais — "Pizzaria do Zé" entra
    mesmo com a categoria vaga, e é assim que o filtro erra para o lado certo.
    """
    alvo = ev._sem_acento((categoria or "") + " " + (nome or ""))
    return any(ev._sem_acento(r) in alvo for r in RAMOS)


# ─── a corrente ─────────────────────────────────────────────────────────────

def id_do_link(url: str) -> str:
    """O uuid da loja, quando a URL é de uma loja do iFood.

    `/delivery/<cidade>/<slug>/<uuid>` tem o id; `/restaurantes` e as páginas
    institucionais não têm, e devolvem vazio em vez de casar por engano.
    """
    if "ifood.com.br" not in url or "/delivery/" not in url:
        return ""
    m = UUID.search(url)
    return m.group(0) if m else ""


def detalhe(merchant_id: str, timeout: int = 20) -> dict | None:
    """`/extra` — CNPJ, endereço com número, CEP e coordenada. Sem navegador."""
    req = urllib.request.Request(EXTRA % merchant_id, headers=CABECALHO)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            json.JSONDecodeError):
        return None


def _confere(poi: dict, det: dict) -> tuple:
    """`(ok, distancia_m)` — o que veio é mesmo a loja deste POI?"""
    end = det.get("address") or {}
    la, lo = end.get("latitude"), end.get("longitude")
    if la is None or lo is None:
        return False, float("inf")
    d = ev.distancia_m(poi["lat"], poi["lng"], float(la), float(lo))
    return d <= RAIO_CONFERE_M, d


def campos(det: dict) -> dict:
    """O que interessa da resposta do iFood, achatado."""
    docs = det.get("documents") or {}
    end = det.get("address") or {}
    cnpj = ((docs.get("CNPJ") or {}).get("value") or "").strip() or None
    ddd = str(det.get("areaCode") or "").strip()
    fone = str(det.get("phoneIf") or "").strip()
    return {
        "merchant_id": det.get("id"),
        "cnpj": cnpj,
        "telefone": (ddd + fone) if (ddd and fone) else None,
        "rua": end.get("streetName"), "numero": end.get("streetNumber"),
        "bairro": end.get("district"), "cep": end.get("zipCode"),
        "cidade": end.get("city"), "uf": end.get("state"),
        "lat": end.get("latitude"), "lng": end.get("longitude"),
        "categoria": (det.get("mainCategory") or {}).get("name")
        if isinstance(det.get("mainCategory"), dict) else det.get("mainCategory"),
    }


SQL = """
select p.id, p.nome, p.categoria, p.maps_lat, p.maps_lng, p.cidade, p.uf
  from pois p
 where p.maps_lat is not null and p.maps_lng is not null
   and coalesce(p.nome, '') <> ''
   and p.cnpj is null
   and coalesce(p.status, '') <> 'fundido'
   and p.tenant_id = (select nullif(current_setting('app.tenant_id', true), '')::uuid)
   and (%(cidade)s = '' or upper(translate(coalesce(p.cidade,''), %(ac)s, %(li)s))
                         = upper(translate(%(cidade)s, %(ac)s, %(li)s)))
"""

_ACENTOS = "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ"
_LISOS = "aaaaeeiooouucAAAAEEIOOOUUC"


def candidatos(cur, cidade: str, poligono=None, limite: int = 0) -> list:
    par = {"cidade": cidade, "ac": _ACENTOS, "li": _LISOS}
    sql = SQL
    if poligono:
        s, n, o, l = au.bbox(poligono)
        sql += (" and p.maps_lat between %(s)s and %(n)s"
                " and p.maps_lng between %(o)s and %(l)s")
        par.update({"s": s, "n": n, "o": o, "l": l})
    cur.execute(sql, par)
    saida = []
    for pid, nome, cat, la, lo, cid, uf in cur.fetchall():
        if poligono and not au.ponto_no_poligono(float(la), float(lo), poligono):
            continue
        if not _pode_estar(cat, nome):
            continue
        saida.append({"id": pid, "nome": nome, "categoria": cat or "",
                      "lat": float(la), "lng": float(lo),
                      "cidade": cid or "", "uf": uf or ""})
        if limite and len(saida) >= limite:
            break
    return saida


def consulta(poi: dict) -> str:
    """`site:ifood.com.br` porque o alvo é UM link, não uma leitura de página.

    Sem a restrição o buscador devolve Instagram, Aiqfome e agregadores, e o
    primeiro link do iFood pode nem aparecer nos dez primeiros.
    """
    lugar = " ".join(x for x in (poi.get("cidade"), poi.get("uf")) if x)
    return f'site:ifood.com.br "{poi["nome"]}" {lugar}'.strip()


async def enriquecer(pois: list, workers: int = 4, usar_proxy: bool = True,
                     visivel: bool = False) -> list:
    """Devolve um registro por POI, com `estado` dizendo o que houve."""
    import minerar_web as MW

    pool = MW.SerpPool(workers, usar_proxy=usar_proxy, visivel=visivel)
    await pool.start()
    resultados = []
    sem = asyncio.Semaphore(workers)

    async def um(poi):
        async with sem:
            try:
                links = await pool.buscar(consulta(poi))
            except Exception as erro:  # noqa: BLE001
                return {**poi, "estado": "erro_busca", "detalhe": type(erro).__name__}
            mid = ""
            for url, _ in (links or []):
                mid = id_do_link(url)
                if mid:
                    break
            if not mid:
                return {**poi, "estado": "sem_link"}
            det = await asyncio.to_thread(detalhe, mid)
            if not det:
                return {**poi, "estado": "sem_detalhe", "merchant_id": mid}
            ok, d = _confere(poi, det)
            if not ok:
                # DESCARTADO, e dito. Gravar o CNPJ de uma homônima seria dado
                # falso com aparência de verificado.
                return {**poi, "estado": "longe", "merchant_id": mid,
                        "dist_m": d, "achado": det.get("name")}
            return {**poi, "estado": "ok", "dist_m": d, **campos(det)}

    for r in await asyncio.gather(*(um(p) for p in pois)):
        resultados.append(r)
    try:
        await pool.close()
    except Exception:  # noqa: BLE001
        pass
    return resultados


def gravar(con, achados: list) -> int:
    """Só o que passou na conferência entra, e só onde ainda falta."""
    cur = con.cursor()
    n = 0
    for a in achados:
        if a.get("estado") != "ok" or not a.get("cnpj"):
            continue
        cur.execute("""
            update pois set cnpj = coalesce(cnpj, %s),
                            telefone = coalesce(nullif(telefone,''), %s),
                            presente_no_ifood = true,
                            ifood_visto_em = now()
             where id = %s""",
                    (a["cnpj"], a.get("telefone"), a["id"]))
        n += cur.rowcount
    return n


def _resumo(achados: list) -> None:
    from collections import Counter
    c = Counter(a["estado"] for a in achados)
    t = max(1, len(achados))
    print()
    for k in ("ok", "longe", "sem_link", "sem_detalhe", "erro_busca"):
        if c.get(k):
            print(f"    {k:12} {c[k]:>5}  {c[k]/t:>5.0%}")
    com = [a for a in achados if a.get("estado") == "ok" and a.get("cnpj")]
    print(f"    {'com CNPJ':12} {len(com):>5}  {len(com)/t:>5.0%}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cidade", default="")
    p.add_argument("--area", default="")
    p.add_argument("--empresa", default="Aegea - Corsan")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true")
    p.add_argument("--visivel", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    cur.execute("select id, nome from tenants where lower(nome)=lower(%s) and ativo",
                (a.empresa.strip(),))
    r = cur.fetchone()
    if not r:
        raise SystemExit(f"empresa {a.empresa!r} não existe")
    cur.execute("select set_config('app.tenant_id', %s, false)", (str(r[0]),))

    poligono = au.carregar_area(a.area) if a.area else None
    if a.area and not poligono:
        raise SystemExit(f"não há área desenhada com a referência {a.area!r}")

    pois = candidatos(cur, a.cidade, poligono, a.limite)
    print(f"  {len(pois):,} POIs sem CNPJ e com ramo que pode estar no iFood")
    if not pois:
        con.close()
        return 0
    for x in pois[:5]:
        print(f"    {x['nome'][:34]:36} {x['categoria'][:24]}")

    achados = asyncio.run(enriquecer(pois, a.workers, not a.sem_proxy, a.visivel))
    _resumo(achados)

    for x in [y for y in achados if y.get("estado") == "ok"][:6]:
        print(f"    ✔ {x['nome'][:28]:30} CNPJ {x.get('cnpj') or '—'} "
              f"· {x.get('rua') or ''} {x.get('numero') or ''} · {x['dist_m']:.0f} m")
    for x in [y for y in achados if y.get("estado") == "longe"][:4]:
        print(f"    ✘ {x['nome'][:28]:30} achou {str(x.get('achado'))[:24]} "
              f"a {x['dist_m']:.0f} m — descartado")

    if not a.aplicar:
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        con.close()
        return 0
    n = gravar(con, achados)
    con.commit()
    print(f"\n  GRAVADO: {n:,} POIs ganharam CNPJ do iFood")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
