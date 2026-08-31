# -*- coding: utf-8 -*-
"""Gera a migracao do schema `radar_comercial` a partir das fontes levantadas.

O que este gerador NAO faz: copiar a estrutura velha. Ele monta a estrutura NOVA
no padrao A2L — `id_empresa` no lugar de `tenant_id`, `FORCE ROW LEVEL SECURITY`,
politicas no molde do `core`, e `id_empresa` como primeira coluna de todo indice.

A PRIMEIRA VERSAO DESTE GERADOR IA CRIAR 14 TABELAS SEM ISOLAMENTO, entre elas
`pois`, `cadastro_cliente` e `fachada_anotacao`. A causa: a migration 0001 poe o
`tenant_id` num `do $$ ... format('alter table %I ...') $$`, e nenhum extrator de
literal SQL consegue atribuir aquilo a uma tabela. So apareceu porque o gerador
IMPRIME quem ficou de fora, em vez de gerar calado.
"""
import io
import json
import re

# Os tipos proprios. Nenhum extrator de TABELA os acharia, e sem eles a
# validacao para na primeira coluna que usa um: `type "motivo_reprova" does not
# exist`. Tirados das migrations com o texto que estava la.
ENUMS = [
    ("decisao_fila", "'pendente', 'aprovado', 'reprovado', 'devolvido'",
     "Onde a triagem para: aprovado entra na base, reprovado sai, devolvido "
     "volta para quem mandou."),
    ("motivo_reprova",
     "'fachada_residencial', 'endereco_divergente', 'comercio_encerrado', "
     "'duplicado', 'evidencia_insuficiente', 'ja_e_comercial', 'outro'",
     "Por que reprovou. E ENUM e nao texto livre porque estes sete sao "
     "contados em relatorio — texto livre viraria sete grafias do mesmo motivo."),
    ("fonte_aba",
     "'poi', 'google', 'receita', 'redes_sociais', 'delivery', 'imagens'",
     "As abas da ficha. Cada campo do catalogo pertence a uma."),
    ("peso_evidencia", "'forte', 'media', 'neutra', 'contraria'",
     "Quanto um campo pesa no veredito comercial."),
    ("prioridade_campo", "'normal', 'alta'",
     "Prioridade da visita de campo."),
    ("pauta_campo",
     "'confirmar_atividade', 'confirmar_numero', 'contar_unidades', "
     "'fotografar_fachada', 'registrar_coordenada', 'confirmar_endereco'",
     "O que o agente vai fazer na visita: a placa existe e o comercio opera; "
     "o numero da porta bate com o cadastro; quantas lojas ha de fato no "
     "imovel; nao ha imagem ou a que ha esta velha; a coordenada esta "
     "imprecisa; mais de um endereco plausivel."),
]

FONTES = ["tipos_das_migrations", "tipos_do_backup", "tipos_do_python",
          "tipos_de_listas_python", "tipos_julgados"]

PRODUTO = [
    "pois", "images_urls", "streetview_imgs", "comentarios",
    "horario_funcionamento", "analise_ia", "fachada_anotacao", "fachada_triagem",
    "foto_maps_triagem", "cadastro_cliente", "area_trabalho", "fonte_arquivos",
    "atribuicao", "atribuicao_divergente", "cnpj_tratado", "cadastur_prestador",
    "cadastur_total_pf", "campo_catalogo", "chat_conversa", "chat_mensagem",
    "chat_anexo", "cruzamento",
    "memoria", "vinculo_poi", "ifood_merchant", "proxy_ip", "proxy_evento",
    "cnefe_coletiva",
]

# Tabelas cuja identidade NAO e um `id` sintetico. Vem da migration 0001, que
# registra o porque: "`analise_ia` e `fonte_arquivos` nao tem `id`: a primeira e
# chaveada por `poi_id`, a segunda pelo arquivo carregado".
SEM_ID = {"analise_ia": "poi_id", "fonte_arquivos": None}

# Vai para `resources_root`: base publica e cache compartilhado, lido por todas
# as ferramentas, escrito so por processo.
REFERENCIA = [
    "ibge_cnefe", "ibge_malha", "rf_empresas", "rf_estabelecimentos",
    "rf_socios", "rf_simples", "rf_cnaes", "rf_municipios", "rf_naturezas",
    "rf_paises", "rf_qualificacoes", "rf_motivos",
    # Cache de normalizacao, movido para ca em 31/08/2026 por decisao do dono do
    # produto. Motivo: o mesmo endereco em texto se separa igual para qualquer
    # cliente E para qualquer ferramenta — o radarTelhados e o radarColetivas
    # normalizam os mesmos enderecos. Cada segmentacao custa uma chamada ao
    # modelo, e pagar de novo por empresa (ou por ferramenta) e desperdicio puro.
    "endereco_segmentado", "logradouro_ajustado",
]

