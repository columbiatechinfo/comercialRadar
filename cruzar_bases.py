# -*- coding: utf-8 -*-
"""Cruzamento entre as bases por CNPJ, endereço e nome.

As quatro bases não têm as mesmas chaves, e fingir que têm é a origem do
casamento errado:

    base               CNPJ   endereço              nome            geo
    pois                ✓     endereco (texto)      ✓               ✓
    cadastro_cliente    —     logradouro+número     — (não tem)     ✓
    cnpj_tratado        ✓     — (só coordenada)     ✓               ✓
    ifood_merchant     (pendente)  rua+número       ✓               (pendente)
    cadastur            ✓     texto livre           ✓               —

Cada par cruza pelas chaves que AMBOS os lados têm. `cadastro_cliente` não
guarda nome do estabelecimento — é cadastro de imóvel —, então nenhum
cruzamento por nome o envolve, e forçar um seria inventar dado. Pelo mesmo
motivo o `cadastur` não entra em cruzamento por geo: ele não tem coordenada, e
a skill de origem não geocodifica de propósito.

Três princípios, todos aprendidos apanhando:

1. **CNPJ não tem gradação.** Documento igual é 1.0; documento diferente não é
   0.9, é ausência de cruzamento.
2. **Nome sozinho não casa.** "Farmácia São João" existe dezenas de vezes na
   cidade. Todo cruzamento por nome exige âncora espacial — coordenada próxima
   ou mesmo bairro. Sem âncora, não se grava.
3. **Ambíguo é registrado, não resolvido.** Quando o melhor candidato empata
   com o segundo, a linha entra com `ambiguo=true` e vai para revisão humana.
   Escolher o primeiro produziria base limpa e errada.

Uso:
    python cruzar_bases.py --cidade canoas                 # todos os pares
    python cruzar_bases.py --cidade canoas --par pois:ifood_merchant
    python cruzar_bases.py --cidade canoas --simular       # não grava
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
from collections import defaultdict

from psycopg2.extras import execute_values

import base_comum as bc

# ─── limiares ────────────────────────────────────────────────────────────────
# Calibrados para errar para o lado de NÃO casar: um cruzamento falso vira
# visita perdida em campo, que custa mais que um cruzamento perdido.
MIN_NOME = 0.72          # similaridade de tokens mínima para casar por nome
EMPATE = 0.05            # candidatos dentro disto do melhor tornam o par ambíguo
RAIO_NOME_M = 300.0      # âncora espacial do cruzamento por nome
RAIO_GEO_M = 40.0        # proximidade pura, evidência fraca
RAIO_ENDERECO_M = 300.0  # teto para aceitar mesmo nome+número (rua homônima)
CELULA_M = 500.0         # lado da célula do índice espacial

# ─── normalização ────────────────────────────────────────────────────────────
# Sufixos societários e ruído que aparecem num lado e não no outro. "PADARIA
# CENTRAL LTDA ME" e "Padaria Central" são a mesma coisa; sem remover isto, a
# similaridade de tokens despenca e o par verdadeiro é descartado.
_RUIDO = {
    "ltda", "me", "epp", "eireli", "sa", "s", "a", "cia", "e", "de", "da", "do",
    "das", "dos", "em", "no", "na", "com", "comercio", "comercial", "servicos",
    "empreendimentos", "participacoes", "filial", "matriz", "the", "restaurante",
}

_VIA = {
    "r": "rua", "av": "avenida", "avn": "avenida", "trav": "travessa",
    "tv": "travessa", "al": "alameda", "pc": "praca", "praça": "praca",
    "rod": "rodovia", "estr": "estrada", "lgo": "largo", "bc": "beco",
}


def sem_acento(s: str | None) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def tokens(s: str | None) -> frozenset:
    """Nome reduzido ao conjunto de palavras que carregam identidade."""
    limpo = re.sub(r"[^a-z0-9 ]", " ", sem_acento(s))
    t = {p for p in limpo.split() if len(p) > 1 and p not in _RUIDO}
    # nome que só tinha ruído volta com o original, senão vira conjunto vazio e
    # casaria com todo outro conjunto vazio
    return frozenset(t) or frozenset(limpo.split())


def parecenca(a: frozenset, b: frozenset) -> float:
    """Sobreposição de tokens, normalizada pelo lado MENOR.

    Jaccard puro pune o nome longo: "Farmácia São João Filial Centro" contra
    "Farmácia São João" daria 0,6. Dividir pelo menor reconhece que um nome é
    prefixo informativo do outro — que é o caso real entre razão social e
    nome fantasia.
    """
    if not a or not b:
        return 0.0
    inter = a & b
    # DUAS palavras em comum, no mínimo. Dividir pelo menor sozinho fazia um
    # nome de uma palavra casar 1.0 com qualquer nome que a contivesse:
    # "PASTEL" batia com "A Casa do Pastel", "SUPLEMENTOS" com "Intense Life
    # Suplementos". Medido contra a Receita: derrubou o empate de 72% para 28%.
    if len(inter) < 2 and not (len(a) == 1 and len(b) == 1 and inter):
        return 0.0
    return len(inter) / min(len(a), len(b))


def digitos(s) -> str:
    return "".join(c for c in str(s or "") if c.isdigit())


def cnpj_norm(s) -> str | None:
    d = digitos(s)
    return d if len(d) == 14 else None


def via_norm(s: str | None) -> str:
    """Logradouro comparável: tipo expandido, sem acento, sem pontuação."""
    p = re.sub(r"[^a-z0-9 ]", " ", sem_acento(s)).split()
    if p and p[0] in _VIA:
        p[0] = _VIA[p[0]]
    return " ".join(p)


_RE_CEP = re.compile(r"\b\d{5}-?\d{3}\b")
_RE_UF = re.compile(r"^[a-z]{2}$")


def partes_endereco(texto: str | None, cidade: str | None = None) -> tuple:
    """Quebra o endereço em texto livre do Maps em (logradouro, número, bairro).

    O formato que o Maps devolve é estável:

        R. Boa Saúde, 1508 - Rio Branco, Canoas - RS, 92200-001, Brasil
        └ logradouro ┘ └nº┘  └ bairro ┘  └cidade┘ UF   └ CEP ┘

    Sem esta leitura, `pois` não produz chave de endereço nenhuma e o cruzamento
    com `cadastro_cliente` — que é o que aponta comércio fora da base do cliente
    — devolve zero. Foi exatamente o que aconteceu na primeira execução.

    Devolve None em cada posição que não se puder afirmar. O Maps repete o nome
    da cidade no lugar do bairro quando não sabe o bairro; isso vira None, e não
    um bairro chamado "Canoas" que casaria com a cidade inteira.
    """
    if not texto:
        return (None, None, None)
    t = re.sub(r",?\s*brasil\s*$", "", texto.strip(), flags=re.I)
    t = _RE_CEP.sub("", t)
    t = re.sub(r"\bcep\b", "", t, flags=re.I)

    # tanto " - " quanto "," separam campos nesse formato; tratar os dois igual
    campos = [c.strip(" ,-") for c in re.split(r"\s+-\s+|,", t)]
    campos = [c for c in campos if c]
    if not campos:
        return (None, None, None)

    logradouro = campos[0]
    numero = None
    resto = campos[1:]
    if resto and re.fullmatch(r"\d{1,6}[a-zA-Z]?", resto[0]):
        numero = digitos(resto[0])
        resto = resto[1:]

    cid = sem_acento(cidade)
    bairro = None
    for c in resto:
        s = sem_acento(c)
        # descarta UF solta, a cidade repetida e sobras numéricas
        if not s or _RE_UF.fullmatch(s) or s == cid or s.isdigit():
            continue
        bairro = c
        break
    return (logradouro, numero, bairro)


# Os tipos de via, já expandidos. Saem da chave porque as bases discordam: o
# cadastro do cliente grava "INDIO SEPE" e o Maps grava "Rua Índio Sepé". Manter
# o tipo fazia as duas nunca casarem — foram 36 pares onde deviam ser milhares.
_TIPOS = {"rua", "avenida", "travessa", "alameda", "praca", "rodovia",
          "estrada", "largo", "beco", "linha", "servidao", "acesso", "viela"}


def partes_cadastur(texto: str | None, cidade: str | None = None) -> tuple:
    """Quebra o endereço do Cadastur em (logradouro, número, bairro).

    O formato NÃO tem separador nenhum:

        Getúlio Vargas 4861 Canoas Centro CEP: 9201024
        └ logradouro ┘ └nº┘ └cidade┘ └bairro┘  └ CEP ┘

    Passá-lo pelo leitor do Maps devolvia o endereço inteiro como se fosse o
    nome da rua — foi o que fez o Cadastur entrar no cruzamento com ZERO chave
    de endereço e só conseguir casar por CNPJ. Como 21,6% das linhas não têm
    CNPJ, aquelas ficavam sem nenhuma forma de casar.

    A âncora é o MUNICÍPIO. Sem separador, não há como saber onde a rua termina
    e a cidade começa — mas o município é conhecido: vem da coluna ao lado.
    Achá-lo no texto parte a string nos dois pedaços certos.

    Sem o município, devolve (None, None, None). Chutar a divisão produziria
    "Getúlio Vargas 4861 Canoas" como nome de rua, que não casa com nada e
    ainda ocupa o lugar de um valor honestamente ausente.
    """
    if not texto or not cidade:
        return (None, None, None)
    t = re.sub(r"\bcep\b\s*:?\s*\d*", " ", texto, flags=re.I)
    t = re.sub(r"\s+\b[A-Z]{2}\b\s*$", " ", t)          # UF no fim
    t = re.sub(r"\s{2,}", " ", t).strip()

    alvo = sem_acento(cidade)
    palavras = t.split()
    corte = None
    normal = [sem_acento(w) for w in palavras]
    n_alvo = alvo.split()
    for i in range(len(normal) - len(n_alvo) + 1):
        if normal[i:i + len(n_alvo)] == n_alvo:
            corte = (i, i + len(n_alvo))
            break
    if corte is None:
        return (None, None, None)

    esquerda = palavras[:corte[0]]
    bairro = " ".join(palavras[corte[1]:]).strip() or None

    # O número é o último grupo de dígitos ANTES do município. "Getúlio Vargas
    # 4861" → 4861. Quando não há, o endereço veio sem número — acontece em 26%
    # das linhas — e a chave simplesmente não se forma, que é o certo.
    numero = None
    if esquerda and esquerda[-1].isdigit():
        numero = esquerda[-1]
        esquerda = esquerda[:-1]
    return (" ".join(esquerda).strip() or None, numero, bairro)


def endereco_chave(rua: str | None, numero) -> str | None:
    """Chave de endereço: NÚCLEO do logradouro + número.

    Sem número não se produz chave. Logradouro sozinho casa a rua inteira, e
    uma avenida tem centenas de imóveis — seria cruzamento por acaso.

    O tipo da via fica de fora porque uma das bases não o guarda. O preço é
    "Rua Brasil, 100" poder colidir com "Avenida Brasil, 100"; quem desfaz isso
    é a distância, exigida em `por_endereco` quando os dois lados têm
    coordenada. Chave frouxa com árbitro é melhor que chave rígida que não casa.
    """
    n = digitos(numero)
    if not n:
        return None
    p = via_norm(rua).split()
    if p and p[0] in _TIPOS:
        p = p[1:]
    v = " ".join(p)
    return f"{v}|{n}" if v else None


def metros(a_lat, a_lng, b_lat, b_lng) -> float | None:
    if None in (a_lat, a_lng, b_lat, b_lng):
        return None
    dy = (float(b_lat) - float(a_lat)) * 111_320.0
    dx = (float(b_lng) - float(a_lng)) * 111_320.0 * math.cos(
        math.radians((float(a_lat) + float(b_lat)) / 2))
    return math.hypot(dx, dy)


# ─── as bases ────────────────────────────────────────────────────────────────
# Cada consulta devolve as mesmas colunas lógicas. Onde a base não tem o campo,
# vem NULL — e o cruzamento por aquela chave simplesmente não acontece, em vez
# de acontecer com valor inventado.
BASES = {
    "pois": """
        select id::text, nome, nome_fantasia, razao_social, cnpj,
               endereco, null::text as numero, null::text as bairro,
               maps_lat as lat, maps_lng as lng
          from comercialradar.pois
         where (%(cidade)s is null or lower(cidade) = lower(%(cidade)s))
    """,
    "cadastro_cliente": """
        select id::text, null::text, null::text, null::text, null::text,
               logradouro, numero, bairro, lat, lng
          from comercialradar.cadastro_cliente
         where (%(cidade)s is null or lower(cidade) = lower(%(cidade)s))
    """,
    "cnpj_tratado": """
        select id::text, nome_fantasia, nome_fantasia, razao_social, cnpj,
               null::text, null::text, null::text, lat, lng
          from comercialradar.cnpj_tratado
    """,
    "ifood_merchant": """
        select merchant_id, nome, nome, null::text, cnpj,
               rua, numero, bairro, lat, lng
          from comercialradar.ifood_merchant
    """,
    # Cadastur: SEM coordenada propria — a skill de origem nao geocodifica, e o
    # endereco moderno e texto livre (numero em 74% das linhas, CEP em 55%).
    # Entao ele cruza por CNPJ, que e chave limpa, e por nome quando alguma
    # outra base emprestar a ancora espacial. Fingir uma coordenada aqui seria
    # inventar o dado que falta.
    #
    # O endereco COMERCIAL vem primeiro: e onde a atividade acontece. O da
    # Receita e a sede fiscal, e quando divergem e justamente o caso que
    # interessa — sede num escritorio, pousada noutro lugar.
    "cadastur": """
        select id::text, nome_fantasia, nome_fantasia, razao_social, cnpj,
               coalesce(endereco_comercial, endereco_rfb),
               null::text as numero, null::text as bairro,
               null::double precision as lat, null::double precision as lng
          from comercialradar.cadastur_prestador
         where (%(cidade)s is null or lower(municipio) = lower(%(cidade)s))
    """,
}


class Registro:
    __slots__ = ("id", "nomes", "cnpj", "endereco", "bairro", "lat", "lng")

    def __init__(self, linha):
        (self.id, nome, fantasia, razao, cnpj,
         rua, numero, bairro, lat, lng) = linha
        # os três nomes viram três conjuntos: a razão social e o nome fantasia
        # frequentemente não se parecem, e casar por qualquer um deles é
        # legítimo desde que a âncora espacial confirme
        self.nomes = [t for t in (tokens(nome), tokens(fantasia), tokens(razao)) if t]
        self.cnpj = cnpj_norm(cnpj)
        self.endereco = endereco_chave(rua, numero)
        self.bairro = sem_acento(bairro) or None
        self.lat = float(lat) if lat is not None else None
        self.lng = float(lng) if lng is not None else None


# Bases cujo endereço vem em texto livre e precisa ser lido antes de virar
# chave. `pois` guarda o endereço como o Maps devolveu, numa string só; o
# Cadastur moderno colapsou CEP, bairro e logradouro numa string — a geração
# legada (2006–2021) tinha as colunas separadas e era MAIS geocodificável que
# a atual.
TEXTO_LIVRE = {"pois", "cadastur"}


def carregar(con, base: str, cidade: str | None) -> list[Registro]:
    with con.cursor() as k:
        k.execute(BASES[base], {"cidade": cidade})
        linhas = k.fetchall()

    if base in TEXTO_LIVRE:
        # Cada base tem o SEU formato de texto livre, e um leitor só para as
        # duas devolvia lixo para uma delas. O do Maps separa por vírgula e
        # traço; o do Cadastur não separa nada.
        ler = partes_cadastur if base == "cadastur" else partes_endereco
        # O Cadastur guarda o município na própria linha, então dá para ler o
        # endereço mesmo numa execução sem `--cidade`. O `pois` depende do que
        # veio no argumento, como sempre dependeu.
        lidas = []
        for l in linhas:
            l = list(l)
            l[5], l[6], l[7] = ler(l[5], cidade)               # rua, número, bairro
            lidas.append(l)
        linhas = lidas
    return [Registro(l) for l in linhas]


# ─── índice espacial ─────────────────────────────────────────────────────────
def grade(regs: list[Registro]) -> dict:
    """Agrupa por célula de ~500 m.

    Comparar 27 mil POIs com 102 mil imóveis é 2,7 bilhões de pares — inviável
    em qualquer escala real. A célula reduz cada comparação aos vizinhos, que é
    o que torna isto executável na cidade inteira e não só numa amostra.
    """
    g = defaultdict(list)
    for r in regs:
        if r.lat is not None:
            g[(int(r.lat * 111_320 / CELULA_M), int(r.lng * 111_320 / CELULA_M))].append(r)
    return g


def vizinhos(g: dict, r: Registro):
    if r.lat is None:
        return
    cy = int(r.lat * 111_320 / CELULA_M)
    cx = int(r.lng * 111_320 / CELULA_M)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            yield from g.get((cy + dy, cx + dx), ())


# ─── os três cruzamentos ─────────────────────────────────────────────────────
def por_cnpj(a: list[Registro], b: list[Registro]) -> list[dict]:
    """Documento igual. Sem gradação e sem âncora — o CNPJ já é a âncora."""
    indice = defaultdict(list)
    for r in b:
        if r.cnpj:
            indice[r.cnpj].append(r)
    saida = []
    for ra in a:
        if not ra.cnpj:
            continue
        for rb in indice.get(ra.cnpj, ()):
            saida.append({
                "id_a": ra.id, "id_b": rb.id, "chave": "cnpj", "score": 1.0,
                "distancia_m": metros(ra.lat, ra.lng, rb.lat, rb.lng),
                "ambiguo": False, "concorrentes": 0,
                "evidencia": {"cnpj": ra.cnpj},
            })
    return saida


def por_endereco(a: list[Registro], b: list[Registro]) -> list[dict]:
    """Logradouro normalizado + número iguais.

    Um endereço pode abrigar várias empresas (galeria, prédio comercial), então
    N-para-N é o comportamento correto, não um defeito a deduplicar.
    """
    indice = defaultdict(list)
    for r in b:
        if r.endereco:
            indice[r.endereco].append(r)
    saida = []
    for ra in a:
        if not ra.endereco:
            continue
        aceitos = []
        for rb in indice.get(ra.endereco, ()):
            d = metros(ra.lat, ra.lng, rb.lat, rb.lng)
            # o árbitro da colisão: mesmo nome e mesmo número longe demais são
            # ruas homônimas de tipos diferentes, não o mesmo lugar
            if d is not None and d > RAIO_ENDERECO_M:
                continue
            aceitos.append((d, rb))
        for d, rb in aceitos:
            saida.append({
                "id_a": ra.id, "id_b": rb.id, "chave": "endereco",
                # confirmado por distância vale mais que aceito por falta de
                # coordenada; o consumidor precisa saber a diferença
                "score": 1.0 if d is not None else 0.85,
                "distancia_m": round(d, 1) if d is not None else None,
                # vários no mesmo endereço não é ambiguidade: é a realidade de
                # uma galeria. Fica anotado quantos são, sem bloquear.
                "ambiguo": False, "concorrentes": max(0, len(aceitos) - 1),
                "evidencia": {"endereco": ra.endereco,
                              "confirmado_por_distancia": d is not None},
            })
    return saida


def por_nome(a: list[Registro], b: list[Registro]) -> list[dict]:
    """Nome parecido COM âncora espacial. Nunca nome sozinho."""
    g = grade(b)
    porbairro = defaultdict(list)
    for r in b:
        if r.bairro:
            porbairro[r.bairro].append(r)

    saida = []
    for ra in a:
        if not ra.nomes:
            continue
        # a âncora: vizinhança geográfica quando há coordenada dos dois lados;
        # o bairro quando não há. Sem nenhuma das duas, o par não é avaliado.
        if ra.lat is not None:
            cands, ancora = vizinhos(g, ra), "geo"
        elif ra.bairro:
            cands, ancora = porbairro.get(ra.bairro, ()), "bairro"
        else:
            continue

        marcados = []
        for rb in cands:
            d = metros(ra.lat, ra.lng, rb.lat, rb.lng)
            if ancora == "geo" and (d is None or d > RAIO_NOME_M):
                continue
            s = max((parecenca(ta, tb) for ta in ra.nomes for tb in rb.nomes),
                    default=0.0)
            if s >= MIN_NOME:
                marcados.append((s, d, rb))

        if not marcados:
            continue
        marcados.sort(key=lambda x: (-x[0], x[1] if x[1] is not None else 1e9))
        melhor = marcados[0][0]
        # empate técnico com o segundo colocado = a máquina não sabe qual é.
        # Grava os empatados marcados para revisão em vez de chutar o primeiro.
        empatados = [m for m in marcados if melhor - m[0] <= EMPATE]
        ambiguo = len(empatados) > 1
        for s, d, rb in empatados:
            saida.append({
                "id_a": ra.id, "id_b": rb.id, "chave": "nome", "score": round(s, 3),
                "distancia_m": d, "ambiguo": ambiguo,
                "concorrentes": len(empatados) - 1,
                "evidencia": {"ancora": ancora,
                              "nome_a": " ".join(sorted(ra.nomes[0]))[:120],
                              "nome_b": " ".join(sorted(rb.nomes[0]))[:120]},
            })
    return saida


def por_geo(a: list[Registro], b: list[Registro]) -> list[dict]:
    """Proximidade pura — a evidência mais fraca, e rotulada como tal.

    Serve para o caso em que nenhuma das outras chaves existe dos dois lados,
    que é justamente `cnpj_tratado` (só coordenada) contra `cadastro_cliente`.
    O score decai com a distância para que o consumidor consiga ordenar.
    """
    g = grade(b)
    saida = []
    for ra in a:
        if ra.lat is None:
            continue
        perto = [(metros(ra.lat, ra.lng, rb.lat, rb.lng), rb) for rb in vizinhos(g, ra)]
        perto = [(d, rb) for d, rb in perto if d is not None and d <= RAIO_GEO_M]
        if not perto:
            continue
        perto.sort(key=lambda x: x[0])
        # SÓ o mais próximo. "Em qual imóvel este ponto está" tem uma resposta;
        # guardar os cinco vizinhos gerava cinco linhas onde quatro eram ruído e
        # multiplicava a tabela por cinco sem acrescentar informação.
        d, rb = perto[0]
        # empate real — dois imóveis à mesma distância — é ambiguidade de
        # verdade e vai marcado para o humano, em vez de resolvido no desempate
        # arbitrário da ordenação
        segundo = perto[1][0] if len(perto) > 1 else None
        ambiguo = segundo is not None and (segundo - d) < 5.0
        saida.append({
            "id_a": ra.id, "id_b": rb.id, "chave": "geo",
            "score": round(max(0.0, 1 - d / RAIO_GEO_M), 3),
            "distancia_m": round(d, 1), "ambiguo": ambiguo,
            "concorrentes": len(perto) - 1,
            "evidencia": {"raio_m": RAIO_GEO_M,
                          "segundo_mais_perto_m": round(segundo, 1) if segundo else None},
        })
    return saida


# quais chaves fazem sentido em cada par, dado o que cada base guarda
CHAVES = {
    "cnpj": (por_cnpj, lambda r: r.cnpj),
    "endereco": (por_endereco, lambda r: r.endereco),
    "nome": (por_nome, lambda r: r.nomes),
    "geo": (por_geo, lambda r: r.lat is not None),
}

GRAVAR = """
insert into comercialradar.cruzamento
       (base_a, id_a, base_b, id_b, chave, score, distancia_m,
        evidencia, ambiguo, concorrentes)
