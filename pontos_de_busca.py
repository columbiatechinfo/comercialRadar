# -*- coding: utf-8 -*-
"""Endereços REAIS espalhados por uma cidade ou por uma área desenhada.

POR QUE ISTO PRECISA EXISTIR

O iFood não aceita coordenada: para trocar a praça do feed é preciso **salvar um
endereço**, e ele exige NÚMERO DA CASA. Não existe "sem número" no fluxo. O
piloto tentou centroide de bairro e levou `address_number_required` em 2 de 9
pontos — o endereço não salvava e o feed não mudava de praça.

Até agora a solução era uma lista escrita à mão: dezesseis endereços de Canoas,
no código. Funcionava para Canoas e para mais nenhum lugar do Brasil.

DE ONDE SAEM OS ENDEREÇOS

Do **CNEFE do IBGE** (`ibge_cnefe`, banco de referência): 111.102.875 endereços
com logradouro, número, CEP e coordenada. É a base que o Censo 2022 usou para
percorrer o país porta a porta — se um endereço está lá, ele existe.

O CAMINHO QUE ESCALA, e por que não é o óbvio

A tabela tem 23 GB e as coordenadas são `text`. Uma consulta por caixa
(`latitude::numeric between ...`) varre as 111 milhões de linhas: sem índice
espacial, o cast impede qualquer uso de índice. Em município grande isso é
minutos por chamada.

O caminho daqui é outro, em dois saltos, cada um usando o índice que existe:

    polígono  --ST_Intersects-->  municípios   (ibge_malha, índice espacial)
    municípios --cod_municipio-->  endereços   (ix_cnefe_cod_municipio)

Só depois, e já sobre um punhado de linhas, o ponto é testado contra o polígono
em memória. Medido: consulta por `cod_municipio` responde em 0,0 s.

A GRADE, e por que a densidade manda a ordem

O feed do iFood cobre um raio grande. Visitar pontos vizinhos devolve as mesmas
lojas — o extrator já para por saturação. Então os pontos saem de uma GRADE
(uma célula de ~`passo_km`), um endereço por célula, o mais próximo do centro
dela.

E são ordenados pela **densidade de endereços da célula**, do centro urbano para
a periferia. A lista escrita à mão visitava os bairros na ordem em que alguém os
digitou; com a densidade na frente, a saturação chega mais cedo e as lojas que
importam entram primeiro.

USO
    python pontos_de_busca.py --cidade Canoas --uf RS
    python pontos_de_busca.py --area                 # a área desenhada no painel
    python pontos_de_busca.py --cidade "Porto Alegre" --passo-km 3 --limite 20
"""
from __future__ import annotations

import argparse
import sys
import unicodedata

import config  # noqa: F401  (.env + UTF-8)
import area_utils as au
import base_comum as bc

# ~2,5 km. O feed do iFood cobre bem mais que isso, e a saturação corta o
# excesso — grade fina custa sessões de navegador, que é o recurso caro.
PASSO_KM_PADRAO = 2.5
GRAU_POR_KM = 1.0 / 111.0

# `cod_especie` do CNEFE. 1 = domicílio particular. Endereço de domicílio é o que
# o iFood espera de um cliente; usar um estabelecimento (7) ou uma edificação em
# construção (8) aumenta a chance de o autocompletar recusar.
ESPECIE_DOMICILIO = "1"


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _municipios_da_cidade(cur, cidade: str, uf: str | None):
    """(cod, nome, uf) pelo nome, ignorando acento e caixa.

    A comparação sem acento acontece EM PYTHON, e não no SQL: a extensão
    `unaccent` não está instalada no banco de referência, e instalá-la exigiria
    alterar um banco compartilhado para resolver um `where`. A `ibge_malha` tem
    3.560 linhas — trazer os nomes de uma UF e comparar aqui não é o gargalo de
    nada, e o filtro por UF ainda vai no banco.
    """
    if uf:
        cur.execute("""select cod_municipio, nome, uf from ibge_malha
                        where upper(uf) = %s""", (uf.upper(),))
    else:
        cur.execute("select cod_municipio, nome, uf from ibge_malha")
    alvo = _sem_acento(cidade)
    achados = [r for r in cur.fetchall() if _sem_acento(r[1]) == alvo]
    if not achados:
        raise SystemExit(
            "municipio '%s'%s nao esta na ibge_malha.\n"
            "A malha e carregada SOB DEMANDA por municipio — pode ser que este "
            "ainda nao tenha sido baixado." % (cidade, " (%s)" % uf if uf else ""))
    if len(achados) > 1 and not uf:
        ufs = ", ".join(sorted(a[2] for a in achados))
        raise SystemExit("ha '%s' em mais de uma UF (%s). Use --uf." % (cidade, ufs))
    return achados


def _municipios_da_area(cur, poligono):
    """Municípios que a área desenhada TOCA — não só o do centroide.

    Uma área desenhada à mão atravessa divisa com frequência: em Canoas o
    desenho pega Esteio e Cachoeirinha sem que ninguém tenha pedido. Resolver só
    pelo centroide perderia os endereços do outro lado da linha, e o feed do
    iFood não conhece divisa nenhuma.
    """
    anel = ", ".join("%s %s" % (lon, lat) for lat, lon in poligono)
    primeiro = "%s %s" % (poligono[0][1], poligono[0][0])
    wkt = "POLYGON((%s, %s))" % (anel, primeiro)
    cur.execute("""select cod_municipio, nome, uf from ibge_malha
                    where ST_Intersects(geom, ST_GeomFromText(%s, 4326))""", (wkt,))
    achados = cur.fetchall()
    if not achados:
        raise SystemExit("a area desenhada nao toca nenhum municipio da ibge_malha")
    return achados


