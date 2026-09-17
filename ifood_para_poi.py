# -*- coding: utf-8 -*-
"""ifood_para_poi.py — as lojas do iFood viram ponto, e o CNPJ sai da gaveta.

O BURACO QUE ISTO FECHA

Em 02/09/2026 o iFood tinha **1.537 lojas** no banco, **1.529 com CNPJ** — e a
lista principal de POIs tinha **77 CNPJs**, todos do Cadastur. Os 1.537 viviam
em `ifood_merchant`, tabela ao lado, e não eram ponto: não recebiam endereço na
etapa de logradouro, não apareciam no painel, não contavam para nada.

A ligação sempre esteve projetada e nunca preenchida — `ifood_merchant.poi_id`
existe desde a criação da tabela, com 0 linhas preenchidas, e `pois` já tem
`presente_no_ifood` e `ifood_visto_em` esperando. Este arquivo é o passo que
faltava.

NÃO HÁ CRUZAMENTO AQUI, E ISSO É REGRA

Regra do dono do produto: nesta fase nenhuma etapa compara uma base com as
outras — as comparações são da base com ela mesma. "Não tem problema o mesmo
item aparecer em bases diversas, porque as informações lá no final se
complementarão."

Então este arquivo **não** procura o POI equivalente para enriquecer, **não**
funde e **não** decide que duas linhas são o mesmo lugar. Ele insere a loja do
iFood como ponto próprio, com `fonte = 'ifood'`. Se o mesmo estabelecimento já
existe vindo do estadual, passam a existir os dois — e o cruzamento, numa fase
posterior, resolve.

A ÚNICA RECUSA VEM DO BANCO, e é a base com ela mesma: o índice
`pois_sem_duplicata` proíbe dois pontos ativos com o MESMO nome e o MESMO
endereço. Nesse caso o `on conflict do nothing` pula a linha, ela fica sem
`poi_id`, e o número aparece no relatório em vez de sumir.

O ENDEREÇO É MONTADO NO FORMATO QUE A ETAPA SEGUINTE LÊ

    Rua do Sindicato, 13 - Harmonia, Canoas - RS, 92325-370

É o mesmo formato que os POIs do Maps já usam, e é o que o libpostal fraciona
melhor — `road`, `house_number`, `suburb`, `city`, `postcode` saem todos
separados. Montar diferente aqui faria a etapa de logradouro trabalhar pior sem
motivo.

O QUE ENTRA NO PONTO

    nome, categoria, telefone, CNPJ    direto da loja
    coordenada                          `lat_origem` / `lng_origem`
    presente_no_ifood, ifood_visto_em   a marca de que veio de lá
    fonte_dado = 'ifood:<merchant_id>'  a volta para a linha de origem

O CNPJ VEM SEM PONTUAÇÃO, como já está na `ifood_merchant`. Formatar aqui criaria
duas grafias do mesmo CNPJ no banco, e a comparação com a Receita passaria a
depender de qual delas o consultante escolheu.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter

import psycopg2.extras

import base_comum as bc

# `de`, `do`, `da`... não recebem maiúscula no meio do nome de cidade. Sem isto,
# `SAPUCAIA DO SUL` viraria `Sapucaia Do Sul`, que não é como a cidade se escreve
# e não é como as outras fontes gravaram.
MINUSCULAS = {"de", "do", "da", "dos", "das", "e", "d"}


def _log(msg: str) -> None:
    print(msg, flush=True)


def nome_de_cidade(s: str) -> str:
    partes = (s or "").strip().lower().split()
    if not partes:
        return ""
    return " ".join(p if i and p in MINUSCULAS else p.capitalize()
                    for i, p in enumerate(partes))


def montar_endereco(rua, numero, bairro, cidade, uf, cep) -> str:
    """`Rua do Sindicato, 13 - Harmonia, Canoas - RS, 92325-370`.

    Cada pedaço ausente some com o separador dele: endereço com vírgula solta
    ou hífen órfão confunde o parser da etapa seguinte, que passa a ver campo
    vazio onde não há campo.
    """
    rua = (rua or "").strip()
    if not rua:
        return ""
    saida = rua
    if numero and str(numero).strip():
        saida += ", %s" % str(numero).strip()
    if bairro and bairro.strip():
        saida += " - %s" % bairro.strip()
    cidade = nome_de_cidade(cidade)
    if cidade:
        saida += ", %s" % cidade
        if uf and uf.strip():
            saida += " - %s" % uf.strip().upper()
    cep = re.sub(r"\D", "", cep or "")
    if len(cep) == 8:
        saida += ", %s-%s" % (cep[:5], cep[5:])
    return saida


SQL_LOJAS = """
    select id, merchant_id, nome, categoria, telefone, cnpj,
           rua, numero, bairro, cidade, uf, cep, lat, lng, visto_em
      from radar_comercial.ifood_merchant
     where poi_id is null
       and coalesce(nome,'') <> ''
       %s
     order by id
