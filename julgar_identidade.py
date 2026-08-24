# -*- coding: utf-8 -*-
"""É o mesmo estabelecimento? Quem decide é a IA, não uma régua de texto.

O CASO QUE MOTIVOU

    pedido:  "Bussbier Cerveja Artesanal e Bebidas"
    Maps:    "BussBier Chopp Para Festas"
    semelhança de texto: 0,548  ->  o código dizia DIVERGENTE

E jogava fora, ou entregava com um carimbo de desconfiança. Só que:

    endereço   R. Roberto Francisco Behrens, 200 — o mesmo do banco
    telefone   (51) 98496-9787 — o mesmo do banco
    categoria  Loja de Conveniência
    ramo       chopp, cerveja, bebidas — a mesma coisa

Qualquer pessoa vê que é a mesma loja. `SequenceMatcher` não vê, porque compara
letras e não sabe que "Chopp" e "Cerveja Artesanal" são o mesmo negócio.

POR QUE ISTO NÃO CONTRARIA O LIMIAR DE 0,90

O limiar continua onde está, e continua certo para o PIPELINE: lá são milhares
de POIs sem revisão humana, e um falso positivo entra calado no banco. O
comentário do `nome_match` guarda os casos que passaram a 0,72 — um "ESF João
XXIII" casando com uma UBS de outro estado.

O que muda é o CHAT, onde a conversa pode custear uma segunda opinião. A régua
de texto deixa de ser o veredito e passa a ser o gatilho: quando ela desconfia,
a IA olha o conjunto — nome, endereço, telefone, categoria — e decide.

O DESENHO, que o projeto já validou

Julgamento ISOLADO: esta chamada faz UMA pergunta e nada mais. Sem histórico,
sem outras tarefas, sem a conversa em volta. Misturar julgamento com outra
tarefa na mesma chamada foi o que tornou o veredito visual não confiável, e
separá-los foi o que o consertou.

E o julgamento é feito SOBRE EVIDÊNCIA, não sobre os nomes soltos: endereço e
telefone iguais valem mais que qualquer semelhança de escrita — e nomes
parecidos com endereços distantes são justamente a armadilha.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

PERGUNTA = """Você recebe DOIS registros de estabelecimento comercial e decide
uma coisa só: são o MESMO negócio, no mundo real?

PEDIDO PELO USUÁRIO
  nome: {nome_pedido}
  cidade: {cidade}

ENCONTRADO NO GOOGLE MAPS
  nome: {nome_achado}
  endereço: {endereco}
  telefone: {telefone}
  categoria: {categoria}

COMO DECIDIR
- Endereço igual e telefone igual são prova FORTE de ser o mesmo, mesmo com
  nomes bem diferentes: loja muda de nome, usa nome fantasia, ou o Maps guarda
  um nome antigo.
