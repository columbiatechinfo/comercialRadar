# -*- coding: utf-8 -*-
"""Para cada loja do iFood, os candidatos parecidos na Receita — para gente ver.

Muda quem decide. O `cruzar_ifood_receita.py` escolhe sozinho e cala o que
rejeitou; aqui a máquina só ORDENA e mostra, e quem julga é a pessoa.

Por que isso é melhor neste caso: o nome de fachada e a razão social muitas
vezes não se parecem em nada — "300 Gastroburguer" é `HELENA BURGER`, e nenhum
limiar automático aceita isso sem também aceitar dezenas de erros. Mas um
humano olha a lista, vê `HELENA BURGER` em hamburgueria no bairro certo, e
resolve em dois segundos.

Por isso o corte aqui é FROUXO de propósito — o objetivo é não perder o
verdadeiro, mesmo ao custo de mostrar quatro errados ao lado dele.

Gera um HTML para avaliação, um cartão por loja.

Uso:
    python candidatos_receita.py --cidade canoas --limit 40 --saida cand.html
"""
from __future__ import annotations

import argparse
import html
import sys
from collections import defaultdict

import base_comum as bc
from cruzar_bases import sem_acento, tokens
from cruzar_ifood_receita import COMUNS, MUNICIPIO_RF

POR_LOJA = 6         # quantos candidatos mostrar
MIN_MOSTRAR = 0.30   # frouxo: melhor mostrar errado que esconder o certo

CARREGAR_RF = """
select e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv,
       e.nome_fantasia, m.razao_social, e.cnae_principal,
       c.descricao,
       trim(coalesce(e.tipo_logradouro,'') || ' ' || coalesce(e.logradouro,'')),
       e.numero, e.bairro, e.cep, e.data_inicio
  from public.rf_estabelecimentos e
  left join public.rf_empresas m on m.cnpj_basico = e.cnpj_basico
  left join public.rf_cnaes    c on c.codigo      = e.cnae_principal
 where e.uf = %s and e.municipio = %s and e.situacao_cadastral = '02'
"""

LOJAS = """
select merchant_id, nome, categoria, bairro, slug
  from comercialradar.ifood_merchant
 where cidade = %s and nome is not null
 order by nome
"""


def parecenca_frouxa(a, b) -> float:
    """Sobreposição simples, SEM a exigência de duas palavras.

    Aqui se quer recall: mostrar. A regra estrita vale para decidir sozinho,
    e decidir sozinho é justamente o que este módulo não faz.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default="canoas")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--saida", default="candidatos_receita.html")
    args = p.parse_args()

    uf, mun = MUNICIPIO_RF[args.cidade.lower()]
    ref = bc.conectar_referencia()
    with ref, ref.cursor() as k:
        k.execute(CARREGAR_RF, (uf, mun))
        estabs = k.fetchall()
    print(f"Receita: {len(estabs):,} ativos em {args.cidade}", flush=True)

    con = bc.conectar()
    with con.cursor() as k:
        k.execute(LOJAS, (args.cidade,))
        lojas = k.fetchall()[:args.limit]
    con.close()

    # índice invertido por token, para não comparar 40 × 58 mil
    conj = []
    idx = defaultdict(list)
    for i, e in enumerate(estabs):
        t = [x for x in (tokens(e[1]), tokens(e[2])) if x]
        conj.append(t)
        for c in t:
            for tk in c:
                if len(tk) > 2 and tk not in COMUNS:
                    idx[tk].append(i)

    partes = []
    for mid, nome, categoria, bairro, slug in lojas:
        alvo = tokens(nome)
        vistos = set()
        for tk in alvo:
            if len(tk) > 2 and tk not in COMUNS:
                vistos.update(idx.get(tk, ()))
        marcados = []
        for i in vistos:
            s = max((parecenca_frouxa(alvo, c) for c in conj[i]), default=0.0)
            if s >= MIN_MOSTRAR:
                marcados.append((s, estabs[i]))
        marcados.sort(key=lambda x: -x[0])

        linhas = []
        for s, e in marcados[:POR_LOJA]:
            cnpj, fant, razao, cnae, cnae_txt, logra, num, bai, cep, ini = e
            # bairro igual é sinal forte e merece destaque visual
            mesmo = (bairro and bai
                     and sem_acento(bairro) == sem_acento(bai))
            linhas.append(f"""
      <tr class="{'igual' if mesmo else ''}">
        <td class="s">{s:.2f}</td>
        <td><b>{html.escape(fant or '—')}</b><br>
            <span class="rz">{html.escape(razao or '')}</span></td>
        <td class="cnae">{html.escape((cnae_txt or cnae or '')[:44])}</td>
        <td>{html.escape(logra or '')}, {html.escape(num or 's/n')}<br>
            <span class="rz">{html.escape(bai or '')} · {html.escape(cep or '')}</span></td>
        <td class="cnpj">{cnpj}</td>
      </tr>""")

        corpo = ("".join(linhas) if linhas else
                 '<tr><td colspan="5" class="vazio">nenhum parecido na Receita</td></tr>')
        partes.append(f"""
  <section>
    <h2>{html.escape(nome.strip())}</h2>
    <p class="meta">{html.escape(categoria or '')} · {html.escape(bairro or 'sem bairro')}
       · <a href="https://www.ifood.com.br/delivery/{html.escape(slug or '')}"
            target="_blank">ver no iFood</a></p>
    <table><thead><tr><th>nota</th><th>empresa</th><th>ramo</th>
      <th>endereço</th><th>CNPJ</th></tr></thead>
      <tbody>{corpo}</tbody></table>
  </section>""")

    doc = f"""<title>Candidatos na Receita</title>
