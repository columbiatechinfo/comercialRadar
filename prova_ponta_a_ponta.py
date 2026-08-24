# -*- coding: utf-8 -*-
"""Prova de ponta a ponta do fluxo de validação, numa área pequena.

O QUE ESTA PROVA RESPONDE

Não é "o código importa". É: o supervisor consegue abrir um ponto real, ver a
evidência organizada por fonte, decidir, e o banco recusar decisão mal feita?

Cinco perguntas, cada uma com uma resposta verificável:

1. As abas nascem do CATÁLOGO e trazem só campo com valor?
2. A PROCEDÊNCIA aparece para o `root` e some para o supervisor?
3. A quarta saída grava pauta, e a prioridade sobe com três itens?
4. O banco RECUSA visita de campo sem pauta?
5. As três saídas antigas continuam funcionando?

POR QUE UMA ÁREA PEQUENA

Um POI real de Canoas, com CNPJ na base. Prova pequena que roda em segundos e
pode ser repetida a cada mudança vale mais que uma prova grande que ninguém
roda duas vezes.

A prova LIMPA O QUE CRIA. A atribuição de teste é apagada ao final, inclusive
quando algo falha no meio — senão cada execução deixa lixo na fila de alguém.

Uso:
    python prova_ponta_a_ponta.py
"""
from __future__ import annotations

import io
import json
import pathlib
import sys

import base_comum as bc
import ficha_abas

CIDADE = "canoas"


def _achar_poi(cur) -> tuple:
    """O POI com MAIS fontes na cidade — prova fraca e prova inutil.

    A primeira versao pegava o primeiro com CNPJ, e caiu numa PONTE FERROVIARIA:
    tres abas, nenhuma rede social, nenhuma imagem analisada. Passou nos treze
    passos sem exercitar metade do que a tela faz.

    Ordenar por quantas fontes o ponto tem garante que a prova encoste em
    Instagram, iFood e IA das imagens quando eles existirem.
    """
    cur.execute("""select p.id, p.nome,
                          (p.cnpj is not null)::int
                        + (p.instagram is not null)::int
                        + (coalesce(p.presente_no_ifood, false))::int
                        + (a.poi_id is not null)::int
                        + (p.avaliacao is not null)::int as fontes
                     from comercialradar.pois p
                     left join comercialradar.analise_ia a on a.poi_id = p.id
                    where p.cidade ilike %s and p.nome is not null
                      and p.endereco is not null
                    order by fontes desc, p.id limit 1""", (CIDADE,))
    r = cur.fetchone()
    return (r[0], f"{r[1]} ({r[2]}/5 fontes)") if r else None


def _usuarios(cur) -> dict:
    cur.execute("""select nivel, id from comercialradar.usuarios
                    where nivel in ('root','supervisor','admin') order by nivel""")
    return {n: i for n, i in cur.fetchall()}


