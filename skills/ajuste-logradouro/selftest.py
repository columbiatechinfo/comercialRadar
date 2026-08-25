# -*- coding: utf-8 -*-
"""
selftest.py — GATE. Roda antes e depois de qualquer mudança. Sem verde, não entrega.

Invariantes verificados:
  1. Similaridade: motor rapidfuzz × puro-Python são BIT-IDÊNTICOS (tolerância 1e-9).
  2. Normalização: casos-âncora de abreviação, título, numeral extenso e romano.
  3. FIDELIDADE SEMÂNTICA: bateria adversarial completa (texto E tier esperados).
  4. Número estruturado: modificador/faixa/KM preservados; 12A ≠ 12B no gate.
  5. Complemento: grudado desdobrado, string canônica ordenada, ruído auditado.
  6. Pareamento: blocking 3×3 == força bruta O(N²) (mesmo conjunto de pares).
  7. Validação: de-para inexistente ABORTA (SystemExit 2), não vira coluna vazia.
  8. Léxico concorrente: N escritores simultâneos, zero aprendizado perdido.
 8b. UPGRADE: léxico v3.1 migra na CARGA (com e sem aprendizado), sem quebrar.
  9. Léxico: aprende substituição 1↔1 com support≥2; NÃO aprende adição nem truncamento.
 10. Marcação: original preservado byte-a-byte; correção só em colunas `aj_`.
 11. Idempotência: 2ª execução com o mesmo léxico produz saída idêntica.
"""
from __future__ import annotations
import os
import random
import shutil
import subprocess
import sys
import tempfile
import json
import hashlib

import numpy as np
import pandas as pd

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import similaridade as S            # noqa: E402
import numero as NU                 # noqa: E402
import adversarial as ADV           # noqa: E402
import lexico_io as LIO             # noqa: E402
import validacao as V               # noqa: E402
import lexico_seguro as LS          # noqa: E402


def A(num, lat=-30.0, lon=-51.0):
    """âncora WGS84 (a persistida pelo léxico)."""
    return {"num": num, "lat": lat, "lon": lon}
import geo as G                     # noqa: E402
import complemento_organizador as CO  # noqa: E402
import normalizacao_base as N       # noqa: E402
import complemento_organizador as C  # noqa: E402
import pareamento_leve as P         # noqa: E402

FALHAS = []


def ok(cond, nome, detalhe=""):
    print(("  OK   " if cond else "  FALHA ") + nome + (f"  [{detalhe}]" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


def latest_dir(root):
    d = json.load(open(os.path.join(root, "latest.json"), encoding="utf-8"))
    return os.path.join(root, d["path"])


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# 1 ------------------------------------------------------------------------
def t_similaridade():
    print("1. Paridade de similaridade")
    if S.MOTOR != "rapidfuzz":
        print("  SKIP rapidfuzz ausente — motor puro-Python é o único caminho")
        return
    from rapidfuzz import fuzz
    rnd = random.Random(42)
    voc = ["RUA", "AVENIDA", "CONSELHEIRO", "XAVIER", "DA", "SILVA", "15", "NOVEMBRO",
           "DOUTOR", "PEREIRA", "TRAVESSA", "EXPEDICIONARIOS", "SANTOS", "DUMONT"]
    pior = 0.0
    for _ in range(4000):
        a = " ".join(rnd.choice(voc) for _ in range(rnd.randint(1, 5)))
        b = " ".join(rnd.choice(voc) for _ in range(rnd.randint(1, 5)))
        pior = max(pior, abs(fuzz.token_sort_ratio(a, b) - S.token_sort_py(a, b)))
    ok(pior < 1e-9, "token_sort rapidfuzz == puro-Python", f"delta max {pior:.3e}")


# 2 ------------------------------------------------------------------------
def t_normalizacao():
    print("2. Normalização")
    ok(N.norm_logradouro("R. Cons Xavier") == "RUA CONS XAVIER", "expande tipo no 1º token")
    ok(N.norm_logradouro("Av Dr Pereira", hard=True) == "AVENIDA DOUTOR PEREIRA",
       "hardening expande título")
    ok(N.norm_logradouro("Rua XV de Novembro", hard=True) == "RUA 15 DE NOVEMBRO",
       "romano -> dígito")
    ok(N.parse_numero("S/N") is None and N.parse_numero("000") is None, "S/N e zero -> None")
    ok(N.parse_numero("0123-A") == 123, "número com sufixo")
    v = N.carregar_vocab_tipo(os.path.join(AQUI, "vocabulario_aprendido.json"))
    ok(N.aplicar_vocab_1tok("NUCLE BOM JESUS", v) == "NUCLEO BOM JESUS", "vocab no slot do tipo")
    ok(N.aplicar_vocab_1tok("RUA NUCLE BOM", v) == "RUA NUCLE BOM", "vocab NÃO toca o nome")
    m = N.marcar("R Cons Xavier", hard=True, vmap=v,
                 ativos={("RUA {} XAVIER", "CONS"): "CONSELHEIRO"})
    ok(m["logr_marcado"] == "RUA CONSELHEIRO XAVIER" and "LEXICO" in m["origem"],
       "cascata marca e reporta origem")


# 3 ------------------------------------------------------------------------
def t_complemento():
    print("5. Complemento organizado")
    o = C.organizar("QD5LT3")
    ok(o["componentes"].get("QUADRA") == "5" and o["componentes"].get("LOTE") == "3",
       "grudado QD5LT3 desdobrado")
    o2 = C.organizar("AP 201 BL B (CEPISA)")
    ok(o2["complemento_organizado"].startswith("BLOCO B"), "ordem canônica grosso->fino",
       o2["complemento_organizado"])
    ok(any("CEPISA" in d for d in o2["descartado"]), "ruído institucional auditado")
    ok(o2["complemento_original"] == "AP 201 BL B (CEPISA)", "original intacto")


# 4 ------------------------------------------------------------------------
def t_pareamento():
    print("6. Pareamento leve == força bruta")
    rnd = np.random.default_rng(7)
    n = 600
    df = pd.DataFrame({
        "__a2l_gid": range(n),
        "aj_source_id": ["a"] * (n // 2) + ["b"] * (n - n // 2),
        "aj_num_base": rnd.integers(1, 40, n).astype(float),
        "__a2l_logr_pre": ["RUA X"] * n,
        "__a2l_x": rnd.uniform(0, 400, n),
        "__a2l_y": rnd.uniform(0, 400, n),
    })
    df["aj_num_chave"] = df["aj_num_base"].astype(int).astype(str) + "|"
    df["aj_num_tipo"] = "NUMERO"
    raio = 30.0
    cand, aud, diag = P.gerar_candidatos(df, raio_m=raio, teto_bloco=10 ** 9)
    got = {(a, b) for a, b, _ in cand}
    x, y, num = df["__a2l_x"].values, df["__a2l_y"].values, df["aj_num_base"].values
    esperado = {(i, j) for i in range(n) for j in range(i + 1, n)
                if num[i] == num[j] and np.hypot(x[i] - x[j], y[i] - y[j]) <= raio}
    ok(got == esperado, "conjunto de pares idêntico ao O(N²)",
       f"blocking {len(got)} x bruta {len(esperado)}")
    ok(len(cand) == len(set(got)), "sem par duplicado entre os 9 offsets")


# 3 ------------------------------------------------------------------------
def t_fidelidade():
    print("3. Fidelidade semântica (bateria adversarial)")
    falhas = []
    for entrada, esperado, tier_max, nota in ADV.CASOS:
        m = N.marcar(entrada, hard=True)
        if m["logr_marcado"] != esperado:
            falhas.append(f"{entrada!r} -> {m['logr_marcado']!r} (esperado {esperado!r})")
        elif m["tier"] != tier_max:
            falhas.append(f"{entrada!r} tier {m['tier']} (esperado {tier_max}) [{nota}]")
    ok(not falhas, f"{len(ADV.CASOS)} casos de fidelidade (texto + tier)",
       " | ".join(falhas[:3]))


# 4 ------------------------------------------------------------------------
def t_numero():
    print("4. Número estruturado")
    casos = {"12A": ("NUMERO", 12, "A"), "12B": ("NUMERO", 12, "B"),
             "279/281": ("FAIXA", 279, "/281"), "KM 5": ("KM", 5, "KM"),
             "00279": ("NUMERO", 279, ""), "S/N": ("SN", None, ""),
             "CEM": ("NUMERO", 100, "")}
    ruim = [k for k, (t, b, m) in casos.items()
            if (NU.parse(k)["tipo"], NU.parse(k)["base"], NU.parse(k)["modificador"]) != (t, b, m)]
    ok(not ruim, "tipo/base/modificador corretos", str(ruim))
    p1=NU.parse("1B")
    ok(p1["base"] == 1 and p1["canonico"] == "1" and p1["complemento_derivado"] == "IMOVEL B",
       "1B -> número 1 + complemento IMOVEL B", str(p1))
    p2=NU.parse("100 FUNDOS")
    ok(p2["base"] == 100 and p2["canonico"] == "100" and p2["complemento_derivado"] == "FUNDOS",
       "anotação textual sai do número e migra ao complemento", str(p2))
    a, b, c = NU.parse("12A"), NU.parse("12B"), NU.parse("12")
    ok(not NU.compativel(a, b), "12A e 12B NÃO ancoram o mesmo endereço")
    ok(not NU.compativel(a, c), "12A e 12 incompatíveis no modo estrito")
    ok(NU.compativel(a, c, modo="compativel"), "modo compatível aceita modificador ausente")
    ok(not NU.compativel(NU.parse("279/281"), NU.parse("279"), modo="compativel"),
       "faixa 279/281 não casa com 279 nem no modo compatível")
    ok(not NU.compativel(NU.parse("S/N"), NU.parse("S/N")), "S/N nunca ancora")


# 6b -----------------------------------------------------------------------
def t_srid_e_coordenada():
    print("6b. SRID e sanitização de coordenada")
    esperado = {"Boa Vista": ((-60.67, 2.82), 31974), "Macapa": ((-51.07, 0.03), 31976),
                "Manaus": ((-60.02, -3.11), 31980), "Porto Alegre": ((-51.23, -30.03), 31982),
                "Recife": ((-34.88, -8.05), 31985)}
    ruins = []
    for nome, ((lon, lat), epsg) in esperado.items():
        got, fam = G.autodetect_srid(lon, lat)
        if got != epsg or fam != "SIRGAS2000":
            ruins.append(f"{nome}: {got}/{fam} != {epsg}")
    ok(not ruins, "EPSG SIRGAS 2000 correto nos dois hemisférios", "; ".join(ruins))
    ok(G.autodetect_srid(-160.0, 20.0)[1] == "WGS84", "fora do SIRGAS declara fallback WGS84")

    x, y, srid, modo, d = G.projetar([-51.23, 999.0, 0.0, float("inf")],
                                     [-30.03, 999.0, 0.0, 10.0])
    ok(d["coord_invalidas"] == 3, "coordenada implausível saneada antes de projetar",
       str(d))
    ok(np.isfinite(x[0]) and not np.isfinite(x[1:]).any(), "nenhum inf sobrevive à projeção")


# 6c -----------------------------------------------------------------------
def t_lexico_canone():
    print("6c. Cânone do léxico (direção por evidência, no contexto + scope)")
    from collections import Counter
    SCOPE = "TEST"
    CTX = "RUA JOAO {}"
    freq = Counter({(CTX, "SILVA"): 5000, (CTX, "SILVAA"): 1,
                    (CTX, "JOSE"): 900, (CTX, "JOSEE"): 2,
                    (CTX, "SOUSA"): 300, (CTX, "SOUZA"): 280})
    a, c, base = LS.decidir_canone("SILVA", "SILVAA", CTX, freq)
    ok((a, c) == ("SILVAA", "SILVA"), "SILVA x SILVAA -> canônico SILVA", f"{a}->{c}")
    a, c, base = LS.decidir_canone("JOSE", "JOSEE", CTX, freq)
    ok((a, c) == ("JOSEE", "JOSE"), "JOSE x JOSEE -> dominância no contexto decide")
    a, c, base = LS.decidir_canone("SOUSA", "SOUZA", CTX, freq)
    ok(c is None and base == "INDEFINIDO", "SOUSA x SOUZA -> INDEFINIDO, não aplica")
    a, c, base = LS.decidir_canone("CONS", "CONSELHEIRO", CTX, Counter(),
                                   nivel_a=0, nivel_b=100)
    ok((a, c, base) == ("CONS", "CONSELHEIRO", "AUTORIDADE_NIVEL"),
       "autoridade DO PAR decide por nível")
    a, c, base = LS.decidir_canone("SOUSA", "SOUZA", CTX, Counter(),
                                   nivel_a=100, nivel_b=100)
    ok(c is None and base == "CONFLITO_AUTORIDADE",
       "autoridades de mesmo nível NÃO caem em frequência")

    C = "RUA {} X"
    lex = LS.lexico_vazio_v33()
    LS.atualizar(lex, [(C, "AAA", "BBB", "FREQUENCIA_CONTEXTO", A(10)),
                       (C, "AAA", "BBB", "FREQUENCIA_CONTEXTO", A(20))], min_support=2, scope_id=SCOPE)
    LS.atualizar(lex, [(C, "BBB", "AAA", "FREQUENCIA_CONTEXTO", A(30))], min_support=1, scope_id=SCOPE)
    ok(not LS.mapa_ativo(lex, SCOPE), "ciclo AAA<->BBB: nenhum dos dois fica ativo",
       str(LS.mapa_ativo(lex, SCOPE)))
    q = lex["scopes"][SCOPE]["quarentena"]
    ok(any(v["motivo"] == "CICLO" for v in q.values()), "ciclo registrado")

    lex2 = LS.lexico_vazio_v33()
    LS.atualizar(lex2, [(C, "STA", "SANTA", "AUTORIDADE_NIVEL", A(10)),
                        (C, "STA", "SANTA", "AUTORIDADE_NIVEL", A(20))], min_support=2, scope_id=SCOPE)
    ok(LS.mapa_ativo(lex2, SCOPE).get((C, "STA")) == "SANTA", "STA->SANTA ativo antes do conflito")
    LS.atualizar(lex2, [(C, "STA", "SANTO", "AUTORIDADE_NIVEL", A(30))], min_support=1, scope_id=SCOPE)
    ok((C, "STA") not in LS.mapa_ativo(lex2, SCOPE),
       "conflito STA->SANTO REBAIXA o STA->SANTA vigente", str(LS.mapa_ativo(lex2, SCOPE)))

    lex3 = LS.lexico_vazio_v33()
    LS.atualizar(lex3, [(C, "B", "CC", "FREQUENCIA_CONTEXTO", A(1))], min_support=1, scope_id=SCOPE)
    LS.atualizar(lex3, [(C, "A", "B", "FREQUENCIA_CONTEXTO", A(2))], min_support=1, scope_id=SCOPE)
    ok((C, "A") not in LS.mapa_ativo(lex3, SCOPE), "cadeia A->B->CC bloqueada",
       str(LS.mapa_ativo(lex3, SCOPE)))
    ok(LS.decidir_canone("X", "Y", C, Counter({(C, "X"): 2, (C, "Y"): 1}))[1] is None,
       "sem piso de frequência não há dominância")


# 6d -----------------------------------------------------------------------
def t_complemento_falso_positivo():
    print("6d. Complemento — alias não casa por prefixo")
    falsos = ["APARECIDA", "CASAMENTO CIVIL", "BLOQUEIO",
              "LOTEAMENTO NOVO", "TRAS DA ESCOLA", "QUADRAO", "CASARAO"]
    ruins = [t for t in falsos if CO.organizar(t)["componentes"]]
    ok(not ruins, f"{len(falsos)} palavras comuns não viram componente", str(ruins))
    osal=CO.organizar("SALAO COMERCIAL")
    ok("SALAO" in osal["componentes"] and "SALA" not in osal["componentes"],
       "SALAO é elemento CNEFE válido e nunca colapsa em SALA", str(osal["componentes"]))
    validos = {"QD5LT3": "QUADRA", "AP 201": "APARTAMENTO", "SOBRELOJA 2": "SOBRELOJA",
               "QUITINETE 4": "QUITINETE", "TERREO": "TERREO", "ANDAR 3": "ANDAR",
               "AP201": "APARTAMENTO", "CS 3": "CASA"}
    ruins = [k for k, v in validos.items() if v not in CO.organizar(k)["componentes"]]
    ok(not ruins, "componentes legítimos seguem reconhecidos", str(ruins))


# 6e -----------------------------------------------------------------------
def t_srid_metrico():
    print("6e. srid_metrico informado tem de ser métrico")
    ok(not G.validar_srid(4326)[0], "EPSG:4326 (geográfico) recusado")
    ok(not G.validar_srid(4674)[0], "EPSG:4674 SIRGAS geográfico recusado")
    ok(G.validar_srid(31982)[0] and G.validar_srid(31982)[2]["preferencial_br"],
       "EPSG:31982 aceito como preferencial BR")
    ok(G.validar_srid(31974)[0], "EPSG:31974 (hemisfério norte) aceito")
    try:
        G.projetar([-51.0, -40.0], [-30.0, -20.0], srid=4326)
        ok(False, "projetar() aborta com SRID em grau")
    except ValueError:
        ok(True, "projetar() aborta com SRID em grau")


# 6f -----------------------------------------------------------------------
def t_colunas_reservadas():
    print("6f. Namespace reservado e colunas homônimas da entrada")
    tmp = tempfile.mkdtemp(prefix="ajcol_")
    # a entrada traz nomes que o motor USAVA internamente na v3
    pd.DataFrame({"id": ["1", "2"], "logr": ["R CONS XAVIER", "RUA CONSELHEIRO XAVIER"],
                  "num": ["100", "100"], "lat": ["-30.0330", "-30.03305"],
                  "lon": ["-51.2300", "-51.23001"],
                  "gid": ["G-1", "G-2"], "x": ["X1", "X2"],
                  "logr_pre": ["ORIG-1", "ORIG-2"]}).to_csv(
        os.path.join(tmp, "f.csv"), index=False)
    cfg = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": True, "srid_metrico": 31982,
           "fontes": [{"source_id": "f", "arquivo": "f.csv", "autoridade": True,
                       "colunas": {"record_id": "id", "logradouro": "logr", "numero": "num",
                                   "lat": "lat", "lon": "lon"}}]}
    ok(V.validar(cfg)["ok"], "gid/x/logr_pre na entrada NÃO são namespace reservado")

    import yaml as _y
    with open(os.path.join(tmp, "cfg.yaml"), "w", encoding="utf-8") as fh:
        _y.safe_dump({k: v for k, v in cfg.items() if k != "_base_dir"}, fh)
    r = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"),
                        os.path.join(tmp, "cfg.yaml"), "--out", os.path.join(tmp, "out")],
                       cwd=AQUI, capture_output=True, text=True)
    ok(r.returncode == 0, "execução com colunas homônimas", r.stderr[-300:])
    if r.returncode == 0:
        out = pd.read_csv(os.path.join(latest_dir(os.path.join(tmp, "out")), "ajuste_logradouro.csv"),
                          dtype=str, keep_default_na=False)
        intactas = (out["gid"].tolist() == ["G-1", "G-2"] and out["x"].tolist() == ["X1", "X2"]
                    and out["logr_pre"].tolist() == ["ORIG-1", "ORIG-2"])
        ok(intactas, "colunas gid/x/logr_pre da entrada saem intactas",
           str(out.get("gid", pd.Series()).tolist()))
        ok(not [c for c in out.columns if c.startswith("__a2l_")],
           "nenhuma coluna interna vaza para a saída")

    pd.DataFrame({"id": ["1"], "logr": ["RUA A"], "num": ["10"], "lat": ["-30.0"],
                  "lon": ["-51.2"], "aj_logr_marcado": ["JA EXISTIA"]}).to_csv(
        os.path.join(tmp, "g.csv"), index=False)
    cfg2 = {**cfg, "fontes": [{**cfg["fontes"][0], "arquivo": "g.csv"}]}
    rel = V.validar(cfg2)
    ok(not rel["ok"] and any("RESERVADO" in e for e in rel["erros"]),
       "coluna aj_* na entrada ABORTA a execução", str(rel["erros"])[:120])
    shutil.rmtree(tmp, ignore_errors=True)