# Infraestrutura compartilhada, nao dado de cliente: o pool de proxy e o mesmo
# para todas as empresas. `proxy_evento` TEM empresa (quem consumiu), `proxy_ip`
# nao (o IP e o mesmo IP).
SEM_EMPRESA_DE_PROPOSITO = {
    "proxy_ip": "o IP e infraestrutura compartilhada; quem consumiu esta em "
                "proxy_evento, que tem empresa",
}

UNICOS = [
    ("ux_pois_place_id_por_empresa", "pois", "id_empresa, place_id",
     "where place_id is not null and place_id <> ''",
     "O mesmo lugar pode existir para duas empresas — cada uma minerou o seu. "
     "A unicidade e POR EMPRESA."),
    ("pois_sem_duplicata", "pois",
     "id_empresa, upper(trim(nome)), upper(trim(endereco))",
     "where fundido_em is null",
     "Nome+endereco iguais na mesma empresa e a mesma coisa minerada duas "
     "vezes. POI ja fundido sai do indice: ele existe so como historico."),
    ("ux_streetview_poi_angulo", "streetview_imgs", "id_empresa, poi_id, angulo",
     "where pano_id is not null and angulo is not null",
     "A mesma vista do mesmo ponto nao entra duas vezes."),
    ("ux_cruzamento_par", "cruzamento",
     "id_empresa, base_a, id_a, base_b, id_b, chave", "",
     "Um par de registros so e cruzado uma vez por chave."),
    # O NOME E `vinculo_sem_duplicata`, e nao `ix_vinculo_poi_ativo`.
    #
    # As migracoes antigas o chamavam `ix_vinculo_poi_ativo`, mas o codigo e os
    # testes procuram por `vinculo_sem_duplicata` — que e o par de
    # `pois_sem_duplicata` e diz o que ele faz, nao como foi feito. Criar os
    # dois seria manter duas vezes o mesmo indice.
    ("vinculo_sem_duplicata", "vinculo_poi", "id_empresa, fonte, id_fonte",
     "where estado = 'vinculado'",
     "Um registro de fonte compoe UM poi por vez. Desvinculado sai do indice e "
     "pode ser vinculado a outro."),
    ("atribuicao_unica", "atribuicao", "id_empresa, poi_id, supervisor_id", "",
     "Um poi nao e atribuido duas vezes ao mesmo supervisor."),
    ("atribuicao_div_pendente_unico", "atribuicao_divergente",
     "id_empresa, poi_id", "where status = 'pendente'",
     "So uma divergencia pendente por poi; as resolvidas ficam como historico."),
]

tipos = {}
for n in FONTES:
    for t, c in json.load(io.open("docs/%s.json" % n, encoding="utf-8")).items():
        if t.startswith("_"):
            continue
        for col, tp in c.items():
            if col.startswith("_"):
                continue
            tipos.setdefault(t, {})[col] = tp[0] if isinstance(tp, list) else tp

est = json.load(io.open("docs/estrutura_das_migrations.json", encoding="utf-8"))
COM_EMPRESA = {t for t in est["com_empresa"] if t in PRODUTO}
COM_EMPRESA -= set(SEM_EMPRESA_DE_PROPOSITO)


# Colunas que APONTAM para `pois.id`. Elas TEM de acompanhar o tipo da chave.
#
# A primeira versao promoveu `pois.id` de `serial` para
# `bigint generated always as identity` e deixou todo `poi_id` como `integer` —
# o tipo que ele tinha quando a chave era `serial`. Ficariam 12 colunas
# apontando para uma chave de outro tipo: o JOIN ainda funciona (o Postgres
# converte), mas a chave estrangeira e recusada na criacao, e o indice do lado
# `integer` deixa de ser usavel quando o `id` passar de 2,1 bilhoes.
REFERENCIAM_POI = {"poi_id", "fundido_para", "descoberto_de", "poi_origem",
                   "poi_destino", "id_poi"}


