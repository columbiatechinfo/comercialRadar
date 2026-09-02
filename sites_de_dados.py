# -*- coding: utf-8 -*-
"""sites_de_dados.py — onde procurar dado de empresa, e o que cada lugar dá.

POR QUE UM CATÁLOGO, E NÃO BUSCA ABERTA

Medido em 02/09/2026, buscando `LANCHERIA XIS LENA Canoas` na web aberta: dos
sete resultados, um era um dicionário no academia.edu, outro um vocabulário no
huggingface, outro um PDF do Ministério de Minas e Energia. A IA leu isso e
tirou um telefone de um vídeo do TikTok.

Na mesma rodada, `MECANICA DIESEL CRIATIVA` saiu completa — CNPJ, razão social,
telefone e ramo, com a fonte — porque um dos resultados por acaso foi um site de
consulta de CNPJ.

A diferença entre os dois casos foi sorte. Este catálogo tira a sorte do
caminho: a busca passa a ser dirigida aos lugares que publicam esse dado, e o
que vier de fora deles entra depois, como complemento.

O QUE FOI MEDIDO EM 02/09/2026, e por que o catálogo tem duas portas

Testei os treze sites de duas formas: busca dirigida (`site:dominio` no
SearXNG) e URL direta com um CNPJ conhecido. O resultado desmontou o desenho
que eu tinha em mente.

    BUSCA DIRIGIDA          só `cnpj.biz` (5 resultados) e `advdinamico` (1)
                            devolveram algo do próprio domínio. Os outros onze
                            devolveram ZERO. O `site:` não é bem suportado
                            pelos motores que este SearXNG consulta.

    URL DIRETA (HTTP)       casadosdados 200 com o CNPJ no corpo · advdinamico
                            200 com o CNPJ · brasilapi 200 · receitaws 200
                            cnpj.biz 422 · econodata 403 · cnpja 429

Então o caminho principal é a BUSCA ABERTA — que funciona, devolve 20
resultados — passada por dois filtros: o de ruído (`e_ruido`) e a preferência
pelos domínios deste catálogo. Foi assim que a `MECANICA DIESEL CRIATIVA` saiu
completa: a busca aberta trouxe o advdinamico por acaso, e o catálogo existe
para que deixe de ser acaso.

A URL direta é a segunda porta, e serve quando o CNPJ já é conhecido: aí não se
busca, se abre. `precisa_navegador` abaixo é o MEDIDO, não o suposto — 403, 422
e 429 são as três formas de dizer "não por HTTP simples".

O QUE CADA ENTRADA DECLARA

    da           quais campos aquele lugar costuma ter
    consulta     como perguntar: `site:` para busca dirigida, ou URL direta
    precisa_navegador   quando HTTP simples não passa (Cloudflare, JS)
    observacao   o que quem for ler o resultado precisa saber

`da` não é promessa: é o que o site publica QUANDO tem. Nenhum deles tem tudo, e
é por isso que a consulta vai a vários e o modelo compara.

NADA AQUI É FONTE OFICIAL, e isso importa. A Receita Federal já está no banco
inteira (72,7 milhões de estabelecimentos) — para CNPJ conhecido, ela responde
melhor e de graça. Estes sites servem ao caminho INVERSO: nome e endereço de um
comércio pequeno que não se sabe qual CNPJ é, ou se tem algum.

O QUE SAI DAQUI É PISTA. O CNPJ passa pelos dígitos verificadores e, quando
possível, pela Receita local antes de virar dado.
"""
from __future__ import annotations

import re
import urllib.parse

# ═══════════════════════════════════════════════════════ o catálogo ═════════
#
# `grupo` diz para que serve, e é o que permite pedir "só os de CNPJ" quando o
# POI já tem telefone, ou "só os de rede social" quando já tem CNPJ.