# 6g -----------------------------------------------------------------------
def t_complemento_referencial():
    print("6g. Complemento — contexto referencial")
    ref = {"PROX CASA 10": "CASA", "PERTO DO AP 201": "APARTAMENTO",
           "ATRAS DO BLOCO 3": "BLOCO", "DEFRONTE LOJA 4": "LOJA"}
    ruins = [k for k, comp in ref.items()
             if CO.organizar(k)["tier"] != "REVISAR"
             or "CONTEXTO_REFERENCIAL" not in CO.organizar(k)["risco"]
             or comp not in CO.organizar(k)["componentes"]]
    ok(not ruins, "referência vira REVISAR + CONTEXTO_REFERENCIAL (sem perder o dado)",
       str(ruins))
    o = CO.organizar("EM FRENTE AO POSTO")
    ok(not o["componentes"] and "CONTEXTO_REFERENCIAL" in o["risco"],
       "'EM FRENTE AO' não vira POSICAO FRENTE", str(o["componentes"]))
    o = CO.organizar("APTO 302 BL A")
    ok(o["tier"] == "CONFIRMA" and not o["risco"], "complemento direto segue CONFIRMA")


# 6h -----------------------------------------------------------------------
def t_support_independente():
    print("6h. Support: jitter, zona UTM e teto")
    SCOPE = "TEST"
    C = "RUA {} XAVIER"
    lex = LS.lexico_vazio_v33()
    LS.atualizar(lex, [(C, "CONS", "CONSELHEIRO", "AUTORIDADE_NIVEL", A(100)),
                       (C, "CONS", "CONSELHEIRO", "AUTORIDADE_NIVEL",
                        A(100, lat=-30.00023))], min_support=2, scope_id=SCOPE)
    e = lex["scopes"][SCOPE]["equiv"][C]["CONS"]
    ok(e["support"] == 1 and e["status"] == "candidato",
       "mesmo imóvel com jitter de 26 m vale 1 voto", f"support={e['support']}")
    LS.atualizar(lex, [(C, "CONS", "CONSELHEIRO", "AUTORIDADE_NIVEL",
                        A(244, lat=-30.0030))], min_support=2, scope_id=SCOPE)
    e = lex["scopes"][SCOPE]["equiv"][C]["CONS"]
    ok(e["support"] == 2 and e["status"] == "ativo", "outro número/cluster vale voto novo")

    a22 = {"num": 100, "lat": -30.0, "lon": -51.0}
    a23 = {"num": 100, "lat": -30.0, "lon": -45.0}
    d = LS.haversine_m(a22["lat"], a22["lon"], a23["lat"], a23["lon"])
    ok(LS.independente(a23, [a22]) and d > 500_000,
       "zonas UTM distintas não colapsam (âncora em WGS84)", f"dist={d/1000:.0f} km")

    lex4 = LS.lexico_vazio_v33()
    LS.atualizar(lex4, [(C, "V", "CANON", "FREQUENCIA_CONTEXTO",
                         {"num": i, "lat": -30.0 + i * 0.01, "lon": -51.0})
                        for i in range(1, 102)], min_support=2, scope_id=SCOPE)
    e = lex4["scopes"][SCOPE]["equiv"][C]["V"]
    ok(e["support"] == LS.MAX_SUPPORT and len(e["ancoras"]) <= LS.MAX_ANCORAS,
       f"support satura em {LS.MAX_SUPPORT} âncoras", f"support={e['support']}")
    LS.atualizar(lex4, [(C, "V", "CANON", "FREQUENCIA_CONTEXTO",
                         {"num": 101, "lat": -30.0 + 101 * 0.01, "lon": -51.0})] * 10,
                 min_support=2, scope_id=SCOPE)
    e = lex4["scopes"][SCOPE]["equiv"][C]["V"]
    ok(e["support"] == LS.MAX_SUPPORT and e.get("status_support") == "saturado",
       "reapresentar a 101ª evidência não infla o support", f"support={e['support']}")


