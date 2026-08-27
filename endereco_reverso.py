# -*- coding: utf-8 -*-
"""endereco_reverso.py — a coordenada vira endereço COM NÚMERO, ou o ponto não entra.

POR QUE ISTO EXISTE

Regra do dono do produto, 27/08/2026: um POI com nome e coordenada mas sem
endereço é válido para comparação, "mas o endereço deve ser gerado a partir da
coordenada". E o critério é duro: só segue com o ponto quem obtiver endereço
**com número**; o resto é apagado.

A CASCATA, E POR QUE ELA NÃO TEM O OSM

A primeira proposta era OSM primeiro, Maps depois. O Photon do i9 foi testado
com dados reais e reprovado — não por estar quebrado, mas por não ter o dado.
MEDIDO sobre 200 POIs de Canoas:

    Photon (layer=house)  acha porta em 99% ... e 73% delas a MAIS DE 100 m
                          Só 2% caem dentro de 20 m. Ele devolve a porta mais
                          próxima que CONHECE, e o OSM quase não tem numeração
                          predial no RS: "Eixo Sul Distribuidora" recebia
                          "Rua Senador Salgado Filho 250", a 834 m.

    CNEFE (IBGE)          acha porta em 100%, com 85% dentro de 20 m e 96%
                          dentro de 50 m, em 0,18 ms por ponto.

Gravar a resposta do Photon seria inventar endereço errado — pior que não ter
endereço, porque endereço errado casa com o vizinho errado no cruzamento. Por
isso a cascata ficou:

    1. CNEFE    111.102.875 endereços, os 5.570 municípios do país já
                carregados. 0,18 ms, local, sem rede.
    2. MAPS     só o resíduo (~4%). Abre navegador com proxy e leva dezenas de
                segundos — vale a pena justamente por ser pouco.

O RAIO DE 50 METROS NÃO É ARBITRÁRIO

É onde a medição para de ser confiável: dentro de 50 m, 96% dos pontos têm uma
porta do CNEFE; além disso, a porta mais próxima começa a ser a do outro
quarteirão. Aceitar 200 m dobraria a cobertura e encheria a base de endereços
que pertencem a outro estabelecimento.
"""
from __future__ import annotations

import math

import base_comum as bc

# Onde a medição diz que a resposta ainda é do PRÓPRIO ponto, e não do vizinho.
RAIO_MAX_M = 50.0

# Célula da grade em graus (~111 m). Uma célula e as 8 vizinhas cobrem os 50 m
# com folga em qualquer direção.
_CELULA = 0.001

# Municípios já carregados nesta execução. O CNEFE de uma cidade grande são
# 177 mil linhas e 0,4 s; recarregar a cada ponto seria o gargalo inteiro.
_GRADES: dict = {}


def _grade_do_municipio(cod_ibge: str) -> dict:
    """Carrega (uma vez) as portas do município numa grade em memória."""
    if cod_ibge in _GRADES:
        return _GRADES[cod_ibge]

    ref = bc.conectar_referencia()
    try:
        cur = ref.cursor()
        cur.execute("""
            select nom_tipo_seglogr, nom_seglogr, num_endereco, cep,
                   latitude::float8, longitude::float8
              from ibge_cnefe
             where cod_municipio = %s
               and num_endereco is not null
               and latitude is not null and longitude is not null""", (cod_ibge,))
        linhas = cur.fetchall()
    finally:
        ref.close()

    grade: dict = {}
    for tipo, via, num, cep, la, lo in linhas:
        grade.setdefault((int(la / _CELULA), int(lo / _CELULA)), []).append(
            (la, lo, (tipo or "").strip(), (via or "").strip(),
             str(num).strip(), (cep or "").strip()))
    _GRADES[cod_ibge] = grade
    return grade


def _metros(la1, lo1, la2, lo2) -> float:
    return math.hypot((la1 - la2) * 111320,
                      (lo1 - lo2) * 111320 * math.cos(math.radians(la1)))


def por_cnefe(lat: float, lng: float, cod_ibge: str) -> dict | None:
    """A porta do CNEFE mais próxima, se estiver dentro do raio.

    Devolve `{rua, numero, cep, distancia_m, fonte}` ou `None` — e `None` aqui
    significa "o CNEFE não tem porta perto o bastante", nunca "erro".
    """
    if lat is None or lng is None or not cod_ibge:
        return None
    grade = _grade_do_municipio(str(cod_ibge))
    if not grade:
        return None

    cy, cx = int(lat / _CELULA), int(lng / _CELULA)
    melhor = None
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for la, lo, tipo, via, num, cep in grade.get((cy + dy, cx + dx), ()):
                d = _metros(lat, lng, la, lo)
                if melhor is None or d < melhor[0]:
                    melhor = (d, tipo, via, num, cep)
    if melhor is None or melhor[0] > RAIO_MAX_M:
        return None

    d, tipo, via, num, cep = melhor
    rua = (f"{tipo} {via}").strip()
    return {"rua": rua, "numero": num, "cep": cep,
            "distancia_m": round(d, 1), "fonte": "cnefe",
            "endereco": f"{rua}, {num}" + (f" - {cep}" if cep else "")}


def por_maps(nome: str, cidade: str, lat: float = None, lng: float = None) -> dict | None:
    """O resíduo. Usa o painel do Maps, que é a única fonte que acha o NEGÓCIO.

    Fica separado de propósito: ele abre navegador com proxy e custa dezenas de
    segundos, então quem chama precisa ver que está pagando por isso.
    """
    if not nome:
        return None
    try:
        import geocodificar
    except ImportError:
        return None
    achado = geocodificar.por_maps(nome, cidade or "", "")
    if not achado:
        return None
    texto = (achado.get("texto") or "").strip()
    if not texto or not any(ch.isdigit() for ch in texto):
        return None          # sem número não serve: é a regra
    return {"rua": texto, "numero": "", "cep": "",
            "distancia_m": None, "fonte": "maps", "endereco": texto}


def endereco_de(lat, lng, cod_ibge: str, nome: str = "", cidade: str = "",
                usar_maps: bool = True) -> dict | None:
    """A cascata inteira. `None` significa: este ponto NÃO ENTRA.

    Quem chama é responsável por apagar o ponto quando vier `None` — a regra do
    dono do produto é explícita, e deixar entrar sem endereço reabriria o buraco
    que o `trigger poi_comparavel` fecha no banco.
    """
    achado = por_cnefe(lat, lng, cod_ibge)
    if achado:
        return achado
    if usar_maps:
        return por_maps(nome, cidade, lat, lng)
    return None
