# -*- coding: utf-8 -*-
"""A ponte entre o nosso banco e a skill `ajuste-logradouro`.

A skill é rigorosa com o que recebe, e por bons motivos: sem autoridade
declarada ela se recusa a escolher entre duas grafias; sem `scope_id` por linha
ela pode gravar conhecimento de um município dentro do léxico de outro; com
`record_id` repetido ela conta o mesmo imóvel duas vezes no support, que é a
moeda da prova.

Nada disso aparece como erro na hora — aparece como léxico levemente errado três
municípios depois. Estes testes cobram o contrato no ponto em que ele é montado.
"""
import csv
import os
import sys

import pytest
import yaml

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import ajuste_logradouro as AL  # noqa: E402


@pytest.fixture()
def cfg(tmp_path):
    contagem = {"cnefe": 176899, "cadastro": 102065, "pois": 20934, "ifood": 770}
    caminho = AL.escrever_config("4304606", tmp_path, contagem)
    return yaml.safe_load(caminho.read_text(encoding="utf-8"))


# ─── a autoridade ────────────────────────────────────────────────────────────

def test_so_o_cnefe_e_autoridade(cfg):
    """Detectar que dois tokens são equivalentes e decidir qual é o certo são
    perguntas diferentes — misturá-las foi o que produziu `SILVA→SILVAA` na
    história da skill.

    A ordem dela é: fonte com autoridade -> dominância de frequência ->
    INDEFINIDO. Sem autoridade declarada, uma equivalência genuína entre formas
    igualmente frequentes NÃO é aprendida. Se o `cadastro` virasse autoridade
    por descuido, a grafia do cliente passaria a mandar no CNEFE — e a base
    oficial existe exatamente para não depender disso.
    """
    com_autoridade = [f["source_id"] for f in cfg["fontes"] if "autoridade_nivel" in f]
    assert com_autoridade == ["cnefe"], \
        f"autoridade em fonte errada: {com_autoridade}"
    assert cfg["fontes"][0]["source_id"] == "cnefe", \
        "o CNEFE deve vir primeiro — é a âncora da decisão"


# ─── o escopo ────────────────────────────────────────────────────────────────

def test_o_scope_e_o_municipio_e_o_lexico_e_dele(cfg):
    """Léxico é por município de propósito: sobrenome raro numa cidade não é
    sobrenome errado na outra.

    O `lexico_path` precisa carregar o código no nome. Um caminho fixo faria
    duas cidades compartilharem estado sem ninguém notar — e o sintoma seria
    uma equivalência aprendida em Canoas sendo aplicada em Parnaíba.
    """
    assert cfg["scope_id"] == "4304606"
    assert "4304606" in cfg["lexico_path"], \
        f"o léxico não é do município: {cfg['lexico_path']}"
    assert cfg["aprendizado"] is True, "aprendizado precisa ser explícito"


def test_toda_linha_leva_o_scope(tmp_path):
    """A skill exige `scope_id` por LINHA quando ele tem 7 dígitos, e valida
    100% delas contra o município declarado. É a trava que impede gravar dados
    de um município dentro do léxico de outro."""
    arq = tmp_path / "x.csv"
    AL._gravar_csv(arq, [("1", "RUA A", "10", "", -29.9, -51.1)], "4304606")
    linhas = list(csv.DictReader(arq.open(encoding="utf-8")))
    assert linhas[0]["scope_id"] == "4304606"


# ─── o esquema ───────────────────────────────────────────────────────────────

def test_o_csv_tem_exatamente_as_colunas_canonicas(tmp_path):
    """O de-para da config mapeia coluna->campo canônico pelo mesmo nome.

    Se o CSV sair com outra coluna, a skill aborta na validação (código 2) —
    o que é melhor que processar com uma coluna zerada, mas o lugar de descobrir
    isso é aqui, e não depois de exportar 280 mil linhas.
    """
    arq = tmp_path / "x.csv"
    AL._gravar_csv(arq, [("1", "RUA A", "10", "SALA 2", -29.9, -51.1)], "4304606")
    with arq.open(encoding="utf-8") as f:
        cabecalho = next(csv.reader(f))
    assert tuple(cabecalho) == AL.CANONICAS


def test_coordenada_nula_vira_vazio_e_nao_a_palavra_None(tmp_path):
    """`None` escrito em CSV vira a string "None", que a skill leria como
    coordenada inválida com um nome esquisito no relatório. Vazio é o que
    significa ausente."""
    arq = tmp_path / "x.csv"
    AL._gravar_csv(arq, [("1", "RUA A", "10", "", None, None)], "4304606")
    linha = next(csv.DictReader(arq.open(encoding="utf-8")))
    assert linha["lat"] == "" and linha["lon"] == ""


# ─── fonte vazia não entra ───────────────────────────────────────────────────

def test_fonte_sem_linha_fica_fora_da_config(tmp_path):
    """POI sem endereço segmentado ainda é caso comum — a leitura pela IA é um
    passo à parte e pode não ter rodado.

    Declarar uma fonte cujo CSV só tem cabeçalho faz a skill validar e reclamar
    de uma fonte que não existe. Melhor não declarar: a ausência já é dita no
    aviso do exportador.
    """
    cfg = yaml.safe_load(AL.escrever_config(
        "4304606", tmp_path, {"cnefe": 100, "cadastro": 50, "pois": 0, "ifood": 0}
    ).read_text(encoding="utf-8"))
    ids = [f["source_id"] for f in cfg["fontes"]]
    assert ids == ["cnefe", "cadastro"], f"fonte vazia entrou: {ids}"


# ─── o defeito que a skill achou em nós ──────────────────────────────────────

def test_o_cnefe_sai_deduplicado_por_codigo():
    """`cod_unico_endereco` NÃO é único na nossa carga: 52 repetidos em 6.000
    na primeira execução (0,9%), e a skill acusou.

    Registro repetido faz o mesmo endereço votar duas vezes no support — e duas
    linhas com o mesmo código não são dois imóveis. Sem isto o léxico aprende de
    eco: uma equivalência ganha `support 2` a partir de uma evidência só.

    A ordem completa no `order by` não é enfeite: sem ela o `distinct on` pode
    escolher linhas diferentes em duas execuções, e a skill depende de
    idempotência.
    """
    sql = " ".join(AL.SQL_CNEFE.split())
    assert "distinct on (cod_unico_endereco)" in sql
    assert "order by cod_unico_endereco" in sql


# ─── o que volta para o banco ────────────────────────────────────────────────

def test_a_marcacao_nao_substitui_o_original():
    """A skill MARCA e nunca sobrescreve, e essa propriedade tem de sobreviver à
    volta para o banco.

    Por isso `logradouro_original` viaja junto e a tabela é separada das de
    cadastro: quem aplica a correção é o processo a jusante, olhando o tier —
    não a ingestão.
    """
    sql = " ".join(open(os.path.join(RAIZ, "migrations",
                                     "0031_logradouro_ajustado.sql"),
                        encoding="utf-8").read().split())
    assert "logradouro_original" in sql and "logradouro_marcado" in sql
    assert "tier" in sql and "run_id" in sql, \
        "sem tier e run_id a marcação não é aplicável nem auditável"
    # E a exceção de RLS precisa estar ESCRITA, não presumida: a regra da 0029
    # cobra a política de toda tabela que tenha tenant_id.
    assert "tenant_id" in sql, "a ausência de tenant_id precisa estar justificada no arquivo"
