# -*- coding: utf-8 -*-
"""O passo Cadastur: leitura, LGPD e a regra de quem vira POI.

Estes testes NÃO tocam a rede nem o portal do MTur. O que a skill de origem faz
é problema dela — e ela tem o próprio `scripts/e2e_sintetico.py`, que roda
offline. O que se prova aqui é o que É nosso: como o arquivo dela vira linha no
nosso banco, o que se recusa a entrar, e quando um prestador vira ponto no mapa.
"""
from __future__ import annotations

import io
import json
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cad():
    return pytest.importorskip("cadastur")


# ── LGPD ─────────────────────────────────────────────────────────────────────

def test_guia_de_turismo_nao_esta_entre_os_conjuntos(cad):
    """Guia de turismo é PESSOA FÍSICA e não entra.

    O portal já criou conjunto novo duas vezes. Se a lista fosse negativa
    ("todos menos o guia"), um cadastro de pessoa física que nascesse amanhã
    entraria sozinho e ninguém veria.
    """
    assert not [d for d in cad.DATASETS_PJ if "guia" in d.lower()]
    assert len(cad.DATASETS_PJ) == 14


def test_pedir_o_guia_pela_linha_de_comando_e_recusado(cad, monkeypatch):
    """E recusado com o motivo, não ignorado em silêncio."""
    import sys
    monkeypatch.setattr(sys, "argv",
                        ["cadastur.py", "--datasets",
                         "prestadores-de-servicos-turisticos-guia-turismo_2",
                         "--uf", "RS"])
    with pytest.raises(SystemExit) as e:
        cad.main()
    assert "PESSOA F" in str(e.value).upper()


def test_dado_pessoal_nao_entra_nem_em_extras(cad):
    """`extras` é o balde do "tudo o que sobrar" — e é por ele que o CPF
    entraria se ninguém o barrasse explicitamente."""
    for campo in ("cpf", "data_nascimento", "nome_responsavel",
                  "email_administrador", "tipo_sanguineo"):
        assert campo in cad.PESSOAL
    # E nenhum deles tem coluna de destino.
    assert not (set(cad.MAPA) & cad.PESSOAL)


def test_a_tabela_nao_tem_coluna_para_dado_pessoal():
    """A migração é a última linha de defesa: sem coluna, não há descuido
    possível."""
    sql = io.open(RAIZ / "migrations" / "0026_cadastur_prestador.sql",
                  encoding="utf-8").read()
    # Só o corpo do `create table`, senão os comentários explicativos — que
    # citam CPF de propósito — derrubariam o teste.
    corpo = sql[sql.index("create table"):sql.index("comment on table")]
    corpo = re.sub(r"--[^\n]*", "", corpo)
    for proibida in ("cpf", "data_nascimento", "nome_social", "tipo_sanguineo"):
        assert not re.search(rf"^\s+{proibida}\s", corpo, re.M), \
            f"a tabela ganhou coluna `{proibida}` — dado pessoal sem finalidade"


# ── Leitura do dado sujo ─────────────────────────────────────────────────────

@pytest.mark.parametrize("entrada, numero, texto", [
    ("162", 162, "162"),
    ("  70 ", 70, "70"),
    ("não informado", None, "não informado"),
    ("", None, None),
    (None, None, None),
    ("nan", None, None),
    ("17000", 17000, "17000"),
    # Acima de 100 mil não é hotel, é digitação. O número sai, o texto fica.
    ("999999999", None, "999999999"),
])
def test_uh_e_leitos_guardam_numero_e_texto(cad, entrada, numero, texto):
    """UH e leitos são AUTODECLARADOS e vêm sujos — 64 valores não numéricos
    no 2T/2026. Guardar só o número perderia o que explica o outlier."""
    assert cad._num(entrada) == (numero, texto)


@pytest.mark.parametrize("entrada, ano", [
    ("2027-09-10", 2027),
    ("10/09/2027", 2027),
    ("2027-09-10T00:00:00", 2027),
    ("indeterminado", None),
    ("", None),
])
def test_validade_aceita_os_dois_formatos_e_nao_estoura(cad, entrada, ano):
    """A skill avisa que `validade` tem formato livre. Um parser que estoura
    aqui derruba a carga inteira por causa de uma linha."""
    data, texto = cad._data(entrada)
    assert (data.year if data else None) == ano
    if entrada:
        assert texto == entrada


# ── O endereço do Cadastur ───────────────────────────────────────────────────

@pytest.mark.parametrize("texto, rua, numero, bairro", [
    ("Getúlio Vargas 4861 Canoas Centro CEP: 9201024",
     "Getúlio Vargas", "4861", "Centro"),
    ("Avenida Getúlio Vargas 5075 Canoas Marechal Rondon CEP: 92020000",
     "Avenida Getúlio Vargas", "5075", "Marechal Rondon"),
    # Sem número acontece em 26% das linhas: a chave não se forma, e é o certo.
    ("Mathias Velho  Canoas Centro CEP: 92310300 RS",
     "Mathias Velho", None, "Centro"),
    # Município ausente do texto: NÃO se chuta a divisão.
    ("Rua do Bonfim  Pirenópolis", None, None, None),
])
def test_endereco_do_cadastur_e_lido_pela_ancora_do_municipio(
        texto, rua, numero, bairro):
    """O formato não tem separador nenhum — passá-lo pelo leitor do Maps
    devolvia o endereço inteiro como nome de rua, e o Cadastur entrava no
    cruzamento com ZERO chave de endereço."""
    cb = pytest.importorskip("cruzar_bases")
    assert cb.partes_cadastur(texto, "canoas") == (rua, numero, bairro)


