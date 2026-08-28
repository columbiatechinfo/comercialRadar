# -*- coding: utf-8 -*-
"""QUANDO O MAPS NÃO ABRE, O PERFIL É SUSPEITO ANTES DO IP.

O DEFEITO, medido em 28/08/2026. A run das 14:44 queimou 5 IPs no passo 4 e
trouxe 0 de 14 POIs, todos com `Page.goto: Timeout 35000ms exceeded`. A leitura
fácil — "os proxies morreram" — é a errada, e o código a adotava sozinho.

O QUE FOI MEDIDO, no mesmo host e nos MESMOS IPs marcados como queimados:

    urllib com credencial, google.com/maps    HTTP 200 em ~1 s (3 de 3)
    Chromium + proxy, sessão única            raiz 4,7 s · profunda 2,0 s
    Chromium + proxy, 10 sessões paralelas    10 de 10 abriram, 5,1 s de parede

Não era o IP, não era a URL, não era memória (94 GB, 57 livres) e não era o
número de workers.

O QUE ERA. A run anterior fora CANCELADA com os dez navegadores vivos, e eles
seguravam justamente os diretórios `.browser_profiles/wN` que o passo 4 reusa.
Na mesma hora, o passo 5 passou em 41 de 46 categorias — e ele usa
`/tmp/cr_descobre_N`, que a run cancelada nunca tocou. É a mesma família do
defeito que o `descobrir_maps` já tinha diagnosticado em 26/08:

    perfil velho          -> goto TIMEOUT, feed 0, links 0
    perfil novo em branco -> feed 1, 20 links

O `descobrir_maps` ganhou a cura naquele dia. O `search_pois_v2` não: ele só
punia o IP e seguia, então um problema de perfil consumia o pool inteiro sem
nunca se resolver. Estes testes prendem a cura nos DOIS.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

BUSCA = os.path.join(RAIZ, "search_pois_v2.py")
DESCOBRE = os.path.join(RAIZ, "descobrir_maps.py")


def _ler(p):
    return io.open(p, encoding="utf-8").read()


def _codigo(caminho):
    """Sem comentário nem docstring: asserção que lê prosa concorda comigo."""
    import ast
    fonte = _ler(caminho)
    fora = set()
    for no in ast.walk(ast.parse(fonte)):
        corpo = getattr(no, "body", None)
        if not isinstance(corpo, list) or not corpo:
            continue
        d = corpo[0]
        if isinstance(d, ast.Expr) and isinstance(d.value, ast.Constant) \
           and isinstance(d.value.value, str):
            fora.update(range(d.lineno, (d.end_lineno or d.lineno) + 1))
    linhas = [("" if i + 1 in fora else l) for i, l in enumerate(fonte.splitlines())]
    return "\n".join(l.split("#")[0] for l in linhas)


def test_a_busca_troca_o_perfil_antes_de_culpar_o_ip():
    """Sem isto, cinco IPs vão para castigo de 10 min por um problema que não é
    deles, e a etapa entrega zero."""
    py = _codigo(BUSCA)
    assert "MAX_CURAS_PERFIL" in py, (
        "a busca voltou a punir o IP direto: um perfil ruim consome o pool "
        "inteiro sem nunca se resolver")
    assert "shutil.rmtree(profile_dir" in py, \
        "a busca parou de jogar o perfil fora quando o Maps não abre"
    # a cura vem ANTES do castigo definitivo, senão o perfil nunca é trocado
    i = py.index("shutil.rmtree(profile_dir")
    j = py.index("IP de castigo por 10 min")
    assert i < j, "o castigo do IP voltou a vir antes da troca de perfil"


def test_a_cura_tem_teto_nos_dois_modulos():
    """Se três perfis novos e três IPs não abriram, o que está errado não é
    nenhum dos dois — insistir só gasta proxy."""
    for caminho, nome in ((BUSCA, "search_pois_v2"), (DESCOBRE, "descobrir_maps")):
        py = _codigo(caminho)
        assert "MAX_CURAS" in py, "%s ficou sem teto de curas" % nome
        assert "= 3" in py, "%s mudou o teto de curas sem avisar" % nome


def test_a_cura_troca_perfil_E_ip():
    """Refazer a sessão com o MESMO proxy falha idêntico quando o problema é o
    IP. Os dois trocam juntos."""
    for caminho, nome in ((BUSCA, "search_pois_v2"), (DESCOBRE, "descobrir_maps")):
        py = _codigo(caminho)
        assert "mark_cooldown" in py, "%s parou de tirar o IP de circulação" % nome
        assert "rmtree" in py, "%s parou de trocar o perfil" % nome


def test_os_dois_modulos_nao_dividem_o_diretorio_de_perfil():
    """FOI ISSO QUE SALVOU O PASSO 5 na run das 14:44.

    O passo 4 usa `.browser_profiles/wN`; o 5 usa `/tmp/cr_descobre_N`. Quando
    uma run cancelada deixou navegadores vivos segurando os primeiros, o passo 4
    entregou 0 de 14 e o 5 entregou 41 de 46 categorias. Se um dia passarem a
    dividir o mesmo diretório, os dois caem juntos.
    """
    busca = _codigo(BUSCA)
    descobre = _codigo(DESCOBRE)
    assert "BROWSER_PROFILES_DIR" in busca, \
        "a busca mudou de diretório de perfil sem avisar"
    assert "cr_descobre_" in descobre, \
        "a descoberta mudou de diretório de perfil sem avisar"
    assert "BROWSER_PROFILES_DIR" not in descobre, (
        "os dois passos passaram a dividir o diretório de perfil: uma run "
        "cancelada agora derruba os dois de uma vez")