def normalizar(tp: str, coluna: str = "") -> str:
    """Tipo + as restricoes que sao SEMANTICA, sem as que a tabela ja resolve.

    A primeira versao cortava `NOT NULL` e `DEFAULT` junto com `PRIMARY KEY`,
    tratando os quatro como ruido. Nao sao: `arquivada boolean not null default
    false` viraria `arquivada boolean`, e uma conversa sem valor passaria a
    nascer NULA — que nao e nem arquivada nem ativa, e todo `where not
    arquivada` deixaria de ve-la. Perdi 40 defaults e 30 not-nulls assim, em
    silencio, ate um ENUM inexistente derrubar a validacao e me fazer olhar.

    Sai fora so o que a propria DDL da tabela declara noutro lugar: chave
    primaria, unicidade e referencia.
    """
    t = " ".join(tp.split())
    for lixo in ("PRIMARY KEY", "UNIQUE", "GENERATED", "REFERENCES"):
        i = t.upper().find(lixo)
        if i > 0:
            t = t[:i]
    t = t.strip().rstrip(",")
    # o schema mudou de nome: tipo proprio nao carrega o prefixo velho
    t = t.replace("comercialradar.", "")

    # separa o TIPO do resto (`not null`, `default ...`, `check (...)`): a
    # traducao abaixo e sobre o tipo, e antes ela era feita sobre a linha
    # inteira — entao `timestamp(3) without time zone not null` nunca casava.
    m = re.match(r"^([a-z_][a-z0-9_]*(?:\s*\([^)]*\))?"
                 r"(?:\s+with(?:out)?\s+time\s+zone)?(?:\[\])?)(.*)$",
                 t, re.I)
    tipo, resto = (m.group(1).strip(), m.group(2).strip()) if m else (t, "")

    tipo = {"serial": "integer", "bigserial": "bigint",
            "timestamp(3)": "timestamptz", "timestamp": "timestamptz",
            "timestamp(3) without time zone": "timestamptz",
            "timestamp without time zone": "timestamptz",
            "timestamptz(3)": "timestamptz"}.get(tipo.lower(), tipo)
    if coluna in REFERENCIAM_POI and tipo.lower() in ("integer", "int", "int4"):
        tipo = "bigint"
    return (tipo + " " + resto).strip()


L = []
w = L.append
w("""-- 0001_radar_comercial.sql — o schema da ferramenta, no padrao A2L.
--
-- DE ONDE ELE SAIU. Nao e copia das 42 migrations do banco antigo: e o schema
-- montado do que o codigo de fato le e grava, com o tipo de cada coluna tirado
-- de cinco fontes independentes (docs/tipos_*.json) e a estrutura — quem tem
-- empresa, qual e a chave natural — tirada das migrations (docs/estrutura_*).
--
-- O cruzamento fechou em zero: das 22 tabelas que as migrations criavam, todas
-- as 22 sao usadas pelo codigo. Nada se perdeu ao comecar do zero.
--
-- O QUE MUDOU EM RELACAO AO BANCO ANTIGO, e por que:
--
--   tenant_id -> id_empresa   O `core` e a autoridade de identidade e chama
--                             assim. Dois nomes para a mesma coisa e o tipo de
--                             divergencia que so aparece num JOIN errado.
--   FORCE ROW LEVEL SECURITY  Vale inclusive para o DONO da tabela. Sem o
--                             FORCE, quem e dono atravessa a propria policy.
--   politicas via core.*      `core.eh_suporte() or id_empresa =
--                             core.empresa_atual()`, a mesma forma do `core`.
--                             Nao ha papel com BYPASSRLS neste banco: o root
--                             atravessa DENTRO da politica, nunca por papel.
--   id_empresa 1a no indice   RLS e avaliada POR LINHA. Indice que nao comeca
--                             por id_empresa nao serve a policy, e o
--                             isolamento vira o gargalo.
--
-- Rodar como `migrator` (A2L_MIGRATOR_URL), nunca como app_user.

set local search_path = radar_comercial, public;

-- ─────────────────────────────────────────────────────────────────────
-- Tipos proprios
-- ─────────────────────────────────────────────────────────────────────
--
-- Vem antes das tabelas porque as tabelas os usam. `if not exists` nao existe
-- para `create type`, entao o bloco `do $$ ... exception when duplicate_object`
-- e o que torna esta migracao repetivel.
""")
for nome, valores, porque in ENUMS:
    for linha in porque.split(". "):
        if linha.strip():
            w("-- " + linha.strip().rstrip(".") + ".")
    w("do $$ begin")
    w("  create type %s as enum (%s);" % (nome, valores))
    w("exception when duplicate_object then null; end $$;")
    w("")