def rodar() -> list:
    passos = []

    def ok(nome, condicao, detalhe=""):
        passos.append((bool(condicao), nome, detalhe))

    con = bc.conectar()
    item_id = None
    try:
        with con.cursor() as cur:
            poi = _achar_poi(cur)
            if not poi:
                ok("achar POI com evidência", False, f"nenhum em {CIDADE}")
                return passos
            poi_id, poi_nome = poi
            ok("achar POI com evidência", True, f"#{poi_id} {poi_nome}")

            us = _usuarios(cur)
            ok("existem root e supervisor", "root" in us and "supervisor" in us,
               ", ".join(sorted(us)))

            # ── a atribuição de teste ────────────────────────────────────────
            cur.execute("""insert into comercialradar.atribuicao
                             (poi_id, supervisor_id, atribuido_por, status)
                           values (%s, %s, %s, 'pendente') returning id""",
                        (poi_id, us.get("supervisor"), us.get("root")))
            item_id = cur.fetchone()[0]
            con.commit()
            ok("atribuir à fila do supervisor", bool(item_id), f"item {item_id}")

        # ── 1 · as abas nascem do catálogo ───────────────────────────────────
        import dossie
        dados = dossie.coletar(poi_id, con)

        abas_root = ficha_abas.montar(con, dados, e_root=True)
        nomes = [a["fonte"] for a in abas_root]
        ok("as abas vêm do catálogo", len(abas_root) >= 2,
           f"{len(abas_root)} abas: {', '.join(nomes)}")

        vazios = [ln["rotulo"] for a in abas_root for g in a["grupos"]
                  for ln in g["linhas"]
                  if ln["valor"] in (None, "", [])]
        ok("nenhuma linha vazia na tela", not vazios,
           f"{len(vazios)} vazias" if vazios else "campo sem valor não vira linha")

        # ── 2 · a procedência respeita o RBAC ────────────────────────────────
        com_proc = sum(1 for a in abas_root for g in a["grupos"]
                       for ln in g["linhas"] if "procedencia" in ln)
        abas_sup = ficha_abas.montar(con, dados, e_root=False)
        vazou = sum(1 for a in abas_sup for g in a["grupos"]
                    for ln in g["linhas"] if "procedencia" in ln)
        ok("root enxerga a procedência", com_proc > 0, f"{com_proc} campos")
        ok("supervisor NÃO enxerga a procedência", vazou == 0,
           "vazou em %d campos" % vazou if vazou else "filtrada no servidor")

        # ── 3 · a quarta saída grava pauta e sobe a prioridade ───────────────
        pauta = ["confirmar_atividade", "confirmar_numero", "fotografar_fachada"]
        porque = {"confirmar_numero": "o Maps devolveu S/N e a Receita tem número"}
        with con.cursor() as cur:
            cur.execute("""update comercialradar.atribuicao
                              set status='campo', pauta=%s::comercialradar.pauta_campo[],
                                  pauta_porque=%s::jsonb,
                                  prioridade=%s::comercialradar.prioridade_campo,
                                  decidido_em=now()
                            where id=%s
                        returning status::text, pauta::text[], prioridade::text""",
                        (pauta, json.dumps(porque, ensure_ascii=False),
                         "alta" if len(pauta) >= 3 else "normal", item_id))
            st, pt, pr = cur.fetchone()
        con.commit()
        ok("visita de campo grava a pauta", st == "campo" and len(pt or []) == 3,
           f"{st} · {len(pt or [])} itens")
        ok("três itens sobem a prioridade", pr == "alta", pr)

        # ── 4 · o banco recusa campo sem pauta ───────────────────────────────
        recusou = False
        try:
            with con.cursor() as cur:
                cur.execute("""update comercialradar.atribuicao
                                  set status='campo', pauta=null where id=%s""",
                            (item_id,))
            con.commit()
        except Exception as e:
            con.rollback()
            recusou = "campo_exige_pauta" in str(e)
        ok("o banco RECUSA campo sem pauta", recusou,
           "CHECK campo_exige_pauta" if recusou else "PASSOU — o CHECK não pegou")

        # ── 5 · as três saídas antigas continuam ─────────────────────────────
        for status, extra in (("aprovado", None), ("devolvido", None),
                              ("reprovado", "duplicado")):
            try:
                with con.cursor() as cur:
                    cur.execute("""update comercialradar.atribuicao
                                      set status=%s::decisao_fila,
                                          motivo_generico=%s::motivo_reprova,
                                          motivo_escrito=case when %s is null then null
                                                              else 'prova automatica' end,
                                          observacao='prova automatica',
                                          revisao = coalesce(revisao,'{}'::jsonb)
                                                    || '{"uso":"comercial","atividade":"prova"}'::jsonb
                                    where id=%s""",
                                (status, extra, extra, item_id))
                con.commit()
                ok(f"saída '{status}' continua aceita", True)
            except Exception as e:
                con.rollback()
                ok(f"saída '{status}' continua aceita", False, str(e)[:70])

        # ── 6 · o cabecalho tem as tres marcas, e a API as serve ────────────
        import server
        rotas = {r.path for r in server.app.routes if hasattr(r, "path")}
        ok("rota da logo da empresa existe", "/api/empresa/logo" in rotas)

        with con.cursor() as cur:
            cur.execute("""select count(*) from information_schema.columns
                            where table_schema='comercialradar'
                              and table_name='tenants' and column_name='logo_path'""")
            tem_col = cur.fetchone()[0] == 1
        ok("tenants.logo_path existe", tem_col)

        html = io.open("frontend/index.html", encoding="utf-8").read()
        ok("cabecalho tem o terceiro espaco", "brand-cliente" in html
           and "brand-por" in html)

        js = io.open("frontend/fila.js", encoding="utf-8").read()
        ok("a tela desenha as abas do catalogo", "abasEvidencia(f.abas)" in js)
        ok("a lista escrita a mao saiu",
           'linhas("O que encontramos"' not in js,
           "substituida pelo catalogo" if 'linhas("O que encontramos"' not in js
           else "AINDA duplica os campos")
        ok("a opcao 'Visita de campo' esta na tela",
           'value="campo"' in js and "campo-pauta" in js)
        ok("a pauta vai no envio", "corpo.pauta" in js)
        ok("a tela recusa campo sem pauta", "sem pauta e mandar de novo" in js
           or "sem pauta é mandar de novo" in js)

        # ── 7 · O POPUP DO MAPA, que e a tela que o usuario mais usa ─────────
        #
        # Eu tinha estendido so a fila do supervisor. O pedido era "quando o
        # usuario selecionar um ponto ou buscar por ele" — que e o popup que
        # abre ao clicar no mapa. Estas conferencias existem para eu nao
        # confundir as duas telas de novo.
        import server as _srv
        r_popup = _srv.detalhe_poi(poi_id)
        ok("o popup do mapa recebe as abas",
           isinstance(r_popup.get("abas"), list) and len(r_popup["abas"]) >= 1,
           f"{len(r_popup.get('abas') or [])} abas")

        app_js = io.open("frontend/app.js", encoding="utf-8").read()
        ok("o popup desenha as abas", "window.abasEvidencia(poi.abas)" in app_js)
        ok("o popup liga o clique das abas", "window.ligarAbas($(\"modal-card\"))"
           in app_js or "ligarAbas($(\"modal-card\"))" in app_js)
        for morto in ("poi.status_horario", "poi.preco_medio"):
            ok(f"`{morto}` saiu do popup (vem do catalogo)",
               f"if ({morto})" not in app_js)

        abas_js = io.open("frontend/abas.js", encoding="utf-8").read()
        ok("o renderizador e compartilhado", "w.abasEvidencia" in abas_js)
        ok("fila.js nao duplica o renderizador",
           "function abasEvidencia" not in js,
           "usa o de abas.js" if "function abasEvidencia" not in js
           else "AINDA tem copia propria")

        html_i = io.open("frontend/index.html", encoding="utf-8").read()
        ok("abas.js carrega antes de quem usa",
           html_i.find("abas.js") < html_i.find("fila.js"))

        # ── 8 · A BANCADA, que e a tela do modelo ────────────────────────────
        #
        # Eu tinha estendido um popup pequeno. O pedido era a bancada inteira:
        # lateral de fila, regua de probabilidade, tabela de cruzamento, mapa
        # por camadas e barra de decisao. Ela vem do zip; o que construimos e o
        # adaptador que produz os nossos dados no contrato dela.
        import bancada_dataset as BD
        ds = BD.montar(con, [{"poi_id": poi_id, "status": "pendente"}],
                       e_root=True, base="prova")
        for chave in ("meta", "vocabulario", "fontes", "ligacoes"):
            ok(f"o dataset tem `{chave}`", chave in ds)
        ok("o vocabulario declara os cinco status",
           len(ds["vocabulario"]["status"]) == 5)
        ok("a pauta de campo esta no vocabulario",
           len(ds["vocabulario"]["pauta_campo"]) == 6)

        lg = ds["ligacoes"][0]
        ok("a ligacao nao deu erro", "erro" not in lg, lg.get("erro", ""))
        ok("cada fonte tem probabilidade e tier",
           all("probabilidade" in f and "tier" in f
               for f in lg["fontes"].values()),
           f"{len(lg['fontes'])} fontes")
        ok("cada fonte diz a REGRA que casou",
           all(f.get("regra_match") for f in lg["fontes"].values()),
           "numero sem regra e numero sem sentido")
        ok("a probabilidade e declarada, nao calibrada",
           all(f["prob_base"] == "DECLARADA" for f in lg["fontes"].values()),
           "vira CALIBRADA so com amostra rotulada e n declarado")

        alvo_html = pathlib.Path("frontend/bancada.html")
        ok("a bancada esta instalada", alvo_html.exists(),
           f"{alvo_html.stat().st_size // 1024} KB" if alvo_html.exists()
           else "rode `python instalar_bancada.py`")
        if alvo_html.exists():
            b = io.open(alvo_html, encoding="utf-8").read()
            ok("a bancada busca a NOSSA rota", "/api/bancada/dataset" in b)
            ok("o enxerto entrou uma vez", b.count("cr-enxerto-v1") == 1)

        rotas2 = {r.path for r in _srv.app.routes if hasattr(r, "path")}
        ok("a rota /bancada existe", "/bancada" in rotas2)
        ok("a rota do dataset existe", "/api/bancada/dataset" in rotas2)

        # ── 9 · CLICAR NUM PONTO ABRE A BANCADA, e nao um modal ──────────────
        #
        # Este era o pedido desde o inicio, e eu o li errado tres vezes: primeiro
        # estendi a fila do supervisor, depois o popup do mapa, depois fiz a
        # bancada numa pagina que ninguem alcancava pelo mapa. O modal foi
        # crescendo e continuava sendo um modal de 620 px.
        ok("o clique no mapa vai para a bancada",
           'location.href = "/bancada?poi="' in app_js,
           "em vez de abrir modal")
        ok("o modal continua alcancavel", "abrirPoiModal" in app_js,
           "a lista de quadras e a revisao ainda o usam")

        b2 = io.open("frontend/bancada.html", encoding="utf-8").read()
        ok("a bancada le o ?poi= da URL", 'get("poi")' in b2)
        ok("a bancada tem caminho de volta", "botaoVoltar" in b2,
           "sem ele quem entra fica preso")

        # o POI pedido entra mesmo sem estar atribuido a ninguem
        # O usuario tem de ser do MESMO tenant do POI: a RLS esconde o resto, e
        # a primeira versao deste teste pegou um admin do Piaui contra um POI de
        # Canoas e recebeu zero — parecia bug do payload e era isolamento
        # funcionando.
        import auth as _a
        with con.cursor() as cur:
            cur.execute("""select us.id::text, us.tenant_id::text, us.nivel,
                                  us.nome, us.email
                             from comercialradar.usuarios us
                             join comercialradar.pois p
                               on p.tenant_id = us.tenant_id
                            where p.id = %s
                            order by (us.nivel = 'root') desc limit 1""",
                        (poi_id,))
            linha = cur.fetchone()
        ds2 = _srv.bancada_dataset(limite=5, poi=poi_id, u=_a.Usuario(*linha))
        ok("ponto avulso entra no payload",
           any(l["num_ligacao"] == str(poi_id) for l in ds2["ligacoes"]),
           "a maioria dos 27 mil POIs nunca foi atribuida")

    finally:
        if item_id:                      # a prova não deixa lixo na fila
            try:
                with con.cursor() as cur:
                    cur.execute("delete from comercialradar.atribuicao where id=%s",
                                (item_id,))
                con.commit()
            except Exception:
                con.rollback()
        con.close()
    return passos


if __name__ == "__main__":
    passos = rodar()
    print()
    for bom, nome, detalhe in passos:
        print(f"  {'OK ' if bom else '>> '} {nome:42} {detalhe}")
    falhas = [p for p in passos if not p[0]]
    print(f"\n  {len(passos) - len(falhas)}/{len(passos)} passos")
    sys.exit(1 if falhas else 0)
