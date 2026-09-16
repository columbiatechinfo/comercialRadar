# -*- coding: utf-8 -*-
"""Da tabela crua declarada para a tabela canônica, e troca no lugar.

O QUE ESTE PASSO RESOLVE. `carregar_base.py` põe o arquivo no Postgres com os
nomes do cliente e tudo como texto — de propósito, porque ninguém sabe o que é
o quê antes de a pessoa declarar. Depois da declaração, alguém precisa
transformar aquilo na tabela que o produto consome. É este módulo.

POR QUE NÃO UMA VIEW. `resources_root.cadastro_corsan` tem sete índices, entre
eles um GiST em `geom` de que o cruzamento depende — `st_dwithin` sobre 2,5
milhões de linhas. View não tem índice: o cruzamento viraria varredura
sequencial e o que hoje leva segundos passaria a levar minutos por cidade.

POR QUE A TABELA NOVA NASCE SEM RLS. `cadastro_corsan` tem
`relforcerowsecurity`, ou seja, nem o dono escapa da política — e
`p_corsan_escreve` chama `core.eh_suporte()` e `core.hierarquia_atual()` SEM
subselect, então é uma chamada de função POR LINHA. Inserir 2,5 milhões de
linhas sob ela é a mesma armadilha que a migração 0081 corrigiu do lado da
leitura, onde custava 14,5 s por consulta. Carregando antes de ligar a RLS, o
custo não existe: a política entra depois, junto com os índices.

A TROCA É POR RENAME, DENTRO DE UMA TRANSAÇÃO. `drop` seguido de `rename`
deixaria uma janela em que a tabela não existe — e há 16 arquivos do produto
que a consultam pelo nome. Renomear as duas numa transação é atômico: quem
consulta vê a velha ou a nova, nunca o vazio. E a anterior fica guardada como
`cadastro_corsan_anterior`, então voltar atrás é outro rename e não uma
restauração de backup.
"""
import argparse
import json
import os
import time

import psycopg2

#: Papel declarado na tela → coluna da tabela canônica.
#:
#: São os dez que a tela pergunta. O produto consome MAIS que isso — ver
#: `EXTRAS` — mas esses dez são os que a pessoa declara, e os únicos sem os
#: quais nada funciona.
PAPEIS = {
    "ligacao":      ("num_ligacao",   "bigint"),
    "endereco":     ("nom_logradouro", "text"),
    "numero":       ("nro",           "text"),
    "bairro":       ("nom_bairro",    "text"),
    "cep":          ("cod_cep",       "text"),
    "cidade":       ("cidade",        "text"),
    "tipo_cliente": ("categoria",     "text"),
    "situacao":     ("sit_ligacao",   "text"),
    "latitude":     ("cod_latitude",  "numeric"),
    "longitude":    ("cod_longitude", "numeric"),
    # A QUALIFICACAO DO CLIENTE (dono do produto, 16/09/2026): SIM, SIM_COM_ANALISE_HUMANA ou NAO, como coluna da
    # base. Sem ela `apta_cruzamento` (coluna gerada: qualificacao like 'SIM%') fica falsa e o vinculo descarta
    # tudo (`regra_vinculo.py`). O texto vira um dos tres status pela mesma regra do `aplicar_qualificacao`, e o
    # que vem depois da virgula ("NAO, sem economia faturada") vai para `qualificacao_motivo`.
    "qualificacao": ("qualificacao",  "qualificacao"),
}

