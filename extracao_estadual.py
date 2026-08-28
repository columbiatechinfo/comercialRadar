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


def _endereco_por_coordenada(lat, lng, cod_municipio: str):
    """A porta do CNEFE mais próxima, como texto. `None` quando não há.

    A base estadual traz dezenas de milhares de POIs por município e muitos sem
    endereço. Só o CNEFE entra aqui — local, 0,18 ms por ponto. O Maps, que
    custa dezenas de segundos e navegador com proxy, ficaria de fora mesmo que
    resolvesse mais: em 27 mil POIs seriam dias.
    """
    try:
        import endereco_reverso as rev
    except ImportError:
        return None
    achado = rev.por_cnefe(lat, lng, str(cod_municipio))
    return achado["endereco"] if achado else None


def ingerir(saida: str, cod_municipio: str, limite: int = 0, aplicar: bool = False,
            empresa: str = ""):
    import pandas as pd          # o `val()` lá embaixo depende de `pd.isna`
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

    # VAZIO DO PANDAS NÃO É `None`, e essa distinção custou 105.148 campos.
    #
    # `str(float('nan'))` devolve a palavra "nan". O `if v is None` deixava
    # passar, e o banco recebeu texto onde devia receber nulo. Medido em
    # 25/08/2026, só na importação de hoje:
    #
    #   28.662  pois.email          18.837  pois.instagram
    #   28.394  pois.website        17.193  pois.telefone
    #    7.919  pois.endereco        1.452  pois.nome
    #
    # O estrago não ficou no banco. O painel conta "com telefone" por
    # `telefone is not null`, então em Cachoeirinha ele mostrava 11.918 POIs com
    # telefone quando 6.559 tinham — 45% inventado. Em site, 74%. E o
    # cruzamento contava "mesmo domínio: nan" como prova: das 1.460 fusões que
    # ele propunha, 1.334 (91%) eram esse nada casando com esse nada.
    #
    # `pd.isna` pega NaN, NaT e pd.NA de uma vez. A guarda de tipo existe porque
    # ele devolve ARRAY para lista e array, e um array num `if` levanta
    # ValueError — que é como um campo multivalorado derrubaria a importação
    # inteira em vez de virar um nulo.
    def val(r, col):
        v = r.get(col)
        if v is None:
            return None
        if not isinstance(v, (list, tuple, set, dict)):
            try:
                if pd.isna(v):
                    return None
            except (TypeError, ValueError):
                pass
        s = str(v).strip()
        # E o texto que JÁ VEIO escrito assim da fonte. O parquet do Overture
        # traz "None" e "null" digitados em campo de contato; deixá-los passar
        # reconstrói o mesmo problema por outro caminho.
        if s.lower() in ("nan", "none", "null", "nat", "<na>", "-", "--", "n/a"):
            return None
        return s or None

    linhas = []
    sem_endereco = 0
    for _, r in df.iterrows():
        pid = f"estadual:{r['cluster_id']}"
        if pid in ja:
            continue
        try:
            lat, lng = float(r["lat"]), float(r["lon"])
        except (TypeError, ValueError):
            continue
        d = {nosso: val(r, deles) for deles, nosso in DE_PARA.items()}

        # SEM ENDEREÇO O POI NÃO ENTRA — e a base estadual traz muitos assim.
        #
        # O trigger `poi_comparavel` recusa desde 27/08/2026: registro sem nome
        # nem endereço tem teto de 1 ponto de evidência e nunca funde com
        # ninguém. Aqui isso derrubava a ETAPA INTEIRA, porque o
        # `execute_values` manda 500 linhas por comando: uma sem endereço
        # levava as outras 499 junto.
        #
        # A coordenada vira endereço pelo CNEFE (95% em 2 ms, medido). Sem Maps
        # nesta etapa: são dezenas de milhares de POIs por município, e o
        # navegador com proxy custaria dias.
        if not (d["endereco"] or "").strip():
            achado = _endereco_por_coordenada(lat, lng, cod_municipio)
            if not achado:
                sem_endereco += 1
                continue
            d["endereco"] = achado

        # NOME NULO VIRA VAZIO, e a diferença não é cosmética.
        #
        # A base estadual traz POI sem nome: só categoria e endereço — "Posto de
        # Combustível, Avenida Getúlio Vargas 7500". Pelo quadro do dono do
        # produto isso é VÁLIDO ("sem nome + endereço com número aceita, a porta
        # identifica sozinha"), mas `pois.nome` é `NOT NULL` desde antes e o
        # `None` estourava a inserção — levando as outras 499 linhas do lote.
        #
        # Vazio satisfaz a coluna e o trigger `poi_comparavel` avalia o resto:
        # com número na porta ele passa, sem número e sem coordenada ele é
        # recusado. Quem decide continua sendo a regra, não o tipo da coluna.
        linhas.append((
            d["nome"] or "", "estadual", lat, lng, lat, lng, pid,
            d["categoria"], d["endereco"], d["telefone"], d["website"],
            d["instagram"], d["email"], d["cidade"], d["uf"],
            "estadual", True,
        ))

    if sem_endereco:
        print(f"  {sem_endereco:,} sem endereço e sem porta do CNEFE por perto — "
              f"não entram (a regra exige endereço)")
    print(f"  prontas para inserir: {len(linhas):,}")
    if not aplicar:
        con.rollback()
        con.close()
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        return

    # `fetch=True` + `returning`, e NÃO `cur.rowcount`.
    #
    # `execute_values` com `page_size` executa VÁRIOS statements, e `rowcount`
    # descreve só o ÚLTIMO. Na importação de Canoas isso apareceu como
    # "GRAVADO: 127 POIs novos" depois de dizer "prontas para inserir: 27.627" —
    # o número era o resto da divisão (55 páginas de 500 + 127). O dado estava
    # certo e o relatório mentia, que é a pior combinação: ninguém vai conferir
    # 27 mil linhas por causa de um número pequeno, vai concluir que a extração
    # não trouxe quase nada.
    #
    # `len(linhas)` também não serviria: com `on conflict do nothing`, parte das
    # linhas pode legitimamente não entrar. Só o banco sabe quantas entraram.
    inseridos = len(psycopg2.extras.execute_values(
        cur,
        """insert into pois
             (nome, fonte, lat_origem, lng_origem, maps_lat, maps_lng, place_id,
              categoria, endereco, telefone, website, instagram, email,
              cidade, uf, status, match_valido)
           values %s
           on conflict do nothing
           returning id""",
        linhas, page_size=500, fetch=True))

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
