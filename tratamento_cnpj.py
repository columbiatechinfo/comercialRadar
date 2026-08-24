# -*- coding: utf-8 -*-
"""tratamento_cnpj.py — a skill `tratamento-cnpj` sobre o nosso banco.

A skill cruza CNPJ da Receita com o CNEFE do IBGE e devolve, para cada empresa,
quatro eixos INDEPENDENTES: aptidão geográfica, perfil comercial, evidência de
existência física e potencial de cruzamento. Mais a rota de tratamento —
reclassificação 1:1 ou individualização multi-economia.

Ela lê e escreve ARQUIVO. Nós temos as duas entradas no banco de referência
(`rf_estabelecimentos`, `ibge_cnefe`), e o destino é o banco do produto. Este
arquivo faz a volta completa: exporta o recorte, roda a skill, reingere.

**Por município, e só o município.** A Receita tem 168 mil estabelecimentos só
em Canoas e o CNEFE tem 176 mil endereços. Rodar por UF seria trocar minutos por
horas para responder a mesma pergunta.

**O código do município tem DOIS mundos.** O usuário fala IBGE (4304606); a
Receita usa código próprio (8589), e o CNEFE usa o do IBGE. Traduzir pelo NOME é
obrigatório — usar o código IBGE direto na Receita traz Porto Alegre no lugar de
Canoas, sem erro nenhum, só com o município errado.

**Os quatro eixos ficam separados no banco.** Achatá-los num score perderia o
que a skill existe para dizer: um cadastro pode ter perfil comercial forte E
coordenada ruim, e isso leva a ações diferentes — uma vira visita, a outra vira
pedido de endereço.

USO
  python tratamento_cnpj.py --municipio 4304606 --empresa "Aegea - Corsan"
  python tratamento_cnpj.py --municipio 4304606 --empresa "Aegea - Corsan" --aplicar
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import psycopg2.extras

import config  # noqa: F401
import base_comum as bc

RAIZ = Path(__file__).resolve().parent
SKILL = RAIZ / "skills" / "tratamento-cnpj" / "scripts" / "pipeline_cnpj_ibge.py"

# Nomes que a skill espera (contrato-dados.md). Manter os nomes DELA aqui, e não
# renomear a saída dela para os nossos, é o que permite atualizar a skill sem
# reescrever este arquivo.
CNEFE_COLS = ["COD_UNICO_ENDERECO", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR",
              "NOM_SEGLOGR", "NUM_ENDERECO", "CEP", "NV_GEO_COORD",
              "LATITUDE", "LONGITUDE", "COD_ESPECIE", "DSC_ESTABELECIMENTO"]


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").upper())
                   if unicodedata.category(c) != "Mn").strip()


def _municipio(cod_ibge: str, ref) -> tuple[str, str, str]:
    """(nome, uf, codigo_receita). Traduz IBGE → nome → Receita.

    A tradução por NOME não basta, e a primeira versão disto errou em silêncio.
    `rf_municipios` tem apenas (codigo, descricao) — sem UF. E nome de município
    se repete muito no Brasil: "Cachoeirinha" existe no RS e em PE, e o primeiro
    código que aparece pode ser o do outro estado. O sintoma foi um recorte com
    ZERO CNPJs ativos numa cidade de 46 mil habitantes, sem erro nenhum.

    Quem desambigua é o dado: entre os códigos que têm esse nome, vale o que de
    fato tem estabelecimentos na UF certa.
    """
    with ref.cursor() as cur:
        cur.execute("select nome, uf from ibge_malha where cod_municipio = %s", (cod_ibge,))
        r = cur.fetchone()
        if not r:
            raise SystemExit(f"município IBGE {cod_ibge} não existe na malha")
        nome, uf = r
        cur.execute("select codigo from rf_municipios where upper(descricao) = %s "
                    "order by codigo", (_norm(nome),))
        candidatos = [x[0] for x in cur.fetchall()]
        if not candidatos:
            raise SystemExit(f"'{nome}' não achado em rf_municipios")
        if len(candidatos) == 1:
            return nome, uf, candidatos[0]
        # Homônimo entre estados: escolhe pelo que existe na UF.
        cur.execute("""select municipio, count(*) from rf_estabelecimentos
                        where uf = %s and municipio = any(%s)
                        group by 1 order by 2 desc limit 1""", (uf, candidatos))
        m = cur.fetchone()
        if not m:
            raise SystemExit(
                f"'{nome}' tem {len(candidatos)} códigos na Receita "
                f"({', '.join(candidatos)}) e nenhum com estabelecimento em {uf}")
        print(f"  [aviso] '{nome}' é homônimo em {len(candidatos)} estados; "
              f"escolhido {m[0]} por ter {m[1]:,} estabelecimentos em {uf}")
        return nome, uf, m[0]


def exportar(cod_ibge: str, dir_tmp: Path, limite: int = 0) -> tuple[Path, Path, str, str]:
    import pandas as pd
    ref = bc.conectar_referencia()
    nome, uf, cod_rf = _municipio(cod_ibge, ref)
    print(f"  município: {nome}/{uf} · IBGE {cod_ibge} · Receita {cod_rf}")

    lim = f"limit {int(limite)}" if limite else ""
    with ref.cursor() as cur:
        # Só ATIVA: empresa baixada não tem ponto físico para cruzar, e arrastá-la
        # multiplicaria o custo do casamento sem mudar decisão nenhuma.
        cur.execute(f"""
            select e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv as cnpj_completo,
                   e.situacao_cadastral as situacao,
                   e.tipo_logradouro, e.logradouro, e.numero, e.complemento,
                   e.bairro, e.cep, e.cnae_principal as cnae_principal_cod,
                   e.matriz_filial, e.data_inicio, e.nome_fantasia, e.uf,
                   %s as municipio, em.razao_social
              from rf_estabelecimentos e
              left join rf_empresas em on em.cnpj_basico = e.cnpj_basico
             where e.uf = %s and e.municipio = %s and e.situacao_cadastral = '02'
             {lim}""", (nome, uf, cod_rf))
        cnpj = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

        # TODAS as colunas, não só as obrigatórias.
        #
        # A primeira versão exportava as onze do contrato mínimo, e a skill
        # respondeu com um alerta honesto: "CNEFE sem colunas opcionais
        # (degradação declarada)". Entre as que faltavam estava
        # `COD_INDICADOR_ESTAB_ENDERECO` — e a documentação dela é explícita:
        # a rota de tratamento deriva DESSE indicador, nunca da contagem de
        # registros. Sem ele, reclassificação 1:1 e individualização
        # multi-economia simplesmente não são distinguidas.
        #
        # Nós temos a coluna. Mandar menos do que se tem, para uma skill que
        # declara o que perdeu, é desperdiçar tanto o dado quanto o aviso.
        cur.execute("""
            select * from ibge_cnefe where cod_municipio = %s""", (cod_ibge,))
        cnefe = pd.DataFrame(cur.fetchall(),
                             columns=[d[0].upper() for d in cur.description])
        faltam = [c for c in CNEFE_COLS if c not in cnefe.columns]
        if faltam:
            raise SystemExit(f"ibge_cnefe sem colunas obrigatórias: {faltam}")
    ref.close()

    print(f"  exportado: {len(cnpj):,} CNPJs ativos · {len(cnefe):,} endereços do CNEFE")
    if cnpj.empty or cnefe.empty:
        raise SystemExit("recorte vazio — nada a cruzar")

    a_cnpj = dir_tmp / f"cnpj_{cod_ibge}.parquet"
    a_cnefe = dir_tmp / f"cnefe_{cod_ibge}.csv"
    cnpj.to_parquet(a_cnpj, index=False)
    cnefe.to_csv(a_cnefe, index=False, sep=";", encoding="utf-8")
    return a_cnpj, a_cnefe, nome, uf


def rodar(a_cnpj: Path, a_cnefe: Path, dir_tmp: Path) -> Path:
    saida = dir_tmp / "resultado.xlsx"
    cmd = [sys.executable, str(SKILL), "--cnpj", str(a_cnpj),
           "--ibge", str(a_cnefe), "--out", str(saida)]
    print(f"  rodando a skill…")
    r = subprocess.run(cmd, capture_output=True, text=True,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if r.returncode != 0:
        print(r.stdout[-2500:])
        print(r.stderr[-2500:])
        raise SystemExit(f"a skill falhou (código {r.returncode})")
    for linha in (r.stdout or "").splitlines()[-12:]:
        print(f"     {linha}")
    # A v2.2 grava Parquet ao lado do xlsx: é o contrato de handoff e não tem o
    # teto de linhas do Excel.
    pq = sorted(glob.glob(str(dir_tmp / "*.parquet")))
    pq = [p for p in pq if "cnpj_" not in Path(p).name]
    if not pq:
        raise SystemExit("a skill não gerou Parquet de saída")
    return Path(pq[0])


def _col(df, *nomes):
    """Primeira coluna que existir, entre os aliases. A skill evoluiu de versão
    e renomeou eixos; procurar por vários nomes evita quebrar a cada release."""
    for n in nomes:
        for c in df.columns:
            if c.upper() == n.upper():
                return c
    return None


def ingerir(res: Path, cod_ibge: str, empresa: str, aplicar: bool):
    import pandas as pd
    df = pd.read_parquet(res)
    print(f"  resultado: {len(df):,} linhas, {len(df.columns)} colunas")

    # Os nomes REAIS da v2.2, conferidos na saída — não os que eu supus.
    # Vários vêm com o prefixo `XFERA_`, que é a marca do produto de origem da
    # skill. Adivinhar nome de coluna faz o adaptador gravar NULL em silêncio,
    # que é pior do que quebrar: a tabela enche de linha sem eixo nenhum e
    # ninguém percebe até alguém perguntar por que a fila está vazia.
    c_cnpj = _col(df, "cnpj_completo", "CNPJ_NORM", "CNPJ")
    c_apt = _col(df, "APTIDAO_CRUZAMENTO", "APTIDAO_GEOGRAFICA")
    c_per = _col(df, "PERFIL_COMERCIAL")
    c_evi = _col(df, "XFERA_PERFIL_ENDERECO", "EVIDENCIA_EXISTENCIA")
    c_pot = _col(df, "POTENCIAL_CRUZAMENTO")
    c_rot = _col(df, "XFERA_ROTA_TRATAMENTO", "ROTA_TRATAMENTO")
    c_lat = _col(df, "XFERA_LAT", "LATITUDE")
    c_lng = _col(df, "XFERA_LON", "LONGITUDE")
    c_inc = _col(df, "XFERA_INCERTEZA_M", "INCERTEZA_M", "PERFIL_CONFIANCA")
    c_raz = _col(df, "razao_social", "RAZAO_SOCIAL")
    c_fan = _col(df, "nome_fantasia", "NOME_FANTASIA")
    c_cna = _col(df, "cnae_principal_cod", "CNAE", "cnae")
    c_sit = _col(df, "situacao", "SITUACAO")

    faltando = [r for r, c in (("cnpj", c_cnpj), ("potencial", c_pot)) if not c]
    if faltando:
        print(f"  colunas da skill: {list(df.columns)[:25]}")
        raise SystemExit(f"não achei no resultado: {', '.join(faltando)}")

    for rot, c in (("perfil comercial", c_per), ("evidência", c_evi), ("rota", c_rot)):
        if c:
            top = df[c].value_counts().head(5)
            print(f"\n  {rot}:")
            for k, v in top.items():
                print(f"     {str(k)[:38]:<40}{v:>8,}")

    if not aplicar:
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        return

    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    cur.execute("select id, nome from tenants where lower(nome)=lower(%s) and ativo", (empresa,))
    r = cur.fetchone()
    if not r:
        raise SystemExit(f"empresa '{empresa}' não existe")
    cur.execute("select set_config('app.tenant_id', %s, false)", (str(r[0]),))

    def v(row, c):
        if not c:
            return None
        x = row.get(c)
        if x is None or (isinstance(x, float) and x != x):
            return None
        s = str(x).strip()
        return s or None

    def f(row, c):
        s = v(row, c)
        try:
            return float(s) if s else None
        except ValueError:
            return None

    linhas = [(
        v(row, c_cnpj), cod_ibge, v(row, c_raz), v(row, c_fan), v(row, c_cna),
        v(row, c_sit), v(row, c_apt), v(row, c_per), v(row, c_evi),
        v(row, c_pot), v(row, c_rot), f(row, c_lat), f(row, c_lng),
        int(f(row, c_inc)) if f(row, c_inc) is not None else None,
    ) for _, row in df.iterrows() if v(row, c_cnpj)]

    psycopg2.extras.execute_values(
        cur,
        """insert into cnpj_tratado
             (cnpj, cod_municipio, razao_social, nome_fantasia, cnae, situacao,
              aptidao_geo, perfil_comercial, evidencia, potencial, rota,
              lat, lng, incerteza_m)
           values %s
           on conflict (tenant_id, cnpj) do update set
             aptidao_geo=excluded.aptidao_geo, perfil_comercial=excluded.perfil_comercial,
             evidencia=excluded.evidencia, potencial=excluded.potencial,
             rota=excluded.rota, lat=excluded.lat, lng=excluded.lng,
             incerteza_m=excluded.incerteza_m""",
        linhas, page_size=500)

    # REPROCESSAR O MESMO MUNICÍPIO É O CASO NORMAL — a Receita publica base
    # nova todo mês, e o CNEFE não muda mas o cruzamento melhora. Sem o upsert,
    # a segunda rodada morria aqui, DEPOIS de 48 s de skill e com a transação
    # inteira revertida: 24 mil CNPJs calculados e jogados fora por causa de uma
    # linha de contabilidade.
    cur.execute("""insert into fonte_arquivos (fonte, referencia, tabela, linhas, status)
                   values ('tratamento-cnpj', %s, 'cnpj_tratado', %s, 'ok')
                   on conflict on constraint fonte_arquivos_pkey do update
                     set linhas = excluded.linhas, status = 'ok',
                         carregado_em = now()""",
                (f"municipio {cod_ibge}", len(linhas)))
    con.commit()
    cur.execute("select count(*) from cnpj_tratado")
    print(f"\n  GRAVADO: {len(linhas):,} CNPJs tratados · total na tabela: {cur.fetchone()[0]:,}")
    con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--municipio", required=True, help="código IBGE, ex.: 4304606")
    ap.add_argument("--empresa", required=True)
    ap.add_argument("--limite", type=int, default=0, help="corta a base CNPJ, para teste")
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="cnpj_") as tmp:
        d = Path(tmp)
        a_cnpj, a_cnefe, nome, uf = exportar(a.municipio, d, a.limite)
        res = rodar(a_cnpj, a_cnefe, d)
        ingerir(res, a.municipio, a.empresa, a.aplicar)


if __name__ == "__main__":
    main()
