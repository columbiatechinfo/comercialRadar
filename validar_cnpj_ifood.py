# -*- coding: utf-8 -*-
"""Confere na Receita se o CNPJ achado na web é REALMENTE o da loja.

Por que isto existe:

A camada web devolveu CNPJ para 8 de 8 lojas — 100%, número bonito e falso.
Conferindo cada um contra a `rf_estabelecimentos`:

    A Casa do Pastel      → AUTOLOCADORA LINCK & MELLO   (locadora)
    00 Espetinho          → EVOLUCAO FINANCEIRA          (financeira)
    14 Bis Lanches        → SINOSGAS                     (gás)
    Açaí & Lanches Bidu   → LANCHES PLANALTINHO, SP      (baixada)

O `minerar_web` lê CNPJ de agregadores que listam dezenas de empresas por
página, e sem conferência aceita o primeiro que casar com a expressão regular.
Um CNPJ errado é pior que CNPJ nenhum: ele parece dado bom, entra no
cruzamento, e vira visita a uma locadora achando que é pastelaria.

A conferência é dupla e nenhuma das duas é opcional:

  1. **A empresa tem que se parecer com a loja.** Nome fantasia OU razão social
     precisa dividir palavras com o nome do iFood. MEI é o caso difícil — a
     razão social é o nome da pessoa —, então quando o nome não bate, o CNPJ
     não é descartado: fica marcado para revisão humana, porque pode estar
     certo e ninguém tem como saber pelo nome.
  2. **Tem que ser da cidade certa e estar ativa.** Empresa baixada não está
     entregando no iFood hoje.

Uso:
    python validar_cnpj_ifood.py --simular
    python validar_cnpj_ifood.py
"""
from __future__ import annotations

import argparse
import sys

from psycopg2.extras import execute_values

import base_comum as bc
from cruzar_bases import tokens
from cruzar_ifood_receita import MUNICIPIO_RF, parecenca

MIN_NOME = 0.5      # mais frouxo que o casamento cego: aqui já há um candidato

# CNAEs que uma loja do iFood pode ter. O aplicativo vende comida, mercado,
# bebida, farmácia e pet — nada mais. Uma locadora de carros ou uma financeira
# não estão entregando açaí, e o nome do titular de um MEI nunca vai denunciar
# isso, então o RAMO é o filtro que o nome não consegue ser.
#
# Prefixos de divisão/grupo, não códigos completos: a Receita usa 7 dígitos e a
# granularidade fina não muda a pergunta "isto pode ser uma loja de delivery?".
CNAE_PLAUSIVEL = (
    "10",      # fabricação de alimentos (padaria industrial, doces)
    "11",      # bebidas
    "4711", "4712", "4713", "4721", "4722", "4723", "4724", "4729",  # varejo alimentício
    "4771", "4772",                                                   # farmácia, perfumaria
    "4789",                                                           # varejo outros (pet, diversos)
    "4691", "4692", "4693",                                           # atacado alimentício
    "5510", "5590",                                                   # hospedagem c/ alimentação
    "5611", "5612", "5620",                                           # restaurantes, bares, lanchonetes
    "9609",                                                           # serviços pessoais diversos
    "4930",                                                           # transporte/entrega
)


def cnae_plausivel(cnae: str | None) -> bool:
    c = (cnae or "").strip()
    return bool(c) and c.startswith(CNAE_PLAUSIVEL)

LOJAS = """
select merchant_id, nome, cidade, cnpj
  from radar_comercial.ifood_merchant
 where cnpj is not null
"""

CONSULTA_RF = """
select e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv as cnpj,
       e.nome_fantasia, m.razao_social, e.municipio, e.uf, e.situacao_cadastral,
       e.tipo_logradouro, e.logradouro, e.numero, e.bairro, e.cep,
       e.cnae_principal
  from public.rf_estabelecimentos e
  left join public.rf_empresas m on m.cnpj_basico = e.cnpj_basico
 where e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv = any(%s)
"""

APAGAR = """
update radar_comercial.ifood_merchant m
   set cnpj = null, cnpj_conf = null,
       bruto = coalesce(m.bruto, '{}'::jsonb)
               || jsonb_build_object('cnpj_recusado', v.cnpj,
                                     'motivo_recusa', v.motivo)
  from (values %s) as v(mid, cnpj, motivo)
 where m.merchant_id = v.mid
"""