w("""
-- ─────────────────────────────────────────────────────────────────────
-- Tabelas
-- ─────────────────────────────────────────────────────────────────────
""")

for t in PRODUTO:
    cols = dict(tipos.get(t, {}))
    cols.pop("tenant_id", None)
    tem = t in COM_EMPRESA

    if t in SEM_EMPRESA_DE_PROPOSITO:
        w("-- SEM id_empresa DE PROPOSITO: %s" % SEM_EMPRESA_DE_PROPOSITO[t])
    w("create table if not exists %s (" % t)
    linhas = []
    if t in SEM_ID:
        chave = SEM_ID[t]
        if chave:
            linhas.append("  -- sem `id` sintetico: a identidade e `%s`" % chave)
            linhas.append("  %-26s bigint not null" % chave)
            cols.pop(chave, None)
        cols.pop("id", None)
    elif "id" in cols:
        tp = normalizar(cols.pop("id"), "id")
        # SO O TIPO decide a forma da chave, e nao o resto da declaracao.
        #
        # Esta comparacao era contra `tp` INTEIRO, e quebrou no dia em que
        # `normalizar` passou a preservar `not null` — que foi um conserto: sem
        # ele, 112 not-nulls e 43 defaults se perdiam. So que `pois.id` vem do
        # dump como `integer NOT NULL`, e "integer NOT NULL" nao e "integer":
        # a chave deixou de virar `identity` e SEIS tabelas nasceram com `id`
        # sem geracao nenhuma. O primeiro INSERT falhou com "null value in
        # column id" — longe da causa, que estava neste `if`.
        base = tp.split()[0].lower() if tp else ""
        pk = ("bigint generated always as identity" if base in ("integer", "bigint")
              else "uuid default gen_random_uuid()" if base == "uuid" else tp)
        linhas.append("  %-26s %s primary key" % ("id", pk))
    else:
        linhas.append("  %-26s bigint generated always as identity primary key" % "id")
    if tem:
        linhas.append("  %-26s uuid not null references core.tb_empresas(id)"
                      % "id_empresa")
    for col in sorted(cols):
        linhas.append("  %-26s %s" % (col, normalizar(cols[col], col)))
    w(",\n".join(linhas))
    w(");")
    if t in SEM_ID and SEM_ID[t]:
        w("alter table %s add constraint pk_%s primary key (%s);"
          % (t, t, SEM_ID[t]))
    w("")

w("""
-- ─────────────────────────────────────────────────────────────────────
-- Isolamento por empresa
-- ─────────────────────────────────────────────────────────────────────
--
-- `core.eh_suporte()` e `core.empresa_atual()` sao STABLE SECURITY DEFINER e
-- leem `(select auth.uid())` em subselect — o planejador as chama uma vez por
-- consulta, e nao uma vez por linha.
""")
for t in sorted(COM_EMPRESA):
    w("alter table %s enable row level security;" % t)
    w("alter table %s force  row level security;" % t)
    w("create policy p_%s on %s for all" % (t, t))
    w("  using      (core.eh_suporte() or id_empresa = core.empresa_atual())")
    w("  with check (core.eh_suporte() or id_empresa = core.empresa_atual());")
    w("")

w("""
-- ─────────────────────────────────────────────────────────────────────
-- Quem carimba a empresa
-- ─────────────────────────────────────────────────────────────────────
--
-- POR GATILHO, e nao por codigo de rota. Sao mais de trinta INSERTs espalhados
-- pelo pipeline e pela API; exigir `id_empresa` em cada um significa que o
-- primeiro esquecido grava linha sem dono. E linha sem dono NAO some: ela nasce
-- invisivel para todo mundo, e ninguem procura o que nao sabe que perdeu.
--
-- Variavel ausente deixa NULL, e o `not null` recusa. Falha barulhenta e melhor
-- que linha orfa: o erro aparece no primeiro insert, junto da causa.

create or replace function preencher_empresa() returns trigger
language plpgsql as $$
begin
  if new.id_empresa is null then
    new.id_empresa := core.empresa_atual();
  end if;
  return new;
end $$;

comment on function preencher_empresa() is
  'Carimba id_empresa a partir de core.empresa_atual(), que resolve pelo uuid '
  'do usuario em request.jwt.claim.sub. Ausente deixa NULL e o NOT NULL recusa.';
""")
for t in sorted(COM_EMPRESA):
    w("create trigger trg_empresa_%s before insert on %s" % (t, t))
    w("  for each row execute function preencher_empresa();")
w("")

