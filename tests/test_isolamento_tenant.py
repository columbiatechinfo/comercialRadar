# -*- coding: utf-8 -*-
"""Prova que uma empresa não alcança o dado de outra.

Este é o teste que impede a matriz do RBAC de virar documentação. Ele roda com o
papel REAL da API (`comercialradar_app`, sem BYPASSRLS), porque testar com o
worker provaria nada: BYPASSRLS ignora toda policy.

Quatro perguntas, e as quatro precisam de resposta:

  1. A empresa A LÊ linha da empresa B?
  2. A empresa A GRAVA carimbando a empresa B?  ← o `with check`
  3. Sem declarar a empresa, o que se vê?       ← fail-closed
  4. O `root` atravessa mesmo?

A pergunta 2 é a que costuma faltar. Sem ela, o cliente não *vê* a linha alheia
mas consegue *criar* uma dentro do vizinho — e ninguém percebe até o vizinho
reclamar de dado que não é dele.
"""
import os
import sys
import uuid

import psycopg2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: F401,E402
import base_comum as bc  # noqa: E402

HOST = os.environ["I9_POSTGRES_HOST"]
PORT = int(os.environ.get("I9_POSTGRES_PORT", "5444"))
DB = os.environ.get("I9_POSTGRES_DB", "postgres")
OPCOES = "-c search_path=comercialradar,public"


def _conectar(papel: str, senha_var: str):
    senha = (os.environ.get(senha_var) or "").strip()
    if not senha:
        pytest.skip(f"{senha_var} ausente no .env")
    return psycopg2.connect(host=HOST, port=PORT, user=papel, password=senha,
                            dbname=DB, options=OPCOES, connect_timeout=20)


@pytest.fixture(scope="module")
def cenario():
    """Duas empresas, um POI em cada. Criado e removido pelo worker."""
    con = bc.conectar()
    con.autocommit = True
    ids = {}
    with con.cursor() as cur:
        for lado in ("A", "B"):
            cur.execute("insert into tenants (nome) values (%s) returning id",
                        (f"ZZ TESTE {lado} {uuid.uuid4().hex[:8]}",))
            ids[lado] = cur.fetchone()[0]
            cur.execute(
                """insert into pois (nome, fonte, lat_origem, lng_origem, tenant_id)
                   values (%s,'teste',-29.9,-51.2,%s) returning id""",
                (f"ZZ POI {lado}", ids[lado]))
            ids[f"poi_{lado}"] = cur.fetchone()[0]
    yield ids
    # `::uuid[]` explícito: psycopg2 manda a lista como text[], e o Postgres não
    # tem operador `uuid = text`. Sem o cast a limpeza falha e deixa lixo no banco.
    with con.cursor() as cur:
        cur.execute("delete from pois where tenant_id = any(%s::uuid[])",
                    ([str(ids["A"]), str(ids["B"])],))
        cur.execute("delete from tenants where id = any(%s::uuid[])",
                    ([str(ids["A"]), str(ids["B"])],))
    con.close()


def _como_empresa(tenant_id):
    """Conexão do app já declarando a empresa, como a API fará por requisição."""
    con = _conectar("comercialradar_app", "CR_APP_PASSWORD")
    with con.cursor() as cur:
        cur.execute("select set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    return con


def test_a_nao_le_o_poi_de_b(cenario):
    con = _como_empresa(cenario["A"])
    with con.cursor() as cur:
        cur.execute("select count(*) from pois where id = %s", (cenario["poi_B"],))
        assert cur.fetchone()[0] == 0, "empresa A alcançou o POI da empresa B"
        cur.execute("select count(*) from pois where id = %s", (cenario["poi_A"],))
        assert cur.fetchone()[0] == 1, "empresa A não enxerga o próprio POI"
    con.close()


def test_a_nao_grava_carimbando_b(cenario):
    """O `with check`. Sem ele, A cria linha DENTRO de B.

    A exceção é `InsufficientPrivilege` (SQLSTATE 42501), não `CheckViolation`:
    violar o `with check` de uma policy é recusa de PRIVILÉGIO no Postgres, não
    de restrição de tabela. Esperar a classe errada faria este teste falhar com
    o isolamento funcionando — que foi o que aconteceu na primeira execução."""
    con = _como_empresa(cenario["A"])
    with con.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege) as erro:
            cur.execute(
                """insert into pois (nome, fonte, lat_origem, lng_origem, tenant_id)
                   values ('ZZ INVASOR','teste',-29.9,-51.2,%s)""", (cenario["B"],))
        assert "row-level security" in str(erro.value)
    con.rollback()
    con.close()


