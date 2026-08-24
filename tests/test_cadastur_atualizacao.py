# -*- coding: utf-8 -*-
"""Atualização incremental, âncora por CNEFE, total de pessoa física e o ciclo
que o chat passou a fechar.

Nada aqui toca a rede nem o portal do MTur. O que se prova é a REGRA — quando um
prestador vira ponto, de onde a coordenada pode vir, o que se recusa a entrar no
banco, e o que acontece com quem sai do cadastro.
"""
from __future__ import annotations

import io
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cad():
    return pytest.importorskip("cadastur")


@pytest.fixture(scope="module")
def cb():
    return pytest.importorskip("cruzar_bases")


# ── A skill já atualizava; nós é que não usávamos ────────────────────────────

def test_a_skill_traz_os_eventos_de_entidade():
    """`eventos_entidade.csv.gz` é o que torna a atualização incremental
    possível. Se a skill for trocada por uma versão sem isso, a carga volta a
    regravar 388 mil linhas por execução — e nada acusaria."""
    hist = RAIZ / "skills" / "extracao-cadastur-mtur" / "cadastur" / "historico.py"
    fonte = io.open(hist, encoding="utf-8").read()
    for evento in ("ENTROU", "SAIU", "PERMANECEU", "ALTEROU"):
        assert evento in fonte, f"a skill deixou de classificar {evento}"
    assert "eventos_entidade.csv.gz" in fonte


def test_inalterado_nao_e_regravado(cad):
    linhas = [
        {"_dataset": "meios-de-hospedagem", "cnpj": "11.111.111/0001-11"},
        {"_dataset": "meios-de-hospedagem", "cnpj": "22222222000122"},
        {"_dataset": "meios-de-hospedagem", "cnpj": "33333333000133"},
    ]
    eventos = {
        ("meios-de-hospedagem", "11111111000111"): "PERMANECEU",
        ("meios-de-hospedagem", "22222222000122"): "ALTEROU",
        ("meios-de-hospedagem", "33333333000133"): "ENTROU",
    }
    manter, pulou = cad.filtrar_por_evento(linhas, eventos)
    assert pulou == 1
    assert {r["cnpj"] for r in manter} == {"22222222000122", "33333333000133"}


def test_sem_eventos_grava_tudo(cad):
    """Primeira execução não tem baseline: filtrar ali deixaria o banco vazio."""
    linhas = [{"_dataset": "x", "cnpj": "1"}, {"_dataset": "x", "cnpj": "2"}]
    manter, pulou = cad.filtrar_por_evento(linhas, {})
    assert manter == linhas and pulou == 0


def test_quem_sai_nao_e_apagado():
    """O arquivo do MTur só traz quem está regular. Sumir é EVENTO — pode ser
    fechamento, troca de dono ou renovação atrasada —, e apagar destruiria o
    sinal justamente quando ele aparece."""
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def marcar_saidas"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "saiu_em" in corpo
    assert not re.search(r"\bdelete\s+from\b", corpo, re.I), \
        "`marcar_saidas` apaga linha — sumir do snapshot não é motivo para isso"


def test_quem_volta_deixa_de_estar_ausente():
    """Renovação atrasada é comum. Sem limpar `saiu_em`, o painel acusaria para
    sempre uma baixa que já foi desfeita."""
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def marcar_saidas"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "saiu_em = null" in corpo


# ── Pessoa física: contada, não guardada ─────────────────────────────────────

def test_pessoa_fisica_e_contada_e_nao_guardada(cad):
    """A regra que o usuário pediu: descartar como linha, manter como total."""
    assert cad.DATASETS_PF, "o conjunto de pessoa física sumiu"
    assert not set(cad.DATASETS_PF) & set(cad.DATASETS_PJ), \
        "o mesmo conjunto está nas duas listas — uma delas guarda linha"

    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def contar_pf"):]
    corpo = corpo[:corpo.index("\n# ──")]
    # A contagem NÃO pode gravar em `cadastur_prestador`.
    assert "cadastur_prestador" not in corpo
    assert "cadastur_total_pf" in fonte


def test_a_tabela_de_totais_nao_identifica_ninguem():
    """Contar não é tratar dado pessoal — desde que não haja coluna que
    identifique. Esta é a linha de defesa que sobrevive a qualquer refatoração
    do Python."""
    sql = io.open(RAIZ / "migrations" / "0027_cadastur_atualizacao_e_totais.sql",
                  encoding="utf-8").read()
    corpo = sql[sql.index("create table if not exists comercialradar.cadastur_total_pf"):]
    corpo = corpo[:corpo.index("comment on table")]
    corpo = re.sub(r"--[^\n]*", "", corpo)
    for proibida in ("cpf", "nome", "endereco", "email", "telefone",
                     "data_nascimento", "documento"):
        assert not re.search(rf"^\s+{proibida}\b", corpo, re.M), \
            f"`cadastur_total_pf` ganhou a coluna `{proibida}` — deixou de ser total"
    assert "quantidade" in corpo