#: O QUE O PRODUTO USA ALÉM DO QUE A TELA PERGUNTA.
#:
#: A declaração cobre dez papéis; o pipeline lê dezesseis colunas. Estas seis
#: não são perguntadas e, se a base não as trouxer com o nome canônico, ficam
#: nulas — com consequência real e silenciosa:
#:
#:   `qtd_eco_com`/`qtd_eco_ind`  alimentam uma REGRA DURA do julgamento: a
#:       ligação que já declara economia comercial não tem o que reclassificar
#:       e é reprovada. Nulas, essa regra para de reprovar e o operador passa a
#:       visitar cliente que já paga tarifa comercial.
#:   `nom_cliente`, `num_medidor`, `classificacao`  aparecem na planilha que
#:       vai para a rua; nulas, quem bate na porta perde o nome e o medidor.
#:
#: Por isso o casamento é POR NOME quando o papel não foi declarado: se a base
#: já vier no formato canônico, elas entram sozinhas. O relatório no fim diz
#: quais ficaram vazias, para a ausência ser vista e não descoberta depois.
EXTRAS = {
    "nom_cliente": "text", "qtd_eco_res": "smallint", "qtd_eco_com": "smallint",
    "qtd_eco_ind": "smallint", "qtd_eco_pub": "smallint",
    "num_medidor": "text", "classificacao": "text", "end_ligacao": "text",
    "sub_categoria": "text", "utilizacao": "text", "num_doc_1": "text",
    "tipo_faturamento": "text", "tpo_ligacao": "text", "regional": "text",
}

CANONICA = "resources_root.cadastro_corsan"
ANTERIOR = "resources_root.cadastro_corsan_anterior"
NOVA = "resources_root.cadastro_corsan_nova"

INDICES = [
    ("pk_cadastro_corsan", "unique", "btree (id_empresa, num_ligacao)"),
    ("ix_corsan_base", "", "btree (id_empresa, id_base)"),
    ("ix_corsan_cep", "", "btree (id_empresa, cod_cep)"),
    ("ix_corsan_doc", "", "btree (id_empresa, num_doc_1)"),
    ("ix_corsan_geom", "", "gist (geom)"),
    ("ix_corsan_lugar", "", "btree (id_empresa, cidade, nom_bairro)"),
    ("ix_corsan_situacao", "", "btree (id_empresa, sit_ligacao, categoria)"),
]


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def _limpo(coluna, tipo):
    """A expressão que converte a coluna de texto para o tipo canônico.

    NADA AQUI PODE ESTOURAR A CARGA INTEIRA. Uma linha com "1509-A" numa coluna
    que vira `bigint`, ou uma latitude escrita com vírgula decimal, derrubaria
    um `insert ... select` de 2,5 milhões de linhas na linha errada — e o
    operador veria "invalid input syntax" sem saber qual das milhões. Por isso
    cada conversão limpa o que sabe limpar e devolve NULL no que não entende.
    """
    c = '"%s"' % coluna
    if tipo in ("qualificacao", "qualificacao_motivo"):
        # A MESMA REGRA DA PLANILHA DE CANOAS, importada e nao copiada. O `%%` de la existe para o `%` da
        # formatacao do SQL de la; aqui a expressao entra pronta, e o `%%` viraria dois curingas.
        import aplicar_qualificacao as aq
        modelo = aq.STATUS if tipo == "qualificacao" else aq.MOTIVO
        return "(%s)" % modelo.replace("%%", "%").format(col=coluna)
    if tipo == "bigint":
        # Só os dígitos, e nulo se sobrar nada. `nullif` evita `''::bigint`.
        return ("nullif(regexp_replace(%s, '[^0-9]', '', 'g'), '')::bigint" % c)
    if tipo == "smallint":
        return ("coalesce(nullif(regexp_replace(%s, '[^0-9-]', '', 'g'), '')"
                "::smallint, 0)" % c)
    if tipo == "numeric":
        # VÍRGULA DECIMAL É O PADRÃO BRASILEIRO e chega assim em base de
        # companhia estadual. Sem esta troca, `-29,918450` vira NULL e a
        # ligação desaparece do mapa sem nenhum erro.
        return ("nullif(replace(btrim(%s), ',', '.'), '')::numeric" % c)
    return "nullif(btrim(%s), '')" % c


