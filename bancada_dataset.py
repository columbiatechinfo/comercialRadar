# -*- coding: utf-8 -*-
"""Traduz os POIs do ComercialRadar para o formato que a bancada consome.

POR QUE ADAPTADOR, E NÃO TELA NOVA

`assets/modelo_frontend/tela_radar_comercial.zip` traz a bancada de validação
pronta: 149 KB de CSS, 319 KB de JavaScript e 327 KB de HTML, com a lateral de
fila, a régua de probabilidade por fonte, a tabela de cruzamento, o painel de
mapa com camadas por fonte e a barra de decisão. Escrita, especificada e com
vocabulário fechado.

Reimplementar isso à mão sairia pior e levaria dias. E não precisa: a tela
**carrega um `dataset.json`** — foi desenhada para receber dados de fora. O
trabalho é produzir os nossos naquele contrato.

O QUE MUDA DO MODELO PARA CÁ

O modelo é ancorado em LIGAÇÃO — a matrícula da distribuidora. Aqui a âncora é
o POI (ADR 0005). Então cada "ligação" do payload é um POI nosso, e o cadastro
do cliente, quando existe, entra como uma fonte a mais.

E o modelo tem oito fontes. Nós temos dado real para seis; as outras duas —
Energia e Outras Fontes — não entram, pelo mesmo motivo do ADR: aba vazia
parece sistema quebrado, não sistema em construção.

A PROCEDÊNCIA continua obedecendo o RBAC: só o `root` recebe o nome da base.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import ficha_abas

# Vocabulário fechado — os mesmos seis status da fila, com a cor que a lateral
# usa para a barra de cada um.
STATUS = [
    ("pendente",  "Aguardando análise",  "#6B7684"),
    ("aprovado",  "Aprovado por usuário", "#1F9D55"),
    ("devolvido", "A revisar",            "#E08A00"),
    ("campo",     "Direcionado a campo",  "#7C4DFF"),
    ("reprovado", "Rejeitado",            "#D64545"),
]

# O tier traduz a probabilidade em palavra. A régua ordena; o tier explica.
TIER = [("CONCLUSIVO", 90), ("FORTE", 80), ("MODERADO", 65),
        ("FRAGIL", 40), ("SEM_PAR", 0)]

PAUTA = [
    ("confirmar_atividade",  "Confirmar que o comércio opera"),
    ("confirmar_numero",     "Conferir o número da porta"),
    ("contar_unidades",      "Contar as unidades no imóvel"),
    ("fotografar_fachada",   "Fotografar a fachada"),
    ("registrar_coordenada", "Registrar a coordenada no local"),
    ("confirmar_endereco",   "Confirmar qual é o endereço"),
]

MOTIVOS = [
    ("fachada_residencial",    "Fachada residencial"),
    ("endereco_divergente",    "Endereço divergente"),
    ("comercio_encerrado",     "Comércio encerrado"),
    ("duplicado",              "Duplicado"),
    ("evidencia_insuficiente", "Evidência insuficiente"),
    ("ja_e_comercial",         "Já é comercial"),
    ("outro",                  "Outro"),
]

# O que cada aba RELACIONA contra a âncora. É a coluna "regra que casou" da
# tabela de cruzamento — sem ela a tela mostra número sem dizer de onde veio.
REGRA = {
    "poi":           "estabelecimento georreferenciado no endereço",
    "google":        "painel do Google e leitura da fachada em Street View",
    "receita":       "CNPJ confirmado contra o endereço e o ramo",
    "redes_sociais": "perfil que declara o mesmo endereço",
    "delivery":      "loja em plataforma de entrega operando no endereço",
    "imagens":       "leitura das imagens por IA, sem ver o cadastro",
}

RESUMO_FONTE = {
    "poi":           "O ponto como o Google Maps o registra",
    "google":        "Reputação, funcionamento e coordenada do painel",
    "receita":       "CNPJ, razão social e situação cadastral",
    "redes_sociais": "Perfis públicos e a data da última publicação",
    "delivery":      "Presença em plataforma de entrega",
    "imagens":       "O que a IA leu na fachada, sem ver o cadastro",
}


def _tier(p: float) -> str:
    for nome, piso in TIER:
        if p >= piso:
            return nome
    return "SEM_PAR"


def _probabilidade(aba: dict) -> int:
    """Quanto aquela fonte sustenta o ponto, de 0 a 100.

    NÃO é probabilidade calibrada, e o payload diz isso em `prob_base:
    DECLARADA`. Ela ORDENA e EXPLICA: mais campos preenchidos e mais evidência
    forte empurram para cima; evidência contrária puxa para baixo.

    Virar CALIBRADA exigiria amostra rotulada de campo e o `n` declarado — e o
    dia em que houver, o número muda de natureza e o rótulo tem de mudar junto.
    """
    linhas = [ln for g in aba["grupos"] for ln in g["linhas"]]
    if not linhas:
        return 0
    peso = {"forte": 3, "media": 2, "neutra": 1, "contraria": -4}
    bruto = sum(peso.get(ln["peso"], 1) for ln in linhas)
    teto = 3 * len(linhas)
    return max(0, min(100, round(50 + 50 * bruto / max(teto, 1))))


def _vocabulario() -> dict:
    return {
        "status": [{"id": i, "rotulo": r, "cor": c} for i, r, c in STATUS],
        "tier": [{"id": t, "piso": p} for t, p in TIER],
        "peso_evidencia": [
            {"id": "forte", "rotulo": "Forte"},
            {"id": "media", "rotulo": "Média"},
            {"id": "neutra", "rotulo": "Neutra"},
            # Evidência CONTRA é evidência. Esconder o que desmente é o jeito
            # mais rápido de fabricar confiança falsa.
            {"id": "contraria", "rotulo": "Contrária"},
        ],
        "prob_base": [{"id": "DECLARADA"}, {"id": "CALIBRADA"}],
        "pauta_campo": [{"id": i, "rotulo": r} for i, r in PAUTA],
        "motivo_reprova": [{"id": i, "rotulo": r} for i, r in MOTIVOS],
        "prioridade_campo": [{"id": "normal"}, {"id": "alta"}],
    }


def _fontes_catalogo(con, e_root: bool) -> list:
    """As abas que existem, com os campos que o catálogo declara."""
    with con.cursor() as cur:
        cur.execute("""select fonte::text, chave, rotulo, grupo, ajuda,
                              procedencia, formato
                         from comercialradar.campo_catalogo
                        where ativo order by fonte, ordem, id""")
        linhas = cur.fetchall()

    por_fonte: dict = {}
    for fonte, chave, rotulo, grupo, ajuda, proc, formato in linhas:
        campo = {"col": chave, "rotulo": rotulo, "grupo": grupo or "geral",
                 "tipo": formato}
        if ajuda:
            campo["nota"] = ajuda
        if e_root and proc:
            campo["procedencia"] = proc
        por_fonte.setdefault(fonte, []).append(campo)

    saida = []
    for fonte, rotulo in ficha_abas.ABAS:
        if fonte not in por_fonte:
            continue
        saida.append({
            "id": fonte, "rotulo": rotulo,
            "endereco": fonte in ("poi", "google", "receita"),
            "imagem": fonte in ("poi", "google", "imagens"),
            "resumo": RESUMO_FONTE.get(fonte, ""),
            "campos": por_fonte[fonte],
        })
    return saida


def _um_poi(con, poi_id: int, item: dict, e_root: bool) -> dict:
    """Um POI no formato que a bancada espera de uma 'ligação'."""
    import dossie

    dados = dossie.coletar(poi_id, con)
    abas = ficha_abas.montar(con, dados, e_root)

    fontes: dict = {}
    for aba in abas:
        p = _probabilidade(aba)
        fontes[aba["fonte"]] = {
            "probabilidade": p,
            "tier": _tier(p),
            "prob_base": "DECLARADA",
            "regra_match": REGRA.get(aba["fonte"], ""),
            "contrarias": aba["contrarias"],
            "campos": {ln["chave"]: ln["valor"]
                       for g in aba["grupos"] for ln in g["linhas"]},
        }

    with con.cursor() as cur:
        cur.execute("""select veredito, veredito_motivo, confere,
                              recomendar_visita, recomendacao_motivo, n_imagens
                         from comercialradar.analise_ia where poi_id=%s
                        order by criado_em desc limit 1""", (poi_id,))
        ia = cur.fetchone()

    favoraveis = sum(1 for f in fontes.values() if f["probabilidade"] >= 70)
    contra = sum(f["contrarias"] for f in fontes.values())

    return {
        "num_ligacao": str(poi_id),
        "status": item.get("status") or "pendente",
        "prioridade": item.get("prioridade") or "normal",
        "ancora": {
            "poi_id": poi_id,
            "nome": dados.get("nome"),
            "categoria": dados.get("categoria"),
            "logradouro": dados.get("endereco"),
            "bairro": None,
            "municipio": dados.get("cidade"),
            "uf": dados.get("uf"),
            "lat": dados.get("lat"), "lng": dados.get("lng"),
            "cnpj": dados.get("cnpj"),
            "razao_social": dados.get("razao_social"),
        },
        "fontes": fontes,
        "ia": {
            "veredito": (ia[0] if ia else None),
            "motivo": (ia[1] if ia else None),
            "confere": (ia[2] if ia else None),
            # A confiança é a MÉDIA das fontes, e não um número que a IA
            # inventou: assim ela sobe quando mais fontes concordam, que é o
            # que o operador espera ao olhar a régua.
            "confianca": (round(sum(f["probabilidade"] for f in fontes.values())
                                / len(fontes)) if fontes else 0),
            "n_fontes_avaliadas": len(fontes),
            "n_fontes_confirmadoras": favoraveis,
            "placar": {"favoraveis": favoraveis,
                       "contrarias": contra,
                       "neutras": len(fontes) - favoraveis},
            "recomendar_visita": (ia[3] if ia else None),
            "recomendacao_motivo": (ia[4] if ia else None),
            "n_imagens": (ia[5] if ia else 0),
        },
        "decisao": {
            "status": item.get("status") or "pendente",
            "decidido_por": item.get("supervisor"),
            "decidido_em": (item["decidido_em"] if item.get("decidido_em")
                            else None),
            "motivo": item.get("motivo_escrito") or "",
            "observacao": item.get("observacao") or "",
            "pauta": item.get("pauta") or [],
        },
        "fotos": [f.get("src") for f in (dados.get("fotos") or [])
                  if isinstance(f, dict) and f.get("src")][:8],
        "streetview": dados.get("streetview_mirado") or dados.get("streetview"),
    }


def montar(con, itens: list, e_root: bool, base: str = "") -> dict:
    """O payload inteiro: vocabulário, catálogo de fontes e a fila."""
    ligacoes = []
    for it in itens:
        try:
            ligacoes.append(_um_poi(con, it["poi_id"], it, e_root))
        except Exception as e:
            # Um POI que falha não pode derrubar a fila inteira — o operador
            # perderia acesso a 39 casos por causa de um.
            ligacoes.append({"num_ligacao": str(it.get("poi_id")),
                             "status": it.get("status") or "pendente",
                             "erro": f"{type(e).__name__}: {str(e)[:120]}"})
    return {
        "meta": {
            "produto": "ComercialRadar",
            "versao_payload": "1.0.0",
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "base": base or "fila de validação",
            "fonte_ancora": "POI",
            "nota_probabilidade": (
                "prob_base=DECLARADA. O número ORDENA e EXPLICA; não é "
                "probabilidade calibrada. Vira CALIBRADA só com amostra "
                "rotulada de campo e o n declarado."),
            "procedencia_visivel": e_root,
        },
        "vocabulario": _vocabulario(),
        "fontes": _fontes_catalogo(con, e_root),
        "ligacoes": ligacoes,
    }
