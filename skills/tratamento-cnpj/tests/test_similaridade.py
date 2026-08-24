"""Bateria de similaridade de logradouro — separa typo (aceitar) de truncamento (recusar).

Prova que token_sort_ratio sozinho, no limiar de produção, classifica os 18 casos
corretamente — e que a v2.1 (max com token_set_ratio) erra os 8 de truncamento.
"""
from rapidfuzz import fuzz

# (nome no cadastro, nome no CNEFE, DEVE_ACEITAR)
CASOS = [
    # typos legítimos — precisam passar
    ("RIO BRNCO", "RIO BRANCO", True),
    ("SEMINARISTAWENDELINO PLEIN", "SEMINARISTA WENDELINO PLEIN", True),
    ("DOUTOR FLORS", "DOUTOR FLORES", True),
    ("PRESIDENTE VARGAZ", "PRESIDENTE VARGAS", True),
    ("SAO JOAO BATISTA", "SAO JOAO BATSTA", True),
    ("MARECHAL FLORIANO PEIXOT", "MARECHAL FLORIANO PEIXOTO", True),
    ("INDEPENDENCIA", "INDEPENDENCIA", True),
    ("JOAO NEVES DA FONTOURA", "JOAO NEVES DA FONTOURA", True),
    # subconjunto / truncamento — precisam ser recusados
    ("BRASIL", "BRASIL NOVO", False),
    ("SAO JOSE", "SAO JOSE DO NORTE", False),
    ("THEODORICO", "THEODORICO ANTONIO DUARTE", False),
    ("AMERICO", "AMERICO VESPUCIO", False),
    ("CLAUDIO", "CLAUDIO MANOEL DA COSTA", False),
    ("SAO", "SAO BORJA", False),
    ("JOAO PESSOA", "JOAO PESSOA DE OLIVEIRA LIMA", False),
    ("15 DE NOVEMBRO", "15 DE NOVEMBRO DE 1889", False),
    # ruas simplesmente diferentes — recusar
    ("SANTOS DUMONT", "SANTA RITA", False),
    ("BENTO GONCALVES", "BORGES DE MEDEIROS", False),
]


def score(a, b, coef_len, coef_tok):
    s = float(fuzz.token_sort_ratio(a, b))
    la, lb = len(a), len(b)
    pen_len = coef_len * (1 - min(la, lb) / max(la, lb)) if max(la, lb) else 0
    pen_tok = coef_tok * abs(len(a.split()) - len(b.split()))
    return s - pen_len - pen_tok


print(f"{'coef_len':>8} {'coef_tok':>8} {'thr':>4} | {'acertos':>7} | erros")
melhor = None
for coef_len in (0, 20, 30, 40, 50, 60):
    for coef_tok in (0, 3, 5, 10):
        for thr in (85, 88, 90):
            erros = []
            for a, b, aceita in CASOS:
                passou = score(a, b, coef_len, coef_tok) >= thr
                if passou != aceita:
                    erros.append(f"{'FN' if aceita else 'FP'}:{a[:14]}")
            n = len(CASOS) - len(erros)
            if not melhor or n > melhor[0]:
                melhor = (n, coef_len, coef_tok, thr, erros)
            if n >= len(CASOS) - 1:
                print(f"{coef_len:>8} {coef_tok:>8} {thr:>4} | {n:>3}/{len(CASOS)} | {', '.join(erros)}")

n, cl, ct, thr, erros = melhor
print(f"\nMELHOR: coef_len={cl} coef_tok={ct} thr={thr} -> {n}/{len(CASOS)}")
print("erros restantes:", erros or "nenhum")
print(f"\nDetalhe com coef_len={cl}, coef_tok={ct}:")
for a, b, aceita in CASOS:
    s = score(a, b, cl, ct)
    marca = "OK " if (s >= thr) == aceita else "ERR"
    print(f"  {marca} {'aceitar' if aceita else 'recusar':>8} | score={s:6.1f} | {a[:30]:<30} x {b[:30]}")

# --- gate ------------------------------------------------------------------
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from _indice_match import StreetEntry, street_similarity  # noqa: E402
from _normalizacao import normalize_logradouro  # noqa: E402

THR = 90
falhas_v22, falhas_v21 = [], []
for a, b, aceita in CASOS:
    la, lb = normalize_logradouro(a), normalize_logradouro(b)
    e = StreetEntry("", lb.tipo, lb.base, lb.full, lb.nucleo, lb.n_tokens)
    s22 = street_similarity(la.tipo, la.base, la.nucleo, la.n_tokens, e)
    s21 = max(fuzz.token_set_ratio(la.base, lb.base),
              fuzz.token_sort_ratio(la.nucleo, lb.nucleo) if la.nucleo and lb.nucleo else 0)
    if (s22 >= THR) != aceita:
        falhas_v22.append((a, b, s22))
    if (s21 >= THR) != aceita:
        falhas_v21.append((a, b, s21))

print(f"\n{'='*70}\nGATE (limiar de produção = {THR})")
print(f"  v2.2 (token_sort puro)      : {len(CASOS)-len(falhas_v22)}/{len(CASOS)}")
print(f"  v2.1 (max com token_set)    : {len(CASOS)-len(falhas_v21)}/{len(CASOS)}")
for a, b, s in falhas_v21:
    print(f"      v2.1 erra: {a[:26]:<26} x {b[:26]:<26} score={s:.0f}")
raise SystemExit(1 if falhas_v22 else 0)
