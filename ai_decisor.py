"""
ai_decisor.py — Decisor de equivalência de POI via IA (OpenAI gpt-4o-mini)

Para um POI buscado que não teve match direto, mas cuja busca "próximo daqui"
retornou candidatos vizinhos, a IA decide se algum candidato é o MESMO
estabelecimento (abreviações, nome-fantasia vs razão social, sufixos, grafia).

Chave: OPENAI_API_KEY no .env. Modelo: OPENAI_MODEL (default gpt-4o-mini).
"""

import os
import json
from concurrent.futures import ThreadPoolExecutor

import config  # carrega o .env

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY ausente no .env")
        _CLIENT = OpenAI(api_key=key)
    return _CLIENT


def _prompt(nome, endereco, candidatos):
    linhas = "\n".join(
        f"[{i}] {c.get('nome','')} — {c.get('endereco','') or 's/ endereço'} ({c.get('dist_m','?')}m)"
        for i, c in enumerate(candidatos)
    )
    return (
        "Você identifica se um estabelecimento buscado corresponde a algum lugar "
        "próximo encontrado no Google Maps.\n\n"
        f'BUSCADO: "{nome}"  (endereço fornecido: {endereco or "n/d"})\n\n'
        f"CANDIDATOS PRÓXIMOS:\n{linhas}\n\n"
        "Considere abreviações, nome-fantasia vs razão social, sufixos "
        '(ex.: "Med Unique" = "Clínica Med Unique - Saúde e Estética"), erros de '
        "grafia e proximidade. Seja rigoroso: só marque correspondência se for "
        "plausível ser o MESMO lugar.\n\n"
        'Responda SOMENTE em JSON: {"idx": <índice do candidato equivalente ou null>, '
        '"confianca": <0 a 1>, "motivo": "<curto>"}'
    )


def decidir(nome, endereco, candidatos) -> dict:
    if not candidatos:
        return {"idx": None, "confianca": 0.0, "motivo": "sem candidatos"}
    try:
        resp = _client().chat.completions.create(
            model=MODEL, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": _prompt(nome, endereco, candidatos)}],
        )
        out = json.loads(resp.choices[0].message.content)
        idx = out.get("idx")
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        if not isinstance(idx, int) or idx < 0 or idx >= len(candidatos):
            idx = None
        return {"idx": idx, "confianca": float(out.get("confianca", 0) or 0),
                "motivo": str(out.get("motivo", ""))[:120]}
    except Exception as e:
        return {"idx": None, "confianca": 0.0, "motivo": f"erro IA: {str(e)[:80]}"}


def decidir_lote(consultas: list, paralelo: int = 8) -> list:
    with ThreadPoolExecutor(max_workers=paralelo) as ex:
        return list(ex.map(
            lambda q: decidir(q.get("nome", ""), q.get("endereco", ""), q.get("candidatos", [])),
            consultas,
        ))


if __name__ == "__main__":
    testes = [
        {"nome": "Med Unique", "endereco": "Parnaíba PI",
         "candidatos": [{"nome": "Clínica Med Unique - Saúde e Estética", "dist_m": 12},
                        {"nome": "Bar do Chagão", "dist_m": 30}]},
    ]
    for t, r in zip(testes, decidir_lote(testes)):
        print(f"  '{t['nome']}' -> idx={r['idx']} conf={r['confianca']} | {r['motivo']}")
