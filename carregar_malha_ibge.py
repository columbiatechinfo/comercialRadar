# -*- coding: utf-8 -*-
"""A malha municipal do IBGE na resolucao oficial, direto do shapefile.

POR QUE ESTE ARQUIVO EXISTE

`ibge_malha` e a divisa que decide se um POI e de Canoas ou do vizinho, e ela
entrava pela API de malhas do IBGE com `qualidade=intermediaria`. Medido em
Canoas, contra o shapefile oficial da Malha Municipal 2022:

    shapefile IBGE 2022      287 vertices   130,79 km2    (a referencia)
    API, qualidade maxima     96 vertices   130,77 km2    0,14 km2 de erro,  47 m
    API, intermediaria         25 vertices   131,24 km2    2,35 km2 de erro, 272 m
    API, minima                 8 vertices   126,71 km2

Duzentos e setenta e dois metros de desvio numa conurbacao — Canoas encosta em
Porto Alegre, Esteio e Sapucaia — sao quarteiroes comerciais inteiros entrando
ou saindo por engano. O historico do proprio `garantir_malha` ja registra o
mesmo defeito um degrau abaixo: com `minima`, Itambe-PE caia na Paraiba.

A API continua servindo o caminho SOB DEMANDA, e subiu para `maxima`. Este
script e para quando a divisa precisa estar certa de verdade: baixa o produto
oficial da UF e regrava na resolucao completa.

    python carregar_malha_ibge.py --uf RS
    python carregar_malha_ibge.py --uf RS --uf SC --uf PR
    python carregar_malha_ibge.py --todas          # os 27, sob demanda de ninguem

O `/api/malha` desenha a UF inteira e por isso SIMPLIFICA na leitura; o que fica
gravado aqui e o exato, que e o que a mineracao usa para recortar.
"""
import argparse
import io
import json
import subprocess
import sys
import urllib.request
import zipfile

sys.path.insert(0, "/app")

try:
    import shapefile                      # pyshp
except ImportError:                       # pragma: no cover
    # Instala num diretorio descartavel em vez de exigir rebuild da imagem:
    # este script roda de vez em quando, nao no caminho da requisicao.
    subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                    "--target", "/tmp/libs", "pyshp"], check=True)
    sys.path.insert(0, "/tmp/libs")
    import shapefile

import base_comum as bc  # noqa: E402

UFS = ("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR "
       "RS SC SE SP TO").split()
ANO = 2022
BASE = ("https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais"
        "/malhas_municipais/municipio_%d/UFs/%%s/%%s_Municipios_%d.zip" % (ANO, ANO))
UA = {"User-Agent": "comercialRadar/1.0"}


def baixar(url, tempo=600):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=tempo) as r:
        bruto = r.read()
    if bruto[:2] == bytes((0x1F, 0x8B)):          # o IBGE responde gzip
        import gzip
        bruto = gzip.decompress(bruto)
    return bruto


def geometrias_da_uf(uf: str):
    """[(codigo, nome, geojson), ...] na resolucao do arquivo oficial."""
    z = zipfile.ZipFile(io.BytesIO(baixar(BASE % (uf, uf))))
    nome_shp = [x[:-4] for x in z.namelist() if x.lower().endswith(".shp")][0]
    leitor = shapefile.Reader(shp=io.BytesIO(z.read(nome_shp + ".shp")),
                              dbf=io.BytesIO(z.read(nome_shp + ".dbf")))
    campos = [f[0] for f in leitor.fields[1:]]
    i_cod, i_nome = campos.index("CD_MUN"), campos.index("NM_MUN")
    for reg in leitor.iterShapeRecords():
        yield (str(reg.record[i_cod]), str(reg.record[i_nome]),
               json.dumps(reg.shape.__geo_interface__))


def carregar(uf: str, log=print) -> int:
    uf = uf.strip().upper()
    ref = bc.conectar_referencia()
    try:
        antes = medir(ref, uf)
        gravados = 0
        with ref.cursor() as cur:
            for cod, nome, gj in geometrias_da_uf(uf):
                cur.execute(
                    """insert into ibge_malha (cod_municipio, nome, uf, geom)
                       values (%s, %s, %s,
                               ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))
                       on conflict (cod_municipio) do update
                          set nome = coalesce(excluded.nome, ibge_malha.nome),
                              uf = excluded.uf, geom = excluded.geom""",
                    (cod, nome, uf, gj))
                gravados += 1
        ref.commit()
        depois = medir(ref, uf)
        log("  %s: %d municipios · vertices %s -> %s"
            % (uf, gravados, antes, depois))
        return gravados
    finally:
        ref.close()


def medir(ref, uf: str):
    with ref.cursor() as cur:
        cur.execute("""select coalesce(sum(st_npoints(geom)), 0)
                         from ibge_malha where uf = %s""", (uf,))
        return cur.fetchone()[0]


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uf", action="append", default=[],
                   help="sigla; pode repetir")
    p.add_argument("--todas", action="store_true", help="as 27 unidades")
    a = p.parse_args()
    alvos = UFS if a.todas else [u.upper() for u in a.uf]
    if not alvos:
        p.error("informe --uf SIGLA ou --todas")
    total = 0
    for uf in alvos:
        try:
            total += carregar(uf)
        except Exception as e:               # noqa: BLE001
            print("  %s FALHOU: %s" % (uf, str(e)[:120]), flush=True)
    print("  %d municipios regravados na resolucao oficial" % total)
