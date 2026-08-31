# -*- coding: utf-8 -*-
"""conferir_coordenadas.py — a coordenada bate com o próprio endereço do POI?

O QUE ISTO RESPONDE

13.324 POIs vieram da planilha do cliente com uma coordenada que ninguém nunca
conferiu — é o que a migração 0028 marcou como `desconhecida`. Delas, 12.189
têm endereço gravado, e endereço é uma segunda opinião independente.

O caso que motivou isto: a "Pousada Lá em Casa", endereço em Barra Grande /
Cajueiro da Praia-PI, com a coordenada da planilha caindo em **Parnaíba, 42,6
km do próprio endereço**. Não havia como saber sem comparar as duas coisas.

O QUE ELE FAZ, E O QUE NÃO FAZ

Ele CLASSIFICA. Mover doze mil pontos porque um geocodificador discordou seria
trocar um erro conhecido por outro desconhecido — e a planilha do cliente às
vezes é mais certa que o OSM, principalmente em condomínio e área rural.

    confirmada    o ponto está dentro da incerteza do próprio endereço.
                  A precisão sobe de `desconhecida` para a classe que se pode
                  PROVAR — e só ela. O ponto pode ser a porta exata; a prova
                  vai até onde o geocodificador chega.
    divergente    longe do endereço. Fica marcada, e NÃO se move sozinha.
    sem_endereco  o geocodificador não achou. Continua `desconhecida`.

DISTÂNCIA NÃO PROVA ERRO. MUNICÍPIO PROVA.

A primeira versão movia o que estivesse a mais de 2 km do endereço geocodificado.
Conferido caso a caso, isso teria sido destrutivo — em 4 de 5 amostras quem
errava era o geocodificador:

    pedido "Rua Hipolito Jose da Costa"  ->  devolveu "Rua Daniel Cruz da Costa"
    pedido "Avenida Esperanca"           ->  devolveu "Avenida Getulio Vargas"
    pedido "Avenida Guilherme Schell"    ->  devolveu "Agencia de Correios"

O ponto da planilha vem do cadastro do próprio cliente e costuma ser bom;
o geocodificador é difuso e devolve outra rua com cara de acerto. Trocar um
pelo outro por causa da distância seria trocar dado bom por chute.

O que PROVA erro é o ponto cair FORA do município que ele declara — como a
"Pousada Lá em Casa", com endereço em Cajueiro da Praia-PI e coordenada em
Parnaíba. Aí não é imprecisão, é outro lugar. Só esses `--corrigir` move.

Uso:
    python conferir_coordenadas.py --amostra 200        # calibrar
    python conferir_coordenadas.py                      # varredura, só classifica
    python conferir_coordenadas.py --corrigir           # só os fora do município
"""
from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from psycopg2.extras import execute_values

import config  # noqa: F401
import base_comum as bc
import geocodificar

# Acima disto a discordância deixa de ser imprecisão de geocodificador e passa a
# ser outro lugar. Calibrado na amostra: ver o cabeçalho do relatório.
LIMIAR_DIVERGENTE_M = 2000

# Folga sobre a incerteza declarada. O geocodificador diz 150 m para `via`, mas
# uma rua tem centenas de metros de extensão — o ponto da planilha pode estar na
# outra ponta da mesma rua e ainda assim ser o endereço certo.
FOLGA = 3.0

ALVOS = """
select id, nome, endereco, cidade, uf,
       coalesce(maps_lat, lat_origem), coalesce(maps_lng, lng_origem)
  from radar_comercial.pois
 where coord_precisao = 'desconhecida'
   and endereco is not null and btrim(endereco) <> ''
   and coalesce(maps_lat, lat_origem) is not null
   and (%(cidade)s is null or lower(cidade) = lower(%(cidade)s))
 order by id
"""

GRAVAR = """
update radar_comercial.pois p
   set coord_precisao = v.precisao,
       coord_fonte = v.fonte,
       coord_incerteza_m = v.incerteza::int
  from (values %s) as v(id, precisao, fonte, incerteza)
 where p.id = v.id::bigint
"""

MOVER = """
update radar_comercial.pois p
   set maps_lat = v.la::float8, maps_lng = v.lo::float8,
       lat_origem = v.la::float8, lng_origem = v.lo::float8,
       coord_precisao = v.precisao, coord_fonte = v.fonte,
       coord_incerteza_m = v.incerteza::int,
       fonte_dado = coalesce(p.fonte_dado || '+', '') || 'geo:endereco'
  from (values %s) as v(id, la, lo, precisao, fonte, incerteza)
 where p.id = v.id::bigint
"""


