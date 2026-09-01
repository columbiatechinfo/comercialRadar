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
    sql = io.open(RAIZ / "migrations_a2l" / "0001_radar_comercial.sql",
                  encoding="utf-8").read()
    # Só o corpo do `create table`, senão os comentários explicativos — que
    # citam CPF de propósito — derrubariam o teste.
    # O CORPO DA TABELA, e so ele. Antes a fatia ia de "create table" ate
    # "comment on table" — funcionava quando o arquivo tinha UMA tabela.
    # O schema novo tem 28 no mesmo arquivo, entao a fatia pegava tudo.
    i = sql.index("create table if not exists cadastur_prestador (")
    corpo = sql[i:sql.index(");", i)]
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
    confundi-las faz reprocessar eternamente o que não tem solução.

    Lê a 0015, e não a 0001: a 0001 é histórica e ainda descreve as colunas
    dentro da base — ela continuaria passando com o defeito de pé.
    """
    sql = io.open(RAIZ / "migrations_a2l" /
                  "0015_o_vinculo_do_cadastur_sai_da_base.sql",
                  encoding="utf-8").read()
    assert "sem_poi_motivo" in sql
    assert "cadastur_vinculo" in sql


def test_a_base_nao_guarda_escrituracao_do_sistema():
    """O código não pode escrever `poi_id`/`sem_poi_motivo` dentro da BASE.

    Foi o defeito de 01/09/2026: `resources_root.cadastur_prestador` guardava
    o que o sistema concluiu, a limpeza dos dados de teste não alcançava aquilo
    (limpar tabela base é proibido, e com razão), e 102 linhas ficaram
    apontando para POIs apagados. A etapa 3 então disse "pendentes 0" numa
    cidade com 185 prestadores — sem falhar, sem avisar.

    Não há chave estrangeira possível entre schemas de donos diferentes, então
    o ponteiro morto era estruturalmente impossível de impedir. A correção foi
    mover o vínculo para `radar_comercial.cadastur_vinculo`, onde a FK existe.
    """
    fonte = io.open(RAIZ / "cadastur.py", encoding="utf-8").read()
    escreve_na_base = re.search(
        r"update\s+resources_root\.cadastur_prestador[^\"']*?\bset\b[^\"']*?"
        r"(poi_id|sem_poi_motivo|cruzado_em)", fonte, re.I | re.S)
    assert not escreve_na_base, \
        "o codigo escreve escrituracao do sistema dentro da tabela base"
    assert "radar_comercial.cadastur_vinculo" in fonte, \
        "o vinculo deve ser gravado em radar_comercial, nao na base"


def test_a_tabela_tem_rls_e_tenant_na_frente_do_indice():
    """RLS é avaliada por linha; sem `id_empresa` na frente do índice a policy
    força varredura completa e o isolamento vira o gargalo."""
    sql = io.open(RAIZ / "migrations_a2l" / "0001_radar_comercial.sql",
                  encoding="utf-8").read()
    assert "enable row level security" in sql
    assert "force  row level security" in sql
    for idx in re.findall(r"create index[^;]+?on comercialradar\.cadastur_prestador\s*\(([^)]+)\)",
                          sql, re.S):
        assert idx.strip().startswith("id_empresa"), \
            f"índice sem id_empresa na frente: ({idx.strip()})"


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

def test_o_cadastur_virou_ETAPA_da_mineracao_e_nao_entrada_de_menu():
    """A mineração passou a ser um processo só — decisão do dono do produto em
    25/08/2026.

    Este teste cobrava o oposto: que "Bases públicas" fosse uma entrada própria
    no menu. Duas entradas sugeriam caminhos ALTERNATIVOS, e não são: a base
    pública não sabe do comércio que abriu mês passado, a captura não vê o que
    não tem marcador no mapa, e o Cadastur é a única fonte com capacidade
    declarada. Cada uma enxerga o que as outras não veem.

    O que se cobra agora é que o passo não tenha sumido junto com o botão: ele
    é a etapa 3 de `minerar_tudo.py`, e continua alcançável sozinho pelo CLI.
    """
    orq = io.open(RAIZ / "minerar_tudo.py", encoding="utf-8").read()
    assert "cadastur.py" in orq, "o Cadastur sumiu da mineração em vez de virar etapa"
    assert "--pular-cadastur" in orq, "sem saída para pular o passo numa rodada"

    html = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
    assert 'data-mode="cadastur"' not in html,         "a entrada separada voltou ao menu; o processo e um so"
    # E a mineração precisa DIZER que faz tudo — senão o operador procura o
    # botão que não existe mais.
    import re as _re
    bloco = _re.search(r'data-mode="mineracao".*?</button>', html, _re.S)
    assert bloco and "Cadastur" in bloco.group(0),         "o menu nao diz que a mineracao inclui o Cadastur"


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


def test_a_base_nacional_nao_e_baixada_a_cada_mineracao():
    """Regra do dono do produto, 25/08/2026: baixa UMA VEZ, depois só quando
    ele mandar — igual às demais bases públicas grandes.

    A primeira versão da etapa 3 chamava o Cadastur sem `--so-carregar`, e ele
    baixava os 26 recursos FEDERAIS a cada área minerada. O recorte por
    município acontece DEPOIS do download, então o custo não diminuía com a
    área: minerar três bairros da mesma cidade no mesmo dia baixaria a base do
    país três vezes.

    A mineração passa a só CONSULTAR o que está em disco; o download mora num
    botão do painel, deliberado.
    """
    orq = io.open(RAIZ / "minerar_tudo.py", encoding="utf-8").read()
    # A ancora e a CHAMADA da etapa, nao o rotulo "3/7" — esse e montado em
    # tempo de execucao por `_etapa` e nao existe no fonte.
    i = orq.index("_etapa(3,")
    trecho = orq[i:i + 2600]
    assert '"--so-carregar"' in trecho, \
        "a mineração voltou a BAIXAR o Cadastur em vez de consultar o snapshot"
    assert '"--gerar"' in trecho, \
        "sem --gerar o Cadastur carrega e cruza, e não vira POI: sucesso vazio"
    assert "cadastur_baixado()" in trecho, \
        "a etapa não confere se o snapshot existe antes de tentar usá-lo"

    # E o caminho para baixar tem de existir, senão a regra vira um beco: quem
    # nunca baixou fica sem saída.
    html = io.open(RAIZ / "frontend" / "index.html", encoding="utf-8").read()
    assert 'id="btn-base-cadastur"' in html, "sumiu o botão de baixar/atualizar"
    srv = io.open(RAIZ / "server.py", encoding="utf-8").read()
    assert 'modo == "base_cadastur"' in srv, "a rota do download não existe"
    assert '"--refresh"' in srv[srv.index('modo == "base_cadastur"'):][:2000], \
        "não há como pedir dado NOVO: atualizar viraria sinônimo de reaproveitar"