# 6i -----------------------------------------------------------------------
def t_lexico_contextual():
    print("6i. Equivalência aprendida é CONTEXTUAL")
    ativos = {("RUA JOAO {}", "SOUZA"): "SOUSA"}
    ok(LS.aplicar("RUA JOAO SOUZA", ativos) == "RUA JOAO SOUSA",
       "corrige no contexto onde foi provado")
    intocados = ["RUA MARIA SOUZA", "AVENIDA CARLOS SOUZA", "RUA TENENTE SOUZA"]
    ruins = [t for t in intocados if LS.aplicar(t, ativos) != t]
    ok(not ruins, "não toca o mesmo token em outro nome", str(ruins))
    ok(LS.contexto_do_par("RUA JOAO SOUZA", "RUA JOAO SOUSA", "SOUZA", "SOUSA")
       == "RUA JOAO {}", "contexto extraído do par")
    ok(LS.contexto_do_par("RUA A B", "RUA A B C", "B", "C") is None,
       "tamanho diferente não gera contexto")
    lexg = LS.lexico_vazio_v33()
    lexg["scopes"]["TEST"] = {"equiv": {f"RUA {{}} {i}": {"SOUZA": {"canonical": "SOUSA", "status": "ativo"}}
                                            for i in range(4)},
                                "indefinidos": {}, "quarentena": {}}
    ok(LS.candidatas_globais(lexg)[0][:2] == ("SOUZA", "SOUSA"),
       "repetição em N contextos vira SUGESTÃO de promoção (não aplicação)")

    # v3.3: mesma regra nominal em outro município/scope não existe.
    lex = LS.lexico_vazio_v33(); C = "RUA JOAO {}"
    LS.atualizar(lex, [(C, "SOUZA", "SOUSA", "FREQUENCIA_CONTEXTO", A(1)),
                       (C, "SOUZA", "SOUSA", "FREQUENCIA_CONTEXTO", A(2, lat=-30.02))],
                 min_support=2, scope_id="4314902")
    ok(LS.mapa_ativo(lex, "4314902").get((C, "SOUZA")) == "SOUSA"
       and not LS.mapa_ativo(lex, "2211001"),
       "scope_id impede vazamento da regra entre municípios")


# 6j -----------------------------------------------------------------------
def t_srid_zona():
    print("6j. SRID informado × zona dos dados")
    tmp = tempfile.mkdtemp(prefix="ajzona_")
    pd.DataFrame({"id": ["1", "2"], "logr": ["RUA A", "RUA B"], "num": ["10", "12"],
                  "lat": ["-30.03", "-30.04"], "lon": ["-51.23", "-51.24"]}).to_csv(
        os.path.join(tmp, "f.csv"), index=False)
    cfg = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": True,
           "fontes": [{"source_id": "f", "arquivo": "f.csv", "autoridade": True,
                       "colunas": {"record_id": "id", "logradouro": "logr", "numero": "num",
                                   "lat": "lat", "lon": "lon"}}]}
    ok(V.validar({**cfg, "srid_metrico": 31982})["ok"], "zona 22S correta passa")
    r = V.validar({**cfg, "srid_metrico": 31983})
    ok(not r["ok"] and any("zona" in e for e in r["erros"]),
       "zona 23S com dados na 22S vira ERRO", str(r["erros"])[:90])
    r = V.validar({**cfg, "srid_metrico": 3857})
    ok(any("3857" in a for a in r["alertas"]), "Web Mercator alerta (use abortar_em_alerta)")
    shutil.rmtree(tmp, ignore_errors=True)


# 7 ------------------------------------------------------------------------
def t_validacao():
    print("7. Validação de schema")
    tmp = tempfile.mkdtemp(prefix="ajval_")
    pd.DataFrame({"id": ["1"], "logr": ["RUA A"], "num": ["10"],
                  "lat": ["-30.0"], "lon": ["-51.2"]}).to_csv(
        os.path.join(tmp, "f.csv"), index=False)
    base = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": True, "fontes": [
        {"source_id": "f", "arquivo": "f.csv",
         "colunas": {"record_id": "id", "logradouro": "logr", "numero": "num",
                     "lat": "lat", "lon": "lon"}}]}
    ok(V.validar(base)["ok"], "config correto passa")

    errado = {**base, "fontes": [{**base["fontes"][0],
              "colunas": {**base["fontes"][0]["colunas"], "numero": "NUM_IMOVEL"}}]}
    rel = V.validar(errado)
    ok(not rel["ok"] and any("inexistente" in e for e in rel["erros"]),
       "coluna configurada inexistente vira ERRO", str(rel["erros"]))
    try:
        V.exigir(rel)
        ok(False, "exigir() aborta em ERRO")
    except SystemExit as e:
        ok(e.code == 2, "exigir() aborta com código 2")

    semcoord = {**base, "fontes": [{**base["fontes"][0],
                "colunas": {"record_id": "id", "logradouro": "logr", "numero": "num"}}]}
    ok(not V.validar(semcoord)["ok"], "aprendizado sem lat/lon vira ERRO")

    semnum = {**base, "fontes": [{**base["fontes"][0],
              "colunas": {"record_id": "id", "logradouro": "logr",
                          "lat": "lat", "lon": "lon"}}]}
    ok(not V.validar(semnum)["ok"], "aprendizado sem coluna 'numero' vira ERRO")
    typo = {**base, "parametros": {"modo_numero": "estrtio"}}
    ok(not V.validar(typo)["ok"], "typo em enum de parâmetro vira ERRO (não vira default)")
    desconhecido = {**base, "parametros": {"raio": 30}}
    ok(not V.validar(desconhecido)["ok"], "parâmetro desconhecido vira ERRO")
    faixa = {**base, "parametros": {"raio_aprendizado_m": -5}}
    ok(not V.validar(faixa)["ok"], "parâmetro fora de faixa vira ERRO")
    shutil.rmtree(tmp, ignore_errors=True)


# 8 ------------------------------------------------------------------------
def t_lexico_concorrente():
    print("8. Léxico sob concorrência")
    import threading
    tmp = tempfile.mkdtemp(prefix="ajlex_")
    path = os.path.join(tmp, "lex.json")
    n = 24
    erros = []

    def escritor(k):
        try:
            LIO.atualizar_travado(
                path, [(f"RUA {{}} {k}", f"T{k}", f"TOKEN{k}", "FREQUENCIA_CONTEXTO",
                        A(k, lat=-30.0 + k * 0.01))],
                min_support=1, run_id=f"th{k}", scope_id="TEST")
        except Exception as e:                                  # pragma: no cover
            erros.append(repr(e))

    ths = [threading.Thread(target=escritor, args=(i,)) for i in range(n)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    lex = LS.carregar(path)
    ok(not erros, "nenhum escritor falhou", str(erros[:2]))
    gravados = len(lex.get("scopes", {}).get("TEST", {}).get("equiv", {}))
    ok(gravados == n, f"{n} escritores concorrentes, zero perda",
       f"gravados {gravados}/{n}")
    ok(not [f for f in os.listdir(tmp) if ".tmp." in f], "sem arquivo temporário órfão")
    shutil.rmtree(tmp, ignore_errors=True)


# 8b -----------------------------------------------------------------------
LEX_V31 = {
    "versao": 7, "quarentena": {}, "blacklist": [],
    "equiv": {"CONS": {"canonical": "CONSELHEIRO", "support": 2, "score": 2.0,
                       "status": "ativo", "base_decisao": "AUTORIDADE",
                       "last_seen": "2026-08-01",
                       "ancoras": [[100, 477000.0, 6678000.0], [244, 477050.0, 6678100.0]]}},
    "indefinidos": {"SOUSA~SOUZA": {"tokens": ["SOUSA", "SOUZA"], "support": 3,
                                    "ancoras": [[10, 477000.0, 6678000.0]],
                                    "status": "indefinido", "last_seen": "2026-08-01"}},
}


def t_upgrade_v31():
    print("8b. Upgrade de léxico v3.1/v3.2 -> v3.3 (migração na carga)")
    import copy
    import json as _json
    import yaml as _y

    lex = LS.migrar_schema(copy.deepcopy(LEX_V31))
    ok("CONS" in lex.get("equiv_legado_global", {}) and not lex.get("scopes"),
       "equiv global antigo vai para equiv_legado_global")
    ok("SOUSA~SOUZA" in lex.get("indefinidos_legado_global", {}),
       "indefinidos antigos vão para indefinidos_legado_global")
    ok(not LS.mapa_ativo(lex, "TEST"), "legado sem scope NÃO é aplicado")
    antes = _json.dumps(lex, sort_keys=True)
    ok(_json.dumps(LS.migrar_schema(LS.migrar_schema(lex)), sort_keys=True) == antes,
       "migrar_schema é idempotente (v3.3 -> v3.3 inalterado)")
    ok(LS.migrar_schema({})["schema"] == "3.3", "léxico vazio migra sem erro")

    tmp = tempfile.mkdtemp(prefix="ajupg_")
    for f in ("cadastro.csv", "cnefe.csv"):
        shutil.copy(os.path.join(AQUI, "exemplos", f), tmp)
    cfg = {"projeto": "UPGRADE", "srid_metrico": 31982, "hardening": True,
           "lexico_path": "lexico.json",
           "fontes": [{"source_id": "cadastro", "arquivo": "cadastro.csv",
                       "colunas": {"record_id": "matricula", "logradouro": "endereco",
                                   "numero": "nro", "complemento": "compl",
                                   "lat": "latitude", "lon": "longitude"}},
                      {"source_id": "cnefe", "arquivo": "cnefe.csv", "autoridade": True,
                       "colunas": {"record_id": "cod", "logradouro": "logradouro",
                                   "numero": "numero", "complemento": "complemento",
                                   "lat": "lat", "lon": "lon"}}]}
    for nome, aprende in (("off", False), ("on", True)):
        _json.dump(LEX_V31, open(os.path.join(tmp, "lexico.json"), "w"))   # v3.1 puro
        cpath = os.path.join(tmp, f"cfg_{nome}.yaml")
        _y.safe_dump({**cfg, "scope_id": "TEST", "aprendizado": aprende}, open(cpath, "w"))
        r = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"),
                            cpath, "--out", os.path.join(tmp, f"out_{nome}")],
                           cwd=AQUI, capture_output=True, text=True)
        ok(r.returncode == 0, f"léxico v3.1 + aprendizado={aprende} não quebra",
           r.stderr.strip().splitlines()[-1] if r.returncode else "")
        if r.returncode == 0 and not aprende:
            d = pd.read_csv(os.path.join(latest_dir(os.path.join(tmp, "out_off")), "ajuste_logradouro.csv"),
                            dtype=str, keep_default_na=False)
            c1 = d[d.aj_record_id == "C001"].iloc[0]["aj_logr_marcado"]
            ok(c1 == "RUA CONS XAVIER", "sem aprendizado, legado global não corrige", c1)
        if r.returncode == 0 and aprende:
            lexf = _json.load(open(os.path.join(tmp, "lexico.json")))
            ok("SOUSA~SOUZA" in lexf.get("indefinidos_legado_global", {}),
               "exportação com indefinidos antigos não quebra (KeyError: contexto)")
            ok(lexf["runs"][-1]["ativos"] == LS.contar_ativos(lexf) >= 1,
               "runs[].ativos conta todos os scopes",
               f"runs={lexf['runs'][-1]['ativos']} real={LS.contar_ativos(lexf)}")
    shutil.rmtree(tmp, ignore_errors=True)