def metros(la1, lo1, la2, lo2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class Placar:
    """Contagem viva, com trava — são vários trabalhadores somando."""

    def __init__(self, total: int):
        self.total = total
        self.feitos = 0
        self.por = {}
        self.distancias = []
        self._trava = threading.Lock()
        self._t0 = time.time()

    def marcar(self, classe: str, dist: float | None):
        with self._trava:
            self.feitos += 1
            self.por[classe] = self.por.get(classe, 0) + 1
            if dist is not None:
                self.distancias.append(dist)
            if self.feitos % 250 == 0 or self.feitos == self.total:
                seg = time.time() - self._t0
                resta = (self.total - self.feitos) / max(self.feitos / seg, 0.01)
                print(f"  {self.feitos}/{self.total} · {seg/60:.1f} min · "
                      f"faltam ~{resta/60:.0f} min · "
                      + " · ".join(f"{k} {v}" for k, v in sorted(self.por.items())),
                      flush=True)


def conferir_um(linha, placar: Placar) -> dict:
    poi_id, nome, endereco, cidade, uf, la, lo = linha
    try:
        achado = geocodificar.buscar(endereco, cidade or "", uf or "")
    except Exception:
        achado = None
    if not achado:
        placar.marcar("sem_endereco", None)
        return {"id": poi_id, "classe": "sem_endereco"}

    # SÓ VALE CONFIRMAÇÃO DE NÍVEL DE ENDEREÇO. Quando o geocodificador só
    # resolveu até o bairro (800 m) ou a cidade (5 km), estar "dentro da
    # incerteza" não prova nada — e rotular o ponto de `municipio` diria que
    # ele É um centróide de cidade, que é justamente o que ele não é. Foram 245
    # POIs rotulados assim na primeira passada.
    if achado["precisao"] not in ("porta", "porta_aprox", "via"):
        placar.marcar("confirmacao_grosseira", None)
        return {"id": poi_id, "classe": "confirmacao_grosseira"}

    d = metros(float(la), float(lo), achado["lat"], achado["lng"])
    teto = (achado["incerteza_m"] or 150) * FOLGA
    if d <= teto:
        classe = "confirmada"
    else:
        # FORA DO PRÓPRIO MUNICÍPIO é a única prova de erro. Distância grande
        # dentro do município é, na maioria das vezes, o geocodificador
        # chutando a rua — e a planilha do cliente estando certa.
        dentro = geocodificar.dentro_do_municipio(float(la), float(lo),
                                                  cidade or "", uf or "")
        classe = "fora_do_municipio" if dentro is False else "geocod_discorda"
    placar.marcar(classe, d)
    return {"id": poi_id, "classe": classe, "dist": d, "nome": nome,
            "cidade": cidade, "achado": achado}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cidade", default=None, help="restringe a um município")
    p.add_argument("--amostra", type=int, default=0,
                   help="confere só N POIs — para calibrar antes de varrer tudo")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--corrigir", action="store_true",
                   help="move APENAS os que caem fora do próprio município — "
                        "os únicos em que o erro está provado")
    args = p.parse_args()

    con = bc.conectar()
    with con.cursor() as k:
        k.execute(ALVOS, {"cidade": args.cidade})
        linhas = k.fetchall()
    if args.amostra:
        # Espalhado, e não os N primeiros: os primeiros ids são de uma única
        # importação e uma única cidade, e a amostra sairia enviesada.
        passo = max(1, len(linhas) // args.amostra)
        linhas = linhas[::passo][:args.amostra]

    print(f"⟦conferência de coordenada⟧ {len(linhas)} POIs · "
          f"{args.workers} trabalhadores"
          + (" · SIMULAÇÃO (não move nada)" if not args.corrigir else
             f" · CORRIGE acima de {args.acima} m"), flush=True)

    placar = Placar(len(linhas))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        resultados = list(pool.map(lambda l: conferir_um(l, placar), linhas))

    # ── Gravação ────────────────────────────────────────────────────────
    confirmadas = [(r["id"], r["achado"]["precisao"], "planilha+conferida",
                    r["achado"]["incerteza_m"])
                   for r in resultados if r["classe"] == "confirmada"]
    mover = [(r["id"], r["achado"]["lat"], r["achado"]["lng"],
              r["achado"]["precisao"], r["achado"]["fonte"],
              r["achado"]["incerteza_m"])
             for r in resultados if r["classe"] == "fora_do_municipio"]

    with con.cursor() as k:
        if confirmadas:
            execute_values(k, GRAVAR, confirmadas, page_size=500)
        if mover and args.corrigir:
            execute_values(k, MOVER, mover, page_size=500)
    con.commit()

    # ── Relatório ───────────────────────────────────────────────────────
    print(f"\n{'classe':<18}{'quantos':>9}")
    for c, n in sorted(placar.por.items(), key=lambda x: -x[1]):
        print(f"  {c:<16}{n:>9}")
    if placar.distancias:
        ds = sorted(placar.distancias)
        def q(f):
            return ds[min(len(ds) - 1, int(len(ds) * f))]
        print(f"\ndistância do endereço: mediana {q(.5):.0f} m · "
              f"p90 {q(.9):.0f} m · p99 {q(.99):.0f} m · máx {ds[-1]/1000:.1f} km")

    print(f"\n{len(confirmadas)} tiveram a precisão elevada de `desconhecida` "
          f"para a classe provada.")
    if mover:
        if args.corrigir:
            print(f"{len(mover)} MOVIDAS para a coordenada do endereço.")
        else:
            print(f"{len(mover)} caem FORA do município que declaram e NÃO "
                  f"foram movidas. Para mover:\n"
                  f"  python conferir_coordenadas.py --corrigir")
            piores = sorted((r for r in resultados
                             if r["classe"] == "fora_do_municipio"),
                            key=lambda r: -r["dist"])[:10]
            print("\n  as dez mais distantes:")
            for r in piores:
                print(f"    {r['dist']/1000:8.1f} km  {r['id']:<8}"
                      f"{str(r['nome'])[:26]:<28}{str(r['cidade'])[:16]}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