"""

COLUNAS = ("nome", "endereco", "cidade", "uf", "categoria", "telefone", "cnpj",
           "lat_origem", "lng_origem", "fonte", "fonte_dado", "status",
           "presente_no_ifood", "ifood_visto_em", "endereco_fonte")


def do_municipio(cidade: str = "", limite: int = 0,
                 aplicar: bool = False) -> dict:
    con = bc.conectar()
    cur = con.cursor()

    filtro, args = "", []
    if cidade:
        filtro = ("and translate(upper(coalesce(cidade,'')), "
                  "'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC') = "
                  "translate(upper(%s), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC')")
        args.append(cidade)
    sql = SQL_LOJAS % filtro
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, args)
    lojas = cur.fetchall()
    _log("   %d lojas do iFood ainda sem ponto%s"
         % (len(lojas), (" em %s" % cidade) if cidade else ""))
    if not lojas:
        con.close()
        return {"lojas": 0, "criados": 0}

    linhas, sem_endereco = [], 0
    for (_id, mid, nome, cat, tel, cnpj, rua, num, bairro,
         cid, uf, cep, lat, lng, visto) in lojas:
        endereco = montar_endereco(rua, num, bairro, cid, uf, cep)
        if not endereco:
            # Sem rua não há endereço, e o ponto entraria só com o nome. O
            # trigger `exigir_comparavel` deixa passar, mas o ponto nasceria
            # sem a chave que a etapa de logradouro usa. Fica de fora, contado.
            sem_endereco += 1
            continue
        linhas.append((nome.strip(), endereco, nome_de_cidade(cid),
                       (uf or "").strip().upper() or None, cat, tel,
                       re.sub(r"\D", "", cnpj or "") or None,
                       lat, lng, "ifood", "ifood:%s" % mid, "ifood",
                       True, visto, "ifood"))

    _log("   %d com endereço montado · %d sem rua, fora"
         % (len(linhas), sem_endereco))
    por_cidade = Counter(l[2] for l in linhas)
    for c, n in por_cidade.most_common(6):
        _log("      %-22s %5d" % (c, n))

    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar para escrever)")
        for l in linhas[:4]:
            _log("      %-34s %s" % (l[0][:34], l[1][:56]))
        con.close()
        return {"lojas": len(lojas), "criados": 0, "sem_endereco": sem_endereco}

    t0 = time.time()
    # QUANTOS EXISTIAM ANTES — e não `cur.rowcount` depois.
    #
    # `execute_values` manda o lote em páginas, e o `rowcount` do cursor guarda
    # só a ÚLTIMA delas. Na primeira versão isto relatou "408 pontos criados"
    # quando 907 tinham sido criados: o número era o resto da divisão por
    # `page_size`, e teria passado por verdade.
    cur.execute("select count(*) from radar_comercial.pois where fonte = 'ifood'")
    antes = cur.fetchone()[0]

    # `id_empresa` NÃO vai no INSERT: quem carimba é a trigger
    # `preencher_empresa`. `on conflict do nothing` sem alvo cobre qualquer
    # violação de unicidade — aqui, o índice parcial `pois_sem_duplicata`, que
    # tem expressão e predicado e por isso não aceita alvo explícito.
    psycopg2.extras.execute_values(cur, """
        insert into radar_comercial.pois
            (nome, endereco, cidade, uf, categoria, telefone, cnpj,
             lat_origem, lng_origem, fonte, fonte_dado, status,
             presente_no_ifood, ifood_visto_em, endereco_fonte)
        values %s
        on conflict do nothing
    """, linhas, page_size=500)
    con.commit()
    cur.execute("select count(*) from radar_comercial.pois where fonte = 'ifood'")
    criados = cur.fetchone()[0] - antes

    # A volta: cada loja aponta para o ponto que nasceu dela. O par é o
    # `fonte_dado`, que carrega o `merchant_id` — não o nome, que repete.
    cur.execute("""
        update radar_comercial.ifood_merchant m
           set poi_id = p.id
          from radar_comercial.pois p
         where p.fonte_dado = 'ifood:' || m.merchant_id
           and m.poi_id is null
    """)
    ligados = cur.rowcount
    con.commit()

    # O PRINT DA LISTA VIRA EVIDÊNCIA DO PONTO (dono do produto, 17/09/2026): quem abrir o POI vê a imagem em que a
    # loja aparecia na lista daquela praça, com data. SÓ O CAMINHO no Storage, e `dados` nulo de propósito — há
    # consultas que contam "evidência com bytes" como foto capturada, e este print não é foto do lugar.
    cur.execute("""
        insert into radar_comercial.poi_evidencia (id_empresa, poi_id, tipo, storage_path, url_origem, capturado_em)
        select m.id_empresa, m.poi_id, 'ifood_lista', m.bruto->'print_lista'->>'storage_path',
               'https://www.ifood.com.br/inicio', m.visto_em
          from radar_comercial.ifood_merchant m
         where m.poi_id is not null
           and m.bruto->'print_lista'->>'storage_path' is not null
        on conflict (id_empresa, poi_id, tipo)
        do update set storage_path = excluded.storage_path, capturado_em = excluded.capturado_em
    """)
    com_print = cur.rowcount
    con.commit()
    _log("   %d ponto(s) com o print da lista do iFood como evidência" % com_print)

    # AS QUE SOBRARAM, SEPARADAS POR MOTIVO — porque os motivos são dois e
    # pedem condutas opostas. Na primeira versão este relatório dizia que as
    # 630 restantes eram cópias recusadas pelo índice; eram lojas de OUTRAS
    # cidades, que ninguém tinha mandado processar. Quem lesse concluiria que
    # 630 estabelecimentos estavam duplicados no banco.
    cur.execute("""
        select cidade, count(*) from radar_comercial.ifood_merchant
         where poi_id is null group by 1 order by 2 desc
    """)
    restantes = cur.fetchall()
    aqui = sum(n for c, n in restantes
               if cidade and (c or "").upper().startswith(cidade.upper()[:5]))
    fora = sum(n for c, n in restantes) - aqui

    _log("   %d pontos criados · %d lojas ligadas · %.1f s"
         % (criados, ligados, time.time() - t0))
    if aqui:
        _log("   %d loja(s) desta cidade ficaram sem ponto: mesmo nome e mesmo"
             % aqui)
        _log("   endereço de um ponto que já existe — o índice único recusa a cópia.")
    if fora:
        _log("   %d lojas de outras cidades seguem sem ponto (ninguém pediu):"
             % fora)
        for c, n in restantes[:6]:
            if not (cidade and (c or "").upper().startswith(cidade.upper()[:5])):
                _log("      %-22s %5d" % (c, n))
    orfas = aqui + fora
    con.close()
    return {"lojas": len(lojas), "criados": criados, "ligados": ligados,
            "orfas": orfas, "sem_endereco": sem_endereco}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="As lojas do iFood viram POI, com CNPJ e coordenada.")
    p.add_argument("--cidade", default="",
                   help="processa só esta cidade; sem isto, todas")
    p.add_argument("--limite", type=int, default=0,
                   help="processa só as N primeiras (para testar)")
    p.add_argument("--aplicar", action="store_true",
                   help="grava em pois; sem isto é ensaio")
    a = p.parse_args(argv)

    _log("▶ iFood → POI%s" % ((" · %s" % a.cidade) if a.cidade else ""))
    do_municipio(a.cidade, a.limite, a.aplicar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
