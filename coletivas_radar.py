"""
coletivas_radar.py — roda o MÉTODO DELES (skill Radar Coletivo v4.11) sobre o
CNEFE que já está no nosso banco.

A primeira tentativa foi reescrever o método (`coletivas_cnefe.py`). O
agrupamento saiu a 99,86% — mas a tipologia ficou grossa e a inferência de
unidades faltantes não existia, porque são 150 KB de `radar_utils.py` com 14
tipos, léxico curado, DBSCAN de polos comerciais e ocupação por setor
censitário, refinados em 11 versões com auditoria externa. Reescrever aquilo é
produzir números DIFERENTES, que é o oposto do pedido.

Então aqui não se reescreve nada: o pipeline deles roda inteiro. O que este
arquivo faz é trocar a etapa de entrada — em vez de baixar o ZIP da UF (236 MB)
e particionar 6,4 milhões de linhas, ele **exporta do `ibge_cnefe`** o CSV do
município no formato exato que o runner espera.

  eles:  baixar UF → particionar → radar_potenciais → rcc_emissor
  aqui:  SELECT no banco → radar_potenciais → rcc_emissor
                           └── idêntico, é o script deles

DOIS AMBIENTES, DE PROPÓSITO. A skill roda em modo `strict` e compara o runtime
com `requirements.lock.txt`: ela exige pandas 3.0.2, e o comercialRadar roda em
2.3.3. Subir o principal para pandas 3 por causa de uma fase é arriscar todo o
resto; por isso ela ganhou `.venv-coletivo` e é chamada como subprocesso — que é,
aliás, como o pipeline original já se organiza.

USO:
  .venv\\Scripts\\python coletivas_radar.py --municipio 4304606
  .venv\\Scripts\\python coletivas_radar.py --municipio 4304606 --so-exportar
"""

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

import config          # noqa: F401
import base_comum as bc

BASE = Path(__file__).resolve().parent
SKILL = BASE / "skills" / "radar-coletivo"
PY_COLETIVO = BASE / ".venv-coletivo" / "Scripts" / "python.exe"
TRABALHO = BASE / "coletivas"

# A ORDEM E O NOME das colunas são o contrato do runner: ele valida
# COLUNAS_OBRIGATORIAS e aborta se faltar uma. São os nomes do CNEFE em caixa
# alta — os mesmos da tabela no banco, que preservou o schema da fonte.
COLUNAS = [
    "COD_UNICO_ENDERECO", "COD_UF", "COD_MUNICIPIO", "COD_DISTRITO",
    "COD_SUBDISTRITO", "COD_SETOR", "NUM_QUADRA", "NUM_FACE", "CEP",
    "DSC_LOCALIDADE", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR",
    "NUM_ENDERECO", "DSC_MODIFICADOR",
    "NOM_COMP_ELEM1", "VAL_COMP_ELEM1", "NOM_COMP_ELEM2", "VAL_COMP_ELEM2",
    "NOM_COMP_ELEM3", "VAL_COMP_ELEM3", "NOM_COMP_ELEM4", "VAL_COMP_ELEM4",
    "NOM_COMP_ELEM5", "VAL_COMP_ELEM5",
    "LATITUDE", "LONGITUDE", "NV_GEO_COORD", "COD_ESPECIE",
    "DSC_ESTABELECIMENTO", "COD_INDICADOR_ESTAB_ENDERECO",
    "COD_INDICADOR_CONST_ENDERECO", "COD_INDICADOR_FINALIDADE_CONST",
    "COD_TIPO_ESPECI",
]
# `COD_TIPO_ESPECI` é o nome que veio na carga; o runner pede
# `COD_TIPO_ESPECIE`. O apelido é resolvido na exportação, não no banco — mexer
# no nome da coluna lá quebraria quem já lê a tabela.
APELIDOS = {"COD_TIPO_ESPECI": "COD_TIPO_ESPECIE"}
SEP = ";"


