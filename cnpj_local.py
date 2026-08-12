"""
cnpj_local.py — Acha o CNPJ do POI na base da Receita que já está no banco.

A fase Web buscava CNPJ no Yahoo. É frágil e desnecessário: as tabelas `rf_*`
(carregadas por `base_cnpj.py`) têm o Brasil inteiro — 17 GB de estabelecimentos
com endereço, CNAE, telefone e situação cadastral. Medido no `RDK Logs`: o SERP
devolveu ZERO resultados nas três buscas, e o CNPJ dele estava aqui o tempo todo.

A chave é **CEP + número**, não telefone:

    telefone     6 de 7.286 (0,1%)  — o Maps traz o celular do negócio,
                                      a Receita tem o telefone do cadastro
    CEP+número   5.308 de 7.923 (67%)

Só que endereço identifica o PRÉDIO, não a empresa: os 5.308 endereços trouxeram
35.761 candidatos, 7 por endereço em média — `Padaria Bom Jesus` casava com
`NATHALIA FAGUNDES` só por dividirem a porta. Por isso o nome decide **quando há
o que decidir**: com duas ou mais empresas ativas na mesma porta, um score de 0,4
é sorteio e o POI vai para a web. Com UMA só, não há escolha a fazer — o CNPJ é
aceito e a confiança fica registrada em `cnpj_conf`, para o usuário filtrar depois.

Usa o índice `ix_estab_cep`; sem ele a consulta varre 17 GB.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher

import base_comum as bc

# Abaixo disto o nome não sustenta o casamento e o POI vai para a web.
# Calibrado para aceitar "Bicho Point"≈"BICHO POINT" e recusar
# "Padaria Bom Jesus"≈"NATHALIA FAGUNDES PEREIRA".
SIMILAR_MIN = 0.72
# Empresa cujo nome fantasia é o nome da pessoa (MEI recém-aberto) aparece como
# "61.828.458 NATHALIA FAGUNDES" — o próprio CNPJ formatado no começo do nome.
_RE_CNPJ_NO_NOME = re.compile(r"^\d{2}\.\d{3}\.\d{3}\s+")
_RE_CEP = re.compile(r"(\d{5})-?(\d{3})")
# "R. Curitiba, 32 - Mathias Velho" → 32. O número vem depois da vírgula, e
# antes do hífen do bairro; sem isso o CEP e o CNPJ entravam como "número".
_RE_NUM = re.compile(r",\s*(\d{1,6})\b")
_RE_UF = re.compile(r"\b([A-Z]{2})\b(?:,|\s|$)")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = _RE_CNPJ_NO_NOME.sub("", s.strip())
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s).upper()
    return re.sub(r"\s+", " ", s).strip()


_RUIDO = {"LTDA", "ME", "EPP", "EIRELI", "SA", "S A", "CIA", "COMERCIO", "DE",
          "DA", "DO", "E", "EM", "GERAL", "SERVICOS", "LIMITADA"}


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) > 2 and t not in _RUIDO}


def similaridade(poi_nome: str, rf_nome: str) -> float:
    """Quanto o nome do POI e o da Receita se parecem, de 0 a 1.

    Combina duas medidas porque nenhuma sozinha serve: a razão de sequência
    aguenta grafia trocada ("Mercadinho"/"MERCADINHO"), e a interseção de tokens
    aguenta ordem e sufixo societário ("PADARIA BOM JESUS LTDA" × "Bom Jesus")."""
    a, b = _norm(poi_nome), _norm(rf_nome)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = _tokens(poi_nome), _tokens(rf_nome)
    jac = len(ta & tb) / len(ta | tb) if (ta and tb) else 0.0
    # token do POI inteiramente contido no nome da Receita vale muito: é o caso
    # de "Bicho Point" dentro de "BICHO POINT COMERCIO DE ANIMAIS LTDA"
    contido = 1.0 if (ta and ta <= tb) else 0.0
    return max(seq, jac, contido * 0.95)


def chave_endereco(endereco: str) -> tuple | None:
    """(cep, numero, uf) a partir do endereço do Maps. None se não der."""
    if not endereco:
        return None
    cep = _RE_CEP.search(endereco)
    num = _RE_NUM.search(endereco)
    if not cep or not num:
        return None
    uf = _RE_UF.findall(endereco.upper())
    return (cep.group(1) + cep.group(2), num.group(1), uf[-1] if uf else "")


def _candidatos(ceps: list, pares: set, con) -> dict:
    """(cep, numero) → estabelecimentos. SEM juntar com `rf_empresas`.

    O caminho rápido é este, e custou medir para descobrir:

    - `cep = ANY(...)` sozinho: **1,4 s** para 213 mil linhas (usa `ix_estab_cep`);
    - o mesmo com `LEFT JOIN rf_empresas` (69 M de linhas, 8 GB): arrasta as 213
      mil por um join que só interessa para as poucas centenas que sobrevivem ao
      filtro do número;
    - `numero = ANY(...)` junto: 11,4 s — o planejador troca de plano e piora;
    - juntar por `unnest` dos pares: não termina em 10 minutos.

    Então: puxa por CEP, peneira o número em Python (barato), e a razão social
    vem depois, só para quem casou (`_empresas`)."""
    if not ceps:
        return {}
    with con.cursor() as cur:
        cur.execute("""
            SELECT cep, numero, uf, cnpj_basico, cnpj_ordem, cnpj_dv,
                   nome_fantasia, situacao_cadastral, cnae_principal
              FROM rf_estabelecimentos WHERE cep = ANY(%s)""", (ceps,))
        out: dict = {}
        for r in cur.fetchall():
            k = (r[0], str(r[1]).strip())
            if k in pares:
                out.setdefault(k, []).append(r)
    return out


def _empresas(basicos: list, con) -> dict:
    """razão social e natureza, só dos CNPJs que sobreviveram ao filtro."""
    if not basicos:
        return {}
    with con.cursor() as cur:
        cur.execute("""SELECT cnpj_basico, razao_social, natureza_juridica
                         FROM rf_empresas WHERE cnpj_basico = ANY(%s)""", (basicos,))
        return {b: (rs, nj) for b, rs, nj in cur.fetchall()}


def _socios_em_bloco(basicos: list, con) -> dict:
    """Sócios de todos os CNPJs casados, numa consulta só.

    Uma consulta POR POI parecia inofensiva (o índice existe), mas com milhares
    de casamentos são milhares de idas ao banco e o passo inteiro empaca — foi o
    que travou a primeira medição."""
    if not basicos:
        return {}
    with con.cursor() as cur:
        cur.execute("""SELECT cnpj_basico, nome_socio, qualificacao_socio, data_entrada
                         FROM rf_socios WHERE cnpj_basico = ANY(%s)""", (basicos,))
        out: dict = {}
        for b, n, q, d in cur.fetchall():
            lst = out.setdefault(b, [])
            if len(lst) < 12:
                lst.append({"nome": n, "qual": q, "desde": d})
    return out


def casar(regs: list, con=None) -> dict:
    """Preenche cnpj/razao_social/cnae/situação + `cnpj_conf` nos que casarem.

    Não inventa: sem CEP+número não tenta, e nome fraco com VÁRIAS empresas na
    mesma porta é recusado — esses vão para a fase Web, o caminho caro."""
    fechar = con is None
    # `_candidatos` e `_empresas` consultam `rf_estabelecimentos` e `rf_empresas`,
    # que desde 12/08/2026 vivem no banco de REFERÊNCIA — instância separada, para
    # que uma varredura de 72 milhões de linhas não dispute cache com o Auth e o
    # PostgREST (ADR 0003). Chamar `bc.conectar()` aqui daria "relation does not
    # exist" no meio de uma rodada, com cara de tabela apagada.
    con = con or bc.conectar_referencia()
    try:
        chaves = {}
        for r in regs:
            if r.get("cnpj"):
                continue
            k = chave_endereco(r.get("endereco") or r.get("endereco_planilha") or "")
            if k:
                chaves[id(r)] = k
        if not chaves:
            return {"casados": 0, "sem_endereco": len(regs), "fracos": 0}

        pares = {(k[0], k[1]) for k in chaves.values()}
        porpar = _candidatos(sorted({k[0] for k in chaves.values()}), pares, con)
        # a razão social entra AQUI, só para quem sobreviveu ao filtro do número
        emp = _empresas(sorted({c[3] for lst in porpar.values() for c in lst}), con)

        n_ok = n_fraco = n_unico = 0
        casados: list = []
        for r in regs:
            k = chaves.get(id(r))
            if not k:
                continue
            cep, numero, uf = k
            cands = porpar.get((cep, numero)) or []
            if uf:
                cands = [c for c in cands if (c[2] or "").upper() == uf] or cands
            if not cands:
                continue
            nome = r.get("nome") or ""
            # ATIVA primeiro: entre dois nomes igualmente parecidos, a empresa
            # baixada não é a que está com a porta aberta hoje
            melhor, score = None, 0.0
            for c in cands:
                razao = (emp.get(c[3]) or ("", ""))[0]
                s = max(similaridade(nome, c[6] or ""), similaridade(nome, razao))
                if c[7] == "02":
                    s += 0.03
                if s > score:
                    melhor, score = c, s
            if melhor is None:
                n_fraco += 1
                continue

            if score >= SIMILAR_MIN:
                conf = "4/4 receita_local+endereco+nome"
            else:
                # NOME FRACO NÃO É MOTIVO PARA DESCARTAR quando não há escolha a
                # fazer. Medido em Canoas: dos 3.850 recusados por nome, 2.025
                # tinham UMA empresa ativa naquela porta — não havia ambiguidade
                # nenhuma, só o nome da fachada diferente do nome do CNPJ.
                # `Crazy Som`×`CRAZY COMERCIO E LOCACAO LTDA` marcava 0,33, e
                # `Igreja Luterana Emanuel`×`CONGREGACAO EVANGELICA LUTERANA
                # EMANUEL` perdia por UM centésimo. A regra do usuário vale aqui
                # também: aceita e registra a confiança, quem filtra é ele.
                ativas = [c for c in cands if c[7] == "02"]
                unicos = ativas or cands
                if len(unicos) != 1:
                    n_fraco += 1          # 1 entre 7 por score 0,4 é sorteio
                    continue
                melhor = unicos[0]
                conf = "2/4 receita_local+endereco_unico"
                n_unico += 1
            razao, natureza = emp.get(melhor[3]) or ("", "")
            r["cnpj"] = f"{melhor[3][:2]}.{melhor[3][2:5]}.{melhor[3][5:]}/" \
                        f"{melhor[4]}-{melhor[5]}"
            r["razao_social"] = razao or ""
            r["nome_fantasia"] = melhor[6] or ""
            r["cnae"] = melhor[8] or ""
            r["situacao_cadastral"] = melhor[7] or ""
            r["natureza_juridica"] = natureza or ""
            r["cnpj_fonte"] = f"receita_local (nome {score:.2f})"
            r["cnpj_conf"] = f"{conf} (nome {score:.2f})"
            casados.append((r, melhor[3]))
            n_ok += 1

        soc = _socios_em_bloco(sorted({b for _, b in casados}), con)
        for r, basico in casados:
            r["socios"] = json.dumps(soc.get(basico, []), ensure_ascii=False)

        return {"casados": n_ok, "fracos": n_fraco, "endereco_unico": n_unico,
                "sem_endereco": len(regs) - len(chaves)}
    finally:
        if fechar:
            con.close()


# Campos que o casamento preenche e que vão para o banco no modo avulso.
_COLS = ("cnpj", "cnpj_conf", "razao_social", "nome_fantasia", "cnae",
         "situacao_cadastral", "natureza_juridica", "socios")


def rodar_no_banco(cidade: str, aplicar: bool = False) -> dict:
    """Passa a Receita local em TODO POI da cidade que ainda não tem CNPJ.

    Existe para rodar sozinho, sem subir o enriquecimento inteiro: é uma consulta
    ao banco que já está aqui, leva segundos e não usa rede.

    DUAS conexões, de propósito. Os POIs estão no banco do produto; a Receita, no
    de referência. Passar a conexão do produto para o `casar()` — que era o que
    esta função fazia — anula o padrão dele e faz a consulta cair no banco errado.
    """
    con = bc.conectar()
    ref = bc.conectar_referencia()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT id, nome, endereco FROM pois
                            WHERE cidade ILIKE %s
                              AND (cnpj IS NULL OR cnpj = '')
                              AND endereco IS NOT NULL""", (cidade,))
            regs = [{"_id": i, "nome": n, "endereco": e} for i, n, e in cur.fetchall()]
        print(f"{len(regs)} POIs de {cidade} sem CNPJ e com endereço", flush=True)
        res = casar(regs, ref)   # Receita: banco de referência
        print(f"  casados {res['casados']} "
              f"({res['casados'] - res['endereco_unico']} por nome + "
              f"{res['endereco_unico']} por endereço único) · "
              f"recusados {res['fracos']} · sem CEP/número {res['sem_endereco']}",
              flush=True)
        if not aplicar:
            print("  (medição — nada foi escrito; use --aplicar)")
            return res
        sets = ", ".join(f"{c} = %s" for c in _COLS)
        with con.cursor() as cur:
            for r in regs:
                if not r.get("cnpj"):
                    continue
                cur.execute(f"UPDATE pois SET {sets} WHERE id = %s",
                            (*(r.get(c) or None for c in _COLS), r["_id"]))
        con.commit()
        print(f"  ✅ {res['casados']} POIs gravados.", flush=True)
        return res
    finally:
        con.close()
        ref.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cidade", required=True)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    rodar_no_banco(a.cidade, a.aplicar)
