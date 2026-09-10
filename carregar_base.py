# -*- coding: utf-8 -*-
"""Põe o arquivo da base do cliente no Postgres, cru, e registra a base.

O CRU É PROPOSITAL. Cada cliente entrega a base no formato dele, e o passo
seguinte — a pessoa declarar qual coluna é o quê, na tela — só existe porque
não dá para adivinhar. Converter tipo aqui seria decidir antes de perguntar:
uma coluna `NRO` com "1509-A" quebraria um `integer`, e um CEP com zero à
esquerda viraria número e perderia o zero.

Então tudo entra como `text`, com os nomes originais do arquivo. A tabela
canônica — com tipos, índices, `geom` e RLS — é materializada DEPOIS, a partir
do que a pessoa declarou. Ver `materializar_base.py`.

POR QUE `COPY`, E NÃO `INSERT`. São 2.516.709 linhas. Em `execute_values` de
1.000 em 1.000 seriam 2.517 viagens de ida e volta e dezenas de minutos; o
`COPY FROM STDIN` é uma viagem só e o Postgres escreve direto, sem planejar
cada linha. Medido em bases desse tamanho: minutos contra dezenas deles.
"""
import argparse
import csv
import io
import json
import os
import re
import time

import base_comum as bc

#: Onde a API deposita o arquivo. Volume `radar-uploads`, montado só-leitura
#: aqui: quem grava é a API, quem carrega é este.
UPLOADS = os.environ.get("RADAR_UPLOADS", "/app/uploads")