w("""
-- ─────────────────────────────────────────────────────────────────────
-- Auditoria — quem FEZ, e nao quem PODE
-- ─────────────────────────────────────────────────────────────────────
--
-- Reusa `core.registrar_auditoria()`, que ja escreve em `core.tb_auditoria`
-- (particionada por mes) e ja e usada pelas oito tabelas do `core`. Escrever
-- uma segunda funcao de auditoria daria duas tabelas de log com formatos
-- diferentes para a mesma pergunta.
--
-- POR GATILHO, e nao por decorator: rota esquecida nao escapa do registro — foi
-- assim que 29 rotas ficaram sem autenticacao e ninguem percebeu por semanas —
-- e o pipeline, que nunca passaria por um decorator de FastAPI, tambem entra.
--
-- `pois` FICA DE FORA DO INSERT, DE PROPOSITO. O pipeline grava dezenas de
-- milhares por rodada; auditar isso encheria o log de ruido e esconderia
-- justamente o evento raro que se quer achar. O DELETE de POI, esse sim, e
-- registrado: apagar e o que ninguem deveria fazer sem deixar rastro.

create trigger tg_auditoria after insert or update or delete on atribuicao
  for each row execute function core.registrar_auditoria();

create trigger tg_auditoria_del after delete on pois
  for each row execute function core.registrar_auditoria();
""")

w("""
-- ─────────────────────────────────────────────────────────────────────
-- Chaves naturais — o que impede a mesma coisa de entrar duas vezes
-- ─────────────────────────────────────────────────────────────────────
""")
for nome, tab, cols, onde, porque in UNICOS:
    if tab not in PRODUTO:
        continue
    for linha in porque.split(". "):
        if linha.strip():
            w("-- " + linha.strip().rstrip(".") + ".")
    w("create unique index if not exists %s" % nome)
    w("  on %s (%s)%s;" % (tab, cols, ("\n  " + onde) if onde else ""))
    w("")

w("""
-- ─────────────────────────────────────────────────────────────────────
-- Indices de leitura
-- ─────────────────────────────────────────────────────────────────────
--
-- SAO OS INDICES QUE JA EXISTIAM, e nao um por tabela.
--
-- A primeira versao deste gerador emitia UM indice por tabela — `(id_empresa)`
-- e mais nada — e teria jogado fora 78 indices construidos para consultas
-- especificas ao longo de 42 migracoes. Nenhum teste falharia por isso: o
-- sistema funcionaria, so que varrendo. O sintoma apareceria como "o painel
-- ficou lento" meses depois, longe de qualquer commit.
--
-- Cada um foi reescrito com `id_empresa` no lugar de `tenant_id`. Politica de
-- RLS e avaliada por linha, entao indice que nao comeca pela coluna da empresa
-- nao serve a ela — e os que ja comecavam por `tenant_id` continuam servindo.
""")
vistos = {n for n, _, _, _, _ in UNICOS}
for ix in est["indices"]:
    tab = ix["tabela"]
    if tab not in PRODUTO or ix["unico"] or ix["nome"] in vistos:
        continue
    vistos.add(ix["nome"])
    cols = ix["colunas"].replace("tenant_id", "id_empresa")
    resto = ix["resto"].replace("tenant_id", "id_empresa").strip()
    nome = ix["nome"].replace("tenant", "empresa")
    w("create index if not exists %s on %s (%s)%s;"
      % (nome, tab, cols, ("\n  " + resto) if resto else ""))
w("")

w("""
-- Permissoes: `app_user` usa, nao possui. Sem isto ele enxerga o schema e nao
-- enxerga tabela nenhuma, com erro que fala de relacao inexistente.
grant usage on schema radar_comercial to app_user, readonly;
grant select, insert, update, delete on all tables in schema radar_comercial to app_user;
grant select on all tables in schema radar_comercial to readonly;
alter default privileges in schema radar_comercial
  grant select, insert, update, delete on tables to app_user;
alter default privileges in schema radar_comercial
  grant select on tables to readonly;
""")

io.open("migrations_a2l/0001_radar_comercial.sql", "w",
        encoding="utf-8", newline="\n").write("\n".join(L))