def test_o_cadastur_e_uma_base_do_cruzamento():
    """Ele cruza como qualquer outra. Sem isto, o gerador não teria como saber
    o que já existe."""
    cb = pytest.importorskip("cruzar_bases")
    assert "cadastur" in cb.BASES
    assert "cadastur" in cb.TEXTO_LIVRE


def test_o_cadastur_nao_finge_ter_coordenada():
    """A consulta devolve lat/lng NULOS de propósito: a skill não geocodifica,
    e um valor inventado aqui viraria ponto errado com cara de ponto certo."""
    cb = pytest.importorskip("cruzar_bases")
    sql = cb.BASES["cadastur"]
    assert "null::double precision as lat" in sql
    assert "null::double precision as lng" in sql


# ── A regra de quem vira POI ─────────────────────────────────────────────────

def test_o_gerador_usa_o_escritor_unico_de_poi(cad):
    """Chama `realtime_ingest.ingerir_registro`, e não um INSERT próprio.

    É ele que conhece a deduplicação por nome+coordenada, o merge não-destrutivo
    e o resgate de filhos caros (fachada, foto baixada). Um INSERT daqui
    ignoraria os três e recriaria os bugs de agosto de 2026.
    """
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    assert "realtime_ingest.ingerir_registro(" in fonte
    assert not re.search(r"insert\s+into\s+.*\bpois\b", fonte, re.I), \
        "o gerador escreve em `pois` por fora do ingestor"


def test_a_migracao_separa_nao_virou_de_ninguem_tentou():
    """`sem_poi_motivo` nulo e `poi_id` nulo significam coisas diferentes, e
    confundi-las faz reprocessar eternamente o que não tem solução."""
    sql = io.open(RAIZ / "migrations" / "0026_cadastur_prestador.sql",
                  encoding="utf-8").read()
    assert "sem_poi_motivo" in sql
    assert "poi_id is null and sem_poi_motivo is null" in sql   # o índice da fila


def test_a_tabela_tem_rls_e_tenant_na_frente_do_indice():
    """RLS é avaliada por linha; sem `tenant_id` na frente do índice a policy
    força varredura completa e o isolamento vira o gargalo."""
    sql = io.open(RAIZ / "migrations" / "0026_cadastur_prestador.sql",
                  encoding="utf-8").read()
    assert "enable row level security" in sql
    assert "force  row level security" in sql
    for idx in re.findall(r"create index[^;]+?on comercialradar\.cadastur_prestador\s*\(([^)]+)\)",
                          sql, re.S):
        assert idx.strip().startswith("tenant_id"), \
            f"índice sem tenant_id na frente: ({idx.strip()})"


def test_a_ancora_de_coordenada_ignora_cruzamento_ambiguo(cad):
    """Ambíguo é registrado, não resolvido — é a regra do cruzamento.

    Herdar uma coordenada ambígua colocaria o ponto num de dois imóveis, com
    50% de chance, e mandaria alguém a campo no endereço errado sem nada
    indicando o risco.
    """
    assert "not ambiguo" in cad.ANCORA_DO_CRUZAMENTO


def test_a_ancora_prefere_o_documento_ao_endereco(cad):
    """CNPJ é chave exata; endereço é casamento. A ordem não é conveniência."""
    ordem = cad.ANCORA_DO_CRUZAMENTO
    i_cnpj = ordem.index("'cnpj_tratado' then 1")
    i_cad = ordem.index("'cadastro_cliente' then 2")
    assert i_cnpj < i_cad


# ── A tela ───────────────────────────────────────────────────────────────────

def test_o_passo_aparece_no_menu_de_etapas():
    html = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
    assert 'data-mode="cadastur"' in html
    assert 'id="sec-cadastur"' in html
    assert 'id="cad-municipio"' in html and 'id="cad-uf"' in html


def test_a_base_publica_nao_exige_poligono():
    """Ela vem por município, que é como o governo publica. Exigir um retângulo
    desenhado travaria o operador sem dizer por quê."""
    srv = io.open(RAIZ / "server.py", encoding="utf-8").read()
    # A REGRA, não a lista. A primeira versão casava o literal
    # `modo not in ("cadastur",)` e reprovou no dia em que `base_estadual`
    # entrou na mesma isenção — pelo mesmo motivo, e corretamente. Teste que
    # exige a tupla inteira impede acrescentar um caso legítimo.
    import re as _re
    m = _re.search(r"if not poly and modo not in \(([^)]*)\)", srv)
    assert m, "a guarda de polígono sumiu do server.py"
    isentos = [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()]
    assert "cadastur" in isentos, f"o Cadastur voltou a exigir polígono: {isentos}"
    js = io.open(RAIZ / "frontend" / "app.js", encoding="utf-8").read()
    assert 'const precisaArea = modo !== "cadastur"' in js


def test_o_servidor_exige_municipio_e_uf():
    srv = io.open(RAIZ / "server.py", encoding="utf-8").read()
    trecho = srv[srv.index('elif modo == "cadastur"'):]
    trecho = trecho[:trecho.index("else:")]
    assert "if not municipio or len(uf) != 2" in trecho
    assert "cadastur.py" in trecho
