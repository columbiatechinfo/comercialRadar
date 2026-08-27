# -*- coding: utf-8 -*-
"""conferir_municipio.py — POI cujo CEP é de outra cidade não é desta cidade.

A REGRA, do dono do produto em 27/08/2026

> "os que vêm da base com CEP de outra cidade têm que ser deletados com certeza,
>  a fonte do endereço é confiável, assim como os do Google que estiverem fora"

E ela é uma afirmação sobre a natureza do dado, não preferência. O CEP não é
texto livre: ele é atribuído pelos Correios a um trecho de logradouro de um
município. Quando a base de origem grava um CEP, ela está declarando o
município — e essa declaração vale mais que o campo `cidade`, que é preenchido
pelo processo e já errou antes.

POR QUE A COORDENADA NÃO SALVA O REGISTRO

Um POI com CEP de Porto Alegre e coordenada dentro de Canoas parece
contraditório, mas não é: a coordenada veio de uma busca por nome no Google, que
casa com homônimo, enquanto o CEP veio da ficha original. Medido em Canoas: dos
348 com CEP estrangeiro, TODOS os 348 tinham coordenada dentro da divisa. Se a
coordenada mandasse, nenhum seria pego.

O CEP É RESOLVIDO CONTRA O CNEFE, NÃO CONTRA UMA FAIXA

A primeira versão usava a faixa mínima–máxima do município (92010000 a
92442820 em Canoas) e marcava como estrangeiro o `92001970` da Base Aérea de
Canoas — que é daqui e fica abaixo do piso. Faixa é aproximação; o CNEFE tem o
CEP de cada endereço com o município ao lado, e é isso que se pergunta.

Um CEP que o CNEFE não conhece NÃO é motivo para apagar: ausência de registro é
ausência de prova.
"""
from __future__ import annotations

import re

import base_comum as bc

# O PONTO DO MEIO EXISTE NA BASE, e esquecê-lo cega a conferência.
#
# As fontes escrevem `92425638`, `92425-638` e `92.425-638`. A primeira versão
# aceitava só as duas primeiras: com o ponto, o `\b\d{5}` não casava (a regex
# via "92" e "425-638"), e o POI caía silenciosamente no balde "sem CEP" — que
# não é conferido. CEP não lido é POI não conferido, e o erro se disfarça de
# ausência de dado.
_CEP = re.compile(r"\b(\d{2})\.?(\d{3})[\s.-]?(\d{3})\b")


def ceps_do_texto(endereco: str) -> list:
    """Todos os CEPs de 8 dígitos do endereço, em qualquer das grafias usadas."""
    return [a + b + c for a, b, c in _CEP.findall(endereco or "")]


def dono_dos_ceps(ceps: list) -> dict:
    """`{cep: (nome, uf, cod_municipio)}` pelo CNEFE. Uma consulta, não N.

    Resolver um a um contra 111 milhões de linhas estourou 10 minutos na
    primeira tentativa. Em lote é uma varredura só.
    """
    if not ceps:
        return {}
    ref = bc.conectar_referencia()
    try:
        cur = ref.cursor()
        cur.execute("""
            select regexp_replace(c.cep, '\\D', '', 'g'),
                   m.nome, m.uf, c.cod_municipio
              from ibge_cnefe c
              join ibge_malha m on m.cod_municipio = c.cod_municipio
             where regexp_replace(c.cep, '\\D', '', 'g') = any(%s)
             group by 1, 2, 3, 4""", (list(set(ceps)),))
        return {c: (n, u, cod) for c, n, u, cod in cur.fetchall()}
    finally:
        ref.close()


def avaliar(con, cod_ibge: str, cidade: str) -> dict:
    """Quem tem CEP de outro município. Não grava nada.

    Devolve `{"fora": [...], "sem_cep": n, "daqui": n, "cep_desconhecido": n}`.
    """
    cur = con.cursor()
    cur.execute("""select id, nome, endereco, fonte
                     from pois
                    where cidade = %s and coalesce(status,'') <> 'fundido'
                      and coalesce(trim(endereco), '') <> ''""", (cidade,))
    linhas = cur.fetchall()

    todos = []
    for _pid, _n, endereco, _f in linhas:
        todos.extend(ceps_do_texto(endereco))
    dono = dono_dos_ceps(todos)

    fora, daqui, sem_cep, desconhecido = [], 0, 0, 0
    for pid, nome, endereco, fonte in linhas:
        ceps = ceps_do_texto(endereco)
        if not ceps:
            sem_cep += 1
            continue
        conhecidos = [(c, dono[c]) for c in ceps if c in dono]
        if not conhecidos:
            desconhecido += 1          # ausência de registro não é prova
            continue
        # Um endereço pode citar mais de um CEP (o do ponto e o de uma
        # referência). Basta UM ser daqui para o POI ser daqui.
        if any(d[2] == cod_ibge for _c, d in conhecidos):
            daqui += 1
            continue
        c, (n, u, _cod) = conhecidos[0]
        fora.append({"id": pid, "nome": nome, "endereco": endereco,
                     "cep": c, "municipio": f"{n}/{u}", "fonte": fonte})
    return {"fora": fora, "daqui": daqui, "sem_cep": sem_cep,
            "cep_desconhecido": desconhecido}


def aplicar(con, plano: dict, log=print) -> int:
    """Apaga os de outro município. Devolve quantos.

    O `delete` leva junto vínculo, fotos e comentários pelo `CASCADE`, e deixa
    as tabelas BASE intactas pelo `SET NULL` — a mesma separação conferida na
    limpeza de 27/08/2026.
    """
    if not plano["fora"]:
        return 0
    ids = [r["id"] for r in plano["fora"]]
    cur = con.cursor()
    cur.execute("delete from pois where id = any(%s)", (ids,))
    n = cur.rowcount
    con.commit()
    log(f"  {n:,} POIs apagados por serem de outro município")
    return n


def main() -> int:
    import argparse
    import area_utils as au

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cidade", required=True)
    p.add_argument("--municipio", default="", help="código IBGE; sai da área se omitido")
    p.add_argument("--aplicar", action="store_true", help="sem isto, só relata")
    a = p.parse_args()

    cod = a.municipio or au.codigo_ibge_da_area()
    if not cod:
        raise SystemExit("sem código IBGE não sei qual município é o certo")

    con = bc.conectar()
    try:
        plano = avaliar(con, cod, a.cidade)
        print(f"  {a.cidade} ({cod})")
        print(f"    CEP daqui .............: {plano['daqui']:,}")
        print(f"    CEP de OUTRO município : {len(plano['fora']):,}")
        print(f"    CEP fora do CNEFE .....: {plano['cep_desconhecido']:,}  "
              f"(não é prova — ficam)")
        print(f"    sem CEP no endereço ...: {plano['sem_cep']:,}")

        por_cidade: dict = {}
        for r in plano["fora"]:
            por_cidade[r["municipio"]] = por_cidade.get(r["municipio"], 0) + 1
        for k, v in sorted(por_cidade.items(), key=lambda t: -t[1])[:8]:
            print(f"      {k:26} {v:>4}")

        if not a.aplicar:
            print("\n  SIMULAÇÃO — nada apagado. Use --aplicar.")
            return 0
        aplicar(con, plano)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