def nome_municipio(cod: str, con) -> str:
    """Nome no formato do pipeline: `<cod>_<CIDADE_COM_UNDERSCORE>`.

    O nome é cosmético — entra no nome do arquivo. Por isso a falha aqui NÃO
    pode custar a exportação: sem o `rollback`, o erro de uma tabela que não
    existe deixa a transação abortada e o `SELECT` seguinte morre com
    "comandos ignorados até o fim do bloco de transação" — um problema de
    nomenclatura derrubando a carga inteira."""
    import unicodedata
    nome = "MUNICIPIO"
    for tabela, colcod, colnome in (("ibge_malha", "cod_municipio", "nome"),
                                    ("ibge_malha", "codigo", "nome"),
                                    ("rf_municipios", "codigo", "descricao")):
        try:
            with con.cursor() as cur:
                cur.execute(f"SELECT {colnome} FROM {tabela} "
                            f"WHERE {colcod}::text = %s LIMIT 1", (cod,))
                r = cur.fetchone()
            if r and r[0]:
                nome = str(r[0])
                break
        except Exception:
            con.rollback()          # a próxima consulta precisa da conexão limpa
    t = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    t = "".join(c if (c.isalnum() or c == " ") else " " for c in t.upper())
    return "_".join(t.split())


def exportar(cod: str, con) -> Path:
    """CSV do município no formato da fonte. `;` e QUOTE_MINIMAL, como o
    particionador deles — o leitor do runner lê com `;` fixo, e gravar com
    vírgula produz arquivo que o próprio pipeline não consegue ler (defeito D6
    registrado na skill)."""
    entrada = TRABALHO / "municipios"
    entrada.mkdir(parents=True, exist_ok=True)
    try:
        alvo = entrada / f"{cod}_{nome_municipio(cod, con)}.csv"
    except Exception:
        alvo = entrada / f"{cod}_MUNICIPIO.csv"

    cols_sql = ", ".join(c.lower() for c in COLUNAS)
    cabecalho = [APELIDOS.get(c, c) for c in COLUNAS]
    ini = time.time()
    n = 0
    with open(alvo, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=SEP, quoting=csv.QUOTE_MINIMAL,
                       lineterminator="\n")
        w.writerow(cabecalho)
        with con.cursor(name="exp_cnefe") as cur:
            cur.itersize = 20000
            cur.execute(f"SELECT {cols_sql} FROM ibge_cnefe "
                        f"WHERE cod_municipio = %s", (cod,))
            for linha in cur:
                w.writerow(["" if v is None else v for v in linha])
                n += 1
    print(f"   {n:,} linhas → {alvo.name} ({time.time()-ini:.0f}s)"
          .replace(",", "."), flush=True)
    return alvo


def rodar_runner(csv_mun: Path, extra: list = None) -> int:
    """Chama `radar_potenciais.py` — o runner canônico da skill, sem alteração."""
    saida = TRABALHO / "radar"
    saida.mkdir(parents=True, exist_ok=True)
    # `--input/--output`, com o xlsx nomeado como o pipeline deles espera
    # (`RADAR_<cod>_<CIDADE>.xlsx`) — é o nome que o retomador usa para saber
    # o que já ficou pronto.
    alvo = saida / f"RADAR_{csv_mun.stem}.xlsx"
    cmd = [str(PY_COLETIVO), str(SKILL / "scripts" / "radar_potenciais.py"),
           "--input", str(csv_mun), "--output", str(alvo)] + (extra or [])
    print(f"   → {' '.join(Path(c).name if os.sep in str(c) else str(c) for c in cmd)}",
          flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    p = subprocess.run(cmd, cwd=str(SKILL / "scripts"), env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    for linha in (p.stdout or "").splitlines()[-40:]:
        print("   | " + linha, flush=True)
    if p.returncode != 0:
        for linha in (p.stderr or "").splitlines()[-25:]:
            print("   ! " + linha, flush=True)
    return p.returncode


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--municipio", required=True, help="código IBGE de 7 dígitos")
    p.add_argument("--so-exportar", action="store_true")
    p.add_argument("--extra", nargs=argparse.REMAINDER,
                   help="argumentos repassados ao runner da skill")
    a = p.parse_args()

    if not PY_COLETIVO.exists():
        raise SystemExit(
            f"ambiente da skill não existe: {PY_COLETIVO}\n"
            f"crie com:\n"
            f"  python -m venv .venv-coletivo\n"
            f"  .venv-coletivo\\Scripts\\python -m pip install -r "
            f"skills/radar-coletivo/scripts/requirements.lock.txt")

    con = bc.conectar()
    try:
        print(f"🏢 Radar Coletivo — município {a.municipio}", flush=True)
        csv_mun = exportar(a.municipio, con)
    finally:
        con.close()
    if a.so_exportar:
        return
    rc = rodar_runner(csv_mun, a.extra)
    print(f"{'✅' if rc == 0 else '❌'} runner terminou com código {rc}", flush=True)


if __name__ == "__main__":
    main()
