# -*- coding: utf-8 -*-
"""Monta as abas da ficha a partir do CATÁLOGO, não de colunas fixas.

POR QUE CATÁLOGO

A tela antiga desenhava campo por campo no JavaScript. Cada campo novo que a
extração aprendia — e ela aprendeu vários nesta semana — exigia editar HTML.
Uma tela assim envelhece a cada melhoria do pipeline, que é o contrário do que
deveria acontecer.

Com o catálogo, campo novo entra numa linha de `campo_catalogo` e aparece na
aba sozinho. É o ponto de extensão do modelo em `assets/modelo_frontend`, e a
única parte dele que muda como o sistema cresce.

A PROCEDÊNCIA É FILTRADA AQUI, e não no JavaScript

O RBAC diz que só o `root` enxerga de onde o dado veio — IBGE, Google, Receita.
O catálogo guarda essa informação porque ele precisa dela; a API a **remove**
antes de responder para quem não é `root`.

Filtrar no cliente seria fingir: o dado teria chegado ao navegador, e qualquer
um com o console aberto o leria. Campo que a matriz de acesso proíbe não sai do
servidor.
"""
from __future__ import annotations

# Os itens de pauta aceitos. Espelham o enum `pauta_campo` da migration 0023 —
# duplicar a lista em Python e no banco cria a chance de divergirem, e o banco
# ganha sempre. Aqui existe so para a API recusar com 422 legivel em vez de
# deixar estourar um 500 de constraint.
PAUTA_VALIDA = (
    "confirmar_atividade", "confirmar_numero", "contar_unidades",
    "fotografar_fachada", "registrar_coordenada", "confirmar_endereco",
)

CATALOGO = """
select fonte::text, chave, rotulo, grupo, peso::text, formato, procedencia, ajuda
  from comercialradar.campo_catalogo
 where ativo
 order by fonte, ordem, id
"""

# O QUE O DOSSIE NAO TEM, e por que isto existe.
#
# `dossie.coletar` devolve 28 chaves CURADAS para o PDF comprobatorio: nome,
# endereco, CNPJ, fotos, Street View. Nao traz a analise da IA, nem rede social,
# nem delivery, nem os campos de casamento — o documento nao precisa deles.
#
# A bancada precisa. Entao a ficha LE o resto das mesmas tabelas e junta, com o
# dossie ganhando em caso de conflito: ele e a versao formatada, e e o que o PDF
# vai imprimir. Duas coletas divergentes fariam a tela mostrar uma coisa e o
# documento provar outra.
#
# Eu supus que o dossie ja trazia tudo, e as abas de Redes Sociais, Delivery e
# IA das Imagens nasceram vazias na primeira prova. O erro so apareceu porque a
# prova compara catalogo contra dado real.
EXTRA_POI = """
select maps_lat, maps_lng, distancia_m, similaridade, match_valido,
       revisar_motivo, status_horario, plus_code, maps_url,
       natureza_juridica, cnpj_conf, instagram, facebook, email,
       presente_no_ifood, ifood_visto_em, preco_medio,
       coord_precisao, coord_fonte, coord_incerteza_m
  from comercialradar.pois where id = %s
"""

EXTRA_IA = """
select veredito, veredito_motivo, confere, atividade_real, porte,
       pessoas_estimadas, tipo_construcao, outro_estabelecimento, n_imagens,
       recomendar_visita, recomendacao_motivo
  from comercialradar.analise_ia where poi_id = %s
 order by criado_em desc limit 1
"""

# Rótulo da aba na tela, e a ordem em que aparecem. O POI primeiro porque é a
# âncora (ADR 0005); as imagens por último porque são conclusão, não evidência
# bruta.
ABAS = [
    ("poi", "POI"),
    ("google", "Informações Google"),
    ("receita", "Receita Federal"),
    ("redes_sociais", "Redes Sociais"),
    ("delivery", "Delivery"),
    ("imagens", "IA das Imagens"),
]