def plano(cur, base_id):
    """Lê a base declarada e devolve o de-para pronto, sem tocar em nada."""
    cur.execute("""
        select id_empresa, tabela_dados, mapa_colunas, colunas_brutas,
               estado, nome, linhas
          from radar_comercial.base_cliente where id = %s""", (base_id,))
    r = cur.fetchone()
    if not r:
        raise SystemExit("base %s não existe" % base_id)
    id_empresa, tabela, mapa, brutas, estado, nome, linhas = r
    if estado != "pronta":
        raise SystemExit(
            "a base %s está em '%s'. Só materializo o que foi CONFIRMADO na "
            "tela — é lá que alguém declara qual coluna é o quê, e sem isso "
            "eu estaria adivinhando." % (base_id, estado))
    if isinstance(mapa, str):
        mapa = json.loads(mapa)
    if isinstance(brutas, str):
        brutas = json.loads(brutas)
    brutas = [b["coluna"] if isinstance(b, dict) else b for b in (brutas or [])]

    destino = {}
    for papel, (col, tipo) in PAPEIS.items():
        origem = (mapa or {}).get(papel)
        if origem:
            destino[col] = (origem, tipo)
    if "qualificacao" in destino:
        destino["qualificacao_motivo"] = (destino["qualificacao"][0], "qualificacao_motivo")
    # O casamento por nome cobre o que a tela não pergunta.
    for col, tipo in EXTRAS.items():
        if col not in destino and col in brutas:
            destino[col] = (col, tipo)
    return id_empresa, tabela, destino, nome, linhas


def materializar(con, base_id, id_empresa, tabela, destino):
    cur = con.cursor()
    schema, cru = tabela.split(".", 1)

    _log("criando %s com a estrutura da canônica..." % NOVA)
    cur.execute("drop table if exists %s" % NOVA)
    # SEM `including indexes`: índice criado antes da carga é índice mantido a
    # cada uma das 2,5 milhões de linhas. Depois é uma construção só.
    cur.execute("create table %s (like %s including defaults "
                "including generated)" % (NOVA, CANONICA))
    con.commit()

    colunas = ["id_empresa", "id_base"] + sorted(destino)
    valores = ["%s::uuid" % _lit(str(id_empresa)), str(int(base_id))]
    valores += [_limpo(*destino[c]) for c in sorted(destino)]
    sql = ('insert into %s (%s) select %s from %s."%s"'
           % (NOVA, ", ".join('"%s"' % c for c in colunas),
              ", ".join(valores), schema, cru))
    _log("carregando...")
    t0 = time.time()
    cur.execute(sql)
    n = cur.rowcount
    con.commit()
    _log("%d linha(s) em %.1f s" % (n, time.time() - t0))

    # A CONTAGEM VEM ANTES DA RLS, e isto não é ordem arbitrária.
    #
    # Ela estava depois, e devolveu ZERO com as cinco linhas gravadas e a
    # `geom` preenchida — o guarda anunciou "nenhuma linha ganhou coordenada"
    # sobre uma carga perfeita. A causa: a tabela nova já tinha
    # `force row level security`, e esta conexão é a do `migrator`, que não
    # carrega claim de JWT. `core.empresa_atual()` devolve NULL, o `id_empresa
    # = NULL` é NULL, a política nega tudo e o `count` responde 0 sem erro
    # nenhum.
    #
    # Política com `using` filtra calada: não levanta exceção, devolve vazio.
    # Um alarme que dispara à toa é pior que alarme nenhum, porque ensina a
    # ignorar — e o próximo, verdadeiro, passa batido.
    cur.execute("select count(*) filter (where geom is not null) from %s" % NOVA)
    com_geom = int(cur.fetchone()[0] or 0)

    _log("índices...")
    for nome_ix, unico, corpo in INDICES:
        t1 = time.time()
        cur.execute("create %s index %s_nv on %s using %s"
                    % (unico, nome_ix, NOVA, corpo))
        con.commit()
        _log("   %-22s %.1f s" % (nome_ix, time.time() - t1))

    # A RLS ENTRA AGORA, e a de escrita entra CORRIGIDA.
    #
    # `p_corsan_escreve` na tabela viva chama `core.eh_suporte()` e
    # `core.hierarquia_atual()` nus — uma chamada por linha, que é o defeito
    # que a migração 0081 corrigiu só do lado da leitura. Recriá-la aqui do
    # jeito antigo seria carregar o defeito para a tabela nova de propósito.
    _log("RLS...")
    cur.execute("alter table %s enable row level security" % NOVA)
    cur.execute("alter table %s force row level security" % NOVA)
    cur.execute("""
        create policy p_corsan_le on %s for select
            using ((select core.eh_suporte())
                   or id_empresa = (select core.empresa_atual()))""" % NOVA)
    cur.execute("""
        create policy p_corsan_escreve on %s for all
            using ((select core.eh_suporte())
                   or (id_empresa = (select core.empresa_atual())
                       and (select core.hierarquia_atual()) >= 40))""" % NOVA)
    con.commit()
    return n, com_geom


