# -*- coding: utf-8 -*-
"""povoar_vinculo.py — dar abas aos POIs que já existem, e juntar os que são um só.

A tabela `vinculo_poi` (migração 0032) diz de que registros cada POI é feito.
Ela nasceu vazia: os 58 mil POIs do banco vieram de antes dela, e sem uma linha
lá a ficha não tem aba nenhuma e o `x` não tem o que desfazer.

DOIS PASSOS, E ELES SÃO DIFERENTES

**`--proprios`** dá a cada POI o vínculo com a própria origem. Confiança 10,
origem `importacao`, e isso não é generosidade: todo POI do banco hoje é de
FONTE ÚNICA — a fusão aconteceu dentro da skill de extração e nós importamos só
o sobrevivente. O registro *é* o ponto, e não há dúvida a graduar.

**`--juntar`** é o outro trabalho: 191 grupos de POIs com o mesmo nome na mesma
coordenada, vindos de fontes diferentes. São a mesma loja contada duas vezes, e
é o que "juntar ao passar pelo processo correto" quer dizer.

O QUE ACONTECE COM O POI ABSORVIDO

Ele **não é apagado**. Ganha `fundido_em` e `fundido_para`, mantém a linha e o
`place_id`, e
o vínculo dele passa para o sobrevivente como mais uma aba. Assim a junção é
reversível pelo mesmo `x` da tela — desvincular devolve o registro a um POI
próprio, que é exatamente o desenho da migração 0032.

Apagar seria mais simples e seria pior: uma junção errada viraria perda, e a
medição do RS já mostrou que 66% das fusões automáticas erram.

QUEM SOBREVIVE

O que tem mais evidência acumulada — Street View, análise de IA, fotos. Não é
critério estético: a evidência aponta para um `poi_id`, e escolher o outro
obrigaria a mover trabalho pago de lugar, que é a coisa que a seção 29 da
DOCUMENTACAO existe para impedir.

USO
    python povoar_vinculo.py --proprios --empresa "Aegea - Corsan"
    python povoar_vinculo.py --juntar --cidade Canoas            # simula
    python povoar_vinculo.py --juntar --cidade Canoas --aplicar
"""
from __future__ import annotations

import argparse
import json

import psycopg2.extras

import config  # noqa: F401
import base_comum as bc

# Confiança por origem do place_id, para o dia em que um POI tiver mais de uma
# fonte. Hoje todos têm uma só e entram como 10; estes valores existem para o
# `--juntar`, onde a pergunta "por que estes dois são o mesmo?" tem resposta.
CONF_POR_ORIGEM = {"g/": 9, "ChIJ": 9, "0x": 9, "m/": 8}


def _util(v):
    """O valor, ou `None` quando ele é um buraco escrito como texto.

    ISTO É UMA PORTA, E ELA ESTAVA ABERTA. Em 27/08/2026 foram limpos 74.572
    campos com `"nan"` de dentro do JSON dos vínculos — site 37.709, telefone
    23.407, endereço 11.972, categoria 1.484. A limpeza foi uma vez; esta
    função é o que impede a volta.

    O `nan` do pandas virando a string "nan" numa importação não fica inerte:
    `evidencia` já o trata como vazio, mas o PAINEL o mostra como se fosse um
    site chamado nan. Foi assim que 45–74% dos contatos exibidos eram falsos.

    Reaproveita a lista de `evidencia._VAZIO` de propósito — duas listas de
    buraco divergem, e o dia em que divergirem uma delas deixa passar.
    """
    import evidencia as _ev
    if v is None:
        return None
    t = _ev._sem_acento(str(v)).strip()
    return None if t in _ev._VAZIO else v


def _empresa(cur, nome: str) -> str:
    """Mantido como nome local; a logica vive em `bc.assumir_empresa`."""
    return bc.assumir_empresa(cur, nome)[1]


