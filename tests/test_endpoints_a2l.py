# -*- coding: utf-8 -*-
"""ONDE CADA SERVIÇO MORA — um lugar só, e nenhum endereço morto.

POR QUE ESTE TESTE EXISTE. Até 30/08/2026 o endereço de cada serviço era um
padrão embutido no arquivo que o consumia: sete arquivos, cinco endereços, dois
deles duplicados, mais os testes. Trocar de ambiente assim é trocar quarenta
linhas e deixar uma para trás — e a que fica só aparece quando alguém rodar
aquele caminho, semanas depois.

O QUE ACONTECEU NA PRÁTICA, e é o motivo de o teste olhar também o `.env`: o
`endpoints.py` nasceu com os padrões certos e, no primeiro teste, resolveu para
os endereços VELHOS. Variável de ambiente vence padrão de código, e o `.env`
ainda trazia `NOMINATIM_URL=...100.115.117.49:8080`. O módulo estava certo; o
ambiente é que discava para uma máquina que não existe mais.

O i9 de `100.115.117.49` e a Spark de `100.85.164.54` foram desligados: a
infraestrutura virou o servidor Ubuntu do padrão A2L (doc 23).
"""
import io
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

#: As máquinas desligadas. Nenhuma pode voltar a aparecer, em código nem no .env.
MORTAS = ("100.115.117.49", "100.85.164.54")


def test_o_modulo_resolve_para_o_ambiente_novo():
    import endpoints as e
    for nome in ("SUPABASE", "IDENTIDADE", "RECURSOS", "NOMINATIM",
                 "SEARXNG", "VLLM", "PHOTON", "OSRM_CARRO", "OSRM_PE"):
        valor = getattr(e, nome)
        assert valor, "%s ficou vazio" % nome
        for morta in MORTAS:
            assert morta not in valor, (
                "%s resolve para %s, que foi DESLIGADA. Provavelmente há uma "
                "variável no .env sobrescrevendo o padrão." % (nome, morta))


def test_as_portas_seguem_os_blocos_do_doc_17():
    """Faixa 7000-7999, blocos de 100 por categoria, incremento de 10."""
    import endpoints as e
    assert 7700 <= e.PORTA_API <= 7799, "a API saiu do bloco de APIs próprias"
    assert 7800 <= e.PORTA_FRONT <= 7899, "o frontend saiu do bloco de frontends"
    assert 7900 <= e.PORTA_CAPTURA <= 7999, "a página da captura saiu do bloco livre"
    for p in (e.PORTA_API, e.PORTA_FRONT, e.PORTA_CAPTURA):
        assert p % 10 == 0, "%d quebra o incremento de 10 dentro do bloco" % p


def test_o_env_nao_ressuscita_maquina_desligada():
    """Variável de ambiente VENCE o padrão do código — foi assim que o módulo
    novo discou para o i9 velho no primeiro teste."""
    caminho = os.path.join(RAIZ, ".env")
    if not os.path.exists(caminho):
        import pytest
        pytest.skip(".env não existe nesta máquina")
    vivas = [l for l in io.open(caminho, encoding="utf-8").read().splitlines()
             if re.match(r"^[A-Z_]+=", l)]          # só as NÃO comentadas
    ruins = [l.split("=")[0] for l in vivas
             if any(m in l for m in MORTAS)]
    assert not ruins, (
        "estas variáveis do .env apontam para máquina desligada e vão vencer o "
        "padrão do endpoints.py: %s" % ruins)


def test_o_token_dos_recursos_fala_alto_quando_falta_credencial():
    """Sem credencial, toda chamada a mapa e IA vira 401 lá adiante, longe da
    causa. A exceção própria diz onde consertar."""
    import endpoints as e
    fonte = io.open(os.path.join(RAIZ, "endpoints.py"), encoding="utf-8").read()
    assert "class SemCredencialDeRecurso" in fonte, \
        "a falta de credencial voltou a ser silenciosa"
    assert "A2L_RECURSOS_CLIENTE" in fonte and "A2L_RECURSOS_SEGREDO" in fonte, \
        "as variáveis da credencial de recurso sumiram"

    # e a renovação é ANTECIPADA: esperar o 401 faz a primeira chamada de cada
    # 15 minutos falhar, e algumas não são repetidas por ninguém
    assert "_FOLGA_S" in fonte and "expira\"] - _FOLGA_S" in fonte, \
        "o token voltou a ser renovado só depois de vencer"


def test_nenhum_modulo_embute_endereco_morto():
    """O padrão embutido é o que sobrevive à troca de .env — e é justamente o
    que precisa estar certo."""
    ruins = []
    for arq in os.listdir(RAIZ):
        if not arq.endswith(".py") or arq == "endpoints.py":
            continue
        texto = io.open(os.path.join(RAIZ, arq), encoding="utf-8",
                        errors="ignore").read()
        codigo = "\n".join(l.split("#")[0] for l in texto.splitlines())
        for morta in MORTAS:
            if morta in codigo:
                ruins.append("%s -> %s" % (arq, morta))
    assert not ruins, (
        "estes módulos ainda embutem endereço de máquina desligada: %s" % ruins)
