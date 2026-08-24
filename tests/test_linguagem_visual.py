# -*- coding: utf-8 -*-
"""A linguagem visual não volta a se dispersar.

POR QUE ESTE TESTE EXISTE

Antes desta rodada o `style.css` tinha 92 tons diferentes: duas famílias de
cinza convivendo (a do Google e a do Tailwind), dois azuis, e uma cauda longa
em que cada regra inventou o seu. Ninguém fez isso de propósito — foi uma tela
de cada vez, cada uma com um `#f8fafc` ligeiramente diferente do vizinho.

Reunir tudo custou uma tarde. Sem trava, dispersa de novo pelo mesmo caminho:
alguém escreve `#f5f5f5` numa regra nova e nada acusa.

O QUE ELE PROÍBE, E O QUE NÃO PROÍBE

Proíbe cor CRUA nos arquivos de moldura — `style.css` e os tokens. Cor de
moldura é vocabulário fechado, e o vocabulário mora em `tokens.css`.

NÃO proíbe cor no `app.js`: lá as cores CODIFICAM DADO — uma por ramo de
comércio, uma por fonte de origem, uma por face de quadra. Achatar aquilo para
o azul da marca destruiria a legenda do mapa. O que ele cobra do `app.js` é
outra coisa: que toda cor usada como FUNDO DE CHIP leia com texto branco por
cima.
"""
from __future__ import annotations

import io
import pathlib
import re

import pytest

FRONT = pathlib.Path(__file__).resolve().parent.parent / "frontend"

def _hexes(texto: str) -> list:
    return re.findall(r"#[0-9a-fA-F]{3,8}\b", texto)


def _rgb(h: str) -> str:
    """O RGB de um hex, ignorando o alpha e expandindo a forma curta."""
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return h[:6].upper()


def _vocabulario() -> set:
    """O vocabulário sai do PRÓPRIO tokens.css, e não de uma lista aqui.

    Lista fixa no teste envelhece: nasce um token, o teste reprova, alguém
    acrescenta a cor na lista sem pensar, e a trava vira carimbo. Lendo do
    arquivo, acrescentar um token é a única forma de o CSS ganhar uma cor —
    que é exatamente a regra que se quer impor.
    """
    t = io.open(FRONT / "tokens.css", encoding="utf-8").read()
    return {_rgb(h) for h in _hexes(t)} | {"FFFFFF", "000000"}


def test_style_css_so_usa_o_vocabulario():
    """Nenhuma cor crua nova no CSS da moldura.

    Variante com ALPHA de um token passa — `#FFFFFF1A` é o véu branco sobre o
    navy do cabeçalho, e não há como declará-lo como token sem multiplicar o
    vocabulário por oito níveis de transparência. O que ele proíbe é MATIZ
    novo: um cinza, um azul ou um verde que não existe em tokens.css.
    """
    s = io.open(FRONT / "style.css", encoding="utf-8").read()
    vocab = _vocabulario()
    fora = sorted({h for h in _hexes(s) if _rgb(h) not in vocab})
    assert not fora, (
        "matiz fora do vocabulário no style.css: " + ", ".join(fora) +
        "\nToda cor de moldura vira token em tokens.css. Se ela codifica DADO "
        "(ramo, fonte, face), o lugar dela é o app.js.")


def test_tokens_definem_a_variante_legivel_de_cada_acento():
    """Acento claro precisa de um par escuro para quando ele é TEXTO.

    Azul, laranja e vermelho da marca reprovam no AA quando viram texto sobre
    branco — o laranja dá 2.45:1, metade do mínimo. O par existe para isso, e
    some com facilidade numa limpeza distraída.
    """
    t = io.open(FRONT / "tokens.css", encoding="utf-8").read()
    for nome in ("--azul-texto", "--laranja-texto", "--critico-texto"):
        assert re.search(rf"{nome}:\s*#[0-9A-Fa-f]{{6}}", t), \
            f"{nome} sumiu de tokens.css"


def _contraste(a: str, b: str) -> float:
    def lum(h: str) -> float:
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        v = []
        for i in (0, 2, 4):
            c = int(h[i:i + 2], 16) / 255
            v.append(c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4)
        return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + .05) / (y + .05)


