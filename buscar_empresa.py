# -*- coding: utf-8 -*-
"""Quando a Receita não acha, procura o CNPJ na web — e CONFIRMA cada candidato.

POR QUE ESTE DEGRAU EXISTE

A Receita é a fonte boa, mas falha por um motivo banal: o nome da placa não é o
nome do registro. Medido nesta base:

    "Espetão Vancosty" em Canoas   ->  0 na Receita
    "Supermercado Vancosty"        ->  0 pela frase inteira
    "Vancosty"                     ->  1, ATIVA, 00890225000594

E na web, buscando "Espetão Vancosty canoas rs cnpj", o número aparece em três
lugares — Serasa, Econodata e CNPJ Biz — junto com o endereço "Quadra Ee Dois,
1, Loja 02 - Guajuviras", que é o mesmo "EE DOIS, 01" que a Receita registra.
Ou seja: a web sabia ligar a placa ao registro, e nós não estávamos perguntando.

O QUE ISTO NÃO É

Não é aceitar CNPJ de trecho de página. Foi assim que a resposta creditou
"Fonte: Econodata" e "Fonte: Diário Cidade" a números que ninguém conferiu.

Aqui a web serve só para DESCOBRIR candidatos. Cada um volta à Receita pelo
`confirmar_cnpj`, que compara endereço, nome fantasia, CNAE e situação com o que
o Maps mostrou e devolve sim, não ou incerto. A página é a pista; a Receita é a
prova.

A CONSULTA É DIRIGIDA, e é isso que muda o resultado

Perguntar "Espetão Vancosty Canoas endereço telefone" traz o site da rede e o
Facebook. Perguntar pelos termos do cadastro — CNPJ, razão social, inscrição —
traz os diretórios de empresa. Mesmo buscador, respostas de mundos diferentes.
"""
from __future__ import annotations

# Perguntas dirigidas ao vocabulário do REGISTRO, não ao do comércio.
# Cada uma pesca num cardume diferente: a primeira acha diretório de empresa, a
# segunda acha a matriz quando a filial não tem página própria, a terceira pega
# o texto de nota fiscal e contrato que às vezes é a única fonte.
CONSULTAS = [
    "{nome} {cidade} {uf} cnpj",
    "{nome} {cidade} razao social cnpj matriz filial",
    '"{nome}" {cidade} inscricao estadual cnpj',
]

MAX_CANDIDATOS = 6      # acima disso é ruído de rodapé de site, não candidato


def buscar_dados_empresariais(nome: str, cidade: str = "", uf: str = "",
                              endereco: str = "", telefone: str = "",
                              categoria: str = "") -> dict:
    """Procura CNPJ na web e devolve cada candidato JULGADO contra a Receita.

    `endereco` e `telefone` são os que o `consultar_maps` devolveu: sem eles o
    julgamento fica sem com o que comparar e todo candidato vira "incerto".
    """
    import agente_local as A
    import julgar_identidade as JI

    if not nome:
        return {"erro": "informe o nome do estabelecimento"}

    # ── 1. pescar candidatos, com perguntas de cadastro ─────────────────────
    vistos: list = []
    consultadas: list = []
    for molde in CONSULTAS:
        if len(vistos) >= MAX_CANDIDATOS:
            break
        consulta = molde.format(nome=nome, cidade=cidade, uf=uf).strip()
        consultadas.append(consulta)
        try:
            r = A.buscar_web(consulta, 6)
        except Exception:
            continue
        for c in (r.get("cnpjs_no_texto") or []):
            if c not in vistos:
                vistos.append(c)

    if not vistos:
        return {"consultas": consultadas, "candidatos": [],
                "nota": ("nenhum CNPJ apareceu nas buscas dirigidas. Pode ser "
                         "MEI sem registro de nome fantasia, ou negócio novo. "
                         "Ausência aqui não prova que não exista.")}

    # ── 2. cada candidato volta à Receita para ser julgado ──────────────────
    #
    # A web descobre; a Receita prova. Sem este passo isto seria exatamente o
    # "Fonte: Econodata" de antes, com outra roupa.
    julgados = []
    for cnpj in vistos[:MAX_CANDIDATOS]:
        try:
            v = JI.confirmar_cnpj(cnpj, nome=nome, endereco=endereco,
                                  telefone=telefone, categoria=categoria)
        except Exception as e:
            v = {"cnpj": cnpj, "confirma": "incerto",
                 "porque": f"não consegui julgar ({type(e).__name__})"}
        julgados.append(v)

    confirmados = [j for j in julgados if j.get("confirma") == "sim"]
    incertos = [j for j in julgados if j.get("confirma") == "incerto"]

    if confirmados:
        nota = (f"{len(confirmados)} CNPJ(s) CONFIRMADO(s) contra a Receita. "
                f"Cite o CNPJ e diga que a pista veio de busca web e a "
                f"confirmação veio de `consultar_receita`.")
    elif incertos:
        nota = ("nenhum confirmado, mas há INCERTOS: falta endereço para "
                "comparar, ou o registro está numa via com outro nome. "
                "Mostre-os como candidatos, nunca como o CNPJ do ponto.")
    else:
        nota = (f"{len(julgados)} candidato(s) achado(s) na web e TODOS "
                f"NEGADOS contra a Receita — costumam ser o CNPJ do próprio "
                f"site consultado ou de outra unidade da rede. Diga que o CNPJ "
                f"deste ponto não foi encontrado.")

    return {"consultas": consultadas, "candidatos": julgados,
            "confirmados": [j["cnpj"] for j in confirmados], "nota": nota}


ESQUEMA = {
    "type": "function", "function": {
        "name": "buscar_dados_empresariais",
        "description": (
            "Procura CNPJ e razao social na web com consultas DIRIGIDAS ao "
            "vocabulario de cadastro, e devolve cada candidato ja CONFIRMADO "
            "ou NEGADO contra a Receita. "
            "USE quando `consultar_receita` nao achar a empresa pelo nome — e "
            "isso e comum, porque o nome da placa nao e o do registro "
            "('Espetao Vancosty' da zero; 'Vancosty' acha). "
            "Passe o `endereco` e o `telefone` que o `consultar_maps` devolveu: "
            "sem eles nao ha com o que comparar e todo candidato fica incerto."),
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "cidade": {"type": "string"}, "uf": {"type": "string"},
            "endereco": {"type": "string", "description": "endereco do Maps"},
            "telefone": {"type": "string"},
            "categoria": {"type": "string"}},
            "required": ["nome"]}},
}