# ── o segundo arquivo: o que vive em `resources_root` ───────────────────────
#
# Schema SEPARADO e ciclo de vida separado: `resources_root` pertence a
# `resources_loader`, e lido por todas as ferramentas e nao tem RLS — nao ha o
# que isolar em base publica. Por isso nao pode entrar na mesma migracao do
# schema da ferramenta: dono diferente, e mudanca ali afeta os outros sistemas.
R = []
r = R.append
r("""-- 0002_resources_root.sql — base publica e cache compartilhado.
--
-- ESTE ARQUIVO MEXE EM INFRAESTRUTURA COMPARTILHADA. `resources_root` e lido
-- por todas as ferramentas do A2L; mudanca aqui afeta radarTelhados,
-- radarColetivas e o resto. Nao rodar sem combinar.
--
-- Dono do schema: `resources_loader` — nao `migrator`. Rodar como o dono, ou
-- conceder antes. Ver o rodape.
--
-- SEM RLS, e de proposito: base publica nao tem de quem esconder. O CNPJ da
-- Receita e o CNEFE do IBGE sao os mesmos para toda empresa, e por isso a
-- leitura e livre e a escrita e so de processo.
--
-- AS DUAS ULTIMAS TABELAS CHEGARAM AQUI EM 31/08/2026, e vale o registro: elas
-- viviam no schema da ferramenta. Sao cache de normalizacao de endereco — o
-- mesmo texto se separa igual para qualquer cliente E para qualquer ferramenta,
-- e cada segmentacao custa uma chamada ao modelo. Mante-las por ferramenta
-- seria pagar o mesmo trabalho tres vezes.

set local search_path = resources_root, public;
""")
for t in REFERENCIA:
    cols = dict(tipos.get(t, {}))
    cols.pop("tenant_id", None)
    if not cols:
        r("-- %s: nenhuma coluna levantada — conferir antes de criar." % t)
        r("")
        continue
    r("create table if not exists %s (" % t)
    linhas = []
    if "id" in cols:
        tp = normalizar(cols.pop("id"), "id")
        # SO O TIPO decide a forma da chave, e nao o resto da declaracao.
        #
        # Esta comparacao era contra `tp` INTEIRO, e quebrou no dia em que
        # `normalizar` passou a preservar `not null` — que foi um conserto: sem
        # ele, 112 not-nulls e 43 defaults se perdiam. So que `pois.id` vem do
        # dump como `integer NOT NULL`, e "integer NOT NULL" nao e "integer":
        # a chave deixou de virar `identity` e SEIS tabelas nasceram com `id`
        # sem geracao nenhuma. O primeiro INSERT falhou com "null value in
        # column id" — longe da causa, que estava neste `if`.
        base = tp.split()[0].lower() if tp else ""
        pk = ("bigint generated always as identity" if base in ("integer", "bigint")
              else "uuid default gen_random_uuid()" if base == "uuid" else tp)
        linhas.append("  %-26s %s primary key" % ("id", pk))
    for col in sorted(cols):
        linhas.append("  %-26s %s" % (col, normalizar(cols[col], col)))
    r(",\n".join(linhas))
    r(");")
    r("")

r("""
-- Leitura para todos, escrita so para quem carrega.
grant usage on schema resources_root to app_user, readonly, authenticated;
grant select on all tables in schema resources_root to app_user, readonly, authenticated;
alter default privileges in schema resources_root
  grant select on tables to app_user, readonly, authenticated;

-- O QUE FALTA, E QUE NAO DA PARA FAZER DAQUI: o pipeline do Radar Comercial
-- passa a ESCREVER o cache de endereco, e hoje ele nao tem essa permissao —
-- `resources_root` e de `resources_loader`. Alguem com o papel do dono precisa:
--
--   grant insert, update on resources_root.endereco_segmentado  to app_user;
--   grant insert, update on resources_root.logradouro_ajustado  to app_user;
--
-- Sem isso a segmentacao falha com "permission denied for table", que e erro
-- claro — mas so na primeira vez que rodar.
""")
io.open("migrations_a2l/0002_resources_root.sql", "w",
        encoding="utf-8", newline="\n").write("\n".join(R))

fora = [t for t in PRODUTO if t not in COM_EMPRESA]
print("gerado: migrations_a2l/0002_resources_root.sql  (%d tabelas)" % len(REFERENCIA))
print("gerado: migrations_a2l/0001_radar_comercial.sql")
print("  tabelas: %d | com isolamento: %d | colunas: %d"
      % (len(PRODUTO), len(COM_EMPRESA),
         sum(len(tipos.get(t, {})) for t in PRODUTO)))
print("\n  SEM isolamento — cada uma precisa de motivo:")
for t in fora:
    print("    %-24s %s" % (t, SEM_EMPRESA_DE_PROPOSITO.get(t, "*** SEM MOTIVO ***")))