# A grade e a escolha do endereço acontecem NO BANCO.
#
# `DISTINCT ON (celula)` com `ORDER BY` pela distancia ao centro da celula
# devolve um endereco por celula, ja o mais central, sem trazer as centenas de
# milhares de linhas do municipio para o Python. Canoas tem ~120 mil enderecos;
# o que volta daqui sao dezenas de linhas.
GRADE = """
with base as (
  select cod_municipio,
         nom_tipo_seglogr, nom_seglogr, num_endereco, cep, dsc_localidade,
         latitude::numeric  as lat,
         longitude::numeric as lon
    from ibge_cnefe
   where cod_municipio = any(%(muns)s)
     and coalesce(num_endereco, '') <> ''
     and coalesce(nom_seglogr, '') <> ''
     and coalesce(latitude, '')  <> ''
     and coalesce(longitude, '') <> ''
     and cod_especie = %(especie)s
), celulas as (
  select *,
         round(lat / %(passo)s) * %(passo)s as cel_lat,
         round(lon / %(passo)s) * %(passo)s as cel_lon
    from base
), densidade as (
  select cel_lat, cel_lon, count(*) as n from celulas group by 1, 2
)
select distinct on (c.cel_lat, c.cel_lon)
       c.cel_lat, c.cel_lon, d.n,
       c.nom_tipo_seglogr, c.nom_seglogr, c.num_endereco, c.cep,
       c.dsc_localidade, c.lat, c.lon, c.cod_municipio
  from celulas c join densidade d using (cel_lat, cel_lon)
 order by c.cel_lat, c.cel_lon,
          abs(c.lat - c.cel_lat) + abs(c.lon - c.cel_lon)
"""


def _texto(tipo, nome, numero, municipio):
    """`Rua Victor Barreto, 2301, Canoas` — o formato que a caixa do iFood aceita.

    O tipo entra porque o CNEFE guarda `RUA`/`AVENIDA` separado do nome, e sem
    ele "Victor Barreto" sozinho compete com um bairro homônimo no autocompletar.
    """
    tipo = (tipo or "").strip().title()
    nome = (nome or "").strip().title()
    logr = ("%s %s" % (tipo, nome)).strip()
    return "%s, %s, %s" % (logr, str(numero).strip(), municipio)


def pontos(cidade=None, uf=None, poligono=None, area_ref=None,
           passo_km=PASSO_KM_PADRAO, limite=0, especie=ESPECIE_DOMICILIO):
    """Lista de pontos de busca, do mais denso para o menos.

    Cada item: {rotulo, endereco, lat, lon, enderecos_na_celula, municipio}.
    """
    if poligono is None and area_ref is not None:
        poligono = au.carregar_area(area_ref)
        if not poligono:
            raise SystemExit("nao ha area desenhada salva com a referencia %r" % area_ref)
    if poligono is None and not cidade:
        raise SystemExit("informe --cidade ou --area")

    passo = float(passo_km) * GRAU_POR_KM
    con = bc.conectar_referencia()
    try:
        with con.cursor() as cur:
            muns = (_municipios_da_area(cur, poligono) if poligono
                    else _municipios_da_cidade(cur, cidade, uf))
            nome_por_cod = {c: n for c, n, _ in muns}
            cur.execute(GRADE, {"muns": [c for c, _, _ in muns],
                                "passo": passo, "especie": especie})
            linhas = cur.fetchall()
    finally:
        con.close()

    saida = []
    for (cel_lat, cel_lon, n, tipo, nome, numero, cep, loc, lat, lon, cod) in linhas:
        lat, lon = float(lat), float(lon)
        # O recorte fino do polígono acontece AQUI, sobre dezenas de linhas —
        # nunca sobre as 111 milhões.
        if poligono and not au.ponto_no_poligono(lat, lon, poligono):
            continue
        municipio = nome_por_cod.get(cod, "")
        saida.append({
            "rotulo": (loc or "").strip().title() or ("%.3f,%.3f" % (lat, lon)),
            "endereco": _texto(tipo, nome, numero, municipio),
            "lat": lat, "lon": lon, "cep": cep,
            "enderecos_na_celula": int(n), "municipio": municipio,
        })
    # Densidade primeiro: o centro urbano tem as lojas, e a saturação corta a
    # cauda antes de ela custar sessões de navegador.
    saida.sort(key=lambda p: -p["enderecos_na_celula"])
    return saida[:limite] if limite else saida


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cidade")
    p.add_argument("--uf")
    p.add_argument("--area", nargs="?", const=au.AREA_PADRAO, default=None,
                   metavar="NOME", help="usa a area desenhada salva no banco")
    p.add_argument("--passo-km", dest="passo_km", type=float, default=PASSO_KM_PADRAO)
    p.add_argument("--limite", type=int, default=0)
    a = p.parse_args(argv)

    lista = pontos(cidade=a.cidade, uf=a.uf, area_ref=a.area,
                   passo_km=a.passo_km, limite=a.limite)
    print("%d pontos · passo %.1f km" % (len(lista), a.passo_km))
    print()
    for i, pt in enumerate(lista, 1):
        print("  %3d  %-26s %-52s %6d end." % (
            i, pt["rotulo"][:26], pt["endereco"][:52], pt["enderecos_na_celula"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
