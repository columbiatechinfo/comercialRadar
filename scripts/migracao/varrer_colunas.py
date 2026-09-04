# -*- coding: utf-8 -*-
"""Alguma coluna REMOVIDA da `pois` ainda e usada contra a `pois` no codigo vivo?

A etapa 4 morreu hoje por causa de uma que eu tinha deixado passar. Uma varredura
custa segundos; descobrir na run custa a run.

O teste nao e "o nome aparece" — e "o nome aparece num trecho de SQL que fala da
`pois`". Um `insert into maps_data (... avaliacao ...)` e legitimo.
"""
import io
import os
import re

# O que a 0052 tirou da `pois`, menos o que a 0060 devolveu.
REMOVIDAS = ["maps_url", "plus_code", "avaliacao", "total_avaliacoes",
             "resumo_avaliacoes", "status_horario", "razao_social",
             "nome_fantasia", "cnae", "coord_compartilhada", "coord_grupo",
             "descoberto_de", "endereco_gerado_por", "fundido_para",
             "fundido_por"]

VIVOS = """
minerar_tudo.py server.py base_api.py chat_api.py realtime_ingest.py auth.py
area_utils.py telhados.py reusar_area.py origem_estadual.py
cadastro_cliente.py cadastur.py conferir_municipio.py corrigir_coordenada.py
cruzar_ligacao.py detalhar_airbnb.py detalhar_ifood.py extracao_estadual.py
extrair_airbnb.py extrair_ifood.py google_enriquece.py ifood_para_poi.py
minerar_placeid.py normalizar_bases.py resolver_logradouro.py
fontes_para_poi.py enderecar_airbnb.py ifood_para_busca.py
""".split()

# Trechos que falam da `pois` — um `update pois set ...` ou um
# `... from pois` / `join pois` a poucas linhas de distancia.
FALA_DE_POIS = re.compile(
    r"(update\s+(radar_comercial\.)?pois\b|"
    r"into\s+(radar_comercial\.)?pois\b|"
    r"from\s+(radar_comercial\.)?pois\b|"
    r"join\s+(radar_comercial\.)?pois\b)", re.I)

achados = []
for arq in VIVOS:
    if not os.path.exists(arq):
        continue
    linhas = io.open(arq, encoding="utf-8", errors="ignore").read().splitlines()
    for i, l in enumerate(linhas):
        if l.strip().startswith("#") or l.strip().startswith("//"):
            continue
        for c in REMOVIDAS:
            if not re.search(r"\b" + c + r"\b", l):
                continue
            # janela de 14 linhas para tras e 6 para frente
            ini, fim = max(0, i - 14), min(len(linhas), i + 7)
            trecho = "\n".join(linhas[ini:fim])
            if not FALA_DE_POIS.search(trecho):
                continue
            # se o trecho fala de uma tabela de fonte, e legitimo
            if re.search(r"(maps_data|cadastur_data|receita_data|osm_data|"
                         r"overture_data|foursquare_data|pois_completo)", trecho, re.I):
                continue
            achados.append((arq, i + 1, c, l.strip()[:88]))

if not achados:
    print("NENHUMA coluna removida aponta para a `pois` no codigo vivo.")
else:
    print("SUSPEITAS (%d):" % len(achados))
    for a, n, c, l in achados:
        print("  %-24s :%-5d %-18s %s" % (a, n, c, l))