def _completar(con, poi_id: int, dados: dict) -> dict:
    """Junta ao dossie o que a bancada precisa e o PDF nao usa.

    O dossie GANHA em conflito: ele e a versao formatada, e e o que o documento
    comprobatorio imprime.
    """
    juntos = {}
    with con.cursor() as cur:
        for sql in (EXTRA_POI, EXTRA_IA):
            cur.execute(sql, (poi_id,))
            linha = cur.fetchone()
            if not linha:
                continue
            nomes = [d[0] for d in cur.description]
            juntos.update(dict(zip(nomes, linha)))
    juntos.update({k: v for k, v in (dados or {}).items() if v not in (None, "")})
    return juntos


# A PRECISAO EM PALAVRAS. O codigo `porta_aprox` nao diz nada a quem vai a
# campo; "predio certo, numero aproximado" diz. E o raio vem junto porque e o
# que separa "toque a campainha" de "procure na quadra".
PRECISAO_TEXTO = {
    "porta": "Porta — o ponto é do próprio estabelecimento (~15 m)",
    "porta_aprox": "Prédio certo, número aproximado (~40 m)",
    "via": "Rua certa, número não localizado (~150 m)",
    "bairro": "Bairro ou localidade (~800 m)",
    "municipio": "Só o município (~5 km)",
    # NÃO é o mesmo que ruim, e a frase diz isso. Confundir os dois faria o
    # operador descartar 13 mil pontos que podem ser ótimos.
    "desconhecida": "Não declarada — ninguém conferiu de onde veio",
}

FONTE_TEXTO = {
    "maps_painel": "painel do Google Maps",
    "cadastur": "Cadastur/MTur",
    "maps_tile": "pin lido do tile do Maps",
    "cnefe": "CNEFE do IBGE, por endereco",
    "cnefe:porta": "CNEFE do IBGE, coordenada do endereco",
    "cnefe:porta_face": "CNEFE do IBGE, anotada na face",
    "cnpj_tratado": "Receita + CNEFE, por CNPJ",
    "cadastro_cliente": "cadastro de imoveis do cliente",
    "overture_osm": "centroide do Overture/OSM",
    "vizinho": "coordenada do POI vizinho onde a IA leu a fachada",
    "planilha": "a planilha importada",
    "photon": "geocodificador Photon (OSM)",
    "nominatim": "geocodificador Nominatim (OSM)",
    "chat": "informada no chat",
}