def test_sem_empresa_declarada_nao_ve_nada(cenario):
    """Fail-closed: variável ausente devolve NULL, e NULL não casa com nada.

    É o motivo de a policy nunca conter `or current_setting(...) is null` — esse
    OR é a 'correção' que aparece quando as consultas voltam vazias, e ele reabre
    a base inteira para qualquer conexão sem identidade."""
    con = _conectar("comercialradar_app", "CR_APP_PASSWORD")
    with con.cursor() as cur:
        cur.execute("select count(*) from pois")
        assert cur.fetchone()[0] == 0, "conexão sem empresa declarada enxergou linhas"
    con.close()


def test_root_atravessa_as_empresas(cenario):
    con = _conectar("comercialradar_root", "CR_ROOT_PASSWORD")
    with con.cursor() as cur:
        cur.execute("select count(*) from pois where id = any(%s)",
                    ([cenario["poi_A"], cenario["poi_B"]],))
        assert cur.fetchone()[0] == 2, "root não enxergou as duas empresas"
    con.close()


def test_policy_usa_indice_e_nao_varre(cenario):
    """RLS é avaliado POR LINHA. Sem índice começando por tenant_id, a policy
    vira o gargalo: em `cadastro_cliente` são 102 mil linhas por consulta."""
    con = _como_empresa(cenario["A"])
    with con.cursor() as cur:
        cur.execute("explain (analyze, buffers) select count(*) from cadastro_cliente")
        plano = " ".join(r[0] for r in cur.fetchall())
    con.close()
    assert "Seq Scan" not in plano, f"varredura completa apesar do índice:\n{plano}"


def test_nenhuma_tabela_com_tenant_id_fica_sem_politica():
    """A regra geral, e não a lista das quatro que já falharam.

    Em 24/08/2026 quatro tabelas estavam sem RLS — `atribuicao_divergente`,
    `fachada_triagem`, `foto_maps_triagem` e `ifood_merchant` —, e o
    `docs/estado.json` afirmava `tabelas_sem_rls: []`. Eram as criadas DEPOIS do
    passe de 12/08, e são as mesmas que a migration 0024 já tinha pego por outro
    motivo (escreviam e não liam).

    O padrão, portanto, não é descuido pontual: **tabela nova não herda a
    política**. Conferir as quatro por nome não impediria a quinta. Este teste
    pergunta pela REGRA: se a tabela tem `tenant_id`, ela tem RLS, policy e o
    gatilho que carimba a empresa no INSERT.

    O gatilho importa tanto quanto a policy. Sem ele a linha nasce com
    `tenant_id` nulo, e nula não casa com policy nenhuma: some do painel de
    todo mundo, sem erro nenhum.

    DUAS EXCEÇÕES, e as duas são de projeto, não pendência:

      `auditoria`  tem escritor próprio. `registrar_auditoria` já resolve a
                   empresa — da sessão, e quando não há, da própria linha
                   auditada. Somar `preencher_tenant` seria um segundo dono
                   para a mesma coluna.
      `usuarios`   o `root` não pertence a empresa nenhuma. Carimbar a empresa
                   da sessão no INSERT prenderia o root à empresa de quem o
                   criou.
    """
    SEM_GATILHO_POR_PROJETO = {"auditoria", "usuarios"}
    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("""
            select c.relname,
                   c.relrowsecurity,
                   (select count(*) from pg_policies p
                     where p.schemaname = 'comercialradar'
                       and p.tablename = c.relname),
                   (select count(*) from pg_trigger tg
                     join pg_proc pr on pr.oid = tg.tgfoid
                    where tg.tgrelid = c.oid
                      and not tg.tgisinternal
                      and pr.proname = 'preencher_tenant')
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'comercialradar'
               and c.relkind = 'r'
               and exists (select 1 from pg_attribute a
                            where a.attrelid = c.oid
                              and a.attname = 'tenant_id'
                              and a.attnum > 0
                              and not a.attisdropped)
             order by 1""")
        tabelas = cur.fetchall()
    con.close()

    assert tabelas, "nenhuma tabela com tenant_id — a consulta não achou o schema"

    sem_rls = [t[0] for t in tabelas if not t[1]]
    sem_policy = [t[0] for t in tabelas if t[2] == 0]
    sem_gatilho = [t[0] for t in tabelas
                   if t[3] == 0 and t[0] not in SEM_GATILHO_POR_PROJETO]

    assert not sem_rls, f"tabelas com tenant_id e sem RLS ligada: {sem_rls}"
    assert not sem_policy, f"tabelas com RLS e sem policy: {sem_policy}"
    assert not sem_gatilho, (
        "tabelas sem o gatilho preencher_tenant — a linha nasce sem empresa e "
        f"some do painel de todos: {sem_gatilho}")