SITES = {
    # ── consulta de CNPJ: nome/endereço → empresa ───────────────────────────
    "casadosdados": {
        "grupo": "cnpj", "dominio": "casadosdados.com.br",
        "da": ["cnpj", "razao_social", "nome_fantasia", "endereco", "cnae",
               "situacao_cadastral", "socios", "capital"],
        "consulta": "site:casadosdados.com.br {nome} {cidade}",
        "direto": "https://casadosdados.com.br/solucao/cnpj/{cnpj}",
        "precisa_navegador": False,
        "observacao": "MEDIDO 02/09/2026: a pagina de um CNPJ responde 200 com o "
                      "CNPJ no corpo. A BUSCA por nome nao devolve nada no "
                      "SearXNG — serve para CONFERIR um CNPJ, nao para achar um.",
    },
    "cnpj.biz": {
        "grupo": "cnpj", "dominio": "cnpj.biz",
        "da": ["cnpj", "razao_social", "nome_fantasia", "endereco", "telefone",
               "cnae", "situacao_cadastral"],
        "consulta": "site:cnpj.biz {nome} {cidade}",
        "direto": "https://cnpj.biz/{cnpj}",
        "precisa_navegador": True,
        "observacao": "MEDIDO 02/09/2026: 422 a HTTP simples, mas a busca dirigida "
                      "acha 5 paginas dele. O SearXNG ve, o `urllib` nao — e caso "
                      "de navegador do repositorio.",
    },
    "advdinamico": {
        "grupo": "cnpj", "dominio": "advdinamico.com.br",
        "da": ["cnpj", "razao_social", "endereco", "telefone", "cnae", "socios"],
        "consulta": "site:advdinamico.com.br {nome} {cidade}",
        "direto": "https://advdinamico.com.br/empresas/{cnpj}",
        "precisa_navegador": False,
        "observacao": "MEDIDO 02/09/2026: 200 com o CNPJ no corpo, e o unico que a "
                      "busca dirigida alcanca junto com o cnpj.biz. Foi ele que "
                      "resolveu a MECANICA DIESEL CRIATIVA — CNPJ, razao social, "
                      "telefone e CNAE de uma oficina de bairro.",
    },
    "econodata": {
        "grupo": "cnpj", "dominio": "econodata.com.br",
        "da": ["cnpj", "razao_social", "nome_fantasia", "endereco", "telefone",
               "porte", "cnae"],
        "consulta": "site:econodata.com.br {nome} {cidade}",
        "direto": "https://www.econodata.com.br/consulta-empresa/{cnpj}",
        "precisa_navegador": True,
        "observacao": "MEDIDO 02/09/2026: 403 a HTTP simples. Precisa de navegador.",
    },
    "empresascnpj": {
        "grupo": "cnpj", "dominio": "empresascnpj.com",
        "da": ["cnpj", "razao_social", "endereco", "cnae", "situacao_cadastral"],
        "consulta": "site:empresascnpj.com {nome} {cidade}",
        "direto": None, "precisa_navegador": False,
        "observacao": "agregador; cobertura irregular fora das capitais.",
    },
    "cnpja": {
        "grupo": "cnpj", "dominio": "cnpja.com",
        "da": ["cnpj", "razao_social", "endereco", "cnae", "socios"],
        "consulta": "site:cnpja.com {nome} {cidade}",
        "direto": "https://cnpja.com/office/{cnpj}",
        "precisa_navegador": True,
        "observacao": "MEDIDO: 429 — limite de taxa ja na primeira consulta.",
    },
    "escavador": {
        "grupo": "cnpj", "dominio": "escavador.com",
        "da": ["cnpj", "razao_social", "socios", "processos"],
        "consulta": "site:escavador.com {nome} {cidade}",
        "direto": None, "precisa_navegador": True,
        "observacao": "forte em SÓCIO e pessoa física. Cuidado: junta homônimos.",
    },

    # ── guias comerciais: nome/endereço → telefone e horário ────────────────
    "solutudo": {
        "grupo": "contato", "dominio": "solutudo.com.br",
        "da": ["telefone", "endereco", "horario", "ramo", "site"],
        "consulta": "site:solutudo.com.br {nome} {cidade}",
        "direto": None, "precisa_navegador": False,
        "observacao": "guia comercial com boa cobertura de comércio de bairro — "
                      "que é justamente o que as fontes digitais não têm.",
    },
    "apontador": {
        "grupo": "contato", "dominio": "apontador.com.br",
        "da": ["telefone", "endereco", "horario", "ramo", "avaliacoes"],
        "consulta": "site:apontador.com.br {nome} {cidade}",
        "direto": None, "precisa_navegador": False,
        "observacao": "antigo, e por isso tem estabelecimento que fechou; "
                      "conferir se a página tem data.",
    },
    "telelistas": {
        "grupo": "contato", "dominio": "telelistas.net",
        "da": ["telefone", "endereco", "ramo"],
        "consulta": "site:telelistas.net {nome} {cidade}",
        "direto": None, "precisa_navegador": False,
        "observacao": "lista telefônica; telefone fixo com boa cobertura.",
    },
    "guiamais": {
        "grupo": "contato", "dominio": "guiamais.com.br",
        "da": ["telefone", "endereco", "horario", "ramo"],
        "consulta": "site:guiamais.com.br {nome} {cidade}",
        "direto": None, "precisa_navegador": False,
        "observacao": "idem telelistas, com mais comércio pequeno.",
    },

    # ── redes sociais: onde o comércio pequeno de fato está ─────────────────
    "instagram": {
        "grupo": "rede", "dominio": "instagram.com",
        "da": ["instagram", "telefone", "endereco", "ramo", "seguidores"],
        "consulta": "site:instagram.com {nome} {cidade}",
        "direto": None, "precisa_navegador": True,
        "observacao": "para comércio de bairro é MAIS atualizado que qualquer "
                      "cadastro. Exige navegador; a página sem sessão dá pouco.",
    },
    "facebook": {
        "grupo": "rede", "dominio": "facebook.com",
        "da": ["facebook", "telefone", "endereco", "horario", "ramo"],
        "consulta": "site:facebook.com {nome} {cidade}",
        "direto": None, "precisa_navegador": True,
        "observacao": "páginas de negócio trazem endereço e horário; exige "
                      "navegador.",
    },

    # ── oficiais e de conferência ───────────────────────────────────────────
    "brasilapi": {
        "grupo": "oficial", "dominio": "brasilapi.com.br",
        "da": ["cnpj", "razao_social", "nome_fantasia", "endereco", "cnae",
               "situacao_cadastral", "socios"],
        "consulta": None,
        "direto": "https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
        "precisa_navegador": False,
        "observacao": "JSON, sem chave, dado da Receita. NÃO busca por nome — "
                      "só CONFIRMA um CNPJ. É a trava contra CNPJ inventado.",
    },
    "receitaws": {
        "grupo": "oficial", "dominio": "receitaws.com.br",
        "da": ["cnpj", "razao_social", "endereco", "situacao_cadastral"],
        "consulta": None,
        "direto": "https://www.receitaws.com.br/v1/cnpj/{cnpj}",
        "precisa_navegador": False,
        "observacao": "3 consultas por minuto no plano livre. Reserva da "
                      "BrasilAPI, não primeira escolha.",
    },
}

