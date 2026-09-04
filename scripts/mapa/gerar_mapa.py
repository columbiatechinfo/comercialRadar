# -*- coding: utf-8 -*-
"""Gera o mapa do schema a partir do dump real. Nada e transcrito a mao."""
import io
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DUMP = os.path.join(BASE, "schema.txt")
SAIDA = os.path.join(BASE, "mapa_schema.html")

# ── le o dump ───────────────────────────────────────────────────────────────
secao = None
tabelas, colunas, fks, contagem = {}, {}, [], {}
for linha in io.open(DUMP, encoding="utf-8"):
    linha = linha.rstrip("\n").rstrip("\r")
    if linha.startswith("###"):
        secao = linha[3:]
        continue
    if not linha or "|" not in linha:
        continue
    p = linha.split("|")
    if secao == "TABELAS":
        tabelas[p[0]] = {"linhas": int(p[1]), "tam": p[2]}
    elif secao == "COLUNAS":
        colunas.setdefault(p[0], []).append((p[1], p[2]))
    elif secao == "FK":
        fks.append((p[0], p[1], p[2]))
    elif secao == "CONTAGEM":
        contagem[p[0]] = int(p[1])

# contagem exata onde temos; senao a estimativa do planner
for t, d in tabelas.items():
    d["n"] = contagem.get(t, d["linhas"] if d["linhas"] > 0 else 0)
    d["estimado"] = t not in contagem

# ── as 22 colunas da `pois` que nunca guardaram nada (censo de 03/09/2026) ──
VAZIAS = {
    "cnpj_conf", "coord_compartilhada", "coord_grupo", "cruzado_em",
    "descoberto_de", "distancia_m", "endereco_gerado_por", "endereco_original",
    "fundido_em", "fundido_para", "fundido_por", "natureza_juridica",
    "nome_original", "ocr_texto", "preco_medio", "revisar_manual",
    "revisar_motivo", "similaridade", "situacao_cadastral", "socios",
    "streetview_path", "coord_anterior_lat", "coord_anterior_lng",
}
# as que sairam para a tabela da fonte (escrita dupla, saem na fase seguinte)
MUDOU_DE_CASA = {
    "place_id": "maps_data", "maps_url": "maps_data", "plus_code": "maps_data",
    "avaliacao": "maps_data", "total_avaliacoes": "maps_data",
    "resumo_avaliacoes": "maps_data", "status_horario": "maps_data",
    "detalhado_em": "maps_data", "detalhado_por": "maps_data",
    "razao_social": "cadastur_data", "nome_fantasia": "cadastur_data",
    "cnae": "cadastur_data",
}

FAM = [
    ("eixo", "O eixo — a base do cliente",
     "Cada linha e uma instalacao que a Corsan ja cobra todo mes. Ela existe "
     "com ou sem radar, e e nela que tudo se pendura.",
     ["cadastro_corsan", "base_cliente", "ligacao_poi"]),
    ("ponto", "O ponto — o POI",
     "O estabelecimento. Guarda so o que e verdade sobre o LUGAR, venha de "
     "onde vier: nome, endereco, coordenada, contato.",
     ["pois"]),
    ("fonte", "As fontes — uma tabela cada",
     "O que SO aquela fonte sabe, ligado ao POI por chave estrangeira. Base "
     "publica tambem e fonte de descoberta: tudo que fornece POI e uma.",
     ["maps_data", "osm_data", "overture_data", "foursquare_data",
      "receita_data", "cadastur_data", "cadastur_vinculo", "ifood_merchant",
      "airbnb_anuncio", "cnpj_tratado"]),
    ("transversal", "O que se pendura no POI",
     "Vale para QUALQUER fonte. Uma tabela por tipo de coisa, com a coluna "
     "`fonte` dizendo quem trouxe cada linha.",
     ["comentarios", "images_urls", "streetview_imgs",
      "horario_funcionamento", "analise_ia", "tile_captura"]),
    ("endereco", "O endereco trabalhado",
     "O endereco cru nao casa com nada. Estas guardam o resultado de "
     "segmentar, corrigir e resolver — caro de refazer, entao se guarda.",
     ["endereco_segmentado", "logradouro_ajustado", "logradouro_resolvido"]),
    ("operacao", "A operacao",
     "As runs, as areas desenhadas, os arquivos carregados, os proxies e o "
     "chat. Nao e dado do cliente: e o trabalho sobre ele.",
     ["job", "job_log", "area_trabalho", "fonte_arquivos", "proxy_ip",
      "proxy_evento", "chat_conversa", "chat_mensagem", "chat_anexo",
      "memoria"]),
    ("dormente", "Dormentes — existem, e ninguem escreve",
     "Sobraram de desenhos anteriores. Estao aqui porque apagar tabela sem "
     "conferir quem a le e como se perde dado; mas nenhuma recebe linha nova.",
     ["cadastro_cliente", "fachada_anotacao", "fachada_triagem",
      "foto_maps_triagem", "atribuicao", "atribuicao_divergente",
      "vinculo_poi", "cruzamento", "campo_catalogo", "cnefe_coletiva"]),
]