# 8c -----------------------------------------------------------------------
def t_hardening_operacional_v322():
    print("8c. Hardening operacional v3.2.2")
    import yaml as _y
    import ajustar_logradouro as AJ

    # booleano string nunca liga feature silenciosamente
    cfgb = {"_base_dir": ".", "scope_id": "TEST", "aprendizado": "false", "hardening": "false", "fontes": []}
    rb = V.validar(cfgb)
    ok(not rb["ok"] and sum("deve ser booleano" in e for e in rb["erros"]) >= 2,
       "booleanos top-level são estritos")

    # source_id vira filename: só identificador seguro é aceito
    tmp = tempfile.mkdtemp(prefix="ajops_")
    pd.DataFrame({"id": ["1"], "logr": ["RUA A"]}).to_csv(os.path.join(tmp, "f.csv"), index=False)
    badsid = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": False,
              "fontes": [{"source_id": "cadastro/sub", "arquivo": "f.csv",
                          "colunas": {"record_id": "id", "logradouro": "logr"}}]}
    rs = V.validar(badsid)
    ok(not rs["ok"] and any("source_id inválido" in e for e in rs["erros"]),
       "source_id inseguro aborta antes de gravar")

    # Multi-zona em aprendizado: não existe uma projeção UTM única silenciosa.
    pd.DataFrame({"id": ["1", "2"], "logr": ["RUA A", "RUA A"], "num": ["10", "10"],
                  "lat": ["-30", "-30"], "lon": ["-51", "-45"]}).to_csv(
        os.path.join(tmp, "multi.csv"), index=False)
    cm = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": True,
          "fontes": [{"source_id": "m", "arquivo": "multi.csv", "autoridade": True,
                      "colunas": {"record_id": "id", "logradouro": "logr", "numero": "num",
                                  "lat": "lat", "lon": "lon"}}]}
    rm = V.validar(cm)
    ok(not rm["ok"] and any("múltiplas partições UTM" in e for e in rm["erros"]),
       "aprendizado multi-zona aborta", str(rm["erros"]))

    # Mesmo diretório da entrada como --out: publicação em runs/ não toca a origem.
    src = os.path.join(tmp, "ajuste_logradouro.csv")
    pd.DataFrame({"id": ["1", "2"], "logr": ["RUA A", "RUA B"]}).to_csv(src, index=False)
    h0 = sha256(src)
    c1 = {"projeto": "OPS", "scope_id": "TEST", "aprendizado": False,
          "fontes": [{"source_id": "cadastro", "arquivo": "ajuste_logradouro.csv",
                      "colunas": {"record_id": "id", "logradouro": "logr"}}]}
    cp = os.path.join(tmp, "cfg.yaml")
    _y.safe_dump(c1, open(cp, "w", encoding="utf-8"))
    r = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"), cp,
                        "--out", tmp], cwd=AQUI, capture_output=True, text=True)
    ok(r.returncode == 0 and sha256(src) == h0,
       "entrada com nome de artefato NÃO é sobrescrita", r.stderr[-300:])
    if r.returncode == 0:
        rd1 = latest_dir(tmp)
        ok(os.path.basename(os.path.dirname(rd1)) == "runs" and os.path.isdir(rd1),
           "run publicado em diretório imutável versionado")
        # CSVs vazios continuam legíveis e com schema fixo.
        eq = pd.read_csv(os.path.join(rd1, "lexico_equivalencias.csv"))
        pa = pd.read_csv(os.path.join(rd1, "pares_mineracao.csv"))
        ok(eq.columns.tolist() == AJ.EQ_COLS and pa.columns.tolist() == AJ.AUD_COLS,
           "CSVs vazios preservam cabeçalho/schema")
        out = pd.read_csv(os.path.join(rd1, "ajuste_logradouro.csv"), dtype=str,
                          keep_default_na=False)
        faltam = [f"aj_compl_{c}" for c in AJ.COMPONENTES if f"aj_compl_{c}" not in out.columns]
        ok(not faltam, "todas as colunas tipadas de complemento existem sempre", str(faltam))

    # Reutilizar o mesmo root não mistura artefatos: latest aponta só para o novo run.
    pd.DataFrame({"id": ["9"], "logr": ["RUA C"]}).to_csv(os.path.join(tmp, "g.csv"), index=False)
    c2 = {"projeto": "OPS2", "scope_id": "TEST", "aprendizado": False,
          "fontes": [{"source_id": "nova", "arquivo": "g.csv",
                      "colunas": {"record_id": "id", "logradouro": "logr"}}]}
    _y.safe_dump(c2, open(cp, "w", encoding="utf-8"))
    r2 = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"), cp,
                         "--out", tmp], cwd=AQUI, capture_output=True, text=True)
    if r2.returncode == 0:
        rd2 = latest_dir(tmp)
        ok(rd2 != rd1 and os.path.exists(os.path.join(rd2, "marcacao_nova.csv"))
           and not os.path.exists(os.path.join(rd2, "marcacao_cadastro.csv")),
           "reuso do root não carrega marcação stale de run anterior")
    else:
        ok(False, "2ª execução no mesmo root", r2.stderr[-300:])

    # run_id é único mesmo no mesmo processo/microinstante.
    ids = {AJ._novo_run_id() for _ in range(100)}
    ok(len(ids) == 100, "run_id único no mesmo processo")

    # Falha DEPOIS do commit do léxico: nada vira latest. v3.2.5 não reescreve
    # o léxico com status operacional; o evento do commit permanece imutável.
    tf = tempfile.mkdtemp(prefix="ajfail_")
    pd.DataFrame({"id": ["1", "2"], "logr": ["R CONS XAVIER", "RUA CONSELHEIRO XAVIER"],
                  "num": ["100", "100"], "lat": ["-30.03", "-30.03001"],
                  "lon": ["-51.23", "-51.23001"]}).to_csv(os.path.join(tf, "f.csv"), index=False)
    cf = {"_base_dir": tf, "scope_id": "TEST", "aprendizado": True, "srid_metrico": 31982,
          "lexico_path": "lex.json",
          "fontes": [{"source_id": "f", "arquivo": "f.csv", "autoridade": True,
                      "colunas": {"record_id": "id", "logradouro": "logr", "numero": "num",
                                  "lat": "lat", "lon": "lon"}}]}
    orig_export = AJ._exportar_artefatos
    def boom(*a, **k):
        raise RuntimeError("falha sintética pós-commit")
    AJ._exportar_artefatos = boom
    try:
        try:
            AJ.run(cf, os.path.join(tf, "out"))
            ok(False, "falha pós-commit propagada")
        except RuntimeError:
            lexj = json.load(open(os.path.join(tf, "lex.json"), encoding="utf-8"))
            evt = lexj.get("runs", [])[-1] if lexj.get("runs") else {}
            ok(evt.get("evento") in ("LEXICO_STATE_CHANGED", "LEXICO_NOOP")
               and "run_status" not in evt,
               "léxico não é reescrito por status operacional do run", str(evt))
            ok(not os.path.exists(os.path.join(tf, "out", "latest.json")),
               "run falho nunca vira latest")
            ok(bool(os.listdir(os.path.join(tf, "out", "failed"))),
               "run falho fica disponível para perícia")
    finally:
        AJ._exportar_artefatos = orig_export
        shutil.rmtree(tf, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)


# 8c2 ----------------------------------------------------------------------
def t_hardening_operacional_v323():
    print("8c2. Hardening operacional v3.2.3")
    import yaml as _y
    import ajustar_logradouro as AJ
    import zipfile
    import socket
    import time as _time

    # Chave desconhecida de topo não pode cair em default perigoso.
    r = V.validar({"_base_dir": ".", "scope_id": "TEST", "aprendizado": False, "aprendizadoo": False, "fontes": []})
    ok(not r["ok"] and any("chave de topo desconhecida" in e for e in r["erros"]),
       "typo de chave top-level é ERRO, não alerta")

    # Aprendizado com side-effect é sempre explícito.
    r = V.validar({"_base_dir": ".", "fontes": []})
    ok(not r["ok"] and any("'aprendizado' é obrigatória" in e for e in r["erros"]),
       "aprendizado sem valor explícito é ERRO")

    # Estrutura YAML inválida deve retornar erro de validação, nunca AttributeError.
    try:
        r = V.validar({"_base_dir": ".", "scope_id": "TEST", "aprendizado": False, "fontes": {"x": 1}})
        cond = (not r["ok"] and any("'fontes' deve ser lista" in e for e in r["erros"]))
        ok(cond, "fontes malformadas falham limpo, sem AttributeError", str(r["erros"]))
    except Exception as e:
        ok(False, "fontes malformadas falham limpo, sem AttributeError", repr(e))

    tmp = tempfile.mkdtemp(prefix="aj323_")
    try:
        # Cabeçalho duplicado: pandas renomearia x -> x.1; agora é fail-fast.
        dup = os.path.join(tmp, "dup.csv")
        open(dup, "w", encoding="utf-8").write("id,logradouro,logradouro\n1,RUA A,RUA B\n")
        cd = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": False,
              "fontes": [{"source_id": "d", "arquivo": "dup.csv",
                           "colunas": {"record_id": "id", "logradouro": "logradouro"}}]}
        rd = V.validar(cd)
        ok(not rd["ok"] and any("duplicada" in e for e in rd["erros"]),
           "cabeçalho CSV duplicado é ERRO")

        # Zero coordenadas válidas em aprendizado para na validação.
        pd.DataFrame({"id": ["1", "2"], "logradouro": ["RUA A", "RUA A"],
                      "num": ["10", "10"], "lat": ["999", ""], "lon": ["999", "0"]}).to_csv(
            os.path.join(tmp, "badgeo.csv"), index=False)
        cg = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": True,
              "fontes": [{"source_id": "g", "arquivo": "badgeo.csv", "autoridade": True,
                           "colunas": {"record_id": "id", "logradouro": "logradouro",
                                       "numero": "num", "lat": "lat", "lon": "lon"}}]}
        rg = V.validar(cg)
        ok(not rg["ok"] and any("nenhuma coordenada válida" in e for e in rg["erros"]),
           "aprendizado com 0 coordenadas válidas aborta na validação")

        # latest.json é parte do namespace controlado, inclusive para o léxico.
        pd.DataFrame({"id": ["1"], "logradouro": ["RUA A"]}).to_csv(
            os.path.join(tmp, "f.csv"), index=False)
        cl = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": False, "lexico_path": "out/latest.json",
              "fontes": [{"source_id": "f", "arquivo": "f.csv",
                           "colunas": {"record_id": "id", "logradouro": "logradouro"}}]}
        dl = V.validar_destino(cl, os.path.join(tmp, "out"))
        ok(any("latest.json" in e and "CONTROLADO" in e for e in dl),
           "léxico não pode colidir com out/latest.json", str(dl))

        # Run normal: status já nasce COMPLETED no diretório publicado e manifest fecha hashes.
        cfg = {"projeto": "V323", "scope_id": "TEST", "aprendizado": False,
               "fontes": [{"source_id": "f", "arquivo": "f.csv",
                            "colunas": {"record_id": "id", "logradouro": "logradouro"}}]}
        cp = os.path.join(tmp, "cfg.yaml")
        _y.safe_dump(cfg, open(cp, "w", encoding="utf-8"))
        out = os.path.join(tmp, "out_ok")
        rr = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"), cp,
                             "--out", out], cwd=AQUI, capture_output=True, text=True)
        ok(rr.returncode == 0, "execução operacional normal", rr.stderr[-300:])
        if rr.returncode == 0:
            rd0 = latest_dir(out)
            em = json.load(open(os.path.join(rd0, "execucao.json"), encoding="utf-8"))
            mf = json.load(open(os.path.join(rd0, "manifest.json"), encoding="utf-8"))
            ok(em.get("status") == "RUN_COMPLETED" and em.get("skill_version") == AJ.VERSION,
               "run publicado já está RUN_COMPLETED e versionado")
            got = mf["artifacts"]["ajuste_logradouro.csv"]["sha256"]
            ok(got == sha256(os.path.join(rd0, "ajuste_logradouro.csv")),
               "manifest contém hash verificável dos artefatos")
            ok("runtime" in mf and "pyproj" in mf["runtime"] and mf.get("config_sha256"),
               "manifest registra ambiente + config")

        # Fórmula externa deve permanecer texto no XLSX.
        pd.DataFrame({"id": ["1"], "logradouro": ["RUA A"], "obs": ["=1+1"]}).to_csv(
            os.path.join(tmp, "formula.csv"), index=False)
        cf = {"projeto": "XLSXSAFE", "scope_id": "TEST", "aprendizado": False,
              "fontes": [{"source_id": "f", "arquivo": "formula.csv",
                           "colunas": {"record_id": "id", "logradouro": "logradouro"}}]}
        cfp = os.path.join(tmp, "formula.yaml"); _y.safe_dump(cf, open(cfp, "w", encoding="utf-8"))
        of = os.path.join(tmp, "out_formula")
        rf = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"), cfp,
                             "--out", of], cwd=AQUI, capture_output=True, text=True)
        if rf.returncode == 0 and os.path.exists(os.path.join(latest_dir(of), "ajuste_logradouro.xlsx")):
            xp = os.path.join(latest_dir(of), "ajuste_logradouro.xlsx")
            with zipfile.ZipFile(xp) as z:
                xml = "\n".join(z.read(n).decode("utf-8", errors="ignore")
                                  for n in z.namelist() if n.startswith("xl/worksheets/sheet"))
            ok("<f>1+1</f>" not in xml, "XLSX não executa string iniciada por '=' como fórmula")
        else:
            ok(rf.returncode == 0, "run de proteção XLSX", rf.stderr[-300:])

        # Recovery: staging de PID morto e run incompleto velho vão para failed.
        recroot = os.path.join(tmp, "recover")
        os.makedirs(os.path.join(recroot, ".staging"), exist_ok=True)
        os.makedirs(os.path.join(recroot, "runs"), exist_ok=True)
        os.makedirs(os.path.join(recroot, "failed"), exist_ok=True)
        sd = os.path.join(recroot, ".staging", "dead.tmp"); os.makedirs(sd)
        json.dump({"run_id": "dead", "pid": 99999999, "host": socket.gethostname()},
                  open(os.path.join(sd, "owner.json"), "w"))
        bd = os.path.join(recroot, "runs", "broken"); os.makedirs(bd)
        json.dump({"run_id": "broken", "status": "ARTIFACTS_STAGED"},
                  open(os.path.join(bd, "execucao.json"), "w"))
        antigo = _time.time() - 7200; os.utime(bd, (antigo, antigo))
        rec = AJ._reconciliar_saida(recroot, 60)
        ok(rec["staging_recuperados"] == 1 and not os.path.exists(sd),
           "startup recovery recolhe staging cujo PID morreu", str(rec))
        ok(rec["runs_quarentenados"] == 1 and not os.path.exists(bd),
           "startup recovery quarentena run publicado incompleto", str(rec))

        # TOCTOU: mudar a entrada durante gravação impede publicação/latest.
        tt = os.path.join(tmp, "toctou"); os.makedirs(tt)
        fp = os.path.join(tt, "f.csv")
        pd.DataFrame({"id": ["1"], "logradouro": ["RUA A"]}).to_csv(fp, index=False)
        ct = {"_base_dir": tt, "scope_id": "TEST", "aprendizado": False,
              "fontes": [{"source_id": "f", "arquivo": "f.csv",
                           "colunas": {"record_id": "id", "logradouro": "logradouro"}}]}
        orig_export = AJ._exportar_artefatos
        def mutate(*a, **k):
            z = orig_export(*a, **k)
            with open(fp, "a", encoding="utf-8") as fh:
                fh.write("\n")
            return z
        AJ._exportar_artefatos = mutate
        try:
            try:
                AJ.run(ct, os.path.join(tt, "out"))
                ok(False, "TOCTOU de entrada aborta antes de publicar")
            except RuntimeError as e:
                ok("INPUT_CHANGED" in str(e) and not os.path.exists(os.path.join(tt, "out", "latest.json")),
                   "TOCTOU de entrada aborta antes de publicar", str(e))
        finally:
            AJ._exportar_artefatos = orig_export
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# 8c3 ----------------------------------------------------------------------
def t_hardening_operacional_v324():
    print("8c3. Hardening operacional v3.2.4")
    import threading
    import yaml as _y
    import ajustar_logradouro as AJ

    tmp = tempfile.mkdtemp(prefix="aj324_")
    try:
        # Aprendizado altera estado persistente: sem pyproj a validação deve parar.
        pd.DataFrame({"id": ["1", "2"], "logradouro": ["RUA A", "RUA A"],
                      "num": ["10", "10"], "lat": ["-30", "-30.0002"],
                      "lon": ["-51", "-51.0002"]}).to_csv(
            os.path.join(tmp, "geo.csv"), index=False)
        cg = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": True,
              "fontes": [{"source_id": "g", "arquivo": "geo.csv", "autoridade": True,
                           "colunas": {"record_id": "id", "logradouro": "logradouro",
                                       "numero": "num", "lat": "lat", "lon": "lon"}}]}
        old_pyproj = G._PYPROJ
        try:
            G._PYPROJ = False
            rg = V.validar(cg)
            ok(not rg["ok"] and any("exige pyproj" in e for e in rg["erros"]),
               "aprendizado sem pyproj é ERRO reproduzível", str(rg["erros"]))
        finally:
            G._PYPROJ = old_pyproj

        # Dois runs válidos; corrompe o mais novo. Recovery deve quarentená-lo e
        # recuar latest para o último run cujo manifest ainda confere.
        pd.DataFrame({"id": ["1"], "logradouro": ["RUA A"]}).to_csv(
            os.path.join(tmp, "f.csv"), index=False)
        cfg = {"projeto": "RECOVERY324", "scope_id": "TEST", "aprendizado": False,
               "fontes": [{"source_id": "f", "arquivo": "f.csv",
                            "colunas": {"record_id": "id", "logradouro": "logradouro"}}]}
        cp = os.path.join(tmp, "cfg.yaml")
        _y.safe_dump(cfg, open(cp, "w", encoding="utf-8"))
        out = os.path.join(tmp, "out")
        runs = []
        for _ in range(2):
            rr = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"), cp,
                                 "--out", out], cwd=AQUI, capture_output=True, text=True)
            ok(rr.returncode == 0, "run base para recovery íntegro", rr.stderr[-300:])
            if rr.returncode != 0:
                break
            runs.append(json.load(open(os.path.join(out, "latest.json"), encoding="utf-8"))["run_id"])
        if len(runs) == 2:
            newest = os.path.join(out, "runs", runs[-1])
            with open(os.path.join(newest, "ajuste_logradouro.csv"), "a", encoding="utf-8") as fh:
                fh.write("CORRUPCAO\n")
            rec = AJ._reconciliar_saida(out, 0)
            lp = json.load(open(os.path.join(out, "latest.json"), encoding="utf-8"))
            ok(rec.get("runs_corrompidos", 0) >= 1,
               "recovery detecta SHA/size divergente no run concluído", str(rec))
            ok(lp["run_id"] == runs[0] and AJ._validar_integridade_run(
                   os.path.join(out, lp["path"]))["ok"],
               "latest recua somente para run criptograficamente íntegro", str(lp))
            ok(os.path.isdir(os.path.join(out, "failed", "corrupted", runs[-1])),
               "run corrompido é quarentenado em failed/corrupted")

        # A linhagem do léxico precisa formar uma cadeia SHA/version exata mesmo
        # com escritores concorrentes; isso só é possível se before/after nascer
        # dentro do lock do read-modify-write.
        lexp = os.path.join(tmp, "lex_concorrente.json")
        infos, erros = [], []
        mu = threading.Lock()
        def writer(k):
            try:
                _lex, _novos, info = LIO.atualizar_travado(
                    lexp, [(f"RUA {{}} C{k}", f"V{k}", f"CAN{k}", "FREQUENCIA_CONTEXTO",
                            A(k, lat=-30.0 + k * 0.01, lon=-51.0))],
                    min_support=1, run_id=f"c{k}")
                with mu:
                    infos.append(info)
            except Exception as e:
                with mu:
                    erros.append(repr(e))
        th = [threading.Thread(target=writer, args=(k,)) for k in range(12)]
        [t.start() for t in th]; [t.join() for t in th]
        cadeia = sorted(infos, key=lambda x: x["versao_after"])
        versoes = [x["versao_after"] for x in cadeia]
        encadeia = (not erros and len(cadeia) == 12
                    and versoes == list(range(versoes[0], versoes[0] + 12))
                    and cadeia[0]["sha256_before"] is None
                    and all(cadeia[i]["sha256_before"] == cadeia[i-1]["sha256_after"]
                            for i in range(1, len(cadeia))))
        ok(encadeia, "hash/version before→after do léxico forma cadeia transacional sob concorrência",
           str(erros[:2] or [(x["versao_before"], x["versao_after"]) for x in cadeia]))

        # Manifest v3 separa arquivo físico de estado semântico.
        if os.path.exists(os.path.join(out, "latest.json")):
            rd = latest_dir(out)
            mf = json.load(open(os.path.join(rd, "manifest.json"), encoding="utf-8"))
            lx = mf.get("lexico", {})
            ok(mf.get("schema") == "a2l-run-manifest/4"
               and "revision_before" in lx and "revision_after" in lx
               and "state_version_before" in lx and "state_version_after" in lx
               and "state_sha256_loaded" in lx,
               "manifest v3 separa revisão física de estado semântico", str(lx))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# 8c4 ----------------------------------------------------------------------