#: O schema onde as bases cruas moram — o caixote do pipeline, criado pela
#: migração 0085.
#:
#: NÃO É `resources_root`, e a primeira versão tentou ser: `app_user` não tem
#: CREATE lá, e a carga morria com "permission denied for schema
#: resources_root" DEPOIS de o operador já ter subido o arquivo. A fronteira é
#: deliberada — migração cria estrutura, pipeline escreve linha —, e dar CREATE
#: no schema do cliente ao papel que roda o dia inteiro poria a tabela de 2,5
#: milhões de linhas ao alcance de qualquer defeito de script.
#:
#: A tabela daqui é TRANSITÓRIA: existe entre o upload e a materialização da
#: tabela canônica, e é recriada a cada carga.
SCHEMA = "base_bruta"


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def _nome_de_coluna(bruto, usados):
    """`COD_LATITUDE` vira `cod_latitude`; o que colide ganha sufixo.

    NOME DE COLUNA DO CLIENTE NÃO É IDENTIFICADOR VÁLIDO. Chegam acentos,
    espaço, barra, parêntese e — o pior — duas colunas com o mesmo nome, que o
    Postgres recusa e que faria a carga inteira falhar na última linha do
    `create table`, depois de o operador já ter esperado o upload.
    """
    import unicodedata
    s = unicodedata.normalize("NFKD", str(bruto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    s = s or "coluna"
    if s[0].isdigit():
        s = "c_" + s
    s = s[:58]
    base, n = s, 2
    while s in usados:
        s = "%s_%d" % (base, n)
        n += 1
    usados.add(s)
    return s


def espiar(caminho, quantas=12, com_unicos=True):
    """(colunas, separador, amostra) lendo só o começo. Não carrega a base.

    `colunas` é uma lista de `{"arquivo": <título original>, "coluna": <nome
    no banco>}`, e a amostra é indexada pelo NOME NO BANCO.

    POR QUE NÃO PELO TÍTULO ORIGINAL. Base de cliente vem com título repetido
    — no arquivo de teste de 09/09/2026, `CATEGORIA` aparecia duas vezes, uma
    com "RESIDENCIAL" e outra com "R". Um `dict(zip(cabecalho, linha))`
    colapsa as duas e guarda a última: a tela mostraria "R" nas duas colunas.

    Isso não é cosmético. A pessoa declara qual coluna é o tipo de cliente
    OLHANDO A AMOSTRA — é para isso que ela existe, porque nome de coluna
    sozinho engana. Amostra errada faz a declaração errada, e declarar errado
    a coluna de tipo faz o produto inteiro classificar comércio como
    residência.
    """
    with io.open(caminho, encoding="utf-8-sig", errors="replace",
                 newline="") as f:
        inicio = f.read(64 * 1024)
    linhas = inicio.splitlines()
    if not linhas:
        return [], ",", []
    sep = max((";", "\t", ","), key=lambda s: linhas[0].count(s))
    with io.open(caminho, encoding="utf-8-sig", errors="replace",
                 newline="") as f:
        rd = csv.reader(f, delimiter=sep)
        cab = [c.strip() for c in next(rd, [])]
        if not cab:
            return [], sep, []
        usados = set()
        unicos = [_nome_de_coluna(c, usados) for c in cab]
        amostra = []
        for i, linha in enumerate(rd):
            if i >= quantas:
                break
            amostra.append({u: (linha[j] if j < len(linha) else "")
                            for j, u in enumerate(unicos)})
    colunas = [{"arquivo": a, "coluna": u} for a, u in zip(cab, unicos)]
    return (colunas if com_unicos else cab), sep, amostra


def carregar(con, caminho, tabela, cab, sep):
    """`COPY` do arquivo para uma tabela toda de texto. Devolve as linhas."""
    usados = set()
    colunas = [_nome_de_coluna(c, usados) for c in cab]
    cur = con.cursor()
    cur.execute('drop table if exists %s.%s' % (SCHEMA, tabela))
    cur.execute('create table %s.%s (%s)'
                % (SCHEMA, tabela,
                   ", ".join('"%s" text' % c for c in colunas)))
    con.commit()

    # `HEADER true` FAZ O POSTGRES PULAR A PRIMEIRA LINHA. Sem isso o
    # cabeçalho vira uma linha de dados com "COD_LATITUDE" no lugar de um
    # número, e o erro só aparece na materialização, longe da causa.
    sql = ("copy %s.%s from stdin with (format csv, header true, "
           "delimiter %s, quote '\"', null '')"
           % (SCHEMA, tabela, _literal(sep)))
    with io.open(caminho, "r", encoding="utf-8-sig", errors="replace",
                 newline="") as f:
        try:
            cur.copy_expert(sql, f)
        except Exception:
            con.rollback()
            raise
    con.commit()
    cur.execute("select count(*) from %s.%s" % (SCHEMA, tabela))
    n = int(cur.fetchone()[0] or 0)
    return colunas, n


def _literal(s):
    return "'" + s.replace("'", "''") + "'"


def registrar(con, base_id, nome, cliente, arquivo, tabela, colunas_brutas,
              colunas, linhas):
    """Grava/atualiza a linha em `base_cliente` e a deixa em `rascunho`.

    `rascunho` E NÃO `pronta`: quem promove é a pessoa, na tela, e a `check` da
    migração 0045 recusa `pronta` sem as seis colunas declaradas. Este módulo
    não tem opinião sobre qual coluna é a latitude — ele só põe o dado onde a
    IA e a pessoa possam olhar.
    """
    cur = con.cursor()
    brutas = json.dumps([{"arquivo": a, "coluna": c}
                         for a, c in zip(colunas_brutas, colunas)])
    if base_id:
        cur.execute("""
            update radar_comercial.base_cliente
               set nome = coalesce(nullif(%s,''), nome),
                   cliente = coalesce(nullif(%s,''), cliente),
                   arquivo = %s, tabela_dados = %s, linhas = %s,
                   colunas_brutas = %s::jsonb,
                   -- A DECLARAÇÃO ANTERIOR NÃO SOBREVIVE A UM ARQUIVO NOVO.
                   -- Os nomes das colunas podem ter mudado, e um mapa velho
                   -- apontando para coluna que não existe mais é pior que
                   -- mapa nenhum: ele passa na tela como se estivesse certo.
                   mapa_colunas = null, sugerido_por_ia = null,
                   estado = 'rascunho',
                   confirmado_por = null, confirmado_em = null
             where id = %s
         returning id""",
                    (nome, cliente, os.path.basename(arquivo),
                     "%s.%s" % (SCHEMA, tabela), linhas, brutas, base_id))
        r = cur.fetchone()
        if not r:
            raise SystemExit("base %s não existe" % base_id)
        novo = r[0]
    else:
        cur.execute("""
            insert into radar_comercial.base_cliente
                (id_empresa, nome, cliente, arquivo, tabela_dados, linhas,
                 colunas_brutas, estado, criado_em)
            values ((select core.empresa_atual()), %s, %s, %s, %s, %s,
                    %s::jsonb, 'rascunho', now())
         returning id""",
                    (nome or "base", cliente or "", os.path.basename(arquivo),
                     "%s.%s" % (SCHEMA, tabela), linhas, brutas))
        novo = cur.fetchone()[0]
    con.commit()
    return novo


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Carrega o arquivo da base do cliente, cru, no Postgres")
    p.add_argument("--arquivo", required=True,
                   help="nome do arquivo dentro de %s" % UPLOADS)
    p.add_argument("--base", type=int, default=0,
                   help="id da base a atualizar; 0 cria uma nova")
    p.add_argument("--nome", default="")
    p.add_argument("--cliente", default="")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    caminho = os.path.join(UPLOADS, os.path.basename(a.arquivo))
    if not os.path.exists(caminho):
        raise SystemExit("não achei %s" % caminho)
    tam = os.path.getsize(caminho)
    _log("arquivo %s · %.1f MB" % (os.path.basename(caminho), tam / 1e6))

    colunas_info, sep, amostra = espiar(caminho)
    if not colunas_info:
        raise SystemExit("arquivo sem cabeçalho")
    cab = [c["arquivo"] for c in colunas_info]
    _log("%d coluna(s) · separador %r" % (len(cab), sep))
    for c in colunas_info[:12]:
        v = (amostra[0].get(c["coluna"], "") if amostra else "")
        rot = c["arquivo"]
        if c["coluna"].endswith(("_2", "_3", "_4")):
            rot += "  (repetida → %s)" % c["coluna"]
        _log("   %-34s ex.: %s" % (rot[:34], str(v)[:36]))
    if len(cab) > 12:
        _log("   … e mais %d" % (len(cab) - 12))

    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        return 0

    tabela = "base_%s_bruta" % (a.base or "nova")
    con = bc.conectar()
    t0 = time.time()
    _log("COPY para %s.%s ..." % (SCHEMA, tabela))
    colunas, n = carregar(con, caminho, tabela, cab, sep)
    _log("%d linha(s) em %.1f s" % (n, time.time() - t0))
    base_id = registrar(con, a.base, a.nome, a.cliente, caminho, tabela,
                        cab, colunas, n)
    con.close()
    _log("base %s em rascunho — falta declarar as colunas na tela" % base_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
