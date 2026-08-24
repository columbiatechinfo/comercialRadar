#!/usr/bin/env python3
"""Diagnóstico do ambiente antes de uma carga Cadastur v3."""
from __future__ import annotations
import importlib, json, shutil, sys
from pathlib import Path

REQ = {"pandas":"2.2", "pyarrow":"15", "openpyxl":"3.1", "xlrd":"2.0.1", "lxml":"5.0", "psycopg":"3.2"}
OPTIONAL = {"psycopg"}  # obrigatório apenas se --postgres-dsn for usado

def main() -> int:
    out={"python":sys.version.split()[0],"dependencias":{},"gate":"OK"}; falhas=[]; avisos=[]
    for mod,minimo in REQ.items():
        try:
            m=importlib.import_module(mod)
            out["dependencias"][mod]={"instalado":True,"versao":getattr(m,"__version__","?"),"minimo":minimo,"opcional":mod in OPTIONAL}
        except Exception as e:
            out["dependencias"][mod]={"instalado":False,"erro":str(e),"minimo":minimo,"opcional":mod in OPTIONAL}
            (avisos if mod in OPTIONAL else falhas).append(mod)
    uso=shutil.disk_usage(Path.cwd())
    out["disco"]={"livre_gb":round(uso.free/1024**3,2),"total_gb":round(uso.total/1024**3,2)}
    if falhas:
        out["gate"]="FALHOU"; out["acao"]="pip install -r requirements.txt"
    elif avisos:
        out["gate"]="OK_COM_AVISO"; out["aviso"]="psycopg ausente: apenas carga PostgreSQL direta fica indisponivel"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if not falhas else 2

if __name__=='__main__': raise SystemExit(main())