DESC = {
    "cadastro_corsan": "As 2,5 milhoes de ligacoes da Corsan. Mora em "
        "`resources_root`, e o unico dado que o cliente entregou pronto.",
    "base_cliente": "Como LER a base do cliente: de-para de colunas e quais "
        "valores de tipo significam atividade comercial. Sem isso o sistema "
        "adivinha, e adivinhar errado classifica comercio como residencia.",
    "ligacao_poi": "O vinculo. Cada par ligacao-POI com os cinco criterios "
        "separados, para quem le ver O QUE bateu em vez de so um numero.",
    "pois": "O estabelecimento. Tinha 70 colunas guardando tres coisas "
        "diferentes; 22 delas nunca guardaram nada.",
    "maps_data": "Place ID do Google, URL da ficha, Plus Code, nota, "
        "quantidade de avaliacoes, resumo da IA e status de horario.",
    "osm_data": "Id do objeto no OpenStreetMap e as tags — que nao tem "
        "esquema fixo, por isso `jsonb`.",
    "overture_data": "Id do Overture, categoria hierarquica, confianca "
        "propria e quais bases contribuiram para o ponto.",
    "foursquare_data": "Id do Foursquare, categorias e popularidade. Forte "
        "em varejo e restauracao.",
    "receita_data": "Razao social, CNAE, natureza juridica, situacao "
        "cadastral e socios. Vazia: os 58.834 POIs da Receita so tem CNPJ.",
    "cadastur_data": "O que o Cadastur (MTur) diz do POI. Base publica, com "
        "a mesma funcao de CNEFE e Receita.",
    "cadastur_vinculo": "Qual registro do Cadastur casou com qual POI, e o "
        "motivo quando nao casou nenhum.",
    "ifood_merchant": "A loja no iFood: identificador, categoria, nota, "
        "faixa de preco e o que a ficha mostrou.",
    "airbnb_anuncio": "O anuncio: hospedes, quartos, camas, banheiros, "
        "anfitriao, comodidades, regras e o print da ficha.",
    "cnpj_tratado": "O CNPJ depois do tratamento — aptidao geografica, "
        "perfil comercial e a evidencia que sustentou a decisao.",
    "comentarios": "As avaliacoes de todas as fontes. Ganhou `fonte` em "
        "03/09/2026; antes nao havia como saber de quem era a linha.",
    "images_urls": "As fotos do estabelecimento, de qualquer fonte. Guarda "
        "URL, caminho no Storage e os bytes.",
    "streetview_imgs": "As capturas do Street View, com angulo, heading e "
        "a posicao da camera.",
    "horario_funcionamento": "Um registro por dia da semana.",
    "analise_ia": "O veredito da IA de visao sobre a fachada: atividade "
        "real, porte, tipo de construcao e o motivo de cada um.",
    "tile_captura": "Os tiles de satelite, presos a RUN e a caixa "
        "geografica — nao a um POI, porque um tile serve a dezenas. E o que "
        "o passo dos telhados vai ler. Guarda caminho, nao bytes.",
    "endereco_segmentado": "O endereco quebrado em logradouro, numero, "
        "bairro, CEP — com o metodo e o modelo que fizeram a quebra.",
    "logradouro_ajustado": "A correcao do nome da via, com o risco e o "
        "status de revisao humana.",
    "logradouro_resolvido": "O endereco resolvido ate a quadra e a face, "
        "com a forca da decisao e a distancia em metros.",
    "job": "Uma run. Tipo, estado e quando comecou.",
    "job_log": "As linhas de log daquela run.",
    "area_trabalho": "Os poligonos desenhados no painel, ou o municipio "
        "inteiro carregado da malha do IBGE.",
    "fonte_arquivos": "Que arquivo foi carregado, quantas linhas, para que "
        "tabela e se deu certo.",
    "proxy_ip": "Os IPs do plano de proxy.",
    "proxy_evento": "O consumo de banda medido, e nao estimado.",
    "chat_conversa": "As conversas do painel.",
    "chat_mensagem": "As mensagens.",
    "chat_anexo": "Os anexos.",
    "memoria": "O que o assistente do painel deve lembrar entre sessoes.",
    "cadastro_cliente": "84 colunas, vazia. Era o desenho antigo, em que o "
        "cadastro do cliente virava POI em vez de ser o eixo.",
    "fachada_anotacao": "67 colunas, vazia. A anotacao manual de fachada, "
        "de antes da IA de visao.",
    "fachada_triagem": "A triagem que decidia que fachada valia olhar.",
    "foto_maps_triagem": "O mesmo, para as fotos do Maps.",
    "atribuicao": "De qual fonte veio cada campo do POI.",
    "atribuicao_divergente": "Onde duas fontes discordaram.",
    "vinculo_poi": "O cruzamento POI-com-POI, substituido pelo vinculo "
        "ancorado na ligacao.",
    "cruzamento": "O pareamento entre duas bases, com score e evidencia.",
    "campo_catalogo": "O catalogo de campos que o painel sabe exibir.",
    "cnefe_coletiva": "As coletivas identificadas no CNEFE.",
}

