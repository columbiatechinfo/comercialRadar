# -*- coding: utf-8 -*-
"""
base_cnefe.py — Cadastro Nacional de Endereços para Fins Estatísticos (IBGE, Censo 2022).

106,8 milhões de endereços do Brasil com lat/lng, CEP, logradouro e ESPÉCIE
(1=domicílio particular, 2=coletivo, comércio, etc.). Um .zip por UF, HTTP direto.
Grava em ibge_cnefe (colunas derivadas do cabeçalho do CSV — robusto a mudanças).

USO:
  .venv\\Scripts\\python base_cnefe.py --uf PI          # só Piauí
  .venv\\Scripts\\python base_cnefe.py                  # Brasil inteiro (27 UFs)
  .venv\\Scripts\\python base_cnefe.py --recriar        # limpa antes
"""
import re
import ssl
import zipfile
import argparse
import unicodedata
import urllib.request

import base_comum as bc

BASE = ("https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/"
        "Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/")
_SSL = ssl.create_default_context()
TABELA = "ibge_cnefe"


def _norm(c: str) -> str:
    c = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode()
    c = re.sub(r"[^a-zA-Z0-9]+", "_", c).strip("_").lower()
    return c or "col"


def listar_ufs() -> list:
    """Lista os arquivos <cod>_<UF>.zip disponíveis no FTP do IBGE."""
    req = urllib.request.Request(BASE, headers={"User-Agent": bc.UA})
    with urllib.request.urlopen(req, timeout=60, context=_SSL) as r:
        html = r.read().decode("utf-8", "ignore")
    return sorted(set(re.findall(r'href="(\d{2}_[A-Z]{2}\.zip)"', html)))


def _header_cols(zip_path):
    """Lê o cabeçalho do 1º CSV do zip e devolve (nomes_normalizados, entradas_csv)."""
    with zipfile.ZipFile(zip_path) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            return None, []
        with z.open(csvs[0]) as f:
            primeira = f.readline().decode("latin-1", "ignore").rstrip("\r\n")
        cols = [_norm(c) for c in primeira.split(";")]
        # deduplica nomes repetidos
        vistos, saida = {}, []
        for c in cols:
            vistos[c] = vistos.get(c, 0) + 1
            saida.append(c if vistos[c] == 1 else f"{c}_{vistos[c]}")
        return saida, csvs


def garantir_tabela(conn, cols):
    ddl = "CREATE TABLE IF NOT EXISTS %s (%s)" % (
        TABELA, ", ".join(f"{c} text" for c in cols))
    with conn, conn.cursor() as cur:
        cur.execute(ddl)


def run(ufs, recriar, indices):
    conn = bc.conectar()
    bc.garantir_controle(conn)
    disponiveis = listar_ufs()
    if ufs:
        alvos = [a for a in disponiveis if a.split("_")[1].split(".")[0] in ufs]
    else:
        alvos = disponiveis
    print(f"🏠 CNEFE (IBGE) | {len(alvos)} UF(s) | endereços/residências", flush=True)

    if recriar:
        with conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {TABELA}")
            cur.execute("DELETE FROM fonte_arquivos WHERE fonte='cnefe'")
        print("  🧹 ibge_cnefe recriada")

    for i, nome in enumerate(alvos, 1):
        if bc.ja_carregado(conn, "cnefe", nome):
            print(f"  {i}/{len(alvos)} {nome:12} — já carregado, pula"); continue
        destino = bc.TMP / nome
        try:
            print(f"  {i}/{len(alvos)} {nome:12} baixando…", flush=True)
            bc.baixar(BASE + nome, destino)
            cols, csvs = _header_cols(destino)
            if not cols:
                print("      sem CSV no zip, ignora"); continue
            garantir_tabela(conn, cols)
            tam = destino.stat().st_size
            print(f"      COPY → {TABELA} ({tam/1e6:.0f} MB, {len(cols)} cols)…", flush=True)
            n = 0
            with zipfile.ZipFile(destino) as z:
                for c in csvs:
                    with z.open(c) as f:
                        n += bc.copy_csv(conn, TABELA, f, header=True, colunas=cols)
            bc.marcar(conn, "cnefe", nome, TABELA, n, tam)
            print(f"      ✓ {n:,} endereços".replace(",", "."), flush=True)
        except Exception as e:
            print(f"      ✗ erro: {str(e)[:140]}", flush=True)
        finally:
            bc.limpar_tmp(destino)

    if indices:
        print("  criando índices…", flush=True)
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{TABELA}'")
            existentes = {r[0] for r in cur.fetchall()}
            for col in ("cod_municipio", "cod_uf", "cep", "cod_especie"):
                if col in existentes:
                    cur.execute(f"CREATE INDEX IF NOT EXISTS ix_cnefe_{col} ON {TABELA} ({col})")
        print("  ✓ índices criados")
    conn.close()
    print("✅ CNEFE concluído.", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--uf", default="", help="UFs separadas por vírgula (padrão: todas)")
    p.add_argument("--recriar", action="store_true")
    p.add_argument("--indices", action="store_true")
    a = p.parse_args()
    ufs = [u.strip().upper() for u in a.uf.split(",") if u.strip()]
    run(ufs, a.recriar, a.indices)
