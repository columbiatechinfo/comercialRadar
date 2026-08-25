# -*- coding: utf-8 -*-
"""O passo de IA que separa o endereço grudado — e as travas que o tornam usável.

A IA entra aqui e em nenhum outro lugar da cadeia de endereço: segmentar é
LEITURA de formato heterogêneo, e não existe prova disponível, só interpretação.
Canonizar é outra coisa — tem prova (coordenada, número, autoridade) e pertence
à `ajuste-logradouro`.

Estes testes cobram as três travas que separam leitura de alucinação. Nenhum
deles toca a rede: a Spark é dublada, porque o que está sob teste é o nosso
julgamento sobre a resposta, não o modelo.
"""
import json
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import segmentar_endereco as S  # noqa: E402

MAPS = "R. Mal. Rondon, 1199 - Niterói, Canoas - RS, 92120-210, Brasil"


def _dublar(monkeypatch, respostas):
    """Substitui a chamada à Spark por uma lista de respostas fixas."""
    chamadas = []

    def _falso(enderecos):
        chamadas.append(list(enderecos))
        return respostas[len(chamadas) - 1]

    monkeypatch.setattr(S, "_chamar", _falso)
    return chamadas


# ─── a IA roda só na Spark ───────────────────────────────────────────────────

def test_o_i9_e_recusado_como_endpoint_de_ia():
    """Regra do dono do produto, 25/08/2026: a única máquina de IA é a Spark.

    O i9 é a máquina do trabalho pesado de DADOS — DuckDB sobre o Overture, o
    PBF do OSM e o Postgres de produção. Modelo ali disputa a mesma RAM de uma
    extração estadual e cria uma segunda verdade sobre qual modelo respondeu o
    quê.

    A recusa é em código porque combinado não impede configuração: bastaria
    alguém apontar `SPARK_LLM_URL` para o i9 "para resolver rápido" e a regra
    viraria comentário.
    """
    with pytest.raises(SystemExit) as e:
        S._conferir_endpoint("http://100.115.117.49:8081/v1")
    assert "i9" in str(e.value), "a recusa precisa dizer QUAL máquina foi barrada"
    # E a da Spark passa.
    S._conferir_endpoint("http://100.85.164.54:8000/v1")


# ─── nada é inventado ────────────────────────────────────────────────────────

def test_campo_que_nao_esta_no_texto_e_descartado(monkeypatch):
    """O modelo "arrumando" o texto é corrupção silenciosa da evidência.

    Aqui ele expande `R.` para `Rua` e completa o CEP. As duas coisas parecem
    ajuda e destroem a prova: quem canoniza `R.` -> `RUA` é a skill, com léxico,
    autoridade e support — não o palpite de um modelo, que ninguém pode auditar
    depois.
    """
    _dublar(monkeypatch, [[{
        "logradouro": "Rua Marechal Rondon",   # EXPANDIU — não está no texto
        "numero": "1199",
        "complemento": "",
        "bairro": "Niterói",
        "cep": "92120-999",                    # INVENTOU
        "cidade": "Canoas",
        "uf": "RS",
    }]])
    r = S.segmentar([MAPS], threads=1)[MAPS]
    assert r["logradouro"] == "", "logradouro expandido tinha de ser descartado"
    assert r["cep"] == "", "CEP inventado tinha de ser descartado"
    assert r["numero"] == "1199" and r["bairro"] == "Niterói"
    assert r["metodo"] == "revisar", "sem logradouro ancorado, o registro é para revisão"


def test_o_que_esta_no_texto_passa_inteiro(monkeypatch):
    """A conferência não pode ser tão dura que rejeite leitura correta.

    A comparação ignora acento e pontuação de propósito: a pergunta é se o
    modelo TROCOU PALAVRA, não se ele mexeu no ponto da abreviação.
    """
    _dublar(monkeypatch, [[{
        "logradouro": "R. Mal. Rondon", "numero": "1199", "complemento": "",
        "bairro": "Niterói", "cep": "92120-210", "cidade": "Canoas", "uf": "RS",
    }]])
    r = S.segmentar([MAPS], threads=1)[MAPS]
    assert r["metodo"] == "ia" and not r["motivo"]
    assert r["logradouro"] == "R. Mal. Rondon"
    assert r["cidade"] == "Canoas" and r["bairro"] == "Niterói"


