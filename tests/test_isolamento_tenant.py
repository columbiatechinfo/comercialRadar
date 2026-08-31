# -*- coding: utf-8 -*-
"""Prova que uma empresa não alcança o dado de outra.

Este é o teste que impede a matriz do RBAC de virar documentação. Ele roda com o
papel REAL da API — `app_user`, sem `BYPASSRLS` — porque testar com um papel
privilegiado provaria nada: quem ignora policy passa em qualquer teste de policy.

Quatro perguntas, e as quatro precisam de resposta:

  1. A empresa A LÊ linha da empresa B?
  2. A empresa A GRAVA carimbando a empresa B?  ← o `with check`
  3. Sem identidade declarada, o que se vê?     ← fail-closed
  4. O `root` atravessa mesmo?

A pergunta 2 é a que costuma faltar. Sem ela, o cliente não *vê* a linha alheia
mas consegue *criar* uma dentro do vizinho — e ninguém percebe até o vizinho
reclamar de dado que não é dele.

O QUE MUDOU EM 31/08/2026, e por que este arquivo foi reescrito e não ajustado.

No banco antigo havia dois papéis: `comercialradar_root` com `BYPASSRLS` e
`comercialradar_app` sem. A travessia do root era privilégio de PAPEL, e o teste
conectava com um ou com outro.

No banco `a2l` não existe papel com `BYPASSRLS` disponível à aplicação. Todo
mundo conecta como `app_user`, e o que muda é a IDENTIDADE declarada em
`request.jwt.claim.sub` — de onde `core.empresa_atual()`, `core.nivel_atual()` e
`core.eh_suporte()` tiram tudo, lendo `core.tb_users`.

Isso muda o que o teste declara. Uma renomeação mecânica de `app.tenant_id` para
`request.jwt.claim.sub` deixaria o teste passando o uuid da EMPRESA no lugar do
uuid do USUÁRIO — e aí `auth.uid()` apontaria para alguém que não existe, toda
consulta voltaria vazia, e as quatro perguntas passariam por motivo errado.
"""
import os
import sys
import uuid

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: F401,E402

DSN = (os.environ.get("A2L_DB_URL") or "").strip()
OPCOES = "-c search_path=radar_comercial,public"

#: O root da instalação. Ele monta o cenário: a policy de INSERT de
#: `core.tb_empresas` é `core.eh_suporte()`, então ninguém abaixo de nível 9 cria
#: empresa. E é ele quem responde a pergunta 4.
ROOT = "b5b544cb-ed45-470f-bb10-4cbda574d3f2"

#: O administrator, que pertence a uma empresa. É o "lado A" das perguntas 1 e 2.
#:
#: ESTE TESTE NÃO CRIA USUÁRIO, e isso é decisão. `core.tb_users.id` referencia
#: `auth.users` do GoTrue, então criar um exigiria a chave de serviço — e um
#: teste ou é pulado por falta dela (e não protege nada) ou carrega credencial de
#: produção (e é um risco novo).
#:
#: Não é preciso: o teste precisa de duas EMPRESAS e de UM usuário. A empresa B
#: existe só para não ser alcançada; ela não precisa de ninguém dentro.
ADMIN = "a63f69ea-0df0-4e12-8e5a-3e2989192b38"

#: A empresa que existe so para NAO ser alcancada. Criada uma vez e
#: reaproveitada: no padrao A2L empresa se desativa, nao se apaga.
EMPRESA_TESTE = "ZZ TESTE ISOLAMENTO"

pytestmark = pytest.mark.skipif(
    not DSN, reason="A2L_DB_URL ausente: este teste fala com o banco de verdade")


def _conectar(quem=None):
    """Conexão de aplicação com uma identidade declarada — do jeito que a API faz.

    `quem=None` é o caso 3: ninguém declarado. Não é conveniência de teste — é a
    situação de qualquer conexão nova antes de a API declarar o usuário, e o que
    ela enxerga aí é o que um bug de autenticação exporia.

    DUAS COISAS AQUI NÃO PODEM MUDAR, e as duas custaram medição:

    `true` e não `false`. Escopo de SESSÃO na 7110 VAZA: ele sobrevive à
    devolução do backend ao pool, e a próxima conexão a pegar aquele backend
    herda a empresa. Medido — dez conexões novas, sem declarar nada,
    responderam com a empresa que o teste anterior havia declarado. Foi a
    primeira versão deste arquivo que sujou o pool e revelou isso.

    `autocommit = False` e não `True`. Com autocommit, cada comando é sua
    própria transação, e o escopo de transação morre no fim dela — a identidade
    não chegaria à consulta seguinte, e os testes veriam vazio por motivo
    errado. Quem chama fecha com `rollback()`.
    """
    con = psycopg2.connect(DSN, options=OPCOES, connect_timeout=20)
    con.autocommit = False
    if quem:
        with con.cursor() as cur:
            cur.execute("select set_config('request.jwt.claim.sub', %s, true)",
                        (str(quem),))
    return con


