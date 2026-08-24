# -*- coding: utf-8 -*-
"""Desfaz o ENRIQUECIMENTO que colou dado de outro estabelecimento no POI.

O sintoma: POI cuja coordenada fica a centenas de quilômetros da cidade que ele
declara. A causa não é a coordenada — é o enriquecimento.

Como acontece: a planilha do cliente traz "Restaurante Bamboo's" num endereço de
Parnaíba. O enriquecimento busca esse nome, acha "Coco Bambu Teresina" — nome
parecido, cidade errada, 267 km de distância — e escreve NOME, ENDEREÇO, CIDADE
e UF daquele estabelecimento por cima do registro. A coordenada continua a da
planilha, porque essa ninguém sobrescreveu. Fica um POI que diz ser de Teresina,
está em Parnaíba, e não é nenhum dos dois: é o cliente de Parnaíba com a
identidade de um restaurante de Teresina colada em cima.

Por que passou: o guarda de distância existe (`search_pois_v2.montar_result`),
mas só marca `match_valido` quando CONSEGUE medir. Nestes casos `distancia_m`
nunca foi calculada, e o ingester só recusa quem chega explicitamente inválido —
então "não medido" entrou como bom.

O que este script faz, e o que ele NÃO faz:

  - **Não apaga o POI.** É endereço real, veio do cadastro do cliente, e é
    justamente o que a ferramenta existe para analisar.
  - **Devolve o nome original** e limpa os campos que vieram do casamento errado.
  - **Marca `match_valido = false`**, para o POI sair da contagem de achados
    válidos até ser reprocessado com o guarda funcionando.

Rode com --aplicar; sem isso apenas mostra.
"""
import math
import os
import sys
import unicodedata

sys.path.insert(0, r"C:\Users\ceo\Documents\Sistemas\comercialRadar")
os.chdir(r"C:\Users\ceo\Documents\Sistemas\comercialRadar")
import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402

APLICAR = "--aplicar" in sys.argv
LIMITE_KM = float(os.environ.get("COERENCIA_LIMITE_KM", "50"))


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                   if unicodedata.category(c) != "Mn")


def _hav(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = p2 - p1, math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def incoerentes(con, ref):
    with con.cursor() as cur:
        cur.execute("""select id, nome, nome_original, cidade, uf, fonte, status,
                              coalesce(maps_lat, lat_origem), coalesce(maps_lng, lng_origem)
                         from pois
                        where cidade is not null and uf is not null
                          and coalesce(maps_lat, lat_origem) is not null
                          and match_valido is not false""")
        linhas = cur.fetchall()
    with ref.cursor() as rc:
        rc.execute("""select upper(nome), uf, ST_Y(ST_Centroid(geom)),
                             ST_X(ST_Centroid(geom)) from ibge_malha""")
        cent = {(_norm(n), u): (la, lo) for n, u, la, lo in rc.fetchall()}

    fora = []
    for i, n, no, ci, uf, fo, st, la, lo in linhas:
        alvo = cent.get((_norm(ci), uf))
        if not alvo:
            continue
        km = _hav(la, lo, *alvo)
        if km > LIMITE_KM:
            fora.append((i, n, no, ci, uf, fo, st, round(km)))
    return fora


def main():
    con = bc.conectar()
    con.autocommit = False
    ref = bc.conectar_referencia()
    fora = incoerentes(con, ref)
    ref.close()

    com_original = [f for f in fora if f[2]]
    sem_original = [f for f in fora if not f[2]]
    print(f"  {len(fora)} POIs a mais de {LIMITE_KM:.0f} km da cidade que declaram")
    print(f"     com nome_original (dá para reverter): {len(com_original)}")
    print(f"     sem nome_original (só invalidar)    : {len(sem_original)}")

    por_fonte = {}
    for f in fora:
        por_fonte[f[5]] = por_fonte.get(f[5], 0) + 1
    print(f"     por fonte: {por_fonte}")

    print("\n  amostra do que será desfeito:")
    for i, n, no, ci, uf, fo, st, km in fora[:8]:
        print(f"    #{i} '{(no or '?')[:26]}' virou '{(n or '?')[:26]}' em {ci}/{uf} ({km} km)")

    if APLICAR:
        with con.cursor() as cur:
            for i, n, no, ci, uf, fo, st, km in fora:
                if no:
                    # Devolve a identidade que veio do cliente e apaga a que foi
                    # colada por cima. `endereco_fonte` fica nulo para a próxima
                    # passada saber que este POI precisa de endereço.
                    cur.execute("""update pois
                                      set nome = %s, endereco = null, cidade = null,
                                          uf = null, endereco_fonte = null,
                                          match_valido = false,
                                          status = 'enriquecimento_incoerente'
                                    where id = %s""", (no, i))
                else:
                    cur.execute("""update pois
                                      set match_valido = false,
                                          status = 'enriquecimento_incoerente'
                                    where id = %s""", (i,))
        con.commit()
        print(f"\n  APLICADO: {len(fora)} POIs revertidos e marcados para reprocessar")
    else:
        con.rollback()
        print("\n  SIMULACAO — nada gravado")
    con.close()


if __name__ == "__main__":
    main()
