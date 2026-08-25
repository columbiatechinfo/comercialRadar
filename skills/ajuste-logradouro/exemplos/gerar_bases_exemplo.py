# -*- coding: utf-8 -*-
"""
gerar_bases_exemplo.py — fixtures determinísticas (2 fontes, RS/Porto Alegre)
=============================================================================
Duas bases do MESMO território escritas de formas diferentes — é essa diferença
que vira prova para o léxico. Inclui de propósito:

  * equivalências APRENDÍVEIS (substituição 1↔1, plausível, em 2 imóveis
    distintos): CONS≡CONSELHEIRO, EXP≡EXPEDICIONARIOS
  * distrator de ADIÇÃO: SANTOS vs SANTOS DUMONT (0↔1) — NÃO pode ser aprendido
  * TRUNCAMENTO: XAVIER vs XAVIER DA SILVA (0↔2) — NÃO pode ser aprendido
  * complementos sujos: grudado (QD5LT3), ruído institucional, S/N
"""
import csv
import os

BASE = os.path.dirname(os.path.abspath(__file__))
LAT0, LON0 = -30.0330, -51.2300
D = 9.0e-5                                   # ~10 m em latitude


def pt(i, j, dx=0.0, dy=0.0):
    return round(LAT0 - i * 25 * D + dy, 7), round(LON0 + j * 25 * D + dx, 7)


CAD = [
    # id, logradouro, numero, complemento, i, j
    ("C001", "R CONS XAVIER",        "100", "QD5LT3",                 0, 0),
    ("C002", "R CONS XAVIER",        "244", "AP 201 BL B",            1, 0),
    ("C003", "AV DR PEREIRA",        "250", "CASA FUNDOS (CEPISA)",   2, 0),
    ("C004", "TV EXP MARTINS",       "30",  "SOBRELOJA 2",            3, 0),
    ("C005", "TV EXP MARTINS",       "77",  "QUADRA 12 LOTE 05",      4, 0),
    ("C006", "RUA SANTOS",           "410", "AP 12",                  5, 0),
    ("C007", "RUA XAVIER",           "88",  "",                       6, 0),
    ("C008", "AVENI QUINZE DE NOVEMBRO", "S/N", "SN",                 7, 0),
    ("C009", "NUCLE BOM JESUS",      "15",  "CS 3",                   8, 0),
    ("C010", "R MAL DEODORO",        "1200", "APTO 902 TORRE A",      9, 0),
    # --- casos adversariais da revisao externa ---
    ("C011", "AV BEIRA MAR",         "500", "",                      10, 0),
    ("C012", "RUA MEIA PRAIA",       "20",  "",                      11, 0),
    ("C013", "R CONS XAVIER",        "12A", "",                      12, 0),
]

CNEFE = [
    ("N001", "RUA CONSELHEIRO XAVIER",   "100", "QUADRA 5 LOTE 3",     0, 0),
    ("N002", "RUA CONSELHEIRO XAVIER",   "244", "BLOCO B APARTAMENTO 201", 1, 0),
    ("N003", "AVENIDA DOUTOR PEREIRA",   "250", "CASA FUNDOS",         2, 0),
    ("N004", "TRAVESSA EXPEDICIONARIOS MARTINS", "30", "SOBRELOJA 2",  3, 0),
    ("N005", "TRAVESSA EXPEDICIONARIOS MARTINS", "77", "QUADRA 12 LOTE 5", 4, 0),
    ("N006", "RUA SANTOS DUMONT",        "410", "APARTAMENTO 12",      5, 0),
    ("N007", "RUA XAVIER DA SILVA",      "88",  "",                    6, 0),
    ("N008", "AVENIDA 15 DE NOVEMBRO",   "S/N", "",                    7, 0),
    ("N009", "NUCLEO BOM JESUS",         "15",  "CASA 3",              8, 0),
    ("N010", "RUA MARECHAL DEODORO",     "1200", "TORRE A APARTAMENTO 902", 9, 0),
    ("N011", "AVENIDA BEIRA MAR",        "500", "",                    10, 0),
    ("N012", "RUA MEIA PRAIA",           "20",  "",                    11, 0),
    ("N013", "RUA CONSELHEIRO XAVIER",   "12B", "",                    12, 0),
]


def escrever(nome, linhas, cols, desloc):
    with open(os.path.join(BASE, nome), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for rid, logr, num, cmp_, i, j in linhas:
            lat, lon = pt(i, j, dy=desloc)
            w.writerow([rid, logr, num, cmp_, lat, lon])
    print(f"  {nome}: {len(linhas)} registros")


if __name__ == "__main__":
    escrever("cadastro.csv", CAD,
             ["matricula", "endereco", "nro", "compl", "latitude", "longitude"], 0.0)
    escrever("cnefe.csv", CNEFE,
             ["cod", "logradouro", "numero", "complemento", "lat", "lon"], 7.0e-5)
    print("fixtures geradas em", BASE)