def _formatar(valor, formato: str):
    """Deixa o valor legivel ANTES de sair do servidor.

    Se cada tela formatar por conta propria, duas telas mostram o mesmo dado de
    dois jeitos e o PDF de um terceiro. Visto na revisao da API:

        Esta no iFood      False              -> "Nao"
        CNAE principal     4782201 - None     -> "4782201"
        Nº de avaliacoes   0                  -> continua 0, que e verdade

    Zero NAO vira travessao: "zero avaliacoes" e informacao — nota alta com
    nenhuma avaliacao e exatamente o que quem audita precisa notar.
    """
    if isinstance(valor, bool):
        return "Sim" if valor else "Nao"

    # Codigo de vocabulario fechado vira frase. Sai daqui, e nao de cada tela,
    # pelo mesmo motivo do resto deste arquivo: duas telas traduziriam de dois
    # jeitos e o PDF de um terceiro.
    if formato == "precisao":
        return PRECISAO_TEXTO.get(valor, valor)
    if formato == "fonte_coord":
        # A origem vem COMPOSTA de alguns caminhos — "photon:via" diz quem
        # respondeu e com que classe. A classe já aparece no campo ao lado, e
        # repeti-la aqui só ocupa espaço; o que falta traduzir é quem respondeu.
        base = str(valor or "").split(":", 1)[0]
        return FONTE_TEXTO.get(valor) or FONTE_TEXTO.get(base) or valor

    # JSON GUARDADO COMO TEXTO. `pois.socios` e uma coluna `text` com um array
    # dentro, e nao um `jsonb` — entao chegava aqui como string e saia na tela
    # como `[{"nome": "ALVARO...", "qualificacao": "Diretor"}, ...]`.
    # Desembrulhar aqui evita que cada tela reinvente esse tratamento.
    if isinstance(valor, str) and valor[:1] in ("[", "{"):
        import json as _json
        try:
            valor = _json.loads(valor)
        except Exception:
            pass                      # nao era JSON: segue como texto

    if isinstance(valor, str):
        # "4782201 - None" nasce de um concat com lado vazio la atras
        v = valor.replace(" - None", "").replace(" - none", "").strip()
        return v.rstrip(" -") or None
    if isinstance(valor, (list, tuple)):
        # Lista de dicionarios sai como JSON cru na tela — visto com os socios da
        # Receita: `[{"nome": "ALVARO...", "qualificacao": "Diretor"}, ...]`.
        # Quem le quer os nomes, com o papel entre parenteses.
        itens = []
        for x in valor:
            if isinstance(x, dict):
                nome = x.get("nome") or x.get("razao_social") or ""
                papel = x.get("qualificacao") or x.get("papel") or ""
                if nome:
                    itens.append(f"{nome} ({papel})" if papel else nome)
            elif x not in (None, ""):
                itens.append(str(x))
        return itens or None
    return valor


def montar(con, dados: dict, e_root: bool) -> list:
    """Devolve as abas prontas: [{fonte, rotulo, grupos:[{grupo, linhas:[...]}]}].

    `dados` é o que o `dossie.coletar` já montou — a MESMA coleta que vira o PDF
    comprobatório. Duas coletas diferentes acabariam divergindo, e a tela
    mostraria uma coisa enquanto o documento provaria outra.
    """
    with con.cursor() as cur:
        cur.execute(CATALOGO)
        linhas_cat = cur.fetchall()

    tudo = _completar(con, dados.get("poi_id"), dados)

    por_fonte: dict = {}
    for fonte, chave, rotulo, grupo, peso, formato, procedencia, ajuda in linhas_cat:
        valor = _formatar(tudo.get(chave), formato)

        # Campo sem valor NÃO vira linha vazia. Linha vazia parece dado perdido;
        # ausência silenciosa é lida como "não se aplica", que é o certo.
        #
        # `0` e `False` NAO entram nesta peneira: "zero avaliacoes" e "nao esta
        # no iFood" sao respostas, nao ausencias.
        if valor is None or valor == "" or valor == []:
            continue

        linha = {"chave": chave, "rotulo": rotulo, "grupo": grupo or "Geral",
                 "peso": peso, "formato": formato, "valor": valor}
        if ajuda:
            linha["ajuda"] = ajuda
        # A PROCEDÊNCIA SÓ SAI PARA O `root`. Ver docstring do módulo.
        if e_root and procedencia:
            linha["procedencia"] = procedencia
        por_fonte.setdefault(fonte, []).append(linha)

    saida = []
    for fonte, rotulo_aba in ABAS:
        linhas = por_fonte.get(fonte) or []
        if not linhas:
            continue                    # aba sem dado não aparece (ADR 0005)
        grupos: dict = {}
        for ln in linhas:
            grupos.setdefault(ln["grupo"], []).append(ln)
        contrarias = sum(1 for ln in linhas if ln["peso"] == "contraria")
        saida.append({
            "fonte": fonte, "rotulo": rotulo_aba, "campos": len(linhas),
            # Quantas evidências pesam CONTRA. A tela marca a aba: quem decide
            # precisa ver que há objeção antes de abrir, não depois de aprovar.
            "contrarias": contrarias,
            "grupos": [{"grupo": g, "linhas": ls} for g, ls in grupos.items()],
        })
    return saida