@pytest.fixture(scope="module")
def cenario():
    """A empresa do administrator, uma empresa alheia, e um POI em cada.

    Montado e desmontado pelo root, que e quem pode criar empresa e quem enxerga
    as duas para limpar depois.

    A IDENTIDADE E DECLARADA DENTRO DE CADA TRANSACAO QUE GRAVA, e nao uma vez no
    inicio. Escopo de transacao morre no commit — que e justamente o que o torna
    seguro na 7110 —, entao a segunda transacao comecaria sem ninguem declarado e
    a policy negaria o insert. Declarar em escopo de sessao para "resolver" isso
    e o erro que suja o pool inteiro.
    """
    ids = {}

    con = psycopg2.connect(DSN, options=OPCOES, connect_timeout=20)
    con.autocommit = False
    with con.cursor() as cur:
        cur.execute("select set_config('request.jwt.claim.sub', %s, true)", (ROOT,))

        cur.execute("select id_empresa from core.tb_users where id = %s", (ADMIN,))
        r = cur.fetchone()
        if not r or not r[0]:
            con.rollback()
            con.close()
            pytest.skip("o administrator de referencia nao existe nesta instalacao")
        ids["A"] = r[0]

        # UMA EMPRESA DE TESTE, REAPROVEITADA — e nao uma por execucao.
        #
        # No padrao A2L empresa nao se apaga, se DESATIVA: `app_user` tem select,
        # insert e update em `core.tb_empresas`, e nao tem delete. Isso e certo
        # (a rota da API de identidade tambem chama "desativar"), mas significa
        # que criar uma por rodada deixaria lixo permanente crescendo no `core`,
        # que e schema compartilhado com as outras ferramentas.
        marca = uuid.uuid4().hex[:8]
        cur.execute("select id from core.tb_empresas where name = %s", (EMPRESA_TESTE,))
        r = cur.fetchone()
        if r:
            ids["B"] = r[0]
        else:
            cur.execute("""insert into core.tb_empresas
                             (name, identification_doc, id_type_doc, reset_email)
                           values (%s, %s, 2, %s) returning id""",
                        (EMPRESA_TESTE, "00000000000000",
                         "zz-isolamento@teste.invalido"))
            ids["B"] = cur.fetchone()[0]

        # NOME UNICO POR EXECUCAO. O indice `pois_sem_duplicata` recusa
        # nome+endereco repetidos na mesma empresa, e a empresa A e REAL — o
        # resto de uma execucao que falhou no meio fica la e bloqueia a
        # proxima. Com a marca no nome, cada rodada e sua.
        for lado in ("A", "B"):
            # `id_empresa` EXPLICITO aqui, e so aqui: o root nao pertence a
            # empresa nenhuma, entao o gatilho nao teria o que carimbar. E a
            # mesma excecao que o `area_utils` faz, pelo mesmo motivo.
            cur.execute("""insert into pois
                             (nome, endereco, fonte, lat_origem, lng_origem, id_empresa)
                           values (%s,'Rua Teste, 1','teste',-29.9,-51.2,%s)
                        returning id""", (f"ZZ POI {lado} {marca}", ids[lado]))
            ids[f"poi_{lado}"] = cur.fetchone()[0]
    con.commit()
    con.close()

    yield ids

    con = psycopg2.connect(DSN, options=OPCOES, connect_timeout=20)
    con.autocommit = False
    with con.cursor() as cur:
        cur.execute("select set_config('request.jwt.claim.sub', %s, true)", (ROOT,))
        # SO os POIs. A empresa de teste FICA, e e reaproveitada na proxima
        # rodada — `app_user` nao pode apagar empresa, e nao deveria mesmo.
        cur.execute("delete from pois where id = any(%s)",
                    ([ids["poi_A"], ids["poi_B"]],))
    con.commit()
    con.close()


