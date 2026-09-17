# -*- coding: utf-8 -*-
"""casar_por_cnpj.py — a loja do iFood casa pela Receita do MESMO CNPJ.

Pedido do dono do produto em 12/09/2026: "use o CNPJ com os do iFood para
cruzamento". Medido em Canoas: 514 das 972 lojas do iFood nunca se vincularam,
porque o numero que a loja publica nao existe no cadastro da Corsan para aquela
rua (Acai Polpa Norte, Rua Wilson Brum da Silva, 208: a ligacao ao lado e o 310,
a 3 m). Todas as 949 lojas de Canoas trazem CNPJ, e 815 CNPJs estao na Receita.

DOIS CAMINHOS, nesta ordem:

1. O POI DA RECEITA COM O MESMO CNPJ JA ESTA VINCULADO: a loja entra nas
   mesmas ligacoes. E o mesmo negocio; a IA junta os dois registros.
2. NAO ESTA: o endereco oficial da Receita (rua e numero resolvidos) casa com a
   ligacao pela regra de sempre — rua, numero e bairro batendo, ligacao SIM ou
   SIM_COM_ANALISE_HUMANA. A rua e comparada pela `via_chave` (algarismo igual a
   extenso, sem artigo), a mesma do casamento por endereco.

O par que ja existe nao e tocado — nem o descartado pela IA, que fica
descartado. Quem confirma o vinculo e a IA, na avaliacao.

    python casar_por_cnpj.py --cidade Canoas            # so conta
    python casar_por_cnpj.py --cidade Canoas --aplicar
"""
import argparse
import collections
import re
import time

import base_comum as bc
import regra_vinculo as rv
import via_chave as vc
from cruzar_ligacao import _via


