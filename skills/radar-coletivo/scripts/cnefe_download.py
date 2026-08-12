#!/usr/bin/env python3
"""
CNEFE IBGE 2022 — Downloader + Enriquecimento de colunas por UF
Fonte: ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/
       Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/

Uso:
  python cnefe_download.py                  # baixa todos os 27 arquivos
  python cnefe_download.py --ufs RS SC SP   # apenas UFs especificadas
  python cnefe_download.py --ufs RS --skip-download  # só enriquece já baixados
"""

import argparse
import ftplib
import zipfile
import os
import sys
import time
from pathlib import Path

import pandas as pd

# ─────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────
# Separador canonico do CNEFE. Uma constante, usada na leitura E na escrita:
# duas literais em pontos distintos do codigo foi exatamente como elas
# divergiram.
SEP_CNEFE = ";"

FTP_HOST = "ftp.ibge.gov.br"
FTP_PATH = "/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/"
OUTPUT_DIR = Path(__file__).parent  # mesma pasta do script

# Mapa completo cod_uf → sigla (ordem IBGE)
UF_MAP = {
    11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA",
    16: "AP", 17: "TO", 21: "MA", 22: "PI", 23: "CE",
    24: "RN", 25: "PB", 26: "PE", 27: "AL", 28: "SE",
    29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP",
    41: "PR", 42: "SC", 43: "RS", 50: "MS", 51: "MT",
    52: "GO", 53: "DF",
}
# Inverso: sigla → (cod, filename)
SIG_MAP = {v: (k, f"{k:02d}_{v}.zip") for k, v in UF_MAP.items()}

# ─────────────────────────────────────────────────────────────
# DICIONÁRIOS PARA ENRIQUECIMENTO
# ─────────────────────────────────────────────────────────────
ESPECIE = {
    1: "Domicílio particular",
    2: "Domicílio coletivo",
    3: "Estabelecimento agropecuário",
    4: "Estabelecimento de ensino",
    5: "Estabelecimento de saúde",
    6: "Outras finalidades",
    7: "Edificação em construção/reforma",
    8: "Estabelecimento religioso",
}

NIVEL_GEO = {
    1: "Endereço original (Censo 2022)",
    2: "Endereço modificado (aptos mesmo número)",
    3: "Endereço estimado",
    4: "Face de quadra",
    5: "Localidade",
    6: "Setor censitário",
}

INDICADOR_ESTAB = {
    1: "Único",
    2: "Múltiplo até 10",
    3: "Múltiplo > 10",
    4: "Múltiplo (quantidade desconhecida)",
}

INDICADOR_CONST = {
    1: "Único",
    2: "Múltiplo até 10",
    3: "Múltiplo > 10",
    4: "Múltiplo (quantidade desconhecida)",
}

FINALIDADE_CONST = {
    1: "Residencial",
    2: "Não residencial",
    3: "Misto",
    4: "Indeterminado",
}

TIPO_ESPECIE = {
    101: "Casa",
    102: "Casa de vila/condomínio",
    103: "Apartamento",
    104: "Outros",
}

UF_NOME = {
    11: "Rondônia", 12: "Acre", 13: "Amazonas", 14: "Roraima",
    15: "Pará", 16: "Amapá", 17: "Tocantins", 21: "Maranhão",
    22: "Piauí", 23: "Ceará", 24: "Rio Grande do Norte",
    25: "Paraíba", 26: "Pernambuco", 27: "Alagoas", 28: "Sergipe",
    29: "Bahia", 31: "Minas Gerais", 32: "Espírito Santo",
    33: "Rio de Janeiro", 35: "São Paulo", 41: "Paraná",
    42: "Santa Catarina", 43: "Rio Grande do Sul",
    50: "Mato Grosso do Sul", 51: "Mato Grosso", 52: "Goiás",
    53: "Distrito Federal",
}

# ─────────────────────────────────────────────────────────────
# FTP DOWNLOAD
# ─────────────────────────────────────────────────────────────
def salvar(df, destino):
    """Escrita canonica de um CSV CNEFE. Unico ponto de saida do downloader.

    Existe para que leitura e escrita NAO possam divergir de novo: quem grava
    passa por aqui, quem le passa por `cnefe_coletivas.load_cnefe`, e as duas
    usam `SEP_CNEFE`.
    """
    import pandas as _pd
    destino = str(destino)
    comp = "gzip" if destino.endswith(".gz") else None
    _pd.DataFrame(df).to_csv(destino, index=False, sep=SEP_CNEFE,
                             compression=comp, encoding="utf-8")
    return destino