def test_campo_descartado_deixa_rastro(monkeypatch):
    """`parcial` sem motivo seria rótulo sem recurso.

    Quem for revisar precisa ver o que o modelo tentou pôr ali — é o que
    distingue "o texto não tinha" de "o modelo inventou".
    """
    _dublar(monkeypatch, [[{
        "logradouro": "R. Mal. Rondon", "numero": "1199", "complemento": "SALA 9",
        "bairro": "Niterói", "cep": "92120-210", "cidade": "Canoas", "uf": "RS",
    }]])
    r = S.segmentar([MAPS], threads=1)[MAPS]
    assert r["metodo"] == "parcial"
    assert "complemento" in r["motivo"] and "SALA 9" in r["motivo"]
    assert r["complemento"] == ""


# ─── uma chamada por valor distinto ──────────────────────────────────────────

def test_endereco_repetido_e_lido_uma_vez_so(monkeypatch):
    """O mesmo endereço aparece em muitos POIs — galeria, prédio, rede.

    Segmentar linha a linha pagaria a mesma leitura milhares de vezes. É a
    mesma memoização por valor distinto que a própria skill documenta.
    """
    chamadas = _dublar(monkeypatch, [[
        {"logradouro": "R. Mal. Rondon", "numero": "1199", "complemento": "",
         "bairro": "Niterói", "cep": "", "cidade": "Canoas", "uf": "RS"},
    ]])
    S.segmentar([MAPS, MAPS, MAPS, " " + MAPS + " "], threads=1)
    assert len(chamadas) == 1, "houve mais de uma ida à Spark"
    assert chamadas[0] == [MAPS], "o endereço repetido não foi deduplicado"


# ─── lote que falha não some ─────────────────────────────────────────────────

def test_falha_da_spark_marca_cada_endereco_com_o_motivo(monkeypatch):
    """Buraco silencioso na base é pior que erro declarado.

    Sem isto, um lote que estourasse sumiria da saída e a contagem final diria
    "menos endereços distintos" sem que ninguém soubesse por quê.
    """
    def _explode(enderecos):
        raise ValueError("a Spark devolveu conteúdo vazio")

    monkeypatch.setattr(S, "_chamar", _explode)
    r = S.segmentar([MAPS], threads=1)[MAPS]
    assert r["metodo"] == "falhou"
    assert "conteúdo vazio" in r["motivo"]
    assert all(r[c] == "" for c in S.CAMPOS)


# ─── o vocabulário vem da skill ──────────────────────────────────────────────

def test_taxonomia_do_complemento_vem_da_skill_e_nao_de_copia():
    """Se os dois falarem vocabulários diferentes, o ganho morre na fronteira.

    O complemento que eu entrego com rótulo que a skill não conhece vira
    `aj_compl_identificador` — "valor sem rótulo", que ela mantém e sinaliza mas
    não usa. Cópia manual da lista envelheceria em silêncio: a v3.3.5 acabou de
    acrescentar ENTRADA, COMODO, COBERTURA, PORTARIA e PALAFITA do padrão CNEFE.
    """
    sys.path.insert(0, os.path.join(RAIZ, "skills", "ajuste-logradouro"))
    import complemento_organizador as CO

    vocab = S._vocabulario_complemento()
    assert vocab, "vocabulário vazio"
    for termo in vocab:
        assert termo in CO.ORDEM, f"{termo} não é componente da skill"
    for novo in ("ENTRADA", "COMODO", "COBERTURA", "PORTARIA", "PALAFITA"):
        assert novo in vocab, f"{novo} (CNEFE, v3.3.5) não chegou ao prompt"
    assert "{COMPONENTES}" not in S._prompt(), "o prompt saiu com o marcador cru"


# ─── o contrato com a skill ──────────────────────────────────────────────────