- Nomes que descrevem o MESMO RAMO contam a favor ("Chopp", "Cerveja
  Artesanal" e "Bebidas" são o mesmo negócio; "Auto Peças" e "Padaria" não).
- Endereços diferentes na mesma cidade contam CONTRA, por mais parecido que
  seja o nome — é a armadilha do homônimo.
- Na dúvida real, responda "incerto". Não invente certeza.

Responda SÓ com este JSON, sem mais nada:
{{"mesmo": "sim"|"nao"|"incerto", "porque": "<uma frase curta>"}}"""


def _perguntar(texto: str, segundos: int) -> dict:
    """Uma pergunta ao modelo, isolada, devolvendo o JSON que ele respondeu.

    Falha em silencio de proposito: julgamento que nao aconteceu volta como
    erro, e o chamador transforma em "incerto". Nunca em aprovacao.
    """
    import agente_local as A

    corpo = {"model": A.MODELO, "temperature": 0.0, "max_tokens": 200,
             "messages": [{"role": "user", "content": texto}]}
    try:
        req = urllib.request.Request(
            f"{A.SPARK}/chat/completions", method="POST",
            data=json.dumps(corpo).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=segundos) as r:
            bruto = (json.loads(r.read())["choices"][0]["message"]
                     .get("content") or "")
    except Exception as e:
        return {"erro": f"nao consegui julgar ({type(e).__name__})"}

    ini, fim = bruto.find("{"), bruto.rfind("}")
    if ini < 0 or fim <= ini:
        return {"erro": "resposta ilegivel do julgador"}
    try:
        return json.loads(bruto[ini:fim + 1])
    except Exception:
        return {"erro": "resposta ilegivel do julgador"}


def julgar(nome_pedido: str, nome_achado: str, endereco: str = "",
           telefone: str = "", categoria: str = "", cidade: str = "",
           segundos: int = 60) -> dict:
    """Pergunta ao modelo se são o mesmo lugar. Devolve o veredito dele.

    Falha em silêncio de propósito: se o modelo não responder, devolve
    "incerto". Um julgamento que não aconteceu não pode virar aprovação.
    """
    import agente_local as A

    if not nome_pedido or not nome_achado:
        return {"mesmo": "incerto", "porque": "faltou um dos nomes"}

    corpo = {
        "model": A.MODELO, "temperature": 0.0, "max_tokens": 160,
        "messages": [{"role": "user", "content": PERGUNTA.format(
            nome_pedido=nome_pedido, nome_achado=nome_achado,
            endereco=endereco or "(não informado)",
            telefone=telefone or "(não informado)",
            categoria=categoria or "(não informada)",
            cidade=cidade or "(não informada)")}],
    }
    d = _perguntar(corpo["messages"][0]["content"], segundos)
    if "erro" in d:
        return {"mesmo": "incerto", "porque": d["erro"]}

    resposta = str(d.get("mesmo", "")).strip().lower()
    if resposta not in ("sim", "nao", "não", "incerto"):
        return {"mesmo": "incerto", "porque": "veredito fora do combinado"}
    return {"mesmo": "nao" if resposta == "não" else resposta,
            "porque": str(d.get("porque", ""))[:200]}


PERGUNTA_CNPJ = """Você decide UMA coisa: este CNPJ é do estabelecimento?

O ESTABELECIMENTO (como o Google Maps o mostra)
  nome: {nome}
  endereço: {endereco}
  telefone: {telefone}
  categoria: {categoria}

O REGISTRO NA RECEITA
  CNPJ: {cnpj}
  razão social: {razao}
  nome fantasia: {fantasia}
  endereço: {log}, {num} - {bairro}
  CNAE principal: {cnae}
  situação: {situacao}

JÁ CONFERIDO PARA VOCÊ (não recalcule, use)
  {conferencia}

COMO DECIDIR
- Mesmo logradouro E mesmo número: prova FORTE de ser o mesmo ponto.
- Nome fantasia igual ao do Maps, mesmo com razão social bem diferente, também
  conta muito — a razão social quase nunca é o nome da placa.
- USE A CONFERENCIA DE BAIRRO acima como fato dado; NAO compare os textos
  voce mesmo. Ela ja foi calculada.
- MESMA REGIAO BASTA. Se a conferência acima disser MESMA REGIÃO (ou CEP igual
  ou vizinho) e o ramo for compatível, responda "sim" — MESMO com o nome da rua
  e o número diferentes. O cadastro do governo diverge do Maps por natureza:
  registra pelo loteamento ("Quadra EE Dois, 01") onde o Maps mostra a via de
  acesso ("Av. Dezessete de Abril" ou "Rua João Gualberto"). Exigir a rua
  idêntica reprova o CNPJ certo, que foi o que aconteceu.
  Caso real: o Vancosty de Canoas tem SEIS CNPJs no grupo, e só um está em
  GUAJUVIRAS — o mesmo bairro que o Maps aponta; os outros cinco estão em
  bairros e cidades diferentes. Bairro igual + cidade igual + ramo compatível é
  sinal FORTE, mesmo com o nome da rua diferente.
- BAIRRO DIFERENTE, sim, é sinal forte CONTRA: aí é outra unidade da rede ou a
  matriz. Responda "nao" e diga que parece outra unidade.
- CNAE incompatível com a categoria (locadora de veículos x padaria) é "nao".
- Situação BAIXADA não impede que seja o mesmo negócio — ela diz que o registro
  foi encerrado, o que é informação, não desqualificação.
- Sem endereço para comparar, responda "incerto".

Responda SÓ com este JSON:
{{"confirma": "sim"|"nao"|"incerto", "porque": "<uma frase curta>"}}"""


def _mesmo_bairro(endereco_maps: str, bairro_receita: str) -> str:
    """Diz, como FATO, se o bairro da Receita aparece no endereco do Maps.

    POR QUE ISTO SAI DO MODELO E VIRA CODIGO

    Ensinar a regra do bairro fez o juiz aprovar CNPJs de PQ ESPIRITO SANTO,
    SAO VICENTE e JARDIM BETANIA dizendo "mesmo bairro" — comparando com
    GUAJUVIRAS. Ele nao errou o RACIOCINIO; errou a LEITURA.

    Comparar duas cadeias de texto e o que codigo faz bem e modelo faz mal.
    Entao o fato vem pronto, e o julgamento continua sendo dele: pesar bairro
    contra ramo, nome fantasia e situacao e que e o trabalho de discernimento.
    """
    import unicodedata

    def limpar(t):
        t = "".join(c for c in unicodedata.normalize("NFD", (t or "").upper())
                    if unicodedata.category(c) != "Mn")
        return " ".join(t.replace("-", " ").split())

    a, b = limpar(endereco_maps), limpar(bairro_receita)
    if not b:
        return "a Receita nao informa o bairro — nao da para comparar"
    if not a:
        return "o Maps nao trouxe endereco — nao da para comparar"
    if b in a:
        return (f"MESMA REGIAO: o bairro '{b}' da Receita aparece no endereco "
                f"do Maps. Nome de rua diferente NAO desmente isso — o cadastro "
                f"do governo registra por loteamento onde o Maps mostra a via "
                f"de acesso, e sao o mesmo lugar.")
    return (f"REGIAO DIFERENTE: a Receita diz bairro '{b}' e isso NAO aparece "
            f"no endereco do Maps ('{a[:70]}')")


def _mesmo_cep(endereco_maps: str, cep_receita: str) -> str:
    """Os cinco primeiros digitos do CEP delimitam a REGIAO, nao a porta.

    Serve de segunda opiniao quando o bairro falta ou vem escrito de outro
    jeito: 92415 e 92440 sao Canoas/Guajuviras; 94110 ja e outra cidade. Duas
    ruas do mesmo loteamento compartilham o prefixo.
    """
    import re as _re

    d = "".join(c for c in (cep_receita or "") if c.isdigit())
    m = _re.search(r"(\d{5})-?(\d{3})", endereco_maps or "")
    if len(d) < 5 or not m:
        return "sem CEP nos dois lados para comparar"
    if d[:5] == m.group(1):
        return f"CEP IGUAL ({d[:5]})"
    if d[:3] == m.group(1)[:3]:
        return f"CEP VIZINHO ({d[:5]} x {m.group(1)}) — mesma regiao postal"
    return f"CEP DISTANTE ({d[:5]} x {m.group(1)}) — regioes diferentes"


def confirmar_cnpj(cnpj: str, nome: str, endereco: str = "", telefone: str = "",
                   categoria: str = "", segundos: int = 60) -> dict:
    """O CNPJ é DESTE ponto? Consulta a Receita e submete ao julgamento da IA.

    POR QUE EXISTE

    `consultar_receita` acha candidatos; ninguém dizia se o candidato ERA o
    estabelecimento. No Supermercado Vancosty a resposta entregou
    00.890.225/0005-94 como se fosse dele, quando a Receita registra aquele
    CNPJ em "EE DOIS, 01" e o Maps mostra o ponto na Av. Dezessete de Abril, em
    Guajuviras. Endereços diferentes: pode ser a matriz, outra loja da rede, ou
    coincidência de nome.

    Afirmar sem cruzar é o mesmo erro do "Fonte: Econodata" por outro caminho —
    número certo de empresa errada é pior que número nenhum, porque some a
    dúvida que faria alguém conferir.

    A NEGATIVA É RESULTADO, e não falha: saber que aquele CNPJ NÃO é do ponto
    poupa a visita e diz onde procurar em seguida.
    """
    import agente_local as A

    d = A.consultar_receita(cnpj=cnpj)
    empresas = (d or {}).get("empresas") or []
    if not empresas:
        return {"confirma": "nao", "cnpj": cnpj,
                "porque": "este CNPJ não existe na base da Receita"}

    e = empresas[0]
    corpo = PERGUNTA_CNPJ.format(
        nome=nome or "(não informado)", endereco=endereco or "(não informado)",
        telefone=telefone or "(não informado)",
        categoria=categoria or "(não informada)",
        cnpj=cnpj, razao=e.get("razao_social") or "(sem)",
        fantasia=e.get("nome_fantasia") or "(sem nome fantasia)",
        log=e.get("logradouro") or "?", num=e.get("numero") or "?",
        bairro=e.get("bairro") or "?", cnae=e.get("cnae") or "?",
        situacao=e.get("situacao") or "?",
        conferencia=(_mesmo_bairro(endereco, e.get("bairro") or "") + " | " +
                     _mesmo_cep(endereco, e.get("cep") or "")))

    v = _perguntar(corpo, segundos)
    return {"cnpj": cnpj, "confirma": v.get("confirma", "incerto"),
            "porque": v.get("porque", ""),
            "registro_na_receita": {
                "razao_social": e.get("razao_social"),
                "nome_fantasia": e.get("nome_fantasia"),
                "endereco": f"{e.get('logradouro')}, {e.get('numero')} - "
                            f"{e.get('bairro')}",
                "situacao": e.get("situacao"), "cnae": e.get("cnae")}}


ESQUEMA_CNPJ = {
    "type": "function", "function": {
        "name": "confirmar_cnpj",
        "description": (
            "Diz se um CNPJ e MESMO do estabelecimento. Consulta a Receita e "
            "compara razao social, nome fantasia, endereco e CNAE com o que o "
            "Maps mostrou, devolvendo 'sim', 'nao' ou 'incerto' com o motivo. "
            "USE SEMPRE antes de afirmar um CNPJ — inclusive o que veio de "
            "`consultar_receita`, porque nome parecido em endereco diferente "
            "costuma ser OUTRA UNIDADE da rede, nao este ponto. "
            "Passe o `nome` e o `endereco` que o `consultar_maps` devolveu."),
        "parameters": {"type": "object", "properties": {
            "cnpj": {"type": "string"},
            "nome": {"type": "string", "description": "nome do Maps"},
            "endereco": {"type": "string", "description": "endereco do Maps"},
            "telefone": {"type": "string"},
            "categoria": {"type": "string"}},
            "required": ["cnpj", "nome"]}},
}