def ftp_download(filename: str, dest_path: Path) -> None:
    """Download com retry e progress bar simples."""
    for attempt in range(1, 4):
        try:
            ftp = ftplib.FTP(FTP_HOST, timeout=60)
            ftp.login()
            ftp.cwd(FTP_PATH)
            size = ftp.size(filename)
            downloaded = 0
            start = time.time()

            with open(dest_path, "wb") as f:
                def callback(chunk):
                    nonlocal downloaded
                    f.write(chunk)
                    downloaded += len(chunk)
                    pct = downloaded / size * 100 if size else 0
                    elapsed = time.time() - start
                    speed = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
                    print(
                        f"\r  {pct:5.1f}%  {downloaded/1024/1024:7.1f}/{size/1024/1024:.1f} MB"
                        f"  {speed:.2f} MB/s",
                        end="", flush=True,
                    )
                ftp.retrbinary(f"RETR {filename}", callback, blocksize=1024 * 256)

            ftp.quit()
            print()  # newline após progress
            return
        except (ftplib.Error, OSError, EOFError) as e:
            print(f"\n  ⚠ Tentativa {attempt}/3 falhou: {e}")
            if dest_path.exists():
                dest_path.unlink()
            if attempt < 3:
                time.sleep(5 * attempt)
    raise RuntimeError(f"Falha ao baixar {filename} após 3 tentativas.")