def test_numero_nao_e_normalizado_aqui(monkeypatch):
    """`S/N` e `KM 12` passam inteiros — quem classifica é a skill.

    A `ajuste-logradouro` tem `aj_num_tipo` (`NUMERO`/`FAIXA`/`KM`/`SN`) e a
    regra do CNEFE de separar número de modificador. Esvaziar `S/N` aqui
    destruiria a informação antes de ela chegar em quem sabe tipá-la — e a
    primeira versão do prompt fazia exatamente isso.
    """
    texto = "Av. Dep. Pinheiro Machado, S/N - Rodoviária, Parnaíba - PI"
    _dublar(monkeypatch, [[{
        "logradouro": "Av. Dep. Pinheiro Machado", "numero": "S/N",
        "complemento": "", "bairro": "Rodoviária", "cep": "",
        "cidade": "Parnaíba", "uf": "PI",
    }]])
    r = S.segmentar([texto], threads=1)[texto]
    assert r["numero"] == "S/N", "o número foi normalizado antes da skill"
    assert r["metodo"] == "ia"


# ─── o tipo de via, e só ele ─────────────────────────────────────────────────

def test_tipo_de_via_expandido_e_aceito_o_nome_trocado_nao():
    """A recusa estava certa e a consequência era cara demais.

    O modelo insiste em devolver `Avenida Boqueirão` onde o texto diz
    `Av. Boqueirão`. O grounding barrava — e o registro INTEIRO caía em
    `revisar`, sumindo da fonte. Numa amostra de Canoas isso derrubou endereços
    perfeitamente legíveis, e pelo único desvio que não importa: o tipo de via é
    exatamente o que a `ajuste-logradouro` canoniza na fase seguinte.

    A relaxação vale SÓ para o primeiro token. O nome continua intocável — é ele
    que identifica a via, e inventá-lo é o erro que nenhuma fase posterior pega.
    """
    aceita = [
        ("Avenida Boqueirao", "Av. Boqueirão, 1270 - Igara, Canoas - RS"),
        ("Rua Bolivia", "R. Bolívia, 91 - São José, Canoas - RS"),
    ]
    for valor, texto in aceita:
        ok, expandido = S._ancorado_logradouro(valor, texto)
        assert ok and expandido, f"{valor!r} devia passar marcado como expandido"

    barra = [
        ("Avenida Boa Vista", "Av. Boqueirão, 1270 - Igara"),      # nome trocado
        ("Rua Marechal Rondon", "R. Mal. Rondon, 1199 - Niterói"),  # nome expandido
        ("Avenida A", "Av. B, 10"),                                  # resto curto demais
    ]
    for valor, texto in barra:
        ok, _ = S._ancorado_logradouro(valor, texto)
        assert not ok, f"{valor!r} passou, e não devia"

    # E o caminho limpo continua limpo, sem marca de parcial.
    ok, expandido = S._ancorado_logradouro("Av. Boqueirão", "Av. Boqueirão, 1270")
    assert ok and not expandido


def test_numero_de_imovel_trocado_e_barrado(monkeypatch):
    """O erro mais perigoso que o modelo cometeu de verdade.

    Medido em Canoas: texto `Avenida Santos Ferreira, 2505` e o modelo devolveu
    `2515`. Duas vezes, em endereços diferentes. Número trocado não parece erro
    em lugar nenhum a jusante — vira chave de junção errada e, pior, vira
    evidência falsa no support do léxico, que é a moeda da prova.
    """
    texto = "Avenida Santos Ferreira, 2505 - Canoas, Canoas - RS, 92027-033"
    _dublar(monkeypatch, [[{
        "logradouro": "Avenida Santos Ferreira", "numero": "2515",
        "complemento": "", "bairro": "Canoas", "cep": "92027-033",
        "cidade": "Canoas", "uf": "RS",
    }]])
    r = S.segmentar([texto], threads=1)[texto]
    assert r["numero"] == "", "número trocado passou"
    assert "2515" in r["motivo"], "o valor inventado precisa ficar no rastro"
    assert r["metodo"] == "parcial"
