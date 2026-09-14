# -*- coding: utf-8 -*-
"""Monta frontend/seek.html a partir do docs/seek.html (o desenho aprovado) e das
pecas do Comercial Radar: o CSS e o motor do cerebro entram sem edicao; a entrada
propria, a demonstracao e o script da esteira sao trocados."""
import re
import sys
from pathlib import Path

# Uso, da raiz do repositorio:  python3 frontend/seek_fonte/montar.py
# (base.html e o desenho aprovado; a saida e frontend/seek.html)
aqui = Path(__file__).parent
fonte = Path(sys.argv[1] if len(sys.argv) > 1 else aqui / "base.html").read_text(encoding="utf-8").splitlines(keepends=True)
saida = Path(sys.argv[2] if len(sys.argv) > 2 else aqui.parent / "seek.html")


def linhas(a, b):
    """linhas a..b, contando de 1, inclusivas"""
    return "".join(fonte[a - 1:b])


assert fonte[6].startswith("<style>"), fonte[6]
assert "imp-obs b{" in fonte[697], fonte[697]
assert fonte[699].startswith("</style>"), fonte[699]
assert fonte[865].startswith("<script>"), fonte[865]
assert "20-cerebro-motor.js" in fonte[866], fonte[866]
assert "raiz.MotorCerebro = {cria: cria};" in fonte[2198], fonte[2198]
assert "97-seek.js" in fonte[2202], fonte[2202]

texto = "".join(fonte)
logo = re.search(r'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 73.4 28.8".*?</svg>', texto, re.S).group(0)

html = ("<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        "<title>SEEK · A2L</title>\n"
        + linhas(7, 699)
        + (aqui / "extra.css").read_text(encoding="utf-8")
        + "</style>\n</head>\n<body>\n"
        + linhas(704, 720)
        + (aqui / "corpo.html").read_text(encoding="utf-8").replace("@@LOGO@@", logo)
        + "\n<!-- login, token em toda chamada a /api/ e o cracha em window.EU -->\n"
        + "<script src=\"/static/sessao.js\"></script>\n"
        + "<script>\n"
        + linhas(867, 2202)
        + (aqui / "app.js").read_text(encoding="utf-8")
        + "</script>\n</body>\n</html>\n")
saida.write_text(html, encoding="utf-8", newline="\n")
print("ok", saida, len(html.splitlines()), "linhas", len(html.encode("utf-8")) // 1024, "KB")

# A GESTAO DAS APROVACOES (14/09/2026): a mesma marca e os mesmos tokens da SEEK
# (o CSS do desenho aprovado + o extra da SEEK), com corpo, estilo e script proprios.
# Sai ao lado da SEEK, em frontend/gestao.html, servida em /gestao.
gestao = ("<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
          "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
          "<title>Gestão · SEEK · A2L</title>\n"
          + linhas(7, 699)
          + (aqui / "extra.css").read_text(encoding="utf-8")
          + (aqui / "gestao.css").read_text(encoding="utf-8")
          + "</style>\n</head>\n<body>\n"
          + (aqui / "gestao_corpo.html").read_text(encoding="utf-8").replace("@@LOGO@@", logo)
          + "\n<!-- login, token em toda chamada a /api/ e o cracha em window.EU -->\n"
          + "<script src=\"/static/sessao.js\"></script>\n"
          + "<script>\n"
          + (aqui / "gestao.js").read_text(encoding="utf-8")
          + "</script>\n</body>\n</html>\n")
saida_gestao = saida.parent / "gestao.html"
saida_gestao.write_text(gestao, encoding="utf-8", newline="\n")
print("ok", saida_gestao, len(gestao.splitlines()), "linhas", len(gestao.encode("utf-8")) // 1024, "KB")