# ─────────────────────────────────────────────────────────────
# ENRIQUECIMENTO
# ─────────────────────────────────────────────────────────────
def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona colunas descritivas a partir dos códigos numéricos."""

    # Normaliza nome das colunas (o CSV do IBGE usa ponto-e-vírgula e aspas)
    df.columns = df.columns.str.strip().str.strip('"')

    # ── UF
    if "COD_UF" in df.columns:
        df["NOM_UF"] = df["COD_UF"].map(UF_NOME)
        df["SIG_UF"] = df["COD_UF"].map(UF_MAP)

    # ── Espécie
    if "COD_ESPECIE" in df.columns:
        df["DSC_ESPECIE"] = df["COD_ESPECIE"].map(ESPECIE)

    # ── Nível de geocodificação
    if "NV_GEO_COORD" in df.columns:
        df["DSC_NIVEL_GEO"] = df["NV_GEO_COORD"].map(NIVEL_GEO)
        # Flag de qualidade: 1–2 = alta, 3 = estimada, 4–6 = baixa
        df["QUALIDADE_GEO"] = df["NV_GEO_COORD"].map(
            {1: "Alta", 2: "Alta", 3: "Estimada", 4: "Baixa", 5: "Baixa", 6: "Baixa"}
        )

    # ── Indicador de estabelecimento
    if "COD_INDICADOR_ESTAB_ENDERECO" in df.columns:
        df["DSC_INDICADOR_ESTAB"] = df["COD_INDICADOR_ESTAB_ENDERECO"].map(INDICADOR_ESTAB)

    # ── Indicador de construção
    if "COD_INDICADOR_CONST_ENDERECO" in df.columns:
        df["DSC_INDICADOR_CONST"] = df["COD_INDICADOR_CONST_ENDERECO"].map(INDICADOR_CONST)

    # ── Finalidade da construção
    if "COD_INDICADOR_FINALIDADE_CONST" in df.columns:
        df["DSC_FINALIDADE_CONST"] = df["COD_INDICADOR_FINALIDADE_CONST"].map(FINALIDADE_CONST)

    # ── Tipo de edificação
    if "COD_TIPO_ESPECIE" in df.columns:
        df["DSC_TIPO_ESPECIE"] = df["COD_TIPO_ESPECIE"].map(TIPO_ESPECIE)

    # ── Endereço completo composto
    parts = []
    for col in ["NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR"]:
        if col in df.columns:
            parts.append(df[col].fillna("").str.strip())
    if "NUM_ENDERECO" in df.columns:
        parts.append("nº " + df["NUM_ENDERECO"].fillna("").astype(str).str.strip())
    if "DSC_MODIFICADOR" in df.columns:
        parts.append(df["DSC_MODIFICADOR"].fillna("").str.strip())
    if "DSC_LOCALIDADE" in df.columns:
        parts.append(df["DSC_LOCALIDADE"].fillna("").str.strip())

    if parts:
        df["ENDERECO_COMPLETO"] = (
            pd.concat(parts, axis=1)
            .apply(lambda r: " ".join(v for v in r if v), axis=1)
            .str.strip()
        )

    # ── Flag domicílio particular (uso mais frequente em saneamento)
    if "COD_ESPECIE" in df.columns:
        df["FLAG_DOMICILIO"] = (df["COD_ESPECIE"] == 1).astype("int8")

    return df


# ─────────────────────────────────────────────────────────────
# PROCESSAMENTO POR UF
# ─────────────────────────────────────────────────────────────
def process_uf(sig: str, skip_download: bool) -> None:
    cod, filename = SIG_MAP[sig]
    zip_path = OUTPUT_DIR / filename
    out_csv = OUTPUT_DIR / f"CNEFE_2022_{sig}.csv.gz"

    # ── Download
    if not skip_download:
        if zip_path.exists():
            print(f"  [{sig}] ZIP já existe, pulando download.")
        else:
            print(f"  [{sig}] Baixando {filename}...")
            ftp_download(filename, zip_path)
    else:
        if not zip_path.exists():
            print(f"  [{sig}] ⚠ ZIP não encontrado e --skip-download ativo. Ignorando.")
            return

    # ── Leitura e enriquecimento
    print(f"  [{sig}] Descomprimindo e processando...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise FileNotFoundError(f"Nenhum CSV dentro de {filename}")
        # Normalmente um único CSV por UF
        inner_csv = csv_names[0]
        with zf.open(inner_csv) as f:
            df = pd.read_csv(
                f,
                sep=SEP_CNEFE,
                encoding="utf-8",
                dtype={
                    "ID": "int64",
                    "COD_UNICO_ENDERECO": "int64",
                    "COD_UF": "int16",
                    "COD_MUNICIPIO": "int32",
                    "COD_SETOR": "str",
                    "CEP": "str",
                    "NUM_ENDERECO": "str",
                    "LATITUDE": "float64",
                    "LONGITUDE": "float64",
                    "NV_GEO_COORD": "int16",
                    "COD_ESPECIE": "int16",
                    "COD_INDICADOR_ESTAB_ENDERECO": "float32",
                    "COD_INDICADOR_CONST_ENDERECO": "float32",
                    "COD_INDICADOR_FINALIDADE_CONST": "float32",
                    "COD_TIPO_ESPECIE": "float32",
                },
                low_memory=False,
            )

    n_orig = len(df)
    df = enrich(df)

    # ── Salva CSV comprimido na mesma pasta
    # SEPARADOR EXPLICITO. `to_csv` sem `sep` grava VIRGULA, e `load_cnefe`
    # le com ';' fixo: o downloader oficial da skill nao produzia entrada
    # valida para o pipeline da propria skill — o arquivo relido virava UMA
    # coluna com o cabecalho inteiro no nome. Nao havia teste E2E
    # download -> preparar_base para pegar isso.
    salvar(df, out_csv)
    print(
        f"  [{sig}] ✓ {n_orig:,} registros → {out_csv.name}"
        f"  ({out_csv.stat().st_size / 1024 / 1024:.1f} MB)"
    )

    # Remove ZIP para economizar espaço (comente se quiser manter)
    # zip_path.unlink()


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Download + enriquecimento do CNEFE IBGE 2022")
    parser.add_argument(
        "--ufs",
        nargs="+",
        metavar="UF",
        help="Siglas das UFs (ex: RS SC SP). Padrão: todas as 27.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Pula FTP e apenas processa ZIPs já existentes.",
    )
    args = parser.parse_args()

    target_ufs = [u.upper() for u in args.ufs] if args.ufs else list(SIG_MAP.keys())
    invalid = [u for u in target_ufs if u not in SIG_MAP]
    if invalid:
        print(f"UFs inválidas: {invalid}")
        print(f"Válidas: {sorted(SIG_MAP.keys())}")
        sys.exit(1)

    print(f"\nCNEFE IBGE 2022 — {len(target_ufs)} UF(s): {', '.join(target_ufs)}")
    print(f"Destino: {OUTPUT_DIR}\n")

    errors = []
    for sig in target_ufs:
        try:
            process_uf(sig, args.skip_download)
        except Exception as e:
            print(f"  [{sig}] ERRO: {e}")
            errors.append((sig, str(e)))

    print("\n─────────────────────────────")
    print(f"Concluído. Sucesso: {len(target_ufs) - len(errors)}  Erros: {len(errors)}")
    for sig, err in errors:
        print(f"  [{sig}] {err}")


if __name__ == "__main__":
    main()