CHAVE = {  # as colunas que valem destacar em cada tabela
    "pois": ["id", "id_empresa", "nome", "categoria", "cnpj", "endereco",
             "cidade", "uf", "pt_geo", "telefone", "email", "website",
             "instagram", "fonte", "fonte_dado", "place_id", "sessao",
             "id_base", "id_ligacao_base", "status"],
    "ligacao_poi": ["id", "ligacao", "poi_id", "mesmo_endereco", "mesmo_numero",
                    "ate_20m", "mesmo_telhado", "telhado_comercial", "metros",
                    "criterios_ok", "confianca", "fonte_poi", "origem",
                    "tile_id", "suspeita_motivo"],
}

def fmt(n):
    return ("{:,}".format(n)).replace(",", ".")

def familia_de(t):
    for slug, _, _, ts in FAM:
        if t in ts:
            return slug
    return "operacao"

# ── monta o HTML ────────────────────────────────────────────────────────────
def card(t):
    d = tabelas.get(t)
    cols = colunas.get(t, [])
    if t == "cadastro_corsan":
        n, tam, cols = contagem.get(t, 0), "no resources_root", []
    else:
        n, tam = (d["n"], d["tam"]) if d else (0, "")
    vazia = (n == 0)
    destaque = CHAVE.get(t)
    mostrar = [c for c in cols if not destaque or c[0] in destaque] or cols
    resto = len(cols) - len(mostrar)

    linhas = []
    for nome, tipo in mostrar[:24]:
        cls = "col"
        nota = ""
        if t == "pois" and nome in MUDOU_DE_CASA:
            cls += " col--mudou"
            nota = '<span class="nota">→ %s</span>' % MUDOU_DE_CASA[nome]
        linhas.append(
            '<li class="%s"><code>%s</code><span class="tipo">%s</span>%s</li>'
            % (cls, nome, tipo.replace("timestamp with time zone", "timestamptz")
                              .replace("double precision", "float8")
                              .replace("character varying", "varchar"), nota))

    alvos = sorted({dst for (src, _, dst) in fks if src == t and dst != "tb_empresas"})
    liga = ""
    if alvos:
        liga = ('<p class="liga">aponta para '
                + ", ".join('<b>%s</b>' % a for a in alvos) + "</p>")

    return """
    <article class="tabela%s" data-fam="%s" id="t-%s">
      <header>
        <h3>%s</h3>
        <span class="qtd%s">%s</span>
      </header>
      <p class="oq">%s</p>
      %s
      <ul class="cols">%s</ul>
      %s
      <footer><span>%d colunas</span><span>%s</span></footer>
    </article>""" % (
        " tabela--vazia" if vazia else "", familia_de(t), t, t,
        " qtd--zero" if vazia else "",
        "vazia" if vazia else fmt(n) + " linhas",
        DESC.get(t, ""), liga, "".join(linhas),
        ('<p class="resto">+ %d colunas</p>' % resto) if resto > 0 else "",
        len(cols), tam)

secoes = []
for slug, titulo, sub, ts in FAM:
    cards = "".join(card(t) for t in ts if t in tabelas or t == "cadastro_corsan")
    secoes.append("""
  <section class="familia" data-fam="%s">
    <div class="familia__cab">
      <h2>%s</h2>
      <p>%s</p>
    </div>
    <div class="grade">%s</div>
  </section>""" % (slug, titulo, sub, cards))

total_cols = sum(len(c) for c in colunas.values())
CTX = {
    "n_tabelas": len(tabelas) + 1,
    "n_colunas": total_cols,
    "n_fks": len(fks),
    "pois": fmt(contagem["pois"]),
    "ligacoes": fmt(contagem["cadastro_corsan"]),
    "vinculos": fmt(contagem["ligacao_poi"]),
    "secoes": "".join(secoes),
}
# Substituicao por marcador, e nao `%` de formatacao: o CSS esta cheio de
# porcentagem literal, e cada uma teria de virar `%%` — um esquecimento
# quebraria a geracao longe da causa.
html = io.open(os.path.join(BASE, "molde.html"), encoding="utf-8").read()
for k, v in CTX.items():
    html = html.replace("{{" + k + "}}", str(v))
io.open(SAIDA, "w", encoding="utf-8", newline="\n").write(html)
print("gerado:", SAIDA)
print("tabelas:", CTX["n_tabelas"], "| colunas:", total_cols, "| fks:", len(fks))