def t_hardening_operacional_v325():
    print("8c4. Filesystem & Audit Closure v3.2.5")
    import yaml as _y
    import ajustar_logradouro as AJ

    tmp = tempfile.mkdtemp(prefix="aj325_")
    try:
        fp = os.path.join(tmp, "f.csv")
        pd.DataFrame({"id": ["1"], "logradouro": ["RUA A"]}).to_csv(fp, index=False)
        base = {"_base_dir": tmp, "scope_id": "TEST", "aprendizado": False,
                "fontes": [{"source_id": "f", "arquivo": "f.csv",
                             "colunas": {"record_id": "id", "logradouro": "logradouro"}}]}

        # Schema estrito também dentro de fontes/colunas.
        c = json.loads(json.dumps(base)); c["fontes"][0]["autoridadee"] = True
        r = V.validar(c)
        ok(not r["ok"] and any("chave desconhecida" in e and "autoridadee" in e for e in r["erros"]),
           "typo dentro de fontes[] é ERRO", str(r["erros"]))
        c = json.loads(json.dumps(base)); c["fontes"][0]["colunas"]["logradouroo"] = "logradouro"
        r = V.validar(c)
        ok(not r["ok"] and any("colunas chave desconhecida" in e for e in r["erros"]),
           "typo dentro de colunas é ERRO", str(r["erros"]))

        # source_id que colide em Windows/case-insensitive é proibido em qualquer SO.
        c = json.loads(json.dumps(base)); c["fontes"].append(
            {"source_id": "F", "arquivo": "f.csv",
             "colunas": {"record_id": "id", "logradouro": "logradouro"}})
        r = V.validar(c)
        ok(not r["ok"] and any("casefold" in e for e in r["erros"]),
           "source_id é único por casefold", str(r["erros"]))

        # vocab_path explícito precisa existir e ser JSON de schema mínimo válido.
        c = json.loads(json.dumps(base)); c["vocab_path"] = "nao_existe.json"
        r = V.validar(c)
        ok(not r["ok"] and any("vocab_path informado mas arquivo inexistente" in e for e in r["erros"]),
           "vocab_path explícito inexistente é ERRO", str(r["erros"]))

        # Estado mutável não pode compartilhar arquivo com vocabulário/config/input.
        shared = os.path.join(tmp, "shared.json")
        json.dump({"tipo_via": {}}, open(shared, "w", encoding="utf-8"))
        before = sha256(shared)
        c = json.loads(json.dumps(base)); c.update({"lexico_path": "shared.json", "vocab_path": "shared.json"})
        errs = V.validar_destino(c, os.path.join(tmp, "out_alias"))
        ok(any("COLISÃO DE PAPÉIS" in e for e in errs),
           "léxico e vocabulário no mesmo realpath são bloqueados", str(errs))
        try:
            AJ.run(c, os.path.join(tmp, "out_alias"))
            ok(False, "colisão de papéis aborta antes de escrever")
        except SystemExit:
            ok(sha256(shared) == before, "colisão aborta antes de modificar o arquivo compartilhado")

        # Diretório controlado symlink não pode desviar publicação/retenção.
        if hasattr(os, "symlink"):
            out = os.path.join(tmp, "out_symlink"); ext = os.path.join(tmp, "externo")
            os.makedirs(out); os.makedirs(ext)
            sentinel = os.path.join(ext, "keep.txt"); open(sentinel, "w").write("KEEP")
            try:
                os.symlink(ext, os.path.join(out, "runs"), target_is_directory=True)
                try:
                    AJ.run(base, out)
                    ok(False, "runs/ symlink é recusado")
                except (SystemExit, RuntimeError) as e:
                    ok(os.path.exists(sentinel), "symlink controlado não escreve/apaga fora de --out", str(e))
            except (OSError, NotImplementedError):
                ok(True, "teste de symlink indisponível no SO")

        # revision cresce em cada gravação; state_version só quando conhecimento muda.
        lexp = os.path.join(tmp, "lex_state.json")
        _l1, _n1, i1 = LIO.atualizar_travado(
            lexp, [("RUA {} A", "ABR", "ABREVIACAO", "TESTE", A(1))],
            min_support=1, run_id="s1")
        _l2, _n2, i2 = LIO.atualizar_travado(lexp, [], min_support=1, run_id="s2")
        ok(i2["revision_after"] == i2["revision_before"] + 1,
           "revision cresce mesmo em commit sem mudança")
        ok(i2["state_version_after"] == i2["state_version_before"]
           and i2["state_sha256_after"] == i2["state_sha256_before"],
           "state_version/state_sha ficam estáveis sem mudança semântica", str(i2))
        ok(i2["state_sha256_before"] == i1["state_sha256_after"],
           "state_sha256 encadeia o conhecimento entre commits", str((i1, i2)))

        # Status operacional não toca mais os bytes do léxico.
        h0 = sha256(lexp)
        ret = LIO.marcar_run_status(lexp, "s1", "RUN_COMPLETED")
        ok(ret is False and sha256(lexp) == h0,
           "RUN_COMPLETED/RUN_FAILED não reescrevem o estado semântico")

        # Pipeline normal publica manifest v3 com as duas linhagens.
        cp = os.path.join(tmp, "cfg325.yaml")
        _y.safe_dump({"projeto": "V325", "scope_id": "TEST", "aprendizado": False,
                      "fontes": base["fontes"]}, open(cp, "w", encoding="utf-8"))
        rr = subprocess.run([sys.executable, os.path.join(AQUI, "ajustar_logradouro.py"), cp,
                             "--out", os.path.join(tmp, "out325")],
                            cwd=AQUI, capture_output=True, text=True)
        ok(rr.returncode == 0, "pipeline v3.2.5 operacional", rr.stderr[-300:])
        if rr.returncode == 0:
            mf = json.load(open(os.path.join(latest_dir(os.path.join(tmp, "out325")), "manifest.json"), encoding="utf-8"))
            lx = mf.get("lexico", {})
            ok(mf.get("schema") == "a2l-run-manifest/4"
               and "file_sha256_loaded" in lx and "state_sha256_loaded" in lx,
               "manifest v3 carrega linhagem física + semântica", str(lx))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# 8d -----------------------------------------------------------------------
