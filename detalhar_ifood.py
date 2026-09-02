# -*- coding: utf-8 -*-
"""CNPJ e endereço das lojas do iFood pelo endpoint público, de graça.

A METADE BARATA, e por que ela não existia ligada ao banco

O `extrair_ifood.py` descobre e deixa cada loja em `estado_detalhe='PENDENTE'`,
que quer dizer "não colhi" e nunca "não existe". Quem detalhava a partir daí era
o `enriquecer_ifood_web.py`, e o cabeçalho dele explica por quê:

    "O que ele não entrega é CNPJ e endereço: eles vivem no
     `merchant-info/graphql`, que responde página de captcha do PerimeterX."

A primeira frase é verdade e a conclusão envelheceu. O `graphql` continua
fechado, mas ele não é a única porta: `marketplace.ifood.com.br/v1/merchants/
{id}/extra` responde 200 sem token, sem navegador e sem captcha, e entrega
exatamente o que falta. Medido em 02/09/2026 sobre as lojas descobertas hoje:

    990 de 990 com dado · 2,6 min · nenhuma falha
    CNPJ 99,8% · número 100% · CEP 100% · telefone 96,5% · avaliações 97,4%

Contra o caminho web, que passa por buscador, LLM e BrasilAPI para chegar ao
mesmo CNPJ — e gasta SERP em cada loja.

A DIVISÃO QUE ISSO DEIXA

    descobrir     caro     navegador + proxy       `extrair_ifood.py`
    detalhar      barato   HTTP puro               este módulo
    o que sobrar  caro     buscador + LLM          `enriquecer_ifood_web.py`

Este módulo não substitui o web: ele o antecede. Loja que o `/extra` não
responder continua PENDENTE e cai para lá, que é onde o gasto se justifica.

O RITMO É DELIBERADO. `detalhar()` sorteia uma pausa antes de cada chamada
(ver o cabeçalho de `poi_estadual/ifood.py`). Um endpoint que responde 200 hoje
não promete responder 200 para dezenas de milhares de requisições seguidas do
mesmo punhado de IPs, e o Akamai já protege o mesmo domínio.

Uso:
    python detalhar_ifood.py                       # todas as pendentes
    python detalhar_ifood.py --slug-como canoas-rs # só as de Canoas
    python detalhar_ifood.py --limite 50           # amostra
    python detalhar_ifood.py --simular             # não grava
"""
from __future__ import annotations

import argparse
import sys

from psycopg2.extras import execute_values

import base_comum as bc

sys.path.insert(0, "skills/extracao-poi-estadual")

# `estado_detalhe` depois desta etapa:
#
#   OK            veio dado do `/extra`
#   SEM_RETORNO   o endpoint disse que a loja não existe (400/404/410). NÃO é
#                 "falhei": é resposta final, e repetir não a traz de volta.
#   PENDENTE      falha recuperável (rede, 429, 5xx) — fica para a próxima
#                 rodada, ou para o caminho web.
FINAIS = ("http_400", "http_404", "http_410")

PENDENTES = """
select merchant_id
  from radar_comercial.ifood_merchant
 where coalesce(estado_detalhe, 'PENDENTE') <> 'OK'
   and (%(slug)s is null or slug like %(slug)s || '%%')
 order by visto_em desc nulls last
"""

# `coalesce(excluded, atual)` em tudo: o detalhe ACRESCENTA e nunca apaga. Uma
# rodada que volte sem telefone não pode zerar o telefone que a anterior trouxe.
GRAVAR = """
update radar_comercial.ifood_merchant m set
  cnpj       = coalesce(v.cnpj,       m.cnpj),
  rua        = coalesce(v.rua,        m.rua),
  numero     = coalesce(v.numero,     m.numero),
  cep        = coalesce(v.cep,        m.cep),
  cidade     = coalesce(v.cidade,     m.cidade),
  uf         = coalesce(v.uf,         m.uf),
  bairro     = coalesce(m.bairro,     v.bairro),
  lat        = coalesce(v.lat,        m.lat),
  lng        = coalesce(v.lng,        m.lng),
  telefone   = coalesce(v.telefone,   m.telefone),
  avaliacoes = coalesce(v.avaliacoes, m.avaliacoes),
  estado_detalhe = 'OK',
  visto_em   = now()
from (values %s) as v(merchant_id, cnpj, rua, numero, cep, cidade, uf, bairro,
                      lat, lng, telefone, avaliacoes)
where m.merchant_id = v.merchant_id
"""

MARCAR = """
update radar_comercial.ifood_merchant
   set estado_detalhe = 'SEM_RETORNO', visto_em = now()
 where merchant_id = any(%s)
"""


def _txt(v):
    v = str(v or "").strip()
    return v or None


def _num(v, tipo):
    try:
        return tipo(v)
    except (TypeError, ValueError):
        return None