def test_as_bases_que_geram_poi_nao_carimbam_empresa():
    """Decisão do dono do produto, 25/08/2026: o POI é DE CADA EMPRESA, mas a
    BASE que o gera é geral.

    Este teste cobrava o oposto para o `ifood_merchant`: que `tenant_id` fosse
    `not null`. Fazia sentido enquanto ele era tratado como dado de cliente —
    e não é. Duas concessionárias na mesma cidade leem o MESMO cadastro do
    iFood, o MESMO Cadastur federal, os MESMOS CNPJs da Receita. Carimbar a
    base faria cada uma baixar, tratar e guardar de novo as mesmas linhas.

    É o raciocínio que já valia para `endereco_segmentado` e
    `logradouro_ajustado` — "R. Mal. Rondon, 1199" é o mesmo endereço para
    qualquer cliente — estendido a montante.

    A coluna não foi apagada: virou `importado_por`, fora da regra de
    isolamento e com a informação preservada. Apagar responderia à regra e
    perderia a pergunta "quem trouxe estas linhas".
    """
    bases = ("cadastur_prestador", "cadastur_total_pf", "cnpj_tratado",
             "cnefe_coletiva", "ifood_merchant")
    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("""select c.relname, c.relrowsecurity,
                              exists (select 1 from pg_attribute a
                                       where a.attrelid = c.oid
                                         and a.attname = 'tenant_id'
                                         and a.attnum > 0 and not a.attisdropped),
                              exists (select 1 from pg_attribute a
                                       where a.attrelid = c.oid
                                         and a.attname = 'importado_por'
                                         and a.attnum > 0 and not a.attisdropped)
                         from pg_class c join pg_namespace n on n.oid = c.relnamespace
                        where n.nspname = 'comercialradar' and c.relname = any(%s)""",
                    (list(bases),))
        linhas = cur.fetchall()
    con.close()

    achadas = {r[0] for r in linhas}
    assert achadas == set(bases), f"base sumiu do banco: {set(bases) - achadas}"
    com_tenant = [r[0] for r in linhas if r[2]]
    assert not com_tenant, f"base pública voltou a carimbar empresa: {com_tenant}"
    sem_historico = [r[0] for r in linhas if not r[3]]
    assert not sem_historico,         f"a coluna foi APAGADA em vez de virar importado_por: {sem_historico}"
    com_rls = [r[0] for r in linhas if r[1]]
    assert not com_rls, f"RLS ligada numa base geral esconde-a de quem pode usá-la: {com_rls}"


def test_o_poi_continua_sendo_de_cada_empresa():
    """A outra metade da mesma decisão, e ela não pode escorregar junto.

    Cada empresa roda a SUA mineração, e o que ela achou é dela. Se `pois`
    perdesse o `tenant_id` no mesmo movimento, o achado de um cliente apareceria
    para outro — que é o oposto do que se está corrigindo.
    """
    con = bc.conectar()
    with con.cursor() as cur:
        cur.execute("""select c.relrowsecurity,
                              exists (select 1 from pg_attribute a
                                       where a.attrelid = c.oid and a.attname = 'tenant_id'
                                         and a.attnum > 0 and not a.attisdropped)
                         from pg_class c join pg_namespace n on n.oid = c.relnamespace
                        where n.nspname = 'comercialradar' and c.relname = 'pois'""")
        rls, tem = cur.fetchone()
    con.close()
    assert tem, "pois perdeu o tenant_id: o achado de um cliente vazaria para outro"
    assert rls, "pois com tenant_id e sem RLS"