values %s
on conflict (tenant_id, base_a, id_a, base_b, id_b, chave) do update set
  score = excluded.score, distancia_m = excluded.distancia_m,
  evidencia = excluded.evidencia, ambiguo = excluded.ambiguo,
  concorrentes = excluded.concorrentes, criado_em = now()
"""


def cruzar(con, nome_a: str, nome_b: str, regs: dict, simular: bool) -> dict:
    # a ordem canônica evita que A↔B e B↔A virem duas linhas do mesmo fato
    if nome_a > nome_b:
        nome_a, nome_b = nome_b, nome_a
    a, b = regs[nome_a], regs[nome_b]
    conta = {}
    # Os pares que alguma chave FORTE já explicou. `geo` é último recurso: dizer
    # "estão a 12 m" sobre duas linhas que já casaram por CNPJ não acrescenta
    # nada, e em volume afoga as linhas que informam — eram 103 mil pares de geo
    # contra 16 mil de endereço no primeiro desenho.
    ja_explicados: set = set()

    for chave, (fn, tem) in CHAVES.items():
        # a chave só roda se AMBOS os lados a possuem em alguma linha
        if not any(tem(r) for r in a) or not any(tem(r) for r in b):
            continue
        t0 = time.time()
        pares = fn(a, b)
        if chave == "geo":
            pares = [p for p in pares
                     if (p["id_a"], p["id_b"]) not in ja_explicados]
        else:
            ja_explicados.update((p["id_a"], p["id_b"]) for p in pares)
        conta[chave] = (len(pares), sum(1 for p in pares if p["ambiguo"]),
                        time.time() - t0)
        if simular or not pares:
            continue
        # `execute_values` e não um execute por linha: são centenas de milhares
        # de cruzamentos numa cidade, e uma ida ao banco por linha multiplica o
        # tempo pelo RTT. É a regra do projeto e aqui ela é a diferença entre
        # minutos e horas.
        linhas = [(nome_a, p["id_a"], nome_b, p["id_b"], p["chave"], p["score"],
                   p["distancia_m"],
                   json.dumps(p["evidencia"], ensure_ascii=False),
                   p["ambiguo"], p["concorrentes"]) for p in pares]
        with con.cursor() as k:
            execute_values(k, GRAVAR, linhas, page_size=1000)
        con.commit()
    return conta


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cidade", default=None)
    p.add_argument("--par", action="append",
                   help="ex.: pois:ifood_merchant (repetível; padrão = todos)")
    p.add_argument("--simular", action="store_true", help="conta sem gravar")
    args = p.parse_args()

    con = bc.conectar()
    print("⟦fase⟧ cruzamento", flush=True)
    regs = {}
    for nome in BASES:
        t0 = time.time()
        regs[nome] = carregar(con, nome, args.cidade)
        com = lambda f: sum(1 for r in regs[nome] if f(r))          # noqa: E731
        print(f"  {nome:<18}{len(regs[nome]):>7} · cnpj {com(lambda r: r.cnpj):>6}"
              f" · end {com(lambda r: r.endereco):>6}"
              f" · nome {com(lambda r: r.nomes):>6}"
              f" · geo {com(lambda r: r.lat is not None):>6}"
              f"   ({time.time()-t0:.1f}s)", flush=True)

    nomes = list(BASES)
    pares = ([tuple(x.split(":")) for x in args.par] if args.par else
             [(nomes[i], nomes[j]) for i in range(len(nomes))
              for j in range(i + 1, len(nomes))])

    print(f"\n{'par':<38}{'chave':<10}{'pares':>8}{'ambíguos':>10}{'s':>7}",
          flush=True)
    total = 0
    for a, b in pares:
        conta = cruzar(con, a, b, regs, args.simular)
        for chave, (n, amb, seg) in conta.items():
            total += n
            print(f"  {a} × {b:<22}{chave:<10}{n:>8}{amb:>10}{seg:>7.1f}", flush=True)
        if not conta:
            print(f"  {a} × {b:<22}{'—':<10}{'sem chave em comum':>18}", flush=True)
    print(f"\n{total} cruzamentos" + (" (simulação, nada gravado)" if args.simular
                                      else " gravados"), flush=True)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
