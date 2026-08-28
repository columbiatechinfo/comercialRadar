# -*- coding: utf-8 -*-
"""O iFOOD BLOQUEIA — e o que se cobra aqui é que ele DIGA isso.

MEDIDO em 28/08/2026, na mesma área e no mesmo IP residencial brasileiro do
plano novo:

    headful (padrão antigo)   3,2 min  ->  "TimeoutError"
    headless, detector velho  3,2 min  ->  "TimeoutError"
    headless, detector novo   0,1 min  ->  "desafio na abertura"

A mesma execução, com o mesmo bloqueio no fim. Mas uma diz o que houve e devolve
o IP; a outra gasta três minutos e um IP para não dizer nada.

O DIAGNÓSTICO, com a página na mão:

    goto            OK em 0,9 s
    url final       https://www.ifood.com.br/
    título          "Um momento…"          (o interstício do Cloudflare)
    marcas          turnstile + cloudflare no HTML
    campo endereço  NENHUM — o app nunca montou

ERAM DOIS DEFEITOS EMPILHADOS, e nenhum deles era o bloqueio:

  1. `tem_captcha` procurava `px-captcha` — o PerimeterX, a proteção ANTERIOR.
     O iFood trocou para Cloudflare Turnstile, cujas marcas não têm nada a ver.
  2. `--visivel` era o padrão, e headful numa máquina sem sessão gráfica
     pendura antes de qualquer avaliação da página.

O BLOQUEIO EM SI CONTINUA, e é interativo: o Turnstile pede clique humano. Isto
aqui não o contorna — cobra que a etapa falhe rápido, diga por quê e não queime
IP à toa.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

EXTRAI = os.path.join(RAIZ, "extrair_ifood.py")
ENRIQ = os.path.join(RAIZ, "enriquecer_ifood.py")


def _ler(p):
    return io.open(p, encoding="utf-8").read()


def _codigo(caminho):
    """O arquivo com os COMENTÁRIOS apagados e o resto intacto.

    DUAS TENTATIVAS ANTES DESTA, e as duas reprovaram código correto:

      1. cortar no primeiro `#` da linha — mas os seletores que este teste
         cobra SÃO `#px-captcha-modal` e `#cf-chl-widget`, e o corte ingênuo os
         decapitava dentro da própria string;
      2. juntar os tokens do `tokenize` com espaço — vira
         `p . set_defaults ( ... )`, e nenhuma asserção exata casa mais.

    O certo é o `tokenize` DIZER ONDE o comentário está e apagar só aquele vão,
    deixando cada outro caractere onde estava. Assim a asserção continua exata e
    deixa de concordar com a minha prosa.
    """
    import tokenize
    linhas = _ler(caminho).splitlines(keepends=True)
    with tokenize.open(caminho) as f:
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type != tokenize.COMMENT:
                continue
            i = tok.start[0] - 1
            a, b = tok.start[1], tok.end[1]
            linhas[i] = linhas[i][:a] + " " * (b - a) + linhas[i][b:]
    return "".join(linhas)


def _texto(caminho, fonte=None):
    """O mesmo texto com os espaços achatados.

    Serve para casar frase quebrada em DUAS linhas de código, que é como toda
    mensagem longa é escrita aqui — uma asserção falhou por isso contra uma
    mensagem que estava lá.
    """
    import re
    return re.sub(r"\s+", " ", fonte if fonte is not None else _ler(caminho))


def test_o_detector_conhece_os_dois_muros():
    """Procurar só a proteção do ano passado não é falhar — é falhar SEM DIZER."""
    py = _codigo(ENRIQ)
    i = py.index("async def tem_captcha(")
    corpo = py[i:py.index("async def ", i + 10)]
    assert "px-captcha" in corpo, "o detector esqueceu o PerimeterX"
    for marca in ("cf-turnstile", "cf-chl", "challenges.cloudflare.com"):
        assert marca in corpo, \
            "o detector não reconhece o Cloudflare Turnstile (%s)" % marca
    # o TÍTULO entra na conta: o interstício troca o documento inteiro, e não
    # sobra seletor do app para procurar
    assert "um momento" in corpo and "just a moment" in corpo, \
        "o detector deixou de olhar o título do interstício"


def test_a_janela_so_abre_onde_ha_gente_olhando():
    """`DISPLAY` NÃO SERVE COMO SINAL, e eu tentei: o WSLg do i9 exporta
    `DISPLAY=:0` e `WAYLAND_DISPLAY=wayland-0` mesmo numa sessão SSH sem tela
    nenhuma. A heurística dizia "tem tela" e o Chromium pendurava igual.

    `isatty` responde a pergunta certa: há uma PESSOA num terminal do outro
    lado? Quem roda à mão vê a janela; subprocess e `ssh ... bash -s` não veem,
    porque ninguém está lá para olhar.
    """
    py = _codigo(EXTRAI)
    assert "p.set_defaults(visivel=sys.stdout.isatty())" in py, \
        "a janela voltou a abrir por padrão, e pendura em máquina sem sessão gráfica"
    assert 'os.environ.get("DISPLAY")' not in py, \
        "voltou o `DISPLAY` como sinal, que o WSLg torna inútil"
    # e ainda dá para forçar, para quem quer ver o que o iFood mostrou
    assert '"--visivel"' in py and '"--oculto"' in py, \
        "sumiu a forma de forçar (ou proibir) a janela à mão"


def test_a_falha_e_dita_e_o_ip_devolvido():
    """Perder o iFood custa CNPJ, não custa a rodada — mas custar um IP e três
    minutos para não dizer nada é outra coisa."""
    py = _codigo(EXTRAI)
    assert '"desafio na abertura"' in py, "o veredito do desafio sumiu"
    assert '"desafio antes do feed"' in py, "o veredito do feed sumiu"
    # e a mensagem final não deixa confundir bloqueio com cidade vazia
    assert "não é 'a cidade não tem " in _codigo(EXTRAI), (
        "a mensagem final voltou a deixar 'bloqueado' parecer 'não há lojas "
        "aqui' — que é uma conclusão de negócio errada")