<style>
 :root {{ --fg:#1a1a1a; --bg:#fff; --linha:#e5e5e5; --fraco:#6b6b6b;
          --marca:#0a7d4b; --marcabg:#eaf7f0; }}
 @media (prefers-color-scheme: dark) {{ :root:not([data-theme=light]) {{
   --fg:#e8e8e8; --bg:#141414; --linha:#2e2e2e; --fraco:#9a9a9a;
   --marca:#4ade80; --marcabg:#12301f; }} }}
 :root[data-theme=dark] {{ --fg:#e8e8e8; --bg:#141414; --linha:#2e2e2e;
   --fraco:#9a9a9a; --marca:#4ade80; --marcabg:#12301f; }}
 body {{ font:15px/1.5 system-ui,sans-serif; color:var(--fg);
         background:var(--bg); margin:0; padding:24px; }}
 h1 {{ font-size:20px; margin:0 0 4px; }}
 .intro {{ color:var(--fraco); margin:0 0 28px; max-width:60ch; }}
 section {{ border-top:1px solid var(--linha); padding:18px 0; }}
 h2 {{ font-size:16px; margin:0 0 2px; }}
 .meta {{ color:var(--fraco); font-size:13px; margin:0 0 10px; }}
 table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
 th {{ text-align:left; color:var(--fraco); font-weight:500;
       font-size:12px; padding:4px 8px; }}
 td {{ padding:7px 8px; vertical-align:top; border-top:1px solid var(--linha); }}
 .s {{ font-variant-numeric:tabular-nums; color:var(--fraco); }}
 .rz, .cnae {{ color:var(--fraco); font-size:12px; }}
 .cnpj {{ font-family:ui-monospace,monospace; font-size:12px; }}
 tr.igual td {{ background:var(--marcabg); }}
 tr.igual .s {{ color:var(--marca); font-weight:600; }}
 .vazio {{ color:var(--fraco); font-style:italic; }}
 a {{ color:inherit; }}
</style>
<h1>Candidatos na Receita — {len(lojas)} lojas do iFood</h1>
<p class="intro">Corte proposital frouxo: melhor mostrar quatro errados do que
esconder o certo. <b>Linha destacada = bairro igual ao do iFood</b>, que é o
sinal mais forte depois do nome. A máquina só ordenou; a escolha é sua.</p>
{''.join(partes)}"""

    with open(args.saida, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(f"{len(lojas)} lojas · {args.saida}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
