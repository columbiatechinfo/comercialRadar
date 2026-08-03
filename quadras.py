# -*- coding: utf-8 -*-
"""Terminal do processo de quadras — cada passo roda sozinho, e dá para retomar.

    quadras.py area   --wkt "POLYGON((...))"        passo 1 (cria a sessão)
    quadras.py area   --arquivo area.wkt
    quadras.py vias   --sessao S [--com-maps] [--sem-proxy]
    quadras.py borda  --sessao S
    quadras.py pontos --sessao S
    quadras.py faces  --sessao S
    quadras.py alinhar --sessao S               passo 6
    quadras.py telhados --sessao S [--com-maps]  passo 7 (Overture)
    quadras.py casar   --sessao S               passo 8 (ponto ↔ telhado)
    quadras.py tudo   --area areas/area_atual.json  1 a 6 na área do mapa
    quadras.py tudo   --wkt "..."                   1 a 6 de uma vez
    quadras.py tudo   --municipio "Itambé/PE"       1 a 6 na CIDADE INTEIRA
    quadras.py tudo   --sessao S                    refaz 2 a 6 da sessão
    quadras.py retomar --sessao S                   segue do passo que faltou
    quadras.py sessoes                              lista as sessões
    quadras.py ver    --sessao S                    resumo do que há na sessão

Rodar um passo isolado REFAZ aquele passo e invalida os seguintes (refazer as
vias invalida quadras, pontos e faces — elas derivam dele). É de propósito: meio
resultado velho misturado com metade novo é pior que refazer.

O nome de cada face sai da MAIORIA dos endereços que caem nela. A leitura no
Maps (`--com-maps`) fica desligada: ela custava ~60 s contra 1,4 s de todo o
resto, e a coluna `via_osm.nome_canonico` continua no banco para quando voltar.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import base_comum as bc  # noqa: E402
import quadras_analise as QA  # noqa: E402
import quadras_db as QD  # noqa: E402


def _municipio_wkt(alvo: str) -> str:
    """Polígono de um município pela malha do IBGE, lida da tabela `ibge_malha`.

    Aceita "Itambé/PE", só o nome (se não repetir entre as UFs carregadas) ou o
    código do IBGE. Sem isso, rodar a cidade inteira exigia extrair o WKT à mão.

    A busca por nome é `unaccent`-equivalente feita no Python só para o casamento
    exato; o SQL traz os candidatos da UF (ou todos, se a UF não foi dita)."""
    import unicodedata

    from shapely import wkt as _w
    import quadras_br as QB

    def norm(s):
        s = unicodedata.normalize("NFKD", str(s or ""))
        return "".join(c for c in s if not unicodedata.combining(c)).strip().lower()

    nome, _, uf = alvo.partition("/")
    nome, uf = norm(nome), norm(uf).upper()
    cod_alvo = alvo.strip()
    c = QB.con()
    try:
        # o código do IBGE é único: se bater, resolve sem olhar nome nem UF
        r = c.execute("SELECT nome, uf, ST_AsText(geom) FROM ibge_malha "
                      "WHERE cod_municipio = ?", [cod_alvo]).fetchone()
        if r:
            achados = [(r[0], r[1], _w.loads(r[2]))]
        else:
            sql = "SELECT nome, uf, ST_AsText(geom) FROM ibge_malha WHERE nome IS NOT NULL"
            par = []
            if uf:
                sql += " AND uf = ?"
                par = [uf]
            achados = [(n, u, _w.loads(w))
                       for n, u, w in c.execute(sql, par).fetchall()
                       if norm(n) == nome]
        ufs = ", ".join(u for (u,) in c.execute(
            "SELECT DISTINCT uf FROM ibge_malha ORDER BY uf").fetchall())
    finally:
        c.close()
    if not achados:
        raise SystemExit(f"município '{alvo}' não está na tabela ibge_malha "
                         f"(UFs carregadas: {ufs or 'nenhuma'})")
    if len(achados) > 1:
        onde = ", ".join(f"{n}/{u}" for n, u, _ in achados)
        raise SystemExit(f"'{alvo}' existe em mais de uma UF ({onde}) — use nome/UF")
    n, u, g = achados[0]
    import math
    km2 = g.area * 111.32 * (111.32 * math.cos(math.radians(g.centroid.y)))
    print(f"município {n}/{u} · {km2:.0f} km² (aproximado)")
    return g.wkt


def _area_wkt(a) -> str:
    if a.municipio:
        return _municipio_wkt(a.municipio)
    if a.wkt:
        return a.wkt
    if a.arquivo:
        return Path(a.arquivo).read_text(encoding="utf-8").strip()
    if a.area:
        # mesmo formato dos demais processos: {"polygon": [[lat, lng], ...]}
        import json
        d = json.loads(Path(a.area).read_text(encoding="utf-8"))
        pol = d.get("polygon") or []
        if len(pol) < 3:
            raise SystemExit(f"{a.area} não tem polígono — desenhe a área no mapa")
        anel = list(pol) + [pol[0]]
        return "POLYGON ((" + ", ".join(f"{ln} {la}" for la, ln in anel) + "))"
    raise SystemExit("informe --wkt, --arquivo ou --area com o polígono")


def _exige_sessao(a) -> str:
    if not a.sessao:
        raise SystemExit("informe --sessao (veja com: quadras.py sessoes)")
    if not QD.sessao(a.sessao):
        raise SystemExit(f"sessão '{a.sessao}' não existe")
    return a.sessao


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("comando", choices=["area", "vias", "borda", "pontos", "faces",
                                       "alinhar", "telhados", "casar", "tudo", "retomar",
                                       "sessoes", "ver"])
    p.add_argument("--sessao")
    p.add_argument("--wkt")
    p.add_argument("--arquivo")
    p.add_argument("--municipio", help="cidade inteira pela malha do IBGE em disco: "
                                       "\"Itambé/PE\", só o nome, ou o código IBGE")
    p.add_argument("--area", help="areas/area_atual.json — o mesmo polígono que os "
                                  "outros processos usam (é como o mapa dispara)")
    p.add_argument("--sem-proxy", action="store_true",
                   help="consulta o Maps direto, sem o pool de proxies")
    p.add_argument("--com-maps", action="store_true",
                   help="lê o nome canônico de cada via no Maps (lento: ~10 s/via). "
                        "Sem isso o nome da face vem da maioria dos endereços dela.")
    a = p.parse_args()

    if a.comando == "sessoes":
        for s in QD.sessoes():
            marca = "✗" if s["erro"] else " "
            print(f"{marca} {s['id']:34s} passo {s['passo']}/8 · "
                  f"{s['municipio'] or '?'}/{s['uf'] or '?'} · "
                  f"{s['vias']} vias · {s['quadras']} quadras · {s['pontos']} pontos"
                  + (f"\n    erro: {s['erro']}" if s["erro"] else ""))
        return

    if a.comando == "ver":
        sid = _exige_sessao(a)
        s = QD.sessao(sid)
        print(f"sessão {sid} · {s['municipio']}/{s['uf']} · passo {s['passo']}/8")
        if s["erro"]:
            print(f"  ERRO: {s['erro']}")
        for n, r in sorted((s["passos"] or {}).items()):
            print(f"  passo {n} ({QA.PASSOS.get(int(n), '?')}): {r}")
        fs = QD.faces(sid)
        for f in fs:
            print(f"  q{f['quadra_id']} face {f['face_idx']}: "
                  f"{f['nome_canonico'] or f['nome_osm'] or '—'} · "
                  f"paridade {f['paridade'] or '—'} "
                  f"(par {f['recuo_par_m']} m / ímpar {f['recuo_impar_m']} m)")
        return

    kw = {"usar_proxy": not a.sem_proxy, "com_maps": a.com_maps}

    if a.comando == "area":
        r = QA.passo1_area(_area_wkt(a))
        print(f"  rode agora:  quadras.py vias --sessao {r['sessao']}")
        return

    if a.comando == "tudo":
        sid = a.sessao or QA.passo1_area(_area_wkt(a))["sessao"]
        QA.rodar(sid, 2, 6, **kw)
        print(f"\nsessão: {sid}")
        return

    if a.comando == "retomar":
        sid = _exige_sessao(a)
        s = QD.sessao(sid)
        de = min(int(s["passo"]) + 1, 6)
        if s["passo"] >= 6 and not s["erro"]:
            print(f"sessão {sid} já concluiu os 6 passos (use um passo isolado "
                  f"para refazer)")
            return
        print(f"retomando {sid} do passo {de}", flush=True)
        QA.rodar(sid, de, 6, **kw)
        return

    sid = _exige_sessao(a)
    con = bc.conectar()
    try:
        if a.comando == "vias":
            QA.passo2_vias(sid, con=con, **kw)
        elif a.comando == "borda":
            QA.passo3_borda(sid, con=con)
        elif a.comando == "pontos":
            QA.passo4_pontos(sid, con=con)
        elif a.comando == "faces":
            QA.passo5_faces(sid, con=con)
        elif a.comando == "alinhar":
            QA.passo6_alinhar(sid, con=con)
        elif a.comando == "telhados":
            import quadras_telhados as QT
            QT.passo7_telhados(sid, com_maps=a.com_maps,
                               usar_proxy=not a.sem_proxy, con=con)
        elif a.comando == "casar":
            import quadras_telhados as QT
            QT.passo8_casar(sid, con=con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