# Sites cujo resultado NÃO deve alimentar a IA: enciclopédia, dicionário,
# repositório de código, PDF de diário oficial. Apareceram na medição e
# envenenaram a resposta — foi de um vídeo do TikTok que saiu um telefone.
RUIDO = (
    "wikipedia.org", "academia.edu", "huggingface.co", "github.com",
    "scribd.com", "slideshare.net", "passeidireto.com", "docplayer",
    "tiktok.com", "youtube.com", "pinterest.", "twitter.com", "x.com",
    "amazon.", "mercadolivre.", "shopee.", "olx.com",
    "jusbrasil.com.br/diarios", "dou.gov.br", "in.gov.br",
    ".pdf", "/diario", "diariooficial", "pncp.gov.br", "gov.br/anp",
    "ime.usp.br", "juntacomercial", "jucesc",
)


def e_ruido(url: str) -> bool:
    """A URL é de um lugar que não publica dado de empresa?

    Não é lista de bloqueio moral: é lista de lugares onde procurar CNPJ de uma
    lancheria só produz alucinação. Um PDF de diário oficial CONTÉM CNPJs — de
    outras empresas, no meio de mil linhas, e o modelo pega o mais próximo.
    """
    u = (url or "").lower()
    return any(r in u for r in RUIDO)