def test_variantes_de_texto_passam_no_aa_sobre_a_propria_tinta():
    """A tinta é o fundo mais escuro onde cada variante aparece.

    Medido contra a tinta, e não contra branco: foi exatamente aí que a
    primeira tentativa passou no branco e reprovou no chip.
    """
    t = io.open(FRONT / "tokens.css", encoding="utf-8").read()
    casos = {"--critico-texto": "#FCF0F0",
             "--laranja-texto": "#FEF6EE",
             "--azul-texto":    "#EBF4FF"}
    for nome, tinta in casos.items():
        cor = re.search(rf"{nome}:\s*(#[0-9A-Fa-f]{{6}})", t).group(1)
        r = _contraste(cor, tinta)
        assert r >= 4.5, f"{nome} ({cor}) dá {r:.2f}:1 sobre {tinta} — reprova"


def test_cor_de_chip_le_com_texto_branco():
    """Cor que vira fundo de chip carrega texto branco por cima.

    As cores de GEOMETRIA (COR_FACE, camadas de quadra) ficam de fora: são
    traço sobre foto aérea, onde vivo é o certo e não há texto por cima.
    """
    js = io.open(FRONT / "app.js", encoding="utf-8").read()

    # As cores declaradas como `cor:` nas listas de fonte/atributo e as
    # passadas inline como `--c:` são as que viram fundo de chip.
    chips = set(re.findall(r'cor:\s*"(#[0-9a-fA-F]{6})"', js))
    chips |= set(re.findall(r'--c:\s*(#[0-9a-fA-F]{6})', js))

    # Fora: as de geometria, declaradas em constantes próprias.
    geometria = set()
    for nome in ("COR_FACE", "COR_DESTOA", "COR_INDEF"):
        m = re.search(rf"const {nome}\s*=\s*(\[[^\]]*\]|\"#[0-9a-fA-F]{{6}}\")",
                      js, re.S)
        if m:
            geometria |= set(re.findall(r"#[0-9a-fA-F]{6}", m.group(1)))
    # E as das camadas do mapa, que são listas [chave, rótulo, cor, ligada].
    for m in re.finditer(r'\[\s*"[^"]+",\s*"[^"]+",\s*"(#[0-9a-fA-F]{6})"',
                         js):
        geometria.add(m.group(1))

    ruins = []
    for c in sorted(chips - geometria):
        r = _contraste("#FFFFFF", c)
        if r < 4.5:
            ruins.append(f"{c} dá {r:.2f}:1 com branco")
    assert not ruins, ("cor de chip ilegível: " + "; ".join(ruins) +
                       "\nEscureça mantendo o matiz — a legenda do mapa "
                       "depende de as cores continuarem distinguíveis.")


def test_tokens_carregam_antes_do_style():
    """`style.css` consome as variáveis de `tokens.css`.

    Na ordem inversa o :root do sistema aponta para nomes que ainda não
    existem e a tela abre sem cor nenhuma — falha silenciosa, sem erro no
    console.
    """
    html = io.open(FRONT / "index.html", encoding="utf-8").read()
    i_tok = html.find("/static/tokens.css")
    i_sty = html.find("/static/style.css")
    assert i_tok != -1, "index.html não carrega tokens.css"
    assert i_tok < i_sty, "tokens.css tem de vir ANTES de style.css"


def test_nenhum_byte_de_controle_no_css():
    """Escapada CSS mal escrita já gravou NUL no arquivo.

    `content: "\\00d7"` num literal Python vira escape OCTAL: grava o byte 0 e
    o ícone some. O `grep` mostra o arquivo como binário e o `Read` não deixa
    ver — só o `repr()` denuncia. É o mesmo erro que já pôs bytes de backspace
    dentro de uma expressão regular deste repositório.
    """
    for arq in ("style.css", "tokens.css"):
        s = io.open(FRONT / arq, encoding="utf-8").read()
        maus = [(i, hex(ord(c))) for i, c in enumerate(s)
                if ord(c) < 9 or 13 < ord(c) < 32]
        assert not maus, f"{arq} tem byte de controle em {maus[:3]}"


@pytest.mark.parametrize("arq", ["sessao.js", "fila.js", "admin.js",
                                 "abas.js", "app.js"])
def test_js_do_front_e_sintaticamente_valido(arq):
    """Crase dentro de comentário HTML já fechou um template literal aqui."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node não instalado")
    r = subprocess.run([node, "--check", str(FRONT / arq)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:400]