CONFIRMAR = """
update radar_comercial.ifood_merchant m
   set cnpj_conf = v.conf,
       rua    = coalesce(m.rua, v.rua),
       numero = coalesce(m.numero, v.numero),
       cep    = coalesce(m.cep, v.cep),
       estado_detalhe = case when coalesce(m.rua, v.rua) is not null
                             then 'OK' else m.estado_detalhe end
  from (values %s) as v(mid, conf, rua, numero, cep)
 where m.merchant_id = v.mid
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--simular", action="store_true")
    args = p.parse_args()

    con = bc.conectar()
    with con.cursor() as k:
        k.execute(LOJAS)
        lojas = k.fetchall()
    if not lojas:
        print("nenhuma loja com CNPJ para conferir.", flush=True)
        con.close()
        return 0

    ref = bc.conectar_referencia()
    with ref, ref.cursor() as k:
        k.execute(CONSULTA_RF, ([c for _, _, _, c in lojas],))
        na_receita = {r[0]: r for r in k.fetchall()}

    confirmar, apagar, revisar = [], [], []
    print(f"conferindo {len(lojas)} CNPJs\n", flush=True)
    for mid, nome, cidade, cnpj in lojas:
        r = na_receita.get(cnpj)
        if not r:
            apagar.append((mid, cnpj, "nao existe na Receita"))
            print(f"  ✗ {nome.strip()[:26]:<28}{cnpj}  não existe na Receita",
                  flush=True)
            continue
        _, fant, razao, mun, uf, sit, tipo, logra, num, bairro, cep, cnae = r

        if sit != "02":
            apagar.append((mid, cnpj, f"situacao {sit}"))
            print(f"  ✗ {nome.strip()[:26]:<28}{str(fant or razao)[:26]:<28}"
                  f"baixada (sit {sit})", flush=True)
            continue

        esperado = MUNICIPIO_RF.get((cidade or "").lower())
        if esperado and (uf, mun) != esperado:
            apagar.append((mid, cnpj, f"municipio {uf}/{mun}"))
            print(f"  ✗ {nome.strip()[:26]:<28}{str(fant or razao)[:26]:<28}"
                  f"outra cidade ({uf} {mun})", flush=True)
            continue

        alvo = tokens(nome)
        s = max((parecenca(alvo, tokens(x)) for x in (fant, razao) if x),
                default=0.0)
        endereco = " ".join(x for x in (tipo, logra) if x).strip() or None
        ramo_ok = cnae_plausivel(cnae)

        # A ORDEM IMPORTA, e eu tinha errado nela: o CNAE rodava primeiro e
        # descartou "INTENSE LIFE SUPLEMENTOS E ARTIGOS" — nome batendo 0,75
        # com a loja — só porque a empresa se registrou num CNAE administrativo.
        # Registro fiscal não descreve fielmente o que a porta vende; o nome,
        # quando bate forte, é evidência melhor. O ramo entra como desempate
        # para quem o nome não resolve, que é o caso do MEI.
        if s >= MIN_NOME:
            confirmar.append((mid, round(s, 3), endereco, num, cep))
            print(f"  ✓ {nome.strip()[:26]:<28}{str(fant or razao)[:26]:<28}"
                  f"{s:.2f}  {str(endereco)[:22]}, {num}", flush=True)
        elif not ramo_ok:
            apagar.append((mid, cnpj, f"nome {s:.2f} + cnae {cnae}"))
            print(f"  ✗ {nome.strip()[:26]:<28}{str(fant or razao)[:26]:<28}"
                  f"nome não bate E ramo incompatível (CNAE {cnae})", flush=True)
        else:
            # nome não ajuda mas o ramo fecha: é o retrato do MEI, cuja razão
            # social é o nome do titular. Nem aceitar nem descartar — é decisão
            # de gente, e a máquina não tem como saber.
            revisar.append((mid, 0.0, endereco, num, cep))
            print(f"  ? {nome.strip()[:26]:<28}{str(fant or razao)[:26]:<28}"
                  f"ramo confere, nome não → revisão", flush=True)

    print(f"\n  {len(confirmar)} confirmados · {len(revisar)} para revisão · "
          f"{len(apagar)} recusados", flush=True)
    if args.simular:
        print("  (simulação — nada gravado)", flush=True)
        con.close()
        return 0

    with con.cursor() as k:
        if confirmar:
            execute_values(k, CONFIRMAR, confirmar, page_size=500)
        if revisar:
            execute_values(k, CONFIRMAR, revisar, page_size=500)
        if apagar:
            execute_values(k, APAGAR, apagar, page_size=500)
    con.commit()
    con.close()
    print("  gravado", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
