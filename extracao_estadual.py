# -*- coding: utf-8 -*-
"""extracao_estadual.py — a skill `extracao-poi-estadual` virando dado no banco.

A skill entrega ARQUIVO: CSV e GeoParquet de uma UF inteira, com dedup por
evidência e procedência linha a linha. A premissa deste projeto é que dado mora
no banco. Este arquivo é a ponte, e ele faz três coisas que a skill não faz.

**Ingere por MUNICÍPIO, não por estado.** O RS tem 383 mil POIs no padronizado.
Despejar isso na base de um cliente que trabalha uma cidade não é cobertura, é
entulho: a tela fica lenta, a fila de aprovação enche de ponto que ninguém
pediu, e o custo de enriquecer sobe para todos eles. O município é a unidade que
o usuário de fato escolhe, e é a que a `quadras_br` já usa.

**Carimba a empresa dona.** O `tenant_id` vem da variável de sessão, como todo o
resto — a trigger cuida. Sem isso a extração cairia sem dono e, com a RLS
ligada, ficaria invisível para todo mundo.

**Não compete com o que já foi achado.** POI da extração estadual entra com
`fonte='estadual'` e `place_id='estadual:<cluster>'`. Se a mineração já achou o
mesmo estabelecimento, os dois convivem — quem decide se são o mesmo ponto é a
etapa de convergência, com coordenada e nome, não um INSERT otimista.

USO
  # ingerir um município a partir de uma extração já produzida
  python extracao_estadual.py --saida <dir_da_extracao> --municipio 4304606

  # listar o que há na extração
  python extracao_estadual.py --saida <dir_da_extracao> --listar
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import psycopg2.extras

import config  # noqa: F401
import base_comum as bc

# Colunas da saída padronizada da skill (v3.6.1) que viram coluna nossa.
# O que não está aqui não é descartado por acaso: `cluster_id` vira o place_id,
# e o resto — precisão da coordenada, motivos de dedup, hierarquia de categoria —
# é diagnóstico da extração, não dado do estabelecimento.
DE_PARA = {
    "nome": "nome",
    "categoria_pt": "categoria",
    "endereco_completo": "endereco",
    "telefone": "telefone",
    "site": "website",
    "instagram": "instagram",
    "email": "email",
    "NOME_MUNICIPIO": "cidade",
    "UF": "uf",
}


def _padronizado(saida: str) -> Path:
    """Acha o arquivo padronizado da extração. Prefere Parquet: são 383 mil
    linhas, e ler CSV disso custa minutos e memória que o Parquet não pede."""
    for padrao in ("poi_padronizado_*.parquet", "poi_padronizado_*.csv"):
        achados = sorted(glob.glob(os.path.join(saida, padrao)))
        if achados:
            return Path(achados[0])
    raise SystemExit(f"não achei poi_padronizado_* em {saida}")


def _ler(arq: Path, cod_municipio: str | None):
    import pandas as pd
    if arq.suffix == ".parquet":
        df = pd.read_parquet(arq)
    else:
        df = pd.read_csv(arq, dtype=str, low_memory=False)
    df["COD_MUNICIPIO"] = df["COD_MUNICIPIO"].astype(str).str.strip()
    if cod_municipio:
        df = df[df["COD_MUNICIPIO"] == str(cod_municipio).strip()]
    return df


def listar(saida: str):
    arq = _padronizado(saida)
    df = _ler(arq, None)
    print(f"  {arq.name}: {len(df):,} POIs")
    g = (df.groupby(["COD_MUNICIPIO", "NOME_MUNICIPIO", "UF"])
           .size().reset_index(name="n").sort_values("n", ascending=False))
    print(f"  {len(g)} municípios\n")
    print(f"  {'cod':<10}{'município':<32}{'uf':<4}{'POIs':>9}")
    for _, r in g.head(25).iterrows():
        print(f"  {r['COD_MUNICIPIO']:<10}{str(r['NOME_MUNICIPIO'])[:30]:<32}"
              f"{r['UF']:<4}{r['n']:>9,}")


def ingerir(saida: str, cod_municipio: str, limite: int = 0, aplicar: bool = False,
            empresa: str = ""):
    arq = _padronizado(saida)
    df = _ler(arq, cod_municipio)
    if df.empty:
        print(f"  nenhum POI para o município {cod_municipio} em {arq.name}")
        return
    if limite:
        df = df.head(limite)

    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()

    # A EMPRESA VEM DO COMANDO, não do .env.
    #
    # `CR_TENANT_ID` é um valor fixo, e serve para o pipeline de um cliente só.
    # Aqui não serve: a mesma extração do RS alimenta a Corsan hoje e outra
    # concessionária amanhã, e o operador precisa dizer qual. Na primeira
    # execução deste script o padrão do .env teria despejado 17 mil POIs de
    # Canoas na empresa errada — sem erro nenhum, porque a trigger carimba o que
    # a sessão mandar.
    if empresa:
        cur.execute("select id, nome from tenants where lower(nome) = lower(%s) and ativo",
                    (empresa.strip(),))
        r = cur.fetchone()
        if not r:
            cur.execute("select nome from tenants where ativo order by nome")
            disp = ", ".join(x[0] for x in cur.fetchall())
            raise SystemExit(f"empresa '{empresa}' não existe. Ativas: {disp}")
        tenant, dono = str(r[0]), r[1]
        cur.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
    else:
        cur.execute("select current_setting('app.tenant_id', true)")
        tenant = cur.fetchone()[0]
        if not tenant:
            raise SystemExit("informe --empresa, ou defina CR_TENANT_ID no .env")
        cur.execute("select nome from tenants where id = %s::uuid", (tenant,))
        dono = (cur.fetchone() or ["?"])[0]
        print(f"  [aviso] usando a empresa do .env: {dono}. "
              f"Passe --empresa para escolher outra.")
    print(f"  {len(df):,} POIs de {df['NOME_MUNICIPIO'].iloc[0]}/{df['UF'].iloc[0]}"
          f" → empresa {dono}")

    # place_id já existentes: reingerir a mesma extração não pode duplicar.
    ids = [f"estadual:{c}" for c in df["cluster_id"].astype(str)]
    cur.execute("select place_id from pois where place_id = any(%s)", (ids,))
    ja = {r[0] for r in cur.fetchall()}
    print(f"  já no banco: {len(ja):,} · a inserir: {len(ids) - len(ja):,}")

    def val(r, col):
        v = r.get(col)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    linhas = []
    for _, r in df.iterrows():
        pid = f"estadual:{r['cluster_id']}"
        if pid in ja:
            continue
        try:
            lat, lng = float(r["lat"]), float(r["lon"])
        except (TypeError, ValueError):
            continue
        d = {nosso: val(r, deles) for deles, nosso in DE_PARA.items()}
        linhas.append((
            d["nome"], "estadual", lat, lng, lat, lng, pid,
            d["categoria"], d["endereco"], d["telefone"], d["website"],
            d["instagram"], d["email"], d["cidade"], d["uf"],
            "estadual", True,
        ))

    print(f"  prontas para inserir: {len(linhas):,}")
    if not aplicar:
        con.rollback()
        con.close()
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        return

    psycopg2.extras.execute_values(
        cur,
        """insert into pois
             (nome, fonte, lat_origem, lng_origem, maps_lat, maps_lng, place_id,
              categoria, endereco, telefone, website, instagram, email,
              cidade, uf, status, match_valido)
           values %s
           on conflict do nothing""",
        linhas, page_size=500)
    inseridos = cur.rowcount

    # Procedência: qual extração, qual município, quantas linhas. Sem isto,
    # daqui a três meses ninguém sabe de onde vieram estes POIs nem se a
    # extração que os gerou já está velha.
    # Mesmo motivo do `tratamento_cnpj`: reingerir a mesma extração é rotina
    # (extração nova da UF, ou o município rodado de novo), e sem o upsert a
    # segunda vez morria na contabilidade levando junto a ingestão inteira.
    cur.execute("""insert into fonte_arquivos (fonte, referencia, tabela, linhas, status)
                   values ('extracao-poi-estadual', %s, 'pois', %s, 'ok')
                   on conflict on constraint fonte_arquivos_pkey do update
                     set linhas = excluded.linhas, status = 'ok',
                         carregado_em = now()""",
                (f"{arq.name} · municipio {cod_municipio}", inseridos))
    con.commit()
    con.close()
    print(f"\n  GRAVADO: {inseridos:,} POIs novos")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--saida", required=True, help="pasta da extração já produzida")
    ap.add_argument("--municipio", default="", help="código IBGE do município")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--empresa", default="", help="nome da empresa dona do dado")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    if a.listar or not a.municipio:
        listar(a.saida)
        return
    ingerir(a.saida, a.municipio, a.limite, a.aplicar, a.empresa)


if __name__ == "__main__":
    main()