# ── A âncora por CNEFE ───────────────────────────────────────────────────────

def test_o_nivel_de_coordenada_tem_piso(cad):
    """Só nível de ENDEREÇO entra: 1 (colhida ali) e 2 (apto no mesmo número).

    3 é endereço ESTIMADO — o IBGE não tinha coordenada original ou ela era
    inválida. 4 é a FACE DA QUADRA, não a porta. 5 é a localidade e 6 é o
    centróide do setor.

    Medido: 1 e 2 cobrem de 97% a 99% dos endereços das cidades carregadas, e
    aceitar 3 e 4 resgataria zero prestadores. O piso não custa cobertura.
    """
    assert cad.NV_ACEITO == ("1", "2")


def test_a_semantica_do_nivel_esta_escrita_certa(cad):
    """Já documentei isto errado uma vez — trocando localidade (5) e setor (6)
    por 3 e 4, o que fazia a regra parecer mais dura do que os dados justificam.

    O domínio é do `Dicionario_CNEFE_Censo_2022.xls` e não é opinião; deixá-lo
    escrito onde a decisão é tomada é o que impede a próxima leitura de repetir
    o erro.
    """
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    bloco = fonte[fonte.index("`nv_geo_coord` diz de onde"):]
    bloco = bloco[:bloco.index("NV_ACEITO")]
    for nivel, classe in ((1, "ENDERECO_ORIGINAL"), (2, "ENDERECO_MODIFICADO"),
                          (3, "ENDERECO_ESTIMADO"), (4, "FACE_QUADRA"),
                          (5, "LOCALIDADE"), (6, "SETOR_CENSITARIO")):
        assert re.search(rf"^#\s+{nivel}\s+{classe}\b", bloco, re.M), \
            f"o nível {nivel} deixou de estar descrito como {classe}"


@pytest.mark.parametrize("nome, esperado", [
    # Título que o CNEFE guarda em coluna separada
    ("General Flores da Cunha", {"general flores da cunha", "flores da cunha"}),
    ("Dona Cecília", {"dona cecilia", "cecilia"}),
    # Conectivo que aparece num lado e não no outro
    ("Avenida do Carvalho", {"do carvalho", "carvalho"}),
    # Sem título nem conectivo: uma variante só
    ("Getúlio Vargas", {"getulio vargas"}),
])
def test_variantes_de_via_reconciliam_as_duas_bases(cad, nome, esperado):
    """NÃO é similaridade: cada variante é uma string exata e o casamento
    continua sendo igualdade. É a única forma honesta de reconciliar duas bases
    que escrevem o mesmo logradouro de jeitos diferentes sem inventar um score
    de 0,87 que ninguém sabe interpretar."""
    assert cad._variantes_via(nome) == esperado


def test_ambiguidade_e_recusada_e_nao_resolvida(cad):
    """Em Cachoeirinha existem uma AVENIDA e uma RUA "Flores da Cunha" —
    logradouros diferentes, mesmo nome. Escolher a primeira produziria base
    limpa e ponto errado."""
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def _coordenada_por_cnefe"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "len(identidades) != 1" in corpo, \
        "a busca no CNEFE deixou de recusar via ambígua"


@pytest.mark.parametrize("texto, rua, numero", [
    # O complemento roubava o número: pegava o "1" de "SALA 1".
    ("General Flores da Cunha 2586 LOJA 3 SOBRE LOJA SALA 1 Canoas Centro",
     "General Flores da Cunha", "2586"),
    ("Frederico Augusto Ritter 5255 casa 103 Canoas Centro",
     "Frederico Augusto Ritter", "5255"),
    ("Getúlio Vargas 4861 Canoas Centro CEP: 9201024",
     "Getúlio Vargas", "4861"),
])
def test_complemento_nao_rouba_o_numero(cb, texto, rua, numero):
    lido_rua, lido_num, _ = cb.partes_cadastur(texto, "canoas")
    assert (lido_rua, lido_num) == (rua, numero)


def test_a_precisao_da_coordenada_e_declarada():
    """Coordenada de CNPJ e coordenada de endereço não têm a mesma precisão, e
    quem vai a campo precisa saber qual é."""
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    assert "cnefe:porta" in fonte and "cnefe:porta_face" in fonte
    assert 'f"cadastur+{origem_geo}"' in fonte, \
        "o POI voltou a declarar uma origem fixa de coordenada"


# ── Encadeamento ─────────────────────────────────────────────────────────────