def proprios(empresa: str, aplicar: bool) -> None:
    """Cada POI ganha o vínculo com a fonte que o produziu."""
    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    dono = _empresa(cur, empresa)

    cur.execute("""
        select p.id, coalesce(p.fonte,'?'), coalesce(p.place_id,''), p.nome,
               p.maps_lat, p.maps_lng, p.categoria, p.endereco, p.telefone, p.website
          from pois p
         where not exists (select 1 from vinculo_poi v
                            where v.poi_id = p.id and v.estado = 'vinculado')
           -- O POI FUNDIDO NÃO É UM PONTO, e dar-lhe vínculo o ressuscita.
           --
           -- Ele foi absorvido por outro e os vínculos dele passaram para o
           -- sobrevivente — é exatamente por isso que fica "sem vínculo". Ver
           -- essa ausência como buraco a preencher inverte o que a fusão fez:
           -- cria evidência nova para um ponto que deixou de existir como ponto.
           --
           -- MEDIDO em 27/08/2026: 397 vínculos ativos em POI fundido, 286
           -- deles criados numa única rodada. Eles não aparecem no mapa (a
           -- consulta exclui `fundido`), o que é pior: sujeira que não se vê.
           --
           -- A linha do fundido continua no banco, com o `place_id`, para a
           -- fusão poder ser desfeita pelo `x` da ficha. Desfazer devolve o
           -- vínculo ORIGINAL a ele; um vínculo inventado aqui atrapalharia
           -- justamente esse caminho de volta.
           and p.fundido_em is null
           -- SÓ OS POIs DESTA EMPRESA, e isto é conserto de um defeito real.
           --
           -- O gatilho `preencher_tenant` carimba o tenant da SESSÃO, não o do
           -- POI. Na primeira execução isso deu 8.547 vínculos com dono errado:
           -- POIs da Aegea-Piauí e da Columbia Tech ganharam vínculo da Corsan,
           -- invisível para o dono real e visível para quem não devia.
           --
           -- A RLS não podia ter pego: o `with check` da política aprova o que a
           -- sessão está inserindo — o problema não era o tenant do vínculo, era
           -- eu estar lendo POI que não é da sessão. O recorte vai aqui.
           and p.id_empresa = core.empresa_atual()
         order by p.id""")
    linhas = cur.fetchall()
    print(f"  {dono}: {len(linhas):,} POIs sem vínculo")
    if not linhas or not aplicar:
        if linhas:
            print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        con.close()
        return

    dados = []
    for pid, fonte, place_id, nome, la, lo, cat, end, tel, site in linhas:
        # `id_fonte` sai do place_id quando ele carrega o prefixo da fonte
        # (`estadual:abc`, `osm:node/1`); senão é o próprio place_id, e na falta
        # dele o id do POI — que é a única identidade que sobra.
        idf = place_id.split(":", 1)[1] if ":" in place_id else (place_id or f"poi:{pid}")
        dados.append((pid, fonte, idf, nome, la, lo,
                      json.dumps({"categoria": _util(cat), "endereco": _util(end),
                                  "telefone": _util(tel), "site": _util(site)},
                                 ensure_ascii=False),
                      10, "importacao",
                      "POI de fonte única: o registro é o ponto"))

    psycopg2.extras.execute_values(cur, """
        insert into vinculo_poi (poi_id, fonte, id_fonte, nome, lat, lng, dados,
                                 confianca, confianca_origem, motivo)
        values %s on conflict do nothing""", dados, page_size=1000)
    con.commit()
    cur.execute("select count(*) from vinculo_poi where estado='vinculado'")
    print(f"\n  GRAVADO. `vinculo_poi` agora tem {cur.fetchone()[0]:,} vínculos ativos")
    con.close()


def juntar(cidade: str, empresa: str, aplicar: bool) -> None:
    """POIs com o mesmo nome na mesma coordenada viram UM, com várias abas."""
    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    _empresa(cur, empresa)

    # Mesmo nome (sem acento, minúsculo) e mesma coordenada a 5 casas — ~1 m.
    # Não é o dedup da skill: é o resíduo que sobrou POR FORA dela, quando duas
    # extrações diferentes produziram o mesmo ponto sem nunca se encontrarem.
    cur.execute("""
        with n as (
          select id, fonte, place_id, nome,
                 lower(translate(coalesce(nome,''),
                       'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ','aaaaeeiooouucaaaaeeiooouuc')) nm,
                 round(maps_lat::numeric, 5) la, round(maps_lng::numeric, 5) lo,
                 (select count(*) from streetview_imgs s where s.poi_id = pois.id)
               + (select count(*) from analise_ia a where a.poi_id = pois.id) evid
            from pois
           where maps_lat is not null and coalesce(nome,'') <> ''
             and (%s = '' or cidade = %s)
             and fundido_em is null)
        select nm, la, lo,
               array_agg(id order by evid desc, id),
               array_agg(fonte order by evid desc, id),
               array_agg(evid order by evid desc, id)
          from n group by nm, la, lo having count(*) > 1
         order by 1""", (cidade, cidade))
    grupos = cur.fetchall()
    print(f"  {len(grupos):,} grupos de POIs que são o mesmo ponto")
    for nm, la, lo, ids, fontes, evids in grupos[:6]:
        print(f"    {nm[:38]:40} {list(zip(ids, fontes, evids))}")
    if not grupos:
        con.close()
        return
    if not aplicar:
        print(f"\n  SIMULAÇÃO — {sum(len(g[3]) - 1 for g in grupos):,} POIs seriam "
              "absorvidos (marcados 'fundido', NÃO apagados). Use --aplicar.")
        con.close()
        return

    absorvidos = 0
    for nm, la, lo, ids, fontes, evids in grupos:
        # O primeiro é o de MAIOR evidência: a evidência aponta para um poi_id, e
        # escolher outro sobrevivente obrigaria a mover trabalho pago de lugar.
        vive, resto = ids[0], ids[1:]
        for outro in resto:
            cur.execute("""
                update vinculo_poi set poi_id = %s,
                       confianca = 6, confianca_origem = 'importacao',
                       motivo = %s
                 where poi_id = %s and estado = 'vinculado'""",
                        (vive, f"juntado a #{vive}: mesmo nome na mesma coordenada",
                         outro))
            # `fundido_em`/`fundido_para` e nao `status` -- ver migracao 0036.
            cur.execute("""update pois set fundido_em = now(), fundido_para = %s
                            where id = %s""", (vive, outro))
            absorvidos += 1
    con.commit()
    print(f"\n  GRAVADO: {absorvidos:,} POIs absorvidos — marcados 'fundido', "
          "com o vínculo transferido. Reversível pelo `x` da ficha.")
    con.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--proprios", action="store_true")
    p.add_argument("--juntar", action="store_true")
    p.add_argument("--cidade", default="")
    p.add_argument("--empresa", required=True)
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args()
    if a.proprios:
        proprios(a.empresa, a.aplicar)
    if a.juntar:
        juntar(a.cidade, a.empresa, a.aplicar)
    if not (a.proprios or a.juntar):
        p.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
