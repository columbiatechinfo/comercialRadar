# -*- coding: utf-8 -*-
"""Desfazer uma fusão devolve o ponto E a evidência dele.

POR QUE ISTO EXISTE

`--recruzar` reavalia os pares, mas só entre POIs ATIVOS: quem já foi absorvido
está fora do `carregar`. Uma fusão errada era, por isso, definitiva — nenhuma
regra escrita depois a alcançava. Foi o caso do `ParkShoppingCanoas`, absorvido
pela `Pista de Patinação (Iceland)` de dentro dele; a regra de multiloja teria
impedido, e não havia como desfazer.

`--desfundir` resolve isso, e só passou a ser possível com a migração 0036: até
ela, absorver sobrescrevia `pois.status` — que guarda a ORIGEM do ponto — com a
palavra `fundido`, e o valor anterior se perdia.

OS TESTES RODAM CONTRA O BANCO, DENTRO DE UMA TRANSAÇÃO QUE É DESFEITA

`_desfundir` é quase todo SQL: índice parcial, janela, `execute_values`. Um
dublê de cursor testaria a minha imitação do Postgres, não o Postgres — e foram
justamente as regras REAIS do banco que derrubaram três versões desta função:

    duplicate key ... (IGREJA NOSSA SENHORA DO ROSÁRIO, ...)   o índice único
    duplicate key ... (LABORATÓRIO DE ANATOMIA, ULBRA ...)     o lote contra si
    10.690 sobreviventes ocos                                  a evidência ida
"""
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cruzar_fontes as cf  # noqa: E402


@pytest.fixture
def banco():
    """Conexão sem autocommit; o teste termina em `rollback` sempre."""
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        pytest.skip("banco indisponível")
    con.autocommit = False
    try:
        yield con
    finally:
        con.rollback()
        con.close()


def _ocos(cur):
    cur.execute("""select p.id from pois p where p.fundido_em is null
                     and not exists (select 1 from vinculo_poi v
                                      where v.poi_id = p.id
                                        and v.estado = 'vinculado')""")
    return {r[0] for r in cur.fetchall()}


def _orfaos(cur):
    cur.execute("""select count(*) from vinculo_poi v join pois p on p.id = v.poi_id
                    where p.fundido_em is not null and v.estado = 'vinculado'""")
    return cur.fetchone()[0]


def test_desfazer_nao_deixa_vinculo_apontando_para_fundido(banco):
    """O invariante que vale para qualquer caminho: vínculo ativo mora em ponto
    ativo. Desfazer não pode criar o mesmo estrago que a fusão em cadeia criou."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    cf._desfundir(cur, "Canoas")
    assert _orfaos(cur) == 0


def test_o_sobrevivente_nao_fica_sem_evidencia(banco):
    """O DEFEITO QUE ESTE TESTE GUARDA, medido em 27/08/2026.

    Quando os dois POIs tinham o mesmo nome — o caso comum, porque é o nome
    igual que os fez fundir — o sobrevivente fica com DOIS vínculos daquele
    nome: o dele e o que veio do absorvido. A primeira versão devolvia os dois.

        13.252 fusões desfeitas  →  11.314 pontos ocos
        e 10.690 deles eram SOBREVIVENTES

    Ou seja: desfazer esvaziava quem ficou. Na tela seriam 10.690 pontos sem
    telefone, sem site e sem endereço, e nada diria por quê."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    cur.execute("""select distinct fundido_para from pois
                    where fundido_em is not null and fundido_para is not null
                      and cidade ilike 'canoas%'""")
    vivos = {r[0] for r in cur.fetchall()}
    if not vivos:
        pytest.skip("nenhuma fusão com destino gravado para exercitar")

    antes = _ocos(cur)
    cf._desfundir(cur, "Canoas")
    novos = _ocos(cur) - antes
    assert not (novos & vivos), \
        f"{len(novos & vivos)} sobreviventes ficaram sem evidência ao desfazer"


def test_duplicata_literal_nao_volta(banco):
    """`pois_sem_duplicata` proíbe dois ativos com o mesmo nome e endereço.
    Devolver ao mapa quem foi absorvido por ser cópia literal recria a
    duplicata — e o banco recusa a transação inteira, não só a linha."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    cf._desfundir(cur, "Canoas")          # não pode levantar UniqueViolation
    cur.execute("""select count(*) from (
                     select 1 from pois where fundido_em is null
                      group by upper(trim(nome)), upper(trim(endereco))
                     having count(*) > 1) t""")
    assert cur.fetchone()[0] == 0, "desfazer criou duplicata de nome + endereço"


