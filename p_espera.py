# -*- coding: utf-8 -*-
import ast, io, os
A = "avaliar_ligacao.py"
if os.path.exists(A + ".novo"): os.remove(A + ".novo")
t = io.open(A, encoding="utf-8").read()
if "SQL_SEM_CAPTURA_PENDENTE" in t:
    print("ja aplicado"); raise SystemExit(0)

V = 'SQL_DESATUALIZADO = """and ('
if V not in t: raise SystemExit("nao achei SQL_DESATUALIZADO")
N = '''#: NAO JULGAR QUEM AINDA VAI SER FOTOGRAFADO.
#:
#: Perguntado pelo dono do produto em 10/09/2026, e ele estava certo: "como
#: voce esta rodando vereditos se as imagens ainda estao sendo coletadas?".
#:
#: `--desatualizados` pega tambem a ligacao SEM veredito, e com a captura
#: rodando em paralelo isso inclui quem ainda esta na fila dela. Medido no
#: momento da pergunta: das 1.237 julgadas na operacao, 706 sairam SEM IMAGEM
#: NENHUMA e 731 tinham POI aguardando captura. Elas seriam rejulgadas depois,
#: quando a foto chegasse — ou seja, mais da metade do trabalho era provisorio,
#: e cada uma custava duas chamadas ao modelo em vez de uma.
#:
#: A condicao espelha a fila da captura: o POI esta pendente enquanto nao tiver
#: linha de `sv_frente` com bytes OU com uma falha DEFINITIVA. As transitorias
#: continuam na fila e voltam a ser tentadas — por isso nao contam como
#: resolvidas aqui tambem.
SQL_SEM_CAPTURA_PENDENTE = """and not exists (
        select 1 from radar_comercial.ligacao_poi lp2
          join radar_comercial.pois p2 on p2.id = lp2.poi_id
         where lp2.ligacao = lp.ligacao
           and lp2.descartado_em is null
           and p2.fundido_em is null
           and p2.pt_geo is not null
           and not exists (select 1 from radar_comercial.poi_evidencia e2
                            where e2.poi_id = p2.id
                              and e2.tipo = 'sv_frente'
                              and (e2.dados is not null
                                   or e2.storage_path is not null
                                   or e2.motivo_falha like 'o Google confirma%%'
                                   or e2.motivo_falha like 'MAPS_JS_KEY%%')))"""

SQL_DESATUALIZADO = """and ('''
t = t.replace(V, N, 1)

V = '''    if desatualizados:
        filtro = SQL_DESATUALIZADO'''
if V not in t: raise SystemExit("nao achei a montagem")
N = '''    if desatualizados:
        # ESPERAR A CAPTURA E PARTE DE "desatualizado". Julgar quem ainda vai
        # ganhar foto nao adianta o trabalho: adia-o e cobra em dobro.
        filtro = SQL_DESATUALIZADO + "\n" + SQL_SEM_CAPTURA_PENDENTE'''
t = t.replace(V, N, 1)

with io.open(A + ".novo", "w", encoding="utf-8", newline="\n") as f:
    f.write(t)
ast.parse(io.open(A + ".novo", encoding="utf-8").read())
os.replace(A + ".novo", A)
print("avaliar_ligacao.py: nao julga quem ainda sera fotografado")
