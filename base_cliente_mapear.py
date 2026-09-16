# -*- coding: utf-8 -*-
"""base_cliente_mapear.py — a IA lê a base do cliente e diz qual coluna é o quê.

O QUE ESTE PASSO RESOLVE

Cada cliente entrega a base no formato dele. A latitude pode se chamar `lat`,
`LATITUDE`, `y` ou `coord_y`; o número da ligação pode ser `num_ligacao`,
`instalacao` ou `matricula`; o tipo de cliente pode estar em `categoria`,
`tipo_lig` ou `classe`. Sem alguém DECLARAR o que é o quê, o sistema adivinha —
e adivinhar errado a coluna de tipo faz o produto inteiro classificar comércio
como residência.

Este módulo produz a SUGESTÃO. Ele não decide: grava em
`base_cliente.sugerido_por_ia` e deixa a base em `rascunho`. Quem confirma é uma
pessoa, na tela — e é a confirmação que muda o estado para `pronta` e libera
escolher cidade ou desenhar área. O banco impõe isso: a `check` da migração
0045 recusa `pronta` sem as seis declaradas.

O QUE VAI PARA A IA, E POR QUE ASSIM

Nome da coluna sozinho engana: `categoria` pode ser tipo de cliente numa base e
categoria de produto noutra. Então vão junto os TÍTULOS REAIS e uma AMOSTRA DE
LINHAS REAIS — que é o mesmo que a tela mostra ao usuário. O modelo vê
`categoria` com valores `COMERCIAL, RESIDENCIAL, INDUSTRIAL` e não tem como
confundir.

E vão os VALORES DISTINTOS da coluna candidata a tipo de cliente, porque a
sexta pergunta — quais desses tipos indicam atividade comercial — só se responde
vendo a lista. `RESIDENCIAL` é fácil; `MISTO`, `PUBLICO` e `ENTIDADE` não são, e
é justamente aí que a decisão precisa de gente.

A IA É A DA SPARK, e não o assistente do navegador. Isto é classificação sobre
texto curto e estruturado — o que o modelo local faz em segundos, de graça e sem
sair da rede. Assistente público existe para o que precisa da web aberta.

NADA É INVENTADO. Se o modelo apontar uma coluna que não existe na base, a
sugestão daquele campo é descartada aqui mesmo, e o campo volta vazio para a
pessoa preencher. Sugestão errada que passa por boa é pior que sugestão ausente.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter

import base_comum as bc

SPARK = os.environ.get("SPARK_LLM_URL", "http://192.168.3.20:7400/v1")
MODELO = os.environ.get("SPARK_MODELO_MAPA", "ia-principal")

# As seis do fluxo. `tipos_comerciais` não é coluna: é a escolha de valores.
CAMPOS = {
    "latitude": "a latitude do ponto, em graus decimais",
    "longitude": "a longitude do ponto, em graus decimais",
    "ligacao": "o número da instalação/ligação do cliente, que identifica o imóvel",
    "endereco": "o endereço; se estiver partido em várias colunas, a do LOGRADOURO",
    "tipo_cliente": "o tipo/categoria do cliente: habitacional, comércio, indústria...",
}
# Colunas que costumam completar o endereço quando ele vem partido. Não são
# obrigatórias, mas achá-las evita que o endereço chegue sem número na etapa 7.
COMPLEMENTARES = {
    "numero": "o número da porta",
    "bairro": "o bairro",
    "cep": "o CEP",
    "cidade": "a cidade",
    "qualificacao": "a qualificação do cliente para o cruzamento: SIM, SIM com análise humana ou NÃO",
}
AMOSTRA_LINHAS = 12
TETO_VALORES = 40


def _log(m: str) -> None:
    print(m, flush=True)


def amostrar(cur, schema: str, tabela: str, n: int = AMOSTRA_LINHAS) -> tuple:
    cur.execute("""
        select column_name from information_schema.columns
         where table_schema=%s and table_name=%s order by ordinal_position
    """, (schema, tabela))
    colunas = [r[0] for r in cur.fetchall()]
    if not colunas:
        return [], []
    lista = ", ".join('"%s"' % c for c in colunas)
    # `tablesample` numa base de milhões evita varrer tudo só para ver 12 linhas.
    # Em tabela pequena ele pode voltar vazio; daí o `limit` simples atrás.
    try:
        cur.execute('select %s from %s.%s tablesample system (0.01) limit %d'
                    % (lista, schema, tabela, n))
        linhas = cur.fetchall()
    except Exception:                                          # noqa: BLE001
        linhas = []
    if not linhas:
        cur.execute('select %s from %s.%s limit %d' % (lista, schema, tabela, n))
        linhas = cur.fetchall()
    return colunas, linhas


def valores_distintos(cur, schema: str, tabela: str, coluna: str) -> list:
    """Os valores que a coluna assume, com contagem. É o que responde a sexta
    pergunta — quais tipos indicam atividade comercial."""
    try:
        cur.execute('''
            select "%s"::text, count(*) from %s.%s
             where "%s" is not null group by 1 order by 2 desc limit %d
        ''' % (coluna, schema, tabela, coluna, TETO_VALORES))
        return cur.fetchall()
    except Exception:                                          # noqa: BLE001
        return []


def _prompt(colunas: list, linhas: list) -> str:
    cabecalho = " | ".join(colunas)
    corpo = "\n".join(" | ".join(str(v)[:26] if v is not None else ""
                                 for v in linha) for linha in linhas)
    campos = "\n".join("    %-14s %s" % (k, v)
                       for k, v in list(CAMPOS.items()) + list(COMPLEMENTARES.items()))
    return (
        "Esta é uma amostra de uma base de cadastro de clientes de uma empresa "
        "de saneamento. A primeira linha são os títulos das colunas.\n\n"
        + cabecalho + "\n" + corpo + "\n\n"
        "Diz qual coluna corresponde a cada campo abaixo:\n" + campos + "\n\n"
        "Responde APENAS um JSON, um objeto, com uma chave por campo e o valor "
        "sendo o NOME EXATO da coluna, copiado da lista de títulos. "
        "Usa null no campo que não existir nesta base — não force. "
        "Se o endereço estiver partido em várias colunas, aponta em 'endereco' "
        "a do logradouro, e preenche 'numero', 'bairro' e 'cep' com as demais."
    )


def _prompt_tipos(coluna: str, valores: list) -> str:
    lista = "\n".join("    %-30s %d linhas" % (str(v)[:30], n) for v, n in valores)
    return (
        "Numa base de clientes de saneamento, a coluna '%s' guarda o tipo de "
        "cliente. Estes são os valores que ela assume:\n\n%s\n\n"
        "Quais desses valores indicam ATIVIDADE COMERCIAL — um estabelecimento "
        "que atende público ou opera um negócio no local? "
        "Considera comercial o comércio, o serviço e a indústria; não considera "
        "o residencial nem o terreno vago. "
        "Responde APENAS um JSON: {\"comerciais\": [...], \"duvidosos\": [...]} "
        "com os valores copiados letra por letra. Põe em 'duvidosos' o que "
        "depende de decisão humana, como misto, público ou entidade."
        % (coluna, lista))


def _perguntar(pergunta: str, teto: int = 4000):
    dados = json.dumps({
        "model": MODELO,
        "messages": [{"role": "user", "content": pergunta}],
        "temperature": 0, "max_tokens": teto,
    }).encode()
    req = urllib.request.Request(SPARK + "/chat/completions", data=dados,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as h:
            t = json.load(h)["choices"][0]["message"]["content"]
    except Exception as erro:                                  # noqa: BLE001
        _log("   ⚠️  a IA da Spark não respondeu (%s: %s)"
             % (type(erro).__name__, str(erro)[:60]))
        return None
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None


def sugerir(schema: str, tabela: str) -> dict:
    con = bc.conectar()
    cur = con.cursor()
    colunas, linhas = amostrar(cur, schema, tabela)
    if not colunas:
        _log("   ⚠️  %s.%s não existe ou não tem colunas" % (schema, tabela))
        con.close()
        return {}
    _log("   %d colunas · %d linhas de amostra" % (len(colunas), len(linhas)))

    bruto = _perguntar(_prompt(colunas, linhas)) or {}

    # A TRAVA: coluna sugerida tem de EXISTIR na base. O modelo às vezes
    # devolve um nome plausível que não está na lista — e um de-para apontando
    # para coluna inexistente quebra a importação lá na frente, longe daqui.
    existentes = {c.lower(): c for c in colunas}
    mapa, inventadas = {}, []
    for campo in list(CAMPOS) + list(COMPLEMENTARES):
        v = bruto.get(campo)
        if not v or str(v).strip().lower() in ("null", "none", ""):
            continue
        achada = existentes.get(str(v).strip().lower())
        if achada:
            mapa[campo] = achada
        else:
            inventadas.append((campo, v))

    if inventadas:
        _log("   %d sugestões descartadas por apontarem coluna inexistente:"
             % len(inventadas))
        for campo, v in inventadas:
            _log("      %-14s -> %s" % (campo, str(v)[:40]))

    # A sexta pergunta: quais valores do tipo são comerciais.
    tipos = {"comerciais": [], "duvidosos": [], "valores": []}
    if mapa.get("tipo_cliente"):
        valores = valores_distintos(cur, schema, tabela, mapa["tipo_cliente"])
        tipos["valores"] = [[str(v), n] for v, n in valores]
        _log("   coluna de tipo: %s · %d valores distintos"
             % (mapa["tipo_cliente"], len(valores)))
        if valores:
            r = _perguntar(_prompt_tipos(mapa["tipo_cliente"], valores)) or {}
            reais = {str(v).strip().lower() for v, _ in valores}
            for chave in ("comerciais", "duvidosos"):
                tipos[chave] = [x for x in (r.get(chave) or [])
                                if str(x).strip().lower() in reais]
    con.close()
    return {"colunas": colunas, "amostra": [[str(v) if v is not None else None
                                             for v in l] for l in linhas],
            "mapa": mapa, "tipos": tipos}


def gravar(nome: str, cliente: str, schema: str, tabela: str,
           sugestao: dict) -> int:
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("select count(*) from %s.%s" % (schema, tabela))
    linhas = cur.fetchone()[0]
    cur.execute("""
        insert into radar_comercial.base_cliente
            (nome, cliente, arquivo, tabela_dados, linhas, colunas_brutas,
             mapa_colunas, sugerido_por_ia, estado)
        values (%s, %s, %s, %s, %s, %s, %s, %s, 'rascunho')
        on conflict (id_empresa, nome) do update set
            tabela_dados = excluded.tabela_dados,
            linhas = excluded.linhas,
            colunas_brutas = excluded.colunas_brutas,
            mapa_colunas = excluded.mapa_colunas,
            sugerido_por_ia = excluded.sugerido_por_ia
        returning id
    """, (nome, cliente, "%s.%s" % (schema, tabela), "%s.%s" % (schema, tabela),
          linhas, json.dumps(sugestao.get("colunas") or []),
          json.dumps(sugestao.get("mapa") or {}),
          json.dumps({"mapa": sugestao.get("mapa"),
                      "tipos": sugestao.get("tipos"),
                      "amostra": (sugestao.get("amostra") or [])[:6]},
                     ensure_ascii=False)))
    novo = cur.fetchone()[0]
    con.commit()
    con.close()
    return novo


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="A IA sugere qual coluna da base do cliente é o quê.")
    p.add_argument("--tabela", required=True,
                   help="schema.tabela da base já carregada")
    p.add_argument("--nome", default="", help="como chamar esta base")
    p.add_argument("--cliente", default="")
    p.add_argument("--aplicar", action="store_true",
                   help="grava a sugestão em base_cliente, como RASCUNHO")
    a = p.parse_args(argv)

    if "." not in a.tabela:
        _log("use schema.tabela, por exemplo resources_root.cadastro_corsan")
        return 1
    schema, tabela = a.tabela.split(".", 1)
    nome = a.nome or tabela

    _log("▶ mapear %s.%s" % (schema, tabela))
    s = sugerir(schema, tabela)
    if not s:
        return 1

    _log("\n   O QUE A IA SUGERE:")
    for campo in list(CAMPOS) + list(COMPLEMENTARES):
        v = s["mapa"].get(campo)
        obrig = "obrigatória" if campo in CAMPOS else "opcional  "
        _log("      %-14s %-12s %s" % (campo, obrig, v or "— não achou"))

    t = s["tipos"]
    if t["valores"]:
        _log("\n   VALORES DO TIPO DE CLIENTE (os %d mais frequentes):"
             % min(8, len(t["valores"])))
        for v, n in t["valores"][:8]:
            marca = ("comercial" if v in t["comerciais"]
                     else "duvidoso " if v in t["duvidosos"] else "         ")
            _log("      %s  %-28s %8d" % (marca, str(v)[:28], n))

    faltam = [c for c in CAMPOS if not s["mapa"].get(c)]
    if faltam:
        _log("\n   ⚠️  faltam obrigatórias: %s" % ", ".join(faltam))
        _log("      A base fica em RASCUNHO até uma pessoa completar na tela.")

    if a.aplicar:
        i = gravar(nome, a.cliente, schema, tabela, s)
        _log("\n   gravada como base_cliente id=%d, estado RASCUNHO" % i)
        _log("   Confirmar na tela é o que libera escolher área ou cidade.")
    else:
        _log("\n   (ensaio: nada gravado. Use --aplicar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