def t_grafo_lexico_v322():
    print("8d. Grafo do léxico: cadeia/ciclo completo")
    SCOPE = "TEST"
    hoje = "2026-08-20"
    lex = LS.lexico_vazio_v33()
    sd = lex["scopes"].setdefault(SCOPE, {"equiv": {}, "indefinidos": {}, "quarentena": {}})
    sd["equiv"]["RUA {} X"] = {
        "A": {"canonical": "B", "status": "ativo", "support": 2, "score": 2,
              "base_decisao": "TESTE", "last_reinforced": hoje, "last_observed": hoje, "ancoras": []},
        "B": {"canonical": "C", "status": "ativo", "support": 2, "score": 2,
              "base_decisao": "TESTE", "last_reinforced": hoje, "last_observed": hoje, "ancoras": []},
    }
    LS._sanear_grafos(lex, hoje, [SCOPE])
    sts = {v: e["status"] for v, e in sd["equiv"]["RUA {} X"].items()}
    ok(set(sts.values()) == {"quarentena"}, "cadeia inteira vai para quarentena", str(sts))
    ok(not LS.diagnosticar_grafo(lex, SCOPE) and not LS.mapa_ativo(lex, SCOPE),
       "status ativo e aplicabilidade não divergem após saneamento")

    lex2 = LS.lexico_vazio_v33()
    sd2 = lex2["scopes"].setdefault(SCOPE, {"equiv": {}, "indefinidos": {}, "quarentena": {}})
    sd2["equiv"]["RUA {} X"] = {
        "A": {"canonical": "B", "status": "ativo"},
        "B": {"canonical": "C", "status": "ativo"},
        "C": {"canonical": "A", "status": "ativo"},
    }
    LS._sanear_grafos(lex2, hoje, [SCOPE])
    ok(not LS.mapa_ativo(lex2, SCOPE) and all(e.get("status") == "quarentena" for e in sd2["equiv"]["RUA {} X"].values()),
       "ciclo de 3 nós é detectado e quarentenado")