def _log(m):
    print("%s %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def _cnpj(s):
    d = re.sub(r"\D", "", s or "")
    return d.zfill(14) if d else ""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cidade", required=True)
    p.add_argument("--base", type=int, default=1)
    p.add_argument("--fonte", default="ifood", help="a fonte cujas lojas casam pelo CNPJ (padrao: ifood)")
    p.add_argument("--qualificacoes", default="",
                   help="as qualificações da coleta, separadas por vírgula: SIM e SIM_COM_ANALISE_HUMANA ficam "
                        "sempre; NAO só quando listado (a marcação da IA, 17/09/2026)")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    # A COLETA DA MARCAÇÃO DA IA (dono do produto, 17/09/2026): sem `--qualificacoes`, a ligação apta é a de sempre
    import validacao as va
    try:
        coleta = va.coleta_de(va.ler_qualificacoes(a.qualificacoes, padrao=va.COLETA_SEMPRE))
    except ValueError as e:
        p.error(str(e))
    nao = " or coalesce(qualificacao,'') = 'NAO'" if "NAO" in coleta else ""
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("set statement_timeout = '300s'")
    cur.execute("""select p.id, coalesce(p.cnpj,'') from radar_comercial.pois p
                    where lower(coalesce(p.fonte,'')) = %s and p.fundido_em is null
                      and coalesce(p.cnpj,'') <> '' and upper(coalesce(p.cidade,'')) = %s""",
                (a.fonte.lower(), a.cidade.upper()))
    lojas = [(pid, _cnpj(c)) for pid, c in cur.fetchall()]
    _log("%d lojas de %s com CNPJ em %s" % (len(lojas), a.fonte, a.cidade))
    cnpjs = sorted({c for _, c in lojas if c})

    # A RECEITA DOS MESMOS CNPJS: o POI, o endereco resolvido e se esta ativo
    cur.execute("""select p.id, lpad(regexp_replace(coalesce(rd.cnpj,''), '\\D', '', 'g'), 14, '0'),
                          coalesce(lr.logradouro,''), coalesce(lr.numero,''), coalesce(lr.bairro,''),
                          ltrim(coalesce(rd.situacao_cadastral,''),'0') = '2'
                     from radar_comercial.receita_data rd
                     join radar_comercial.pois p on p.id = rd.poi_id and p.fundido_em is null
                     left join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id and lr.forca = 'prova'
                    where lpad(regexp_replace(coalesce(rd.cnpj,''), '\\D', '', 'g'), 14, '0') = any(%s)""", (cnpjs,))
    receita = collections.defaultdict(list)
    for pid, c, logr, nro, bai, ativa in cur.fetchall():
        receita[c].append((pid, logr, nro, bai, ativa))
    rids = [r[0] for v in receita.values() for r in v]
    cur.execute("""select poi_id, ligacao from radar_comercial.ligacao_poi
                    where poi_id = any(%s) and descartado_em is null""", (rids,))
    vinc = collections.defaultdict(set)
    for pid, lig in cur.fetchall():
        vinc[pid].add(str(lig))

    cur.execute("""select num_ligacao::text, coalesce(nom_logradouro,''), coalesce(nro,''), coalesce(nom_bairro,''),
                          coalesce(qualificacao,'') like 'SIM%%'""" + nao + """
                     from resources_root.cadastro_corsan where upper(coalesce(cidade,'')) = %s""", (a.cidade.upper(),))
    apta, porta = {}, collections.defaultdict(list)
    for lig, logr, nro, bai, ok in cur.fetchall():
        apta[lig] = bool(ok)
        v, n = _via(logr), rv.numero_limpo(nro)
        if v and n:
            porta[(vc.chave(v), n)].append((lig, bai))

    cur.execute("""select poi_id, ligacao from radar_comercial.ligacao_poi where poi_id = any(%s)""",
                ([pid for pid, _ in lojas],))
    ja = {(int(pid), str(lig)) for pid, lig in cur.fetchall()}

    placar = collections.Counter()
    pares = {}
    for pid, c in lojas:
        rs = [r for r in receita.get(c, []) if r[4]]
        if not rs:
            placar["sem Receita ativa com esse CNPJ"] += 1
            continue
        ligs = {l for r in rs for l in vinc.get(r[0], set()) if apta.get(l)}
        caminho = "cnpj_receita_vinculada"
        if not ligs:
            caminho = "cnpj_endereco_receita"
            for _r, logr, nro, bai, _at in rs:
                v, n = _via(logr), rv.numero_limpo(nro)
                for lig, bai_l in porta.get((vc.chave(v), n), []) if v and n else []:
                    if apta.get(lig) and rv._bairro_bate({"bairro_lig": bai_l, "bairro_poi": bai}):
                        ligs.add(lig)
        if not ligs:
            placar["a Receita do CNPJ não leva a ligação SIM/SIM_COM"] += 1
            continue
        novas = [l for l in ligs if (pid, l) not in ja]
        if not novas:
            placar["já vinculada a essas ligações"] += 1
            continue
        placar[caminho] += 1
        for l in novas:
            pares[(l, pid)] = caminho
    for k in sorted(placar):
        _log("   %-50s %6d" % (k, placar[k]))
    _log("%d vínculos novos · %d lojas · %d ligações" % (len(pares), len({p for _, p in pares}),
                                                       len({l for l, _ in pares})))
    if not a.aplicar:
        _log("(ensaio: nada gravado. Use --aplicar)")
        return 0
    from psycopg2.extras import execute_values
    linhas = [(a.base, l, pid, False, False, False, False, False, None, 0, 0.70, 1, 0, a.fonte, caminho, "cnpj")
              for (l, pid), caminho in pares.items()]
    execute_values(cur, """
        insert into radar_comercial.ligacao_poi
            (id_base, ligacao, poi_id, mesmo_endereco, mesmo_numero, ate_20m,
             mesmo_telhado, telhado_comercial, metros, criterios_ok, confianca,
             fontes_aderentes, fontes_no_momento, fonte_poi, origem, aceito_por)
        values %s
        on conflict (id_base, ligacao, poi_id) do nothing""", linhas, page_size=1000)
    gravadas = cur.rowcount
    con.commit()
    # RLS FILTRA CALADO: conta o que o banco gravou.
    cur.execute("select count(*) from radar_comercial.ligacao_poi where aceito_por = 'cnpj'")
    _log("o banco tem %d vínculos pelo CNPJ (pedidos agora: %d)" % (int(cur.fetchone()[0] or 0), len(linhas)))
    con.close()
    return 0


if __name__ == "__main__":
    main()