def test_sem_destino_gravado_nao_desfaz(banco):
    """Ponto absorvido é melhor que ponto oco.

    Sem `fundido_para` não há como recuperar a evidência: ela está num vínculo
    que a fusão moveu, e não se sabe para onde. O ponto voltaria invisível — o
    mapa exige vínculo ativo. Medido no primeiro ensaio: 15.388 assim."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    cur.execute("""select count(*) from pois
                    where fundido_em is not null and fundido_para is null""")
    sem_destino = cur.fetchone()[0]
    cf._desfundir(cur, "Canoas")
    cur.execute("""select count(*) from pois
                    where fundido_em is not null and fundido_para is null""")
    assert cur.fetchone()[0] == sem_destino, \
        "fusão sem destino gravado foi desfeita — o ponto volta sem evidência"


def test_o_carimbo_de_cruzado_sai_dos_dois_lados(banco):
    """Sem isso o par volta ao mapa e é PULADO na comparação seguinte, por já
    ter sido visto — desfazer não teria servido para nada."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    # QUEM DE FATO VOLTOU, e não um par qualquer. `_desfundir` passou a pular o
    # que o processo refaria — 10.769 de 13.252 em Canoas —, e a versão anterior
    # deste teste sorteava um par com `limit 1` e falhava quando calhava de ser
    # um dos que ficam.
    cur.execute("""select id, fundido_para from pois
                    where fundido_em is not null and fundido_para is not null
                      and cidade ilike 'canoas%'""")
    candidatos = dict(cur.fetchall())
    if not candidatos:
        pytest.skip("nenhuma fusão com destino gravado")

    cf._desfundir(cur, "Canoas")
    cur.execute("select id from pois where id = any(%s) and fundido_em is null",
                (list(candidatos),))
    voltaram = [r[0] for r in cur.fetchall()]
    if not voltaram:
        pytest.skip("nenhuma fusão foi desfeita nesta base")

    lados = voltaram + [candidatos[i] for i in voltaram]
    cur.execute("select count(*) from pois where id = any(%s) and cruzado_em is not null",
                (lados,))
    assert cur.fetchone()[0] == 0, \
        "o carimbo `cruzado_em` sobreviveu ao desfazer — o par voltaria ao mapa " \
        "e seria PULADO na comparação seguinte, por já ter sido visto"