def test_o_encadeamento_usa_os_processos_que_ja_existem(cad):
    """Cruzar e enriquecer são invocados como SUBPROCESSO. Reimplementar
    qualquer um aqui criaria uma segunda versão que envelheceria em silêncio."""
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def encadear"):]
    corpo = corpo[:corpo.index("\n# ── CLI")]
    assert "cruzar_bases.py" in corpo
    assert "enriquecer_tudo.py" in corpo
    assert "--poi-ids" in corpo


def test_cruza_de_novo_depois_de_gerar(cad):
    """Sem a segunda passada, o ponto recém-nascido não cruza com o cadastro de
    imóveis do cliente — e é esse cruzamento que diz se ele já é cobrado como
    comercial."""
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def encadear"):]
    corpo = corpo[:corpo.index("\n# ── CLI")]
    assert corpo.index("cruzar_bases.py") < corpo.index("enriquecer_tudo.py"), \
        "o cruzamento tem de acontecer ANTES do enriquecimento"


def test_o_enriquecimento_aceita_alvo():
    """Encadear depois de gerar 22 POIs não pode significar varrer os 27 mil do
    banco para alcançar aqueles 22."""
    enr = pytest.importorskip("enriquecer_tudo")
    import inspect
    assert "alvo" in inspect.signature(enr.carregar_carentes).parameters
    assert "alvo" in inspect.signature(enr._sem_streetview_na_area).parameters


def test_poi_ids_vazio_nao_vira_enriquecer_tudo():
    """Pedir alvo e receber lista vazia seria a diferença entre 22 POIs e 27
    mil, decidida por um erro de digitação."""
    fonte = io.open(RAIZ / "enriquecer_tudo.py", encoding="utf-8").read()
    assert "não tem nenhum id válido" in fonte


# ── O ciclo que o chat passou a fechar ───────────────────────────────────────

def test_o_chat_ganhou_o_degrau_de_guardar():
    ag = pytest.importorskip("agente_local")
    assert "guardar_ponto" in ag.FERRAMENTAS
    assert "guardar_ponto" in [e["function"]["name"] for e in ag.ESQUEMA]


def test_guardar_ponto_recusa_sem_coordenada():
    """Ponto sem posição não leva ninguém a lugar nenhum, e um marcador no
    centro da cidade custa uma visita perdida."""
    fp = pytest.importorskip("ferramenta_ponto")
    r = fp.guardar_ponto(nome="Qualquer Coisa")
    assert "erro" in r and "coordenada" in r["erro"]


def test_guardar_ponto_usa_o_escritor_unico():
    """O mesmo motivo do gerador do Cadastur: é ele que conhece a deduplicação,
    o merge não-destrutivo e o resgate de fachada e foto já pagas."""
    fonte = io.open(RAIZ / "ferramenta_ponto.py", encoding="utf-8").read()
    assert "realtime_ingest.ingerir_registro(" in fonte
    assert not re.search(r"insert\s+into\s+.*\bpois\b", fonte, re.I)


def test_guardar_ponto_captura_fachada_e_cruza():
    """As duas etapas que o usuário pediu para existir também no caminho do
    chat."""
    fonte = io.open(RAIZ / "ferramenta_ponto.py", encoding="utf-8").read()
    assert "streetview_capture" in fonte
    assert "cruzar_bases.py" in fonte


def test_a_falha_de_fachada_nao_derruba_o_cadastro():
    """Trocar um POI bom por um erro de captura seria o pior negócio possível."""
    fonte = io.open(RAIZ / "ferramenta_ponto.py", encoding="utf-8").read()
    corpo = fonte[fonte.index("def _fachada"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "except Exception" in corpo


def test_sem_panorama_e_falha_de_captura_sao_distinguidos():
    """Uma é definitiva, a outra volta na fila. Confundi-las manda o operador
    desistir de algo que ia funcionar na tentativa seguinte."""
    fonte = io.open(RAIZ / "ferramenta_ponto.py", encoding="utf-8").read()
    assert "sem_panorama" in fonte and "na_fila" in fonte


# ── A tela ───────────────────────────────────────────────────────────────────

def test_o_botao_diz_atualizar_na_base_publica():
    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    assert "Atualizar fonte" in js


def test_o_card_de_pessoa_fisica_e_totalizador():
    """Sem lista, sem link, sem "ver detalhes" — o detalhe não existe no banco
    de propósito."""
    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    corpo = js[js.index("const pf = (d.pessoa_fisica"):]
    corpo = corpo[:corpo.index("if (d.periodo)")]
    assert "<a " not in corpo, "o card de pessoa física ganhou um link"
    assert "Guardamos apenas este total" in corpo


def test_a_rota_de_resumo_existe():
    srv = io.open(RAIZ / "server.py", encoding="utf-8").read()
    assert '@app.get("/api/cadastur/resumo")' in srv
    # e NÃO é pública: o portão fecha /api/* por padrão
    trecho = srv[srv.index("PUBLICAS = {"):]
    trecho = trecho[:trecho.index("}")]
    assert "cadastur" not in trecho