# 8e -----------------------------------------------------------------------
def t_lexico_nacional_v33():
    print("8e. v3.3 — Léxico Nacional / invariantes semânticos")
    C = "RUA JOAO {}"

    # Escopo territorial é primeira dimensão e regra não vaza de município.
    lex = LS.lexico_vazio_v33()
    LS.atualizar(lex, [(C, "SOUZA", "SOUSA", "FREQUENCIA_CONTEXTO", A(10)),
                       (C, "SOUZA", "SOUSA", "FREQUENCIA_CONTEXTO", A(20, lat=-30.02))],
                 min_support=2, scope_id="4314902", agora="2025-01-01")
    ok(LS.mapa_ativo(lex, "4314902").get((C, "SOUZA")) == "SOUSA"
       and (C, "SOUZA") not in LS.mapa_ativo(lex, "2211001"),
       "regra nominal ativa somente no scope onde foi provada")

    # Replay observa, mas NÃO reforça: decay usa last_reinforced.
    e = lex["scopes"]["4314902"]["equiv"][C]["SOUZA"]
    reforco0 = e.get("last_reinforced")
    support0 = e.get("support")
    LS.atualizar(lex, [(C, "SOUZA", "SOUSA", "FREQUENCIA_CONTEXTO", A(10))],
                 min_support=2, scope_id="4314902", agora="2026-08-20")
    e = lex["scopes"]["4314902"]["equiv"][C]["SOUZA"]
    ok(e.get("support") == support0 and e.get("last_reinforced") == reforco0
       and e.get("last_observed") == "2026-08-20" and e.get("status") == "arquivado",
       "replay não rejuvenesce regra; decay usa último reforço independente",
       str({k:e.get(k) for k in ('support','last_observed','last_reinforced','score','status')}))

    # Arquivado é reativável por prova realmente nova.
    LS.atualizar(lex, [(C, "SOUZA", "SOUSA", "FREQUENCIA_CONTEXTO",
                        A(30, lat=-30.30))], min_support=2,
                 scope_id="4314902", agora="2026-08-20")
    e = lex["scopes"]["4314902"]["equiv"][C]["SOUZA"]
    ok(e.get("status") == "ativo" and e.get("last_reinforced") == "2026-08-20"
       and e.get("support") == support0 + 1,
       "regra arquivada volta a ativo com evidência independente")

    # Autoridade hierárquica: maior nível vence; empate vira conflito explícito.
    from collections import Counter as _Counter
    _v, can, base = LS.decidir_canone("SOUSA", "SOUZA", C, _Counter(),
                                      nivel_a=100, nivel_b=200)
    ok(can == "SOUZA" and base == "AUTORIDADE_NIVEL",
       "maior nível de autoridade decide o canônico")
    _v, can, base = LS.decidir_canone("SOUSA", "SOUZA", C,
                                      _Counter({(C,"SOUSA"):100,(C,"SOUZA"):1}),
                                      nivel_a=100, nivel_b=100)
    ok(can is None and base == LS.CONFLITO_AUTORIDADE,
       "autoridades do mesmo nível discordando nunca são resolvidas por frequência")

    # INDEFINIDO é simétrico e uma decisão posterior fecha a pendência.
    lexi = LS.lexico_vazio_v33()
    o1 = {"scope_id":"TEST", "contexto":C, "token_a":"SOUSA", "token_b":"SOUZA",
          "variante":"SOUSA", "canonico":None, "base_decisao":LS.INDEFINIDO,
          "ancora":A(1), "run_id":"r1", "evidencia":{"source_a":"A","source_b":"B"}}
    o2 = {"scope_id":"TEST", "contexto":C, "token_a":"SOUZA", "token_b":"SOUSA",
          "variante":"SOUZA", "canonico":None, "base_decisao":LS.INDEFINIDO,
          "ancora":A(2, lat=-30.02), "run_id":"r2", "evidencia":{"source_a":"B","source_b":"A"}}
    LS.atualizar(lexi, [o1, o2], min_support=2, scope_id="TEST", agora="2026-01-01")
    inds = lexi["scopes"]["TEST"]["indefinidos"]
    ok(len(inds) == 1 and next(iter(inds.values())).get("support") == 2
       and {next(iter(inds.values())).get("token_a"), next(iter(inds.values())).get("token_b")} == {"SOUSA","SOUZA"},
       "INDEFINIDO usa chave simétrica e preserva os dois tokens")
    od = {"scope_id":"TEST", "contexto":C, "token_a":"SOUSA", "token_b":"SOUZA",
          "variante":"SOUZA", "canonico":"SOUSA", "base_decisao":"AUTORIDADE_NIVEL",
          "ancora":A(3, lat=-30.04), "run_id":"r3",
          "evidencia":{"source_a":"cad","record_a":"1","source_b":"of","record_b":"9"}}
    LS.atualizar(lexi, [od], min_support=1, scope_id="TEST", agora="2026-02-01")
    ie = next(iter(inds.values()))
    ee = lexi["scopes"]["TEST"]["equiv"][C]["SOUZA"]
    ok(ie.get("status") == "resolvido" and ie.get("canonical") == "SOUSA"
       and ie.get("resolved_run_id") == "r3",
       "decisão posterior fecha INDEFINIDO com run de resolução")
    ok(ee.get("evidencias") and ee["evidencias"][0].get("source_a") == "cad",
       "equivalência retém proveniência da evidência")

    # Normalizador é função idempotente, inclusive com tipos/títulos repetidos.
    tipos = ["RUA", "AV", "AVENIDA", "TRAVESSA"]
    titulos = ["DR", "DOUTOR", "PROF", "PROFESSOR", "TEN", "TENENTE"]
    nomes = ["SILVA", "JOAO", "JOSE XAVIER"]
    ruins = []
    for t in tipos:
        for reps in (1,2,3):
            for q in titulos:
                for nome in nomes:
                    x = " ".join([t] * reps + [q, nome])
                    a = N.norm_logradouro(x, hard=True)
                    b = N.norm_logradouro(a, hard=True)
                    if a != b:
                        ruins.append((x,a,b))
    ok(not ruins, "normalização forte satisfaz N(N(x)) = N(x) na matriz adversarial", str(ruins[:2]))

    # Dado direto sempre vence referência, independentemente da ordem textual.
    c1 = CO.organizar("PROX CASA 10 CASA 2")
    c2 = CO.organizar("CASA 2 PROX CASA 10")
    ok(c1["componentes"].get("CASA") == "2" and c2["componentes"].get("CASA") == "2"
       and c1.get("componentes_origem", {}).get("CASA") == "DIRECT"
       and c2.get("componentes_origem", {}).get("CASA") == "DIRECT",
       "DIRECT > REFERENTIAL independe da posição no complemento")

    # scope_id é obrigatório para evitar namespace nacional implícito.
    tmp = tempfile.mkdtemp(prefix="aj33scope_")
    try:
        pd.DataFrame({"id":["1"],"logr":["RUA A"],"num":["10"],
                      "lat":["-30"],"lon":["-51"]}).to_csv(os.path.join(tmp,"f.csv"), index=False)
        cfg = {"_base_dir":tmp, "aprendizado":True, "fontes":[{
            "source_id":"f","arquivo":"f.csv","colunas":{"record_id":"id","logradouro":"logr",
            "numero":"num","lat":"lat","lon":"lon"}}]}
        r = V.validar(cfg)
        ok(not r["ok"] and any("scope_id" in e for e in r["erros"]),
           "scope_id ausente é ERRO de configuração")
        c2 = dict(cfg); c2["scope_id"] = "TEST"
        c2["fontes"] = [dict(cfg["fontes"][0], autoridade=True, autoridade_nivel=0)]
        r2 = V.validar(c2)
        ok(not r2["ok"] and any("conflito" in e for e in r2["erros"]),
           "autoridade legado e autoridade_nivel contraditórios viram ERRO")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Migração 3.2.x é mudança semântica auditável: state_version avança uma vez.
    tmp = tempfile.mkdtemp(prefix="aj33mig_")
    try:
        path = os.path.join(tmp, "lex.json")
        old = {"schema":"3.2", "revision":7, "versao":7, "state_version":3,
               "equiv": {C:{"SOUZA":{"canonical":"SOUSA","status":"ativo","support":2}}},
               "indefinidos":{}, "quarentena":{}, "blacklist":[], "runs":[]}
        with open(path,"w",encoding="utf-8") as fh: json.dump(old, fh)
        _lx, _nv, info = LIO.atualizar_travado(path, [], min_support=2,
                                               run_id="migracao", scope_id="TEST")
        ok(info.get("migration_from") == "3.2" and info.get("state_changed")
           and info.get("state_version_after") == 4,
           "migração 3.2→3.3 é commit semântico explícito", str(info))
        again = LIO.atualizar_travado(path, [], min_support=2, run_id="noop", scope_id="TEST")[2]
        ok(not again.get("state_changed") and again.get("state_version_after") == 4,
           "migração é idempotente; segundo commit vazio não muda conhecimento")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Estado corrompido nunca pode virar léxico vazio silenciosamente.
    tmp = tempfile.mkdtemp(prefix="aj33corrupt_")
    try:
        path = os.path.join(tmp, "lex.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"schema":"3.3", BROKEN')
        before = open(path, "rb").read()
        try:
            LIO.atualizar_travado(path, [], run_id="bad", scope_id="TEST")
            ok(False, "léxico JSON corrompido falha alto")
        except RuntimeError as e:
            ok("LEXICO_CORRUPTO" in str(e) and open(path,"rb").read() == before,
               "léxico corrompido não é sobrescrito por estado vazio")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Hardlink do estado é proibido: dois nomes teriam domínios de lock distintos.
    tmp = tempfile.mkdtemp(prefix="aj33hard_")
    try:
        p1 = os.path.join(tmp, "lex.json"); p2 = os.path.join(tmp, "alias.json")
        with open(p1,"w",encoding="utf-8") as fh: json.dump(LS.lexico_vazio_v33(), fh)
        os.link(p1, p2)
        try:
            LIO.atualizar_travado(p2, [], run_id="hard", scope_id="TEST")
            ok(False, "hardlink de léxico é recusado")
        except RuntimeError as e:
            ok("HARDLINK" in str(e), "hardlink de léxico é recusado para evitar split-brain")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Sidecar de lock não pode ser symlink.
    tmp = tempfile.mkdtemp(prefix="aj33lock_")
    try:
        path = os.path.join(tmp, "lex.json"); alvo = os.path.join(tmp, "fora.lock")
        with open(path,"w",encoding="utf-8") as fh: json.dump(LS.lexico_vazio_v33(), fh)
        open(alvo,"w").write("x")
        os.symlink(alvo, path + ".lock")
        try:
            LIO.atualizar_travado(path, [], run_id="lock", scope_id="TEST")
            ok(False, "lockfile symlink é recusado")
        except RuntimeError as e:
            ok("LOCK_SYMLINK" in str(e), "lockfile symlink não é seguido")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Run publicado não pode conter symlink em nenhuma profundidade.
    tmp = tempfile.mkdtemp(prefix="aj33link_")
    try:
        run = os.path.join(tmp, "run"); os.makedirs(run)
        alvo = os.path.join(tmp, "fora.txt"); open(alvo,"w").write("x")
        os.symlink(alvo, os.path.join(run, "artefato.csv"))
        try:
            import ajustar_logradouro as _AJ
            _AJ._hash_artifacts(run)
            ok(False, "artefato symlink é recusado")
        except RuntimeError as e:
            ok("SYMLINK" in str(e), "artefato symlink é recusado antes de manifest/recovery")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# 8f ------------------------------------------------------------------------
def t_adversarial_closure_v331():
    print("8f. v3.3.1 — adversarial closure")
    import datetime as _dt
    import lexico_seguro as _LS
    import lexico_io as _LIO
    import validacao as _V
    from normalizacao_base import norm_logradouro as _norm

    scope = "4314902"; ctx = "RUA JOAO {}"

    # replay no mesmo instante lógico não muda o estado semântico apenas por last_observed
    lex = _LS.lexico_vazio_v33()
    a = {"scope_id": scope, "contexto": ctx, "token_a": "SOUZA", "token_b": "SOUSA",
         "variante": "SOUZA", "canonico": "SOUSA", "base_decisao": "TEST",
         "ancora": {"num": 100, "lat": -30.0, "lon": -51.0}, "evidencia": {"run_id": "r1"}}
    _LS.atualizar(lex, [a], min_support=1, agora="2026-01-01", scope_id=scope, run_id="r1")
    h1 = _LIO.state_sha256(lex)
    a["evidencia"] = {"run_id": "r2"}
    _LS.atualizar(lex, [a], min_support=1, agora="2026-01-01", scope_id=scope, run_id="r2")
    h2 = _LIO.state_sha256(lex)
    ok(h1 == h2, "replay sem reforço no mesmo estado lógico não muda state_sha256")

    # conflito oficial posterior suspende regra antiga por frequência
    lex = _LS.lexico_vazio_v33()
    for n, lat in [(100, -30.0), (101, -30.001)]:
        _LS.atualizar(lex, [{"scope_id": scope, "contexto": ctx,
            "token_a": "SOUZA", "token_b": "SOUSA", "variante": "SOUZA", "canonico": "SOUSA",
            "base_decisao": "FREQUENCIA_CONTEXTO", "ancora": {"num": n, "lat": lat, "lon": -51.0},
            "evidencia": {"run_id": "freq"}}], min_support=2, agora="2026-01-01", scope_id=scope)
    _LS.atualizar(lex, [{"scope_id": scope, "contexto": ctx,
        "token_a": "SOUZA", "token_b": "SOUSA", "variante": "SOUSA", "canonico": None,
        "base_decisao": _LS.CONFLITO_AUTORIDADE,
        "ancora": {"num": 200, "lat": -30.01, "lon": -51.0},
        "evidencia": {"run_id": "auth", "autoridade_nivel_a": 100, "autoridade_nivel_b": 100}}],
        min_support=2, agora="2026-02-01", scope_id=scope)
    ok(not _LS.mapa_ativo(lex, scope), "conflito de autoridade suspende regra antiga aplicável")

    # autoridade superior consegue reverter regra fraca, preservando a antiga em quarentena
    lex = _LS.lexico_vazio_v33()
    for n, lat in [(100, -30.0), (101, -30.001)]:
        _LS.atualizar(lex, [{"scope_id": scope, "contexto": ctx,
            "token_a": "SOUZA", "token_b": "SOUSA", "variante": "SOUZA", "canonico": "SOUSA",
            "base_decisao": "FREQUENCIA_CONTEXTO", "ancora": {"num": n, "lat": lat, "lon": -51.0},
            "evidencia": {"run_id": "freq"}}], min_support=2, agora="2026-01-01", scope_id=scope)
    for n, lat in [(200, -30.01), (201, -30.011)]:
        _LS.atualizar(lex, [{"scope_id": scope, "contexto": ctx,
            "token_a": "SOUSA", "token_b": "SOUZA", "variante": "SOUSA", "canonico": "SOUZA",
            "base_decisao": "AUTORIDADE_NIVEL", "ancora": {"num": n, "lat": lat, "lon": -51.0},
            "evidencia": {"run_id": "auth", "autoridade_nivel_a": 100, "autoridade_nivel_b": 0}}],
            min_support=2, agora="2026-02-01", scope_id=scope)
    ok(_LS.mapa_ativo(lex, scope).get((ctx, "SOUSA")) == "SOUZA",
       "autoridade superior substitui direção antiga de menor força")

    # scope textual precisa estar em forma canônica para não criar dois municípios por caixa
    cfg = {"aprendizado": False, "scope_id": "canoas", "fontes": []}
    rr = _V.validar(cfg)
    ok(any("MAIÚSCULA" in e for e in rr.get("erros", [])), "scope_id textual não canônico é recusado")

    # fuzz determinístico de idempotência, incluindo duplicatas degeneradas no início
    import random as _random
    parts = ["RUA","R","AV","AVENIDA","EST","ESTRADA","DR","DOUTOR","PROF","PROFESSOR",
             "MAL","MARECHAL","BEIRA","MAR","JOAO","SILVA","SOUZA","SOUSA","XV","15","DE",
             "NOVEMBRO","","RUA RUA","AV AVENIDA","DR DR","PROF PROF","BR-116","ROD BR 116"]
    _random.seed(331); falha = None
    for _ in range(20000):
        raw = " ".join(_random.choice(parts) for __ in range(_random.randint(1, 7))).strip()
        n1 = _norm(raw, hard=True); n2 = _norm(n1, hard=True)
        if n1 != n2:
            falha = (raw, n1, n2); break
    ok(falha is None, "fuzz de idempotência 20k satisfaz N(N(x))=N(x)", str(falha))


# 8g ------------------------------------------------------------------------
def t_rs_hardening_v332():
    print("8g. v3.3.2 — RS / scope proof + datas compostas + escala")
    import validacao as _V
    import lexico_seguro as _LS
    from normalizacao_base import norm_logradouro as _norm

    # Nomes de datas muito usados como logradouro precisam convergir para a
    # mesma forma independentemente de extenso simples/composto.
    casos = {
        "RUA VINTE E QUATRO DE MAIO": "RUA 24 DE MAIO",
        "RUA VINTE E CINCO DE JULHO": "RUA 25 DE JULHO",
        "RUA VINTE E UM DE ABRIL": "RUA 21 DE ABRIL",
        "RUA TRINTA E UM DE MARCO": "RUA 31 DE MARCO",
    }
    ok(all(_norm(k, hard=True) == v for k, v in casos.items()),
       "numerais compostos de datas convergem para forma canônica")

    tmp = tempfile.mkdtemp(prefix="aj332rs_")
    try:
        f = os.path.join(tmp, "rs.csv")
        pd.DataFrame({"id":[1,2], "logr":["RUA TESTE","RUA TESTE"], "num":[100,200],
                      "mun":["4322400","4322400"],
                      "lat":[-29.75,-29.751], "lon":[-57.09,-57.091]}).to_csv(f,index=False)
        base = {"aprendizado": True, "scope_id":"4304606", "lexico_path":os.path.join(tmp,"lex.json"),
                "hardening":True, "fontes":[{"source_id":"f","arquivo":f,"autoridade_nivel":100,
                "colunas":{"record_id":"id","logradouro":"logr","numero":"num","lat":"lat","lon":"lon"}}]}
        rr = _V.validar(base)
        ok(any("colunas.scope_id" in e for e in rr.get("erros", [])),
           "scope municipal IBGE sem prova por linha é recusado")

        base["fontes"][0]["colunas"]["scope_id"] = "mun"
        rr = _V.validar(base)
        ok(any("divergem do scope_id" in e for e in rr.get("erros", [])),
           "dados de Uruguaiana não entram silenciosamente no scope de Canoas")

        base["scope_id"] = "4322400"
        rr = _V.validar(base)
        ok(not any("scope_id" in e and "diverg" in e for e in rr.get("erros", [])),
           "scope IBGE comprovado pela coluna da fonte passa")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # O acesso a um scope já migrado não pode revarrer os demais 496 scopes.
    lex = _LS.lexico_vazio_v33(); lex["scopes"] = {f"43{i:05d}": {"equiv":{},"indefinidos":{},"quarentena":{}} for i in range(497)}
    original = _LS.migrar_schema
    try:
        def _nao_deveria(*a, **k):
            raise AssertionError("migrar_schema chamado no hot path")
        _LS.migrar_schema = _nao_deveria
        sd = _LS._scope(lex, "4304606", create=True)
        ok(isinstance(sd, dict), "hot path de scope 3.3 é O(1), sem revarrer 497 municípios")
    finally:
        _LS.migrar_schema = original



# 8h -----------------------------------------------------------------------
def t_complemento_semantico_v333():
    print("8h. v3.3.3 — complemento semântico/referencial")
    casos = {
        "QD 4 LT 8 CASA 2": ("PARTE_DO_ENDERECO", "USAR_NO_ENDERECO", "QUADRA 4 · LOTE 8 · CASA 2"),
        "PROX IGREJA SAO JOSE": ("NAO_ENDERECO", "NAO_USAR_NO_ENDERECO", ""),
        "CASA 2 ATRAS DA IGREJA": ("MISTO", "USAR_PARCIAL", "CASA 2"),
        "CASA AMARELA PORTAO PRETO": ("NAO_ENDERECO", "NAO_USAR_NO_ENDERECO", ""),
        "AP 201 ENTRADA PELA LATERAL": ("MISTO", "USAR_PARCIAL", "APARTAMENTO 201"),
    }
    ruins=[]
    for txt,(nat,dec,end) in casos.items():
        o=CO.organizar(txt)
        if (o.get("natureza_endereco"),o.get("decisao_cadastral"),o.get("complemento_endereco_real")) != (nat,dec,end):
            ruins.append((txt,o))
    ok(not ruins, "separa endereço real de referência/acesso/descrição", str(ruins[:1]))

    o=CO.organizar("AO LADO DO 125")
    ok(o.get("referencia") == "NUMERO 125" and o.get("relacao_referencia") == "AO_LADO_DE"
       and o.get("referencia_tipo") == "NUMERO", "número citado por proximidade não vira número do endereço")

    o=CO.organizar("PROX CASA 10 CASA 2")
    ok(o.get("complemento_endereco_real") == "CASA 2" and o.get("referencia") == "CASA 10"
       and o.get("decisao_cadastral") == "USAR_PARCIAL", "DIRECT > REFERENTIAL também na decisão cadastral")

    o=CO.organizar("CASA 1 CASA 2")
    ok("CONFLITO_COMPONENTE" in o.get("risco",[]) and o.get("decisao_cadastral") == "REVISAR"
       and not o.get("complemento_endereco_real"), "conflito do mesmo componente nunca escolhe silenciosamente")

    o=CO.organizar("FUNDOS FRENTE")
    ok("CONFLITO_POSICAO" in o.get("risco",[]) and o.get("decisao_cadastral") == "REVISAR",
       "posição contraditória vai para revisão")

    o=CO.organizar("APTO S/N")
    ok(o.get("componentes",{}).get("APARTAMENTO") == "" and "SEM_NUMERO_COMPLEMENTO" in o.get("risco",[]),
       "S/N não vira APARTAMENTO S")

    o=CO.organizar("Q 5 L 3")
    ok(o.get("complemento_endereco_real") == "QUADRA 5 · LOTE 3", "aliases conservadores Q/L com valor")

    o=CO.organizar("COND RESIDENCIAL BL 2 AP 15")
    ok(o.get("empreendimento") and o.get("complemento_endereco_real") == "BLOCO 2 · APARTAMENTO 15",
       "nome de empreendimento é preservado sem contaminar componente físico")


# 8i -----------------------------------------------------------------------
def t_complement_intelligence_v334():
    print("8i. v3.3.4 — complement intelligence")
    import json as _json
    o=CO.organizar("AP 202 BL B ENTRADA LATERAL PROX ESCOLA SAO JOSE")
    ok(o.get("complemento_endereco_limpo") == "BLOCO B · APARTAMENTO 202",
       "endereço limpo exclui acesso e referência", o.get("complemento_endereco_limpo"))
    ok(o.get("utilidade_operacional") == "ALTA" and o.get("referencia_status") == "NAO_APLICAVEL_TEXTUAL",
       "referência/acesso são textuais; validação externa fica fora do contrato")
    seg=_json.loads(o.get("segmentos_json","[]"))
    tipos={x.get("tipo") for x in seg}
    ok({"ENDERECO","REFERENCIA","ACESSO"}.issubset(tipos), "segmentos JSON separa endereço/referência/acesso", str(seg))
    ok(o.get("confianca_metodo") == "HEURISTICA_V1", "método de confiança é explícito")

    o=CO.organizar("SEGUNDA CASA DEPOIS DA IGREJA SAO JOSE")
    refs=o.get("referencias",[])
    ok(refs and refs[0].get("relacao") == "APOS_N_ELEMENTOS" and refs[0].get("ordem") == 2
       and refs[0].get("elemento") == "CASA" and refs[0].get("texto") == "IGREJA SAO JOSE",
       "orientação sequencial vira relação estruturada", str(refs))

    o=CO.organizar("ESQUINA COM RUA BENTO GONCALVES")
    ok(o.get("relacao_referencia") == "NA_ESQUINA_DE" and o.get("referencia_tipo") in ("LOGRADOURO","CRUZAMENTO"),
       "referência por logradouro/cruzamento é estruturada", str(o.get("referencias")))

    o=CO.organizar("DEPOIS DA PONTE")
    ok(o.get("referencia_tipo") == "INFRAESTRUTURA" and o.get("utilidade_operacional") == "ALTA",
       "infraestrutura é referência operacional, não endereço")

    o=CO.organizar("CASA AMARELA PORTAO PRETO")
    ok(o.get("utilidade_operacional") == "MEDIA" and o.get("decisao_cadastral") == "NAO_USAR_NO_ENDERECO",
       "descrição visual é útil operacionalmente sem contaminar endereço")



# 8j -----------------------------------------------------------------------
def t_numero_estrito_cnefe_rs_v335():
    print("8j. v3.3.5 — número estrito + complemento CNEFE/RS")
    casos = {
        "1B": (1, "1", "IMOVEL B"),
        "125-A": (125, "125", "IMOVEL A"),
        "100 FUNDOS": (100, "100", "FUNDOS"),
        "500 AP 201": (500, "500", "AP 201"),
        "7 KM": (7, "7", "MODIFICADOR KM"),
        "279/281": (279, "279", "MODIFICADOR 281"),
    }
    ruins=[]
    for raw,(base,canon,comp) in casos.items():
        n=NU.parse(raw)
        if (n["base"],n["canonico"],n["complemento_derivado"]) != (base,canon,comp):
            ruins.append((raw,n))
    ok(not ruins, "campo número fica estritamente numérico e anotação migra", str(ruins[:2]))
    ok(NU.parse("1A")["chave"] != NU.parse("1B")["chave"],
       "anotação segue na chave interna de prova para 1A != 1B")
    ok(NU.parse("1B FUNDOS")["complemento_derivado"] == "IMOVEL B FUNDOS"
       and NU.parse("1AP201")["complemento_derivado"] == "AP201",
       "sufixo de unidade pode coexistir com complemento colado/explicito")

    combinado=NU.combinar_complemento(NU.parse("1B"), "FUNDOS")
    o=CO.organizar(combinado)
    ok(o["componentes"].get("IMOVEL") == "B" and o["componentes"].get("POSICAO") == "FUNDOS",
       "anotação derivada combina com complemento original sem perda", str(o["componentes"]))

    cnefe = {
        "ENTRADA 1 BLOCO A AP 304": ("ENTRADA", "BLOCO", "APARTAMENTO"),
        "RUA INTERNA A CASA 2": ("RUA_INTERNA", "CASA"),
        "COBERTURA 901": ("COBERTURA",),
        "SUBSOLO": ("SUBSOLO",),
        "PORTARIA 2": ("PORTARIA",),
        "SALAO 1": ("SALAO",),
        "PALAFITA 3": ("PALAFITA",),
        "COMODO 1": ("COMODO",),
    }
    ruins=[]
    for txt, labs in cnefe.items():
        cc=CO.organizar(txt)["componentes"]
        if not all(x in cc for x in labs): ruins.append((txt,cc))
    ok(not ruins, "taxonomia textual cobre elementos adicionais do padrão CNEFE", str(ruins[:2]))
    oo=CO.organizar("ENTRADA 1 BLOCO A AP 304")
    ok(oo["complemento_endereco_limpo"] == "ENTRADA 1 · BLOCO A · APARTAMENTO 304",
       "hierarquia CNEFE: ENTRADA -> BLOCO -> APARTAMENTO", oo["complemento_endereco_limpo"])

# 9+10+11 -------------------------------------------------------------------
def t_pipeline():
    print("9/10/11. Pipeline (léxico, marcação, idempotência)")
    tmp = tempfile.mkdtemp(prefix="ajlogr_")
    skill = os.path.join(tmp, "skill")
    shutil.copytree(AQUI, skill, ignore=shutil.ignore_patterns("saida*", "__pycache__"))
    subprocess.run([sys.executable, "exemplos/gerar_bases_exemplo.py"], cwd=skill,
                   check=True, capture_output=True)
    lex = os.path.join(skill, "lexico_exemplo.json")
    if os.path.exists(lex):
        os.remove(lex)
    r1 = subprocess.run([sys.executable, "ajustar_logradouro.py", "config.exemplo.yaml",
                         "--out", "saida1"], cwd=skill, capture_output=True, text=True)
    ok(r1.returncode == 0, "execução 1 sem erro", r1.stderr[-400:])
    if r1.returncode != 0:
        return

    run1 = latest_dir(os.path.join(skill, "saida1"))
    eq = pd.read_csv(os.path.join(run1, "lexico_equivalencias.csv"))
    at = eq[eq.status == "ativo"]
    ativos = {(r.contexto, r.variante): r.canonico for r in at.itertuples()}
    ok(ativos.get(("RUA {} XAVIER", "CONS")) == "CONSELHEIRO",
       "aprende substituição 1↔1 no contexto, com support>=2", str(ativos))
    ok(ativos.get(("TRAVESSA {} MARTINS", "EXP")) == "EXPEDICIONARIOS",
       "aprende 2ª equivalência", str(ativos))
    tokens = set(eq["variante"])
    ok("DUMONT" not in tokens and "SANTOS" not in ativos, "NÃO aprende adição (SANTOS DUMONT)")
    ok("SILVA" not in tokens, "NÃO aprende truncamento (XAVIER DA SILVA)")

    s1 = pd.read_csv(os.path.join(run1, "ajuste_logradouro.csv"),
                     dtype=str, keep_default_na=False)
    orig = pd.read_csv(os.path.join(skill, "exemplos", "cadastro.csv"),
                       dtype=str, keep_default_na=False)
    cad = s1[s1.aj_source_id == "cadastro"].reset_index(drop=True)
    # comparação de CAMPO BRUTO (csv.reader, sem inferência do pandas) — a versão
    # anterior dizia "byte-a-byte" comparando listas já parseadas: promessa maior
    # que o teste.
    import csv as _csv
    with open(os.path.join(skill, "exemplos", "cadastro.csv"), encoding="utf-8") as fh:
        bruto_in = list(_csv.reader(fh))
    with open(os.path.join(run1, "marcacao_cadastro.csv"), encoding="utf-8") as fh:
        bruto_out = list(_csv.reader(fh))
    cols_in = bruto_in[0]
    idx = [bruto_out[0].index(c) for c in cols_in]
    iguais = all([lo[j] for j in idx] == li
                 for li, lo in zip(bruto_in[1:], bruto_out[1:]))
    ok(iguais, "campos de entrada idênticos no arquivo de saída (csv.reader, sem parse)")
    ok(set(orig.columns).isdisjoint({c for c in s1.columns if c.startswith("aj_")}),
       "nenhuma coluna original sobrescrita")
    ok(set(s1["aj_scope_id"]) == {"EXEMPLO_MUNICIPIO"},
       "cada registro carrega aj_scope_id para empilhamento nacional")
    linha = cad[cad.matricula == "C001"].iloc[0]
    ok(linha["aj_logr_marcado"] == "RUA CONSELHEIRO XAVIER",
       "logradouro marcado com o léxico da rodada", linha["aj_logr_marcado"])
    ok(linha["aj_compl_QUADRA"] == "5" and linha["aj_compl_LOTE"] == "3",
       "complemento tipado na saída")
    ok("aj_compl_natureza_endereco" in cad.columns and "aj_compl_decisao_cadastral" in cad.columns
       and "aj_compl_endereco_real" in cad.columns and "aj_compl_referencia" in cad.columns,
       "camada semântica do complemento presente na saída")
    ok(linha["aj_logr_tier"] in ("ALTA", "CONFIRMA"), "tier emitido por registro")
    bm = cad[cad.matricula == "C011"].iloc[0]
    ok(bm["aj_logr_marcado"] == "AVENIDA BEIRA MAR", "adversarial BEIRA MAR na base real",
       bm["aj_logr_marcado"])
    n13 = cad[cad.matricula == "C013"].iloc[0]
    ok(n13["aj_num_modificador"] == "A" and str(n13["aj_num_canonico"]) == "12"
       and n13.get("aj_compl_IMOVEL", "") == "A",
       "número publicado é só 12; anotação A migra para complemento IMOVEL")
    pares = pd.read_csv(os.path.join(run1, "pares_mineracao.csv"))
    ok(not ((pares.numero_chave == "12|A") | (pares.numero_chave == "12|B")).any(),
       "12A x 12B barrado no gate de prova")
    ok((cad[cad.matricula == "C009"].iloc[0]["aj_logr_marcado"]) == "NUCLEO BOM JESUS",
       "vocabulário de tipo aplicado")

    r2 = subprocess.run([sys.executable, "ajustar_logradouro.py", "config.exemplo.yaml",
                         "--out", "saida2"], cwd=skill, capture_output=True, text=True)
    run2 = latest_dir(os.path.join(skill, "saida2"))
    s2 = pd.read_csv(os.path.join(run2, "ajuste_logradouro.csv"),
                     dtype=str, keep_default_na=False)
    ok(r2.returncode == 0 and s1.equals(s2), "2ª execução idêntica (idempotente)")
    import json as _json
    lexj = _json.load(open(os.path.join(skill, "lexico_exemplo.json"), encoding="utf-8"))
    ok(lexj.get("revision", lexj.get("versao", 0)) >= 2, "revision do léxico cresce a cada gravação",
       f"revision={lexj.get('revision')}")
    ok(len(lexj.get("runs", [])) >= 2, "trilha de execuções gravada no léxico")
    eqcsv = pd.read_csv(os.path.join(run1, "lexico_equivalencias.csv"))
    ok((eqcsv.base_decisao != "").all(), "toda equivalência declara a base da decisão")
    ok(os.path.exists(os.path.join(run1, "lexico_indefinidos.csv")),
       "indefinidos exportados para decisão humana")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("SELFTEST — ajuste-logradouro\n" + "=" * 52)
    t_similaridade(); t_normalizacao(); t_fidelidade(); t_numero(); t_complemento()
    t_pareamento(); t_srid_e_coordenada(); t_lexico_canone()
    t_complemento_falso_positivo(); t_srid_metrico(); t_colunas_reservadas()
    t_complemento_referencial(); t_support_independente(); t_lexico_contextual()
    t_srid_zona(); t_validacao(); t_lexico_concorrente(); t_upgrade_v31()
    t_hardening_operacional_v322(); t_hardening_operacional_v323(); t_hardening_operacional_v324(); t_hardening_operacional_v325(); t_grafo_lexico_v322(); t_lexico_nacional_v33(); t_adversarial_closure_v331(); t_rs_hardening_v332(); t_complemento_semantico_v333(); t_complement_intelligence_v334(); t_numero_estrito_cnefe_rs_v335(); t_pipeline()
    print("=" * 52)
    if FALHAS:
        print(f"FALHOU ({len(FALHAS)}): " + "; ".join(FALHAS))
        sys.exit(1)
    print("TODOS OS INVARIANTES OK")