def consultas(nome: str, cidade: str, uf: str = "", endereco: str = "",
              grupos=("cnpj", "contato", "rede"), cnpj: str = "") -> list:
    """As buscas a fazer, dirigidas aos sites do catálogo mais uma aberta.

    A ABERTA FICA POR ÚLTIMO e existe por um motivo: o site do próprio negócio
    é a melhor fonte quando existe, e nenhum catálogo o conhece. O que ela traz
    passa pelo filtro de ruído.
    """
    nome = (nome or "").strip()
    if not nome:
        return []
    # A ABERTA VEM PRIMEIRO, e isso e resultado de medicao, nao de gosto.
    #
    # Das treze buscas dirigidas, onze devolveram ZERO no SearXNG — o `site:`
    # nao e bem suportado pelos motores que ele consulta. A aberta devolve 20.
    # Quando ela ficava por ultimo e o chamador cortava a lista, sobrava so
    # busca vazia: seis POIs, 0,2 s cada, nenhuma resposta.
    #
    # O endereco entra junto na aberta porque e ele que separa o
    # estabelecimento do homonimo de outra cidade.
    # TRES ABERTAS, DA MAIS LARGA PARA A MAIS ESTREITA — e a ordem importa.
    #
    # Colar o endereco dentro da frase entre aspas devolveu ZERO resultados
    # crus para `"SALA DE COSTURA" RUA PRIMEIRO DE MAIO, 1509 Canoas RS`. Um
    # motor de busca casa a frase inteira, e essa frase nao existe em lugar
    # nenhum. A que funcionou na medicao foi nome + cidade, e so.
    #
    # O endereco entra numa consulta PROPRIA, sem aspas em volta de tudo: ele
    # serve para separar o homonimo, e para isso basta aparecer perto.
    saida = []
    saida.append({"site": "aberta", "grupo": "aberta",
                  "q": " ".join(x for x in ['"%s"' % nome, cidade, uf] if x)})
    saida.append({"site": "aberta", "grupo": "aberta",
                  "q": " ".join(x for x in [nome, cidade, uf,
                                            "CNPJ telefone"] if x)})
    if endereco:
        saida.append({"site": "aberta", "grupo": "aberta",
                      "q": " ".join(x for x in [nome, endereco, cidade] if x)})
    # E so entao as dirigidas, para os dois dominios que o SearXNG alcanca.
    for chave, s in SITES.items():
        if s["grupo"] not in grupos or not s.get("consulta"):
            continue
        saida.append({
            "site": chave, "grupo": s["grupo"],
            "q": s["consulta"].format(nome='"%s"' % nome, cidade=cidade,
                                      uf=uf, endereco=endereco),
        })
    if cnpj:
        saida.append({"site": "aberta", "grupo": "cnpj",
                      "q": "%s %s" % (re.sub(r"\D", "", cnpj), cidade)})
    return saida


def urls_diretas(cnpj: str, grupos=("oficial", "cnpj")) -> list:
    """As páginas que se abre SEM buscar, quando o CNPJ já é conhecido.

    Com CNPJ na mão, buscar é desperdício: a URL é previsível. E a BrasilAPI
    responde JSON da Receita, que é o que transforma um CNPJ achado na web em
    CNPJ confirmado.
    """
    d = re.sub(r"\D", "", cnpj or "")
    if len(d) != 14:
        return []
    saida = []
    for chave, s in SITES.items():
        if s["grupo"] in grupos and s.get("direto"):
            saida.append({"site": chave, "grupo": s["grupo"],
                          "url": s["direto"].format(cnpj=d),
                          "precisa_navegador": s["precisa_navegador"]})
    return saida


def precisa_navegador(url: str) -> bool:
    u = (url or "").lower()
    for s in SITES.values():
        if s["dominio"] in u:
            return bool(s["precisa_navegador"])
    return False


def resumo() -> str:
    """O catálogo em texto, para o log e para quem for ler o prompt."""
    linhas = []
    for grupo in ("cnpj", "contato", "rede", "oficial"):
        nomes = [k for k, v in SITES.items() if v["grupo"] == grupo]
        linhas.append("%-8s %s" % (grupo, ", ".join(sorted(nomes))))
    return "\n".join(linhas)


if __name__ == "__main__":
    print("== catálogo ==")
    print(resumo())
    print("\n== consultas para um exemplo ==")
    for c in consultas("MECANICA DIESEL CRIATIVA", "Canoas", "RS",
                       "Rua Piratini 302"):
        print("   [%-8s] %s" % (c["grupo"], c["q"][:82]))
    print("\n== diretas para um CNPJ conhecido ==")
    for u in urls_diretas("11590570000103"):
        print("   [%-8s] %-52s navegador=%s"
              % (u["grupo"], u["url"][:52], u["precisa_navegador"]))
