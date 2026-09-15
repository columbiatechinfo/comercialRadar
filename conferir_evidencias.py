# -*- coding: utf-8 -*-
"""conferir_evidencias.py — o que ainda falta coletar antes de julgar um lote (dono do produto, 15/09/2026).

"Quem estiver com visada errada tem que ser recoletado, os dados acessorios como fotos com data e Serasa e
web tambem tem que estar pronto, so entao os itens marcados como aprovados comecam a ser reavaliados." O
orquestrador roda esta conferencia depois da recaptura: se sobrou algo que uma segunda passada resolve, ele
recaptura de novo; o que nao se resolve (panorama que nao existe, foto que o Google nao data) vai para o log
com o numero, e o julgamento segue.

Cada contagem usa a MESMA selecao do passo que coleta — a conferencia nao tem regra propria:
  fichas do Maps       recoletar_fichas.pois_das_ligacoes
  foto de rua          recapturar_frente.alvos_das_ligacoes (visada errada ou sem foto)
  leitura das placas   ler_fotos_de_rua.alvos
  ficha do CNPJ        fichas_cnpj.alvos (Serasa)
  busca web            a mesma condicao da fila (`avaliar_ligacao.fila`, exigir_busca): DuckDuckGo feito

    python conferir_evidencias.py --ligacoes-arquivo ligs.txt     # sai 0 se nada falta, 3 se falta
"""
import argparse
import contextlib
import io
import sys

sys.path.insert(0, "/app")
import base_comum as bc  # noqa: E402


def conferir(arquivo, horas_sem_data=24):
    import fichas_cnpj as fc
    import ler_fotos_de_rua as lf
    import recapturar_frente as rf
    import recoletar_fichas as rc
    ligs = [x.strip() for x in open(arquivo) if x.strip()]
    con = bc.conectar()
    r = {"ligacoes": len(ligs)}
    r["fichas_maps"] = len(rc.pois_das_ligacoes(con, arquivo, horas_sem_data))
    with contextlib.redirect_stdout(io.StringIO()):
        alvos_rua = rf.alvos_das_ligacoes(arquivo)
    # SEM COBERTURA DO STREET VIEW (0116): recapturar nao resolve, e a ligacao vai para revisao humana sem a IA
    with con.cursor() as k:
        k.execute("select alvo from radar_comercial.sem_street_view where alvo = any(%s)",
                  ([rf.alvo_sem_street_view(a) for a in alvos_rua],))
        sem_sv = {x[0] for x in k.fetchall()}
    r["sem_street_view"] = len(sem_sv)
    r["foto_de_rua"] = sum(1 for a in alvos_rua if rf.alvo_sem_street_view(a) not in sem_sv)
    r["leitura"] = len(lf.alvos(con, ligs))
    r["serasa"] = len(fc.alvos(con, ligs))
    with con.cursor() as k:
        k.execute("""select count(*) from unnest(%s::text[]) l(ligacao)
                      where not exists (select 1 from radar_comercial.busca_web b
                                         where b.ligacao = l.ligacao and b.tipo = 'endereco' and not b.bloqueado
                                           and b.resultados is not null and b.motor = 'duckduckgo')""", (ligs,))
        r["busca_web"] = k.fetchone()[0]
        # informativo: foto do Maps que segue sem data depois do datador (o proprio Google nao data algumas)
        k.execute("""select count(distinct i.poi_id) from radar_comercial.ligacao_poi lp
                       join radar_comercial.images_urls i on i.poi_id = lp.poi_id
                      where lp.ligacao = any(%s) and lp.descartado_em is null and i.data_imagem is null
                        and i.url like '%%googleusercontent%%'""", (ligs,))
        r["pois_com_foto_sem_data"] = k.fetchone()[0]
    con.close()
    return r


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ligacoes-arquivo", dest="ligacoes_arquivo", required=True)
    p.add_argument("--sem-data-apos-horas", dest="sem_data_apos_horas", type=int, default=24)
    a = p.parse_args(argv)
    r = conferir(a.ligacoes_arquivo, a.sem_data_apos_horas)
    falta = sum(r[c] for c in ("fichas_maps", "foto_de_rua", "leitura", "serasa", "busca_web"))
    print("■ conferência: %d ligações · faltam: fichas do Maps %d · foto de rua %d · leitura %d · Serasa %d · "
          "busca web %d · (POIs com foto do Maps sem data: %d) · (sem cobertura do Street View: %d)"
          % (r["ligacoes"], r["fichas_maps"], r["foto_de_rua"], r["leitura"], r["serasa"], r["busca_web"],
             r["pois_com_foto_sem_data"], r["sem_street_view"]), flush=True)
    return 0 if falta == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