def linha_para_banco(l: dict) -> tuple:
    """Do que o `/extra` devolve para as colunas de `ifood_merchant`."""
    return (
        l.get("id_fonte"),
        _txt(l.get("if.cnpj")),
        _txt(l.get("if.logradouro")),
        _txt(l.get("if.numero")),
        _txt(l.get("cep")),
        _txt(l.get("localidade_fonte")),
        _txt(l.get("if.uf")),
        _txt(l.get("bairro")),
        _num(l.get("lat"), float),
        # o `/extra` chama de `lon`; a coluna se chama `lng`
        _num(l.get("lon"), float),
        _txt(l.get("telefone")),
        _num(l.get("if.avaliacoes"), lambda x: int(float(x))),
    )


def proxies_do_pool(quantos: int = 40) -> list:
    """URLs de proxy para o `urllib`. Sem pool, devolve vazio e o diz."""
    try:
        from proxy_pool import ProxyPool
    except Exception as e:                                     # noqa: BLE001
        print("  ⚠️  pool indisponível (%s) — as chamadas sairão pelo IP direto"
              % type(e).__name__, flush=True)
        return []
    pool = ProxyPool(pais="BR")
    pool.start()
    saida = []
    for p in pool._proxies:
        if pool.pais and p.get("country") != pool.pais:
            continue
        cfg = ProxyPool.to_playwright(p)
        servidor = str(cfg.get("server") or "").replace("http://", "")
        if not servidor:
            continue
        if cfg.get("username"):
            saida.append("http://%s:%s@%s" % (cfg["username"],
                                              cfg.get("password") or "", servidor))
        else:
            saida.append("http://%s" % servidor)
        if len(saida) >= quantos:
            break
    return saida


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug-como", dest="slug", default=None,
                   help="recorta pelo prefixo do slug (ex.: canoas-rs)")
    p.add_argument("--limite", type=int, default=0, help="no máximo N lojas")
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true")
    p.add_argument("--sem-pausa", dest="sem_pausa", action="store_true",
                   help="dispara sem intervalo — só para lista curta")
    p.add_argument("--simular", action="store_true")
    a = p.parse_args()

    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.execute(PENDENTES, {"slug": a.slug})
            ids = [r[0] for r in k.fetchall()]
    finally:
        if a.simular:
            con.close()
            con = None
    if a.limite:
        ids = ids[:a.limite]

    print("⟦fase⟧ ifood-detalhe", flush=True)
    print("%d lojas pendentes%s" % (len(ids), " (slug %s*)" % a.slug if a.slug else ""),
          flush=True)
    if not ids:
        print("nada a detalhar.", flush=True)
        return 0

    proxies = [] if a.sem_proxy else proxies_do_pool()
    print("  %d proxies · %d threads · pausa %s"
          % (len(proxies), a.threads, "desligada" if a.sem_pausa else "(0,4-1,6 s)"),
          flush=True)

    from poi_estadual import ifood

    def ao_vivo(n, ok, falhou):
        print("    %5d de %d · %5d com dado · %4d sem" % (n, len(ids), ok, falhou),
              flush=True)

    import time
    t0 = time.time()
    linhas, falhas = ifood.detalhar(
        ids, proxies=proxies, threads=a.threads, ao_vivo=ao_vivo,
        pausa=None if a.sem_pausa else (0.4, 1.6))
    dt = time.time() - t0

    com_cnpj = sum(1 for l in linhas if _txt(l.get("if.cnpj")))
    print("\n%d de %d com dado · %.1f min · CNPJ em %d (%.1f%%)"
          % (len(linhas), len(ids), dt / 60.0, com_cnpj,
             100.0 * com_cnpj / max(1, len(linhas))), flush=True)

    mortas = [f["id_fonte"] for f in falhas if f.get("motivo") in FINAIS]
    if falhas:
        motivos = {}
        for f in falhas:
            motivos[f["motivo"]] = motivos.get(f["motivo"], 0) + 1
        print("  sem dado: %d — %s" % (len(falhas), motivos), flush=True)
        print("  finais (viram SEM_RETORNO): %d · recuperáveis (seguem "
              "PENDENTE): %d" % (len(mortas), len(falhas) - len(mortas)), flush=True)

    if a.simular:
        for l in linhas[:8]:
            print("   %-38s %-16s %s, %s" % (
                str(l.get("nome"))[:38], l.get("if.cnpj"),
                l.get("if.logradouro"), l.get("if.numero")), flush=True)
        print("(simulação — nada gravado)", flush=True)
        return 0

    valores = [linha_para_banco(l) for l in linhas if l.get("id_fonte")]
    with con.cursor() as k:
        if valores:
            execute_values(k, GRAVAR, valores, page_size=500,
                           template="(%s,%s,%s,%s,%s,%s,%s,%s,%s::float8,"
                                    "%s::float8,%s,%s::int)")
        if mortas:
            k.execute(MARCAR, (mortas,))
        k.execute("""select estado_detalhe, count(*)
                       from radar_comercial.ifood_merchant group by 1""")
        print("gravadas %d · estado: %s" % (len(valores), dict(k.fetchall())),
              flush=True)
    con.commit()
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