def test_a_nao_le_o_poi_de_b(cenario):
    con = _conectar(ADMIN)
    with con.cursor() as cur:
        cur.execute("select count(*) from pois where id = %s", (cenario["poi_B"],))
        assert cur.fetchone()[0] == 0, "empresa A alcançou o POI da empresa B"
        cur.execute("select count(*) from pois where id = %s", (cenario["poi_A"],))
        assert cur.fetchone()[0] == 1, "empresa A não enxerga o próprio POI"
    con.rollback()
    con.close()


def test_a_nao_grava_carimbando_b(cenario):
    """O `with check`. Sem ele, A cria linha DENTRO de B.

    A exceção é `InsufficientPrivilege` (SQLSTATE 42501), e não `CheckViolation`:
    violar o `with check` de uma policy é recusa de PRIVILÉGIO no Postgres, não
    de restrição de tabela. Esperar a classe errada faria este teste falhar com o
    isolamento funcionando — que foi o que aconteceu na primeira execução.
    """
    con = _conectar(ADMIN)
    with con.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege) as erro:
            cur.execute("""insert into pois
                             (nome, endereco, fonte, lat_origem, lng_origem, id_empresa)
                           values ('ZZ INVASOR','Rua Teste, 1','teste',-29.9,-51.2,%s)""",
                        (cenario["B"],))
        assert "row-level security" in str(erro.value)
    con.rollback()
    con.close()


def test_sem_identidade_nao_ve_nada(cenario):
    """Fail-closed: variável ausente devolve NULL, e NULL não casa com nada.

    É o motivo de a policy nunca conter `or core.empresa_atual() is null` — esse
    OR é a "correção" que aparece quando as consultas voltam vazias, e ele reabre
    a base inteira para qualquer conexão sem identidade.
    """
    con = _conectar()
    with con.cursor() as cur:
        cur.execute("select core.empresa_atual()")
        assert cur.fetchone()[0] is None, "conexão sem identidade resolveu uma empresa"
        cur.execute("select count(*) from pois")
        assert cur.fetchone()[0] == 0, "conexão sem identidade enxergou linhas"
    con.rollback()
    con.close()


def test_root_atravessa_as_empresas(cenario):
    """E atravessa pela POLÍTICA, não por privilégio de papel.

    A conexão é a mesma `app_user` das outras: o que muda é só quem está
    declarado. Se algum dia isto voltar a passar por um papel com BYPASSRLS, o
    teste continua verde e para de significar o que significa — por isso ele
    confere também que o papel NÃO tem BYPASSRLS.
    """
    con = _conectar(ROOT)
    with con.cursor() as cur:
        cur.execute("select rolbypassrls from pg_roles where rolname = current_user")
        assert cur.fetchone()[0] is False, \
            "o papel da aplicação ganhou BYPASSRLS — toda policy virou enfeite"
        cur.execute("select core.eh_suporte()")
        assert cur.fetchone()[0] is True, "o root deixou de ser reconhecido como suporte"
        cur.execute("select count(*) from pois where id = any(%s)",
                    ([cenario["poi_A"], cenario["poi_B"]],))
        assert cur.fetchone()[0] == 2, "root não enxergou as duas empresas"
    con.rollback()
    con.close()


def test_o_gatilho_carimba_a_empresa_de_quem_grava(cenario):
    """INSERT sem informar empresa nasce na empresa de quem gravou.

    É o que permite os trinta e poucos INSERTs do pipeline não citarem
    `id_empresa`. Sem o gatilho, o primeiro esquecido gravaria linha sem dono — e
    linha sem dono não some: ela nasce invisível para todos, e ninguém procura o
    que não sabe que perdeu.
    """
    con = _conectar(ADMIN)
    with con.cursor() as cur:
        cur.execute("""insert into pois (nome, fonte) values ('ZZ CARIMBO','teste')
                    returning id_empresa""")
        assert str(cur.fetchone()[0]) == str(cenario["A"]), \
            "o gatilho carimbou a empresa errada, ou não carimbou"
    con.rollback()
    con.close()


def test_policy_usa_indice_e_nao_varre(cenario):
    """RLS é avaliada POR LINHA. Sem índice começando por id_empresa, a policy
    força varredura completa e o isolamento vira o gargalo."""
    con = _conectar(ADMIN)
    with con.cursor() as cur:
        cur.execute("explain (format json) select id from pois where id_empresa = %s",
                    (cenario["A"],))
        plano = str(cur.fetchone()[0])
    con.close()
    assert "Seq Scan" not in plano or "Index" in plano, \
        ("a consulta por empresa virou varredura completa: falta índice "
         "começando por id_empresa")