def trocar(con):
    """A troca atômica. Renomeia as duas dentro de UMA transação."""
    cur = con.cursor()
    cur.execute("drop table if exists %s cascade" % ANTERIOR)
    con.commit()
    cur.execute("begin")
    try:
        cur.execute("alter table %s rename to cadastro_corsan_anterior"
                    % CANONICA)
        cur.execute("alter table %s rename to cadastro_corsan" % NOVA)
        # Os índices da nova ainda têm o sufixo `_nv`; sem renomear, a próxima
        # materialização colide com eles.
        for nome_ix, _u, _c in INDICES:
            cur.execute("alter index resources_root.%s_nv rename to %s"
                        % (nome_ix, nome_ix))
        con.commit()
    except Exception:
        con.rollback()
        raise


def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Materializa a base declarada e troca no lugar")
    p.add_argument("--base", type=int, required=True)
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--trocar", action="store_true",
                   help="além de materializar, substitui a tabela em uso")
    a = p.parse_args(argv)

    url = os.environ.get("A2L_MIGRATOR_URL")
    if not url:
        raise SystemExit("sem A2L_MIGRATOR_URL — materializar cria tabela e "
                         "índice, que o papel do pipeline não pode fazer")
    con = psycopg2.connect(url)
    cur = con.cursor()
    id_empresa, tabela, destino, nome, linhas = plano(cur, a.base)

    _log("base %s «%s» · %s · %s linha(s) declaradas"
         % (a.base, nome, tabela, linhas))
    _log("%d coluna(s) mapeada(s):" % len(destino))
    for col in sorted(destino):
        _log("   %-18s ← %s" % (col, destino[col][0]))
    vazias = [c for c in list(PAPEIS.values()) if c[0] not in destino]
    if vazias:
        _log("PAPÉIS NÃO DECLARADOS: %s"
             % ", ".join(c[0] for c in vazias))
    if "qualificacao" not in destino:
        # A TABELA NOVA SUBSTITUI A INTEIRA: sem a coluna declarada, TODAS as ligacoes saem sem qualificacao —
        # inclusive as de cidades ja qualificadas antes —, `apta_cruzamento` fica falsa e o vinculo descarta tudo.
        _log("ATENÇÃO: sem a coluna de QUALIFICAÇÃO (SIM / SIM com análise / NÃO) a base")
        _log("   inteira sai sem qualificação, e o vínculo descarta todas as ligações.")
    faltam = [c for c in EXTRAS if c not in destino]
    if faltam:
        _log("FICAM NULAS (o arquivo não as traz e a tela não as pergunta):")
        _log("   %s" % ", ".join(faltam))
        if "qtd_eco_com" in faltam:
            _log("   ATENÇÃO: sem `qtd_eco_com` a regra que reprova ligação")
            _log("   que JÁ paga tarifa comercial para de valer, e o operador")
            _log("   passa a visitar quem já é cliente comercial.")

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        con.close()
        return 0

    n, com_geom = materializar(con, a.base, id_empresa, tabela, destino)
    _log("%d linha(s) · %d com coordenada" % (n, com_geom))
    if not com_geom:
        _log("NENHUMA linha ganhou coordenada. Confira se latitude e longitude")
        _log("foram declaradas nas colunas certas — sem `geom` o cruzamento")
        _log("espacial não acha candidato nenhum.")

    if not a.trocar:
        _log("materializada em %s. Use --trocar para substituir a em uso." % NOVA)
        con.close()
        return 0
    trocar(con)
    _log("trocada. A anterior ficou em %s — voltar atrás é outro rename."
         % ANTERIOR)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