def test_nao_desfaz_o_que_o_processo_refaria(banco):
    """Desfazer para refundir na mesma passada é trabalho ida e volta, com o
    mapa duplicado no meio do caminho.

    MEDIDO em Canoas: sem este corte, 13.252 pontos voltavam e o cruzamento
    refundia 10.775 deles — 81%. Os exemplos dizem o que são:

        Primos fratelli   Avenida das Canoas, nº 264
        Primos fratelli   Avenida das Canoas, 264 - Canoas - RS     0 m

    É o mesmo estabelecimento com o endereço escrito de duas formas; desfazer
    isso não revê nada. O que vale desfazer é o que a regra de HOJE não
    refaria — é ali que uma regra nova alcança o passado."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    cur.execute("""select id, fundido_para from pois
                    where fundido_em is not null and fundido_para is not null
                      and cidade ilike 'canoas%'""")
    antes = dict(cur.fetchall())
    if not antes:
        pytest.skip("nenhuma fusão com destino gravado")

    cf._desfundir(cur, "Canoas")
    cur.execute("select id from pois where id = any(%s) and fundido_em is null",
                (list(antes),))
    voltaram = {r[0] for r in cur.fetchall()}

    idx = {p["id"]: p for p in
           cf.carregar(cur, "Canoas", None, com_fundidos=True)}
    import evidencia as ev
    refariam = [m for m in voltaram
                if m in idx and antes[m] in idx
                and ev.avaliar(idx[m], idx[antes[m]])["decisao"] == "fundir"]
    assert not refariam, \
        f"{len(refariam)} fusões foram desfeitas e seriam refeitas idênticas"


def test_a_fusao_grava_o_destino():
    """A causa de tudo isto: até 27/08/2026 a cadeia morria com o processo."""
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    # SEM OS COMENTÁRIOS. A primeira versão deste teste falhou casando com o
    # comentário que EXPLICA a mudança — ele cita `status = 'fundido'` para
    # dizer que saiu. É o terceiro teste meu a tropeçar nisso em um dia; teste
    # que lê comentário testa a prosa.
    s = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    i = s.index("def aplicar(")
    corpo = s[i:s.index("\ndef ", i + 10)]
    assert "fundido_para = f.vive" in corpo, \
        "a fusão voltou a não gravar em quem o POI entrou"
    assert "status = 'fundido'" not in corpo, \
        "a fusão voltou a sobrescrever o `status`, que guarda a ORIGEM do ponto"


def test_ninguem_mais_usa_status_para_saber_quem_e_ponto():
    """`status` voltou a significar origem. Quem ainda o consultasse para saber
    se um POI é ponto veria 139 fundidos em vez de 15.399 — e passaria por não
    estar olhando quase nada."""
    import io
    for arq in ("server.py", "cruzar_fontes.py", "povoar_vinculo.py",
                "corrigir_coordenada.py", "conferir_municipio.py",
                "enriquecer_por_ifood.py"):
        s = io.open(os.path.join(RAIZ, arq), encoding="utf-8").read()
        codigo = "\n".join(l for l in s.splitlines()
                           if not l.lstrip().startswith("#"))
        assert "status, '') <> 'fundido'" not in codigo.replace('"', "'"), \
            f"{arq} ainda decide quem é ponto pelo `status`"


def test_desfazer_converge(banco):
    """O DEFEITO QUE ESTE TESTE GUARDA: a operação girava em falso.

    O alvo pergunta *"a regra de hoje refaria?"*. Para o par que a IA decidiu a
    resposta é SEMPRE não — a regra nunca o faria, e é exatamente por isso que a
    IA foi consultada. Então ele era elegível toda vez: desfeito, perguntado,
    refundido, elegível de novo.

    MEDIDO em 28/08/2026: a execução real desfez 2.483 e o cruzamento refez
    1.913; a passada seguinte encontraria mais 1.824. Não era resto, era laço, e
    cada volta custava uma passada inteira da Spark.

    Com `fundido_por` (migração 0038) a decisão da IA não reabre sozinha: 94 na
    primeira passada, 0 na segunda."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    cf._desfundir(cur, "Canoas")
    assert cf._desfundir(cur, "Canoas") == 0, \
        "desfazer duas vezes seguidas ainda encontra trabalho — voltou a girar"


def test_a_decisao_da_ia_nao_reabre_sozinha(banco):
    """A regra em si, sem depender do estado do banco."""
    cur = banco.cursor()
    cf._empresa(cur, "Aegea - Corsan")
    cur.execute("""select count(*) from pois
                    where fundido_em is not null and fundido_por = 'ia'""")
    if not cur.fetchone()[0]:
        pytest.skip("nenhuma fusão decidida pela IA nesta base")

    cf._desfundir(cur, "Canoas")
    cur.execute("""select count(*) from pois
                    where fundido_em is null and fundido_por = 'ia'""")
    assert cur.fetchone()[0] == 0, \
        "uma fusão decidida pela IA foi desfeita sem `--desfundir-ia`"


def test_a_fusao_grava_quem_decidiu():
    """Sem isto o passado não distingue regra de IA, e o alvo não tem como
    parar de girar."""
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    codigo = "\n".join(l for l in s.splitlines()
                       if not l.lstrip().startswith("#"))
    i = codigo.index("def aplicar(")
    corpo = codigo[i:codigo.index("\ndef ", i + 10)]
    assert "fundido_por = f.quem" in corpo, \
        "a fusão voltou a não gravar quem a decidiu"
