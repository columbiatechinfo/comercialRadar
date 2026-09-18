# -*- coding: utf-8 -*-
"""Avaliacao ENXUTA da ligacao — o processo do dono do produto de 12/09/2026.

POR QUE EXISTE AO LADO DE `avaliar_ligacao`. O processo anterior mandava o
dossie inteiro, ate sete imagens e a pagina da busca lida por outra chamada da
IA, e pedia um JSON com descricao, numeros lidos, fachadas, medidores, fichas
e registros: ~5.700 tokens de entrada e ~900 gerados, 307 s por avaliacao com
a Spark cheia. Provado em 12/09/2026 em 25 a 60 ligacoes de Canoas:

    processo anterior   307 s por chamada
    enxuto, pagina como imagem   19 avaliacoes/min, 69 s
    enxuto, pagina como texto    33 avaliacoes/min, 36 s (25 ao mesmo tempo)

O DESENHO, do dono do produto:
- no maximo 5 fotos, leves: as quatro visadas de rua e a publicada por outra
  fonte (a do Google Maps nao tem data no banco);
- a pagina da busca web vai como TEXTO, o que o navegador entregou
  (`busca_web.texto`); a pagina antiga, que so tem a leitura da IA, vai como a
  lista do que a leitura achou;
- prompt curto: quais registros sao aderentes ao endereco do cadastro, quais se
  confirmam, quais nao combinam com a maioria; o motivo e o veredito;
- regra de unidade: registro sem complemento, com rua e numero iguais, conta
  como da instalacao; complemento diferente nao combina; so e duvida fundada o
  registro que outra instalacao tambem disputa e que nada prende a esta.

Reaproveita de `avaliar_ligacao` a fila, a gravacao do veredito e o descarte
de quem "nao combina" (que o casamento por endereco depois tenta religar).
Uso: python avaliar_enxuto.py --cidade Canoas --exigir-busca-web --vinculo-novo --trabalhadores 80 --aplicar
"""
import argparse
import base64
import collections
import io
import json
import re
import os
import threading
import time

from PIL import Image

import avaliar_ia as ia
import avaliar_ligacao as al
import base_comum as bc
import checagem_veredito as cv
import fachada_da_seta as fds
import fonte_da_busca as fdb
import descrever_imagens as di
import dossie_ligacao as dl
import imagens
import provas_datadas as pdat

LARGURA_FOTO = 640
TEXTO_MAX = 7000
#: NO MAXIMO 16 LIGACOES GRANDES (mais de `al.GRANDE` POIs) na Spark ao mesmo
#: tempo: sem teto, as 80 vagas viram 80 predios de 4 a 10 min cada (12/09/2026).
MAX_GRANDES = 16
VEREDITOS = ("aprovado", "reprovado", "revisao_humana")
#: O PROCESSO QUE FICA GRAVADO NA PERCEPCAO. Desde 13/09/2026 (noite) a IA diz em
#: campo proprio se as fotos mostram o comercio (`fotos`) e quais resultados da
#: busca usou (`busca`), e o motivo nao tem mais limite de frases — pedido do dono
#: do produto para a tela SEEK so pintar de verde a imagem e a busca que
#: confirmaram. O julgamento anterior a isso e rejulgado (`--prompt-antigo`).
#: 14/09/2026 (dono do produto): numero diferente nao e desta instalacao, a busca
#: web so soma, e a data da prova entra — ate 2 anos vale (`provas_datadas`).
#: O NOME COMECA COM "enxuto de ": a checagem reconhece o processo por esse
#: comeco, e um nome fora dele reprovou 6.883 aprovadas em 14/09/2026.
PROCESSO = "enxuto de 14/09/2026 (número, provas datadas, busca complementar)"
#: O PROCESSO LEVE (dono do produto, 15/09/2026), com `--leve`. Medido na Spark com 120 simultaneas e 300
#: aprovadas distintas: 64,5 julgamentos/min contra 21,6 da chamada pesada, sem erro nem JSON invalido.
#:   - UMA foto de rua, de frente para o imovel (`recapturar_frente`), e a foto do Google mais recente com
#:     data, as duas em 768 px: o custo do codificador de visao cresce muito mais que o tamanho da imagem;
#:   - a ficha do Serasa e a busca web vao em TEXTO (o print fica so para a SEEK);
#:   - os comentarios recentes de cliente entram com a data;
#:   - a pergunta e sobre o IMOVEL: fachada comercial prova uso mesmo com nome diferente do registro, e o
#:     estado de conservacao pesa (imovel abandonado);
#:   - saida curta nos campos de apoio, motivo completo; as 2 fontes o CODIGO conta (checagem, regra 7).
#: Comeca com "enxuto de " (a checagem reconhece o processo por esse comeco) e tem "leve" (a regra 7).
PROCESSO_LEVE = "enxuto de 15/09/2026 v6 (leve: foto de rua no pin ou hidrômetro com planta, fachada da seta pelo código, vizinho só com nome, fonte da busca e redes sociais, auditoria das 40 e das aprovadas do R_000, fonte única promove)"
#: A FOTO DE RUA DO PROCESSO LEVE e so a captura nova (`recapturar_frente`: de frente, seta, fov 100). A de antes
#: tinha a mira desenhada e olhava do panorama mais perto — foi para a IA na leve v2 e o dono do produto viu.
FRENTE_NOVA_DESDE = "2026-09-14 23:00-03"
#: a checagem do fim da rodada so grava as ligacoes da fila (--checagem-so-da-fila)
CHECAGEM_SO_DA_FILA = False
LEVE = False
LARGURA_LEVE = 768
#: a foto de rua pode ir maior que a do Google: e onde mora a placa pequena (15/09/2026)
LARGURA_RUA = int(os.environ.get("RADAR_LARGURA_RUA") or LARGURA_LEVE)

#: `--saida DIR`: grava cada julgamento (e as fotos) numa pasta, para a galeria
#: de validacao — o lote de conferencia roda sem `--aplicar`.
SAIDA = None

PROMPT = """Você confere se um imóvel cobrado como RESIDENCIAL tem comércio ou serviço funcionando nele.

Você recebe: a data de hoje; os dados do cadastro da instalação; os registros candidatos (estabelecimentos que bases independentes situam neste endereço), cada um com o número que publica e as provas DATADAS que tem; até 5 fotos (quatro de rua, do mesmo ponto em quatro direções — a mira verde marca a direção da coordenada do registro, que pode ter alguns metros de erro —, e uma publicada no Google), cada uma com a data em que foi tirada quando se sabe; e os resultados de buscas na web pelo endereço, só os que citam a rua e o número desta instalação.

Olhe todos os dados e responda, nesta ordem:
1. Quais registros são aderentes ao endereço do cadastro — rua, número, complemento, bairro — e, destes, quais se confirmam. NÚMERO DIFERENTE NÃO É DESTA INSTALAÇÃO: o registro que publica outro número é de outro imóvel, mesmo vizinho ou na mesma rua, e não é aderente. Se uma foto ou um resultado mostra com clareza um número diferente na fachada ou no endereço do registro, ele também não é desta instalação. Número que não aparece na foto não atrapalha. Em endereço com várias unidades (o cadastro traz complemento, como CASA 02 ou APTO 3): o registro com o mesmo complemento é desta instalação; o registro sem complemento, com rua e número iguais, também conta como desta instalação; o registro com complemento diferente é de outra unidade e não combina. Só é dúvida fundada o registro que também é candidato de outras instalações e que nada — complemento, foto ou busca — prende a esta.
2. Quais registros não combinam com a maioria dos registros e com os dados desta instalação.
3. O motivo do veredito e o veredito:
   - "aprovado" só quando um registro de comércio ou serviço, no MESMO NÚMERO, pertence a esta instalação e tem PROVA RECENTE — de até 2 anos antes de hoje: CNPJ ativo na base atual da Receita, avaliação de cliente, foto (de rua ou publicada) que mostra o comércio, loja vista no iFood, atualização no Overture ou no Foursquare;
   - "revisao_humana" quando o registro é desta instalação mas nenhuma prova é recente (só provas com mais de 2 anos, ou só a busca na web), e o motivo diz qual prova faltou e a idade das que existem; e também quando a dúvida é a qual instalação o registro pertence — a unidade do número —, e o motivo diz qual é;
   - "reprovado" se nenhum registro pertence a esta instalação.
4. As fotos: "confirmam" é true só se alguma foto MOSTRA o comércio ou serviço — placa, letreiro, vitrine, porta de loja, fachada com o nome ou a atividade do registro. Casa, portão ou muro sem sinal de comércio é false. "quais" são os números, na lista de fotos, das que mostram; "o_que_mostram" diz o que se vê nelas e de quando são, ou por que as fotos não provam.
5. A busca: liste os resultados que você usou. "confirma" é true só quando o resultado traz o nome, o telefone, o CNPJ ou a atividade DO REGISTRO junto do endereço desta instalação; resultado de outro negócio no mesmo endereço é false. Resultado que você não usou fica fora; sem nenhum, a lista é vazia.
A DATA DE CADA PROVA PESA. A foto mostra o dia em que foi tirada, e não hoje; entre as fotos, a mais recente vale mais, e a de mais de 1 ano vale menos que uma fonte recente. Prova com mais de 2 anos não aprova sozinha. A BUSCA NA WEB SÓ SOMA: um resultado com o nome e o endereço desta instalação reforça o registro, mas nunca aprova sem outra prova. Templo, igreja, associação e escola não são comércio nem serviço. CNPJ ou MEI com atividade de comércio ou serviço registrada é negócio, mesmo com nome de pessoa. Foto de rua sem sinal de comércio NÃO desmente uma prova recente: muito comércio e serviço funciona em casa comum. Resultado da busca que fala de outro endereço não conta.

Responda SOMENTE um JSON:
{"aderentes": [{"poi": <número>, "confirmado": true|false, "numero": "igual|diferente|nao_visto", "prova_recente": "<a prova de até 2 anos e a data dela, ou nenhuma>", "por": "<o que confirma o registro, ou o que falta>"}],
 "nao_combinam": [{"poi": <número>, "por": "<até 12 palavras>"}],
 "fotos": {"confirmam": true|false, "quais": [<número da foto>], "o_que_mostram": "<o que se vê>"},
 "busca": [{"motor": "DuckDuckGo|Yahoo|Google", "resultado": <número do resultado>, "poi": <número do registro>, "confirma": true|false, "casa": "<o que casa com o registro, ou o que não casa>"}],
 "motivo": "<o motivo do veredito, em detalhe: o que os registros, as fotos e a busca mostraram, o que pesou na decisão e por quê>",
 "veredito": "aprovado|reprovado|revisao_humana"}

────────────────────────────────────────
"""


PROMPT_LEVE = """Você confere se um imóvel cobrado como RESIDENCIAL tem uso NÃO RESIDENCIAL funcionando nele.

USO NÃO RESIDENCIAL é qualquer atividade que não seja só moradia, inclusive as que não parecem loja:
- comércio: loja, mercado, minimercado, bar, restaurante, lanchonete, padaria, açougue, farmácia, posto, loja de material de construção, depósito de gás ou de bebidas;
- serviço: salão, barbearia, estética, oficina mecânica ou de moto (inclusive na garagem de casa), borracharia, lava-jato, funilaria, clínica, consultório, escritório, academia, escola ou curso particular, creche particular, lavanderia, pet shop, gráfica, assistência técnica, chaveiro;
- indústria e produção: fábrica, confecção, marcenaria, serralheria, vidraçaria, marmoraria, metalúrgica, padaria industrial, cozinha de marmita ou de salgados;
- logística e armazenagem: galpão, depósito, transportadora, pátio com caminhões, ônibus ou máquinas, ferro-velho, sucata, reciclagem;
- estacionamento ou garagem paga, pátio de venda de carros;
- hospedagem (hotel, pousada, aluguel por temporada) e agropecuária (criação, horta comercial, agropecuária).
NÃO contam: templo ou igreja, associação ou entidade sem fins lucrativos, escola ou órgão público, condomínio residencial.

Você recebe: HOJE; o cadastro da instalação; os registros candidatos no endereço, cada um com o número que publica e as provas datadas; os comentários recentes de clientes no Google, com a data; em TEXTO, a ficha do CNPJ no Serasa e os resultados da busca na web pelo endereço, quando houve; e imagens numeradas, com o que é e a data:
- foto de rua (Street View) com seta verde semitransparente apontando o pin do Maps do registro e a distância da câmera — ou, quando nenhum registro com pin está a até 60 m, apontando o hidrômetro da instalação (as coordenadas podem ter alguns metros de erro). No canto inferior direito da foto, a PLANTA VISTA DE CIMA: faixa cinza = a rua, ponto branco = a câmera, cone verde claro = o que a foto mostra, ponto verde = o ponto da seta;
- foto publicada no Google do lugar.

A PERGUNTA É SOBRE O IMÓVEL: há uso não residencial funcionando nele?

Responda, nesta ordem:
1. Imagens. OLHE A IMAGEM INTEIRA, e não só a ponta da seta: a seta marca a coordenada, que tem metros de erro, e o imóvel pode ocupar boa parte da foto. Uma imagem só MOSTRA uso com SINAL CONCRETO: letreiro, placa, faixa, banner, adesivo ou anúncio pintado na parede ou no muro de QUALQUER negócio (mesmo com nome diferente do registro, mesmo pequeno), telefone ou nome comercial escrito na fachada, marcador de estabelecimento do Google no imóvel, vitrine com mercadoria, porta de loja ou de enrolar aberta com mercadoria ou atendimento, balcão, cardápio, oficina com carros ou peças em serviço, pátio com caminhões, máquinas, sucata ou material de trabalho, carros à venda, portão de galpão industrial. A LEITURA DA FOTO DE RUA, quando vier, foi feita antes só com a imagem, e a fachada da seta foi decidida pela posição da ponta: texto ou sinal da FACHADA DA SETA É SINAL (confira na imagem). PLACA DE VIZINHO — mesmo colada, mesmo parecendo continuação do imóvel — SÓ É SINAL DESTA INSTALAÇÃO quando VOCÊ confere que o nome ou o telefone dela aparece nos registros, na ficha do Serasa ou na busca na web desta instalação (variação do mesmo nome vale; palavra genérica solta ou nome de rua não); sinal sem texto de vizinho nunca é sinal desta instalação. Sem essa confirmação, não use a placa do vizinho para aprovar nem para dizer o que funciona no imóvel, e não ponha a foto de rua em "quais". NÃO É SINAL: casa, sobrado, muro, grade, portão fechado de casa, carro na garagem, jardim, telhado, caixa d'água — mesmo que um registro diga que há empresa ali. Nome no letreiro DA FACHADA DA SETA diferente do registro não tira o sinal: nome fantasia muda e o negócio pode ter trocado de dono.
2. Estado do imóvel na imagem mais recente: em uso; abandonado ou sem uso (mato alto, portas ou janelas lacradas, quebradas ou pichadas, ruína, placa de aluga-se ou vende-se, vitrine vazia, fachada deteriorada sem ocupação); ou não dá para ver. Diga a data da imagem em que você viu.
3. Aderentes: registros no mesmo endereço — rua, número, complemento, bairro. NÚMERO DIFERENTE É OUTRO IMÓVEL, mesmo vizinho; também não é aderente se uma imagem mostra com clareza outro número. Número que não aparece não atrapalha. Com complemento no cadastro: mesmo complemento é desta instalação; sem complemento, com rua e número iguais, também; complemento diferente é outra unidade. Instalação que é uma UNIDADE (LOJA 026, SALA 3, BOX 12): a prova tem de ser do registro DESSA unidade — foto, letreiro e registro de outra loja do mesmo número não sustentam a aprovação.
4. Não combinam: registros que destoam da maioria e desta instalação.
5. Fontes que confirmam o uso, cada uma UMA vez:
 - Receita: o CNPJ da base, a ficha do Serasa e a busca na web são TODOS a mesma fonte (a busca acha a Receita republicada) — MENOS o resultado de rede social: cada resultado da busca vem marcado com a fonte ([rede social: Instagram], [site de CNPJ = Receita republicada], [guia de empresas], [site próprio ou outro]);
 - Google Maps: a ficha, os comentários e a foto publicada são a mesma fonte;
 - foto de rua: só com sinal concreto (item 1);
 - Instagram, Facebook, TikTok, YouTube, LinkedIn, Kwai, iFood e base estadual: uma fonte cada. REDE SOCIAL CONFIRMADA PESA MUITO: o post com o nome do negócio neste endereço (bloco REDES SOCIAIS NO ENDEREÇO) mostra o negócio ativo e é fonte própria, e o post de até 2 anos é PROVA RECENTE pela data dele.
6. Veredito:
 - "aprovado" em dois casos só:
   a) ao menos 2 FONTES DIFERENTES confirmam o uso, com alguma PROVA RECENTE (até 2 anos: imagem com sinal — a fachada no Street View vale em qualquer data —, comentário, post de rede social, CNPJ ativo na base atual, loja no iFood);
   b) uma fonte só, quando ela é a fachada no Street View com sinal concreto (de qualquer data), ou a foto publicada com sinal concreto de MENOS DE 1 ANO; comentário de menos de 1 ano só vale sozinho com uma imagem que confirma; a loja no iFood nunca aprova sozinha;
   e, nos dois casos, o imóvel não aparece abandonado.
 - "revisao_humana": há sinal de uso, mas não fecha a regra acima (uma fonte só que não basta, prova antiga, imagem sem sinal com registro ativo, imóvel abandonado com prova em contrário, dúvida de unidade) — o motivo diz o que faltou e a idade das provas;
 - "reprovado": nenhum sinal de uso não residencial neste imóvel.
A DATA PESA: cada prova vale para o dia em que foi tirada, postada ou atualizada; a mais recente vale mais. FOTO DE RUA MAIS ANTIGA QUE AS PROVAS DO GOOGLE OU DAS REDES SOCIAIS NÃO DESMENTE ESSAS PROVAS: o negócio pode ter aberto depois dela — compare as datas e fique com a mais recente. Liste em "aderentes" TODO registro em que você apoia o veredito.
CUIDADOS DA AUDITORIA: PLACA DE ALUGA-SE OU VENDE-SE NÃO É SINAL DE USO — é sinal de imóvel vago ou à venda; se a foto com a placa é mais nova que a última prova de atividade (comentário, foto do Google, iFood, post), o imóvel pode ter ficado vago e não aprova — a não ser que você identifique uso comercial, industrial ou de serviço no imóvel. ANÚNCIO DE ALUGUEL OU VENDA DO IMÓVEL na busca ou na rede social é contraprova, não fonte. DATAS: use só as datas escritas no texto; comentário sem data não é prova recente; não cite ficha do Serasa que não veio no texto. Foto de rua anterior à abertura do CNPJ não desmente o CNPJ. ARTE DE DIVULGAÇÃO NÃO É FOTO DO LUGAR: logo, desenho, card de serviços, panfleto ou montagem digital publicados no Google não mostram o imóvel e não contam como imagem com sinal (não ponha em "quais"). Instalação de FUNDOS: a foto da rua não mostra o imóvel dos fundos, e a falta de sinal nela não pesa. Registro com CEP DIFERENTE da instalação pode ser rua homônima de outro bairro: não o confirme sem outra prova do endereço. CADA FONTE PRECISA DA PRÓPRIA PROVA DE ATÉ 2 ANOS (menos a fachada no Street View, que vale em qualquer data): registro sem prova datada (base estadual sem data, CNEFE 2022), post de rede social antigo e comentário de mais de 2 anos NÃO contam como fonte. A visão geral do Google e o card do Maps que aparecem na busca são a própria ficha do Google Maps, não outra fonte; busca na web sem CNPJ no endereço não é a Receita. FOTO DO GOOGLE SÓ CONTA SE MOSTRA O PRÓPRIO IMÓVEL (fachada, interior ou vitrine do lugar): produto, pessoa, serviço prestado em outro local, evento, paisagem, logo, card ou arte NÃO são foto do lugar — não ponha em "quais". Placa de vende/aluga na foto de rua mais nova que os comentários: o negócio pode ter saído. IMAGEM DE FACHADA QUE CONFIRMA E É RECENTE PESA MUITO, e foto publicada ou comentário de cliente recentes também: fachada no Street View de qualquer data com letreiro, faixa, vitrine ou porta de loja BASTA SOZINHA para aprovar; foto publicada do próprio lugar de menos de 1 ano e comentário de cliente de menos de 1 ano também bastam sozinhos; a loja vista no iFood precisa de outra confirmação. Nesses casos aprove e diga qual é a prova, sem exigir a segunda fonte. CNPJ/MEI ativo com atividade não residencial é negócio, mesmo com nome de pessoa — mas é UMA fonte só.

Responda SOMENTE um JSON, curto nos campos de apoio e completo no motivo:
{"uso": {"nao_residencial": true|false, "o_que": "<atividade, até 8 palavras>"},
 "imovel": {"estado": "em_uso|abandonado|nao_visto", "por": "<o que se vê e a data da imagem, até 12 palavras>"},
 "aderentes": [{"poi": <número>, "confirmado": true|false, "numero": "igual|diferente|nao_visto", "prova_recente": "<prova e data, até 8 palavras, ou nenhuma>", "fontes": ["<fontes do item 5>"], "por": "<até 15 palavras>"}],
 "nao_combinam": [{"poi": <número>, "por": "<até 8 palavras>"}],
 "fotos": {"confirmam": true|false, "quais": [<número da imagem com sinal>], "sinal": "<o sinal concreto que se vê, ou nenhum>", "o_que_mostram": "<até 20 palavras, com a data>"},
 "comentarios": {"confirmam": true|false, "por": "<até 15 palavras, com a data>"},
 "busca": [{"fonte": "Serasa|busca na web", "poi": <número do registro>, "confirma": true|false}] (só as que vieram no texto; nenhuma, lista vazia),
 "fontes": ["<as fontes diferentes do item 5 que confirmam o uso, cada uma uma vez>"],
 "motivo": "<o motivo do veredito, em detalhe: o que pesou e por quê>",
 "veredito": "aprovado|reprovado|revisao_humana"}

────────────────────────────────────────
"""


def _jpeg_768(b, largura=LARGURA_LEVE, q=85):
    """A foto do processo leve: 768 px de largura, lados em multiplos de 32 (o bloco do Qwen3.5)."""
    im = Image.open(io.BytesIO(bytes(b))).convert("RGB")
    if im.width > largura:
        im = im.resize((largura, int(im.height * largura / im.width)), Image.LANCZOS)
    w, h = max(32, im.width // 32 * 32), max(32, im.height // 32 * 32)
    if (w, h) != im.size:
        im = im.resize((w, h), Image.LANCZOS)
    s = io.BytesIO()
    im.save(s, "JPEG", quality=q)
    return s.getvalue()


def _fichas_cnpj_em_texto(cur, cnpjs):
    """Os blocos "FICHA DO CNPJ ... NO SERASA" ja estruturados (`ficha_cnpj_web.texto_ia`). Vazio enquanto a
    coleta do Serasa nao grava no banco — a regra nova so a faz quando a base nao traz data e situacao."""
    if not cnpjs:
        return []
    try:
        cur.execute("""select distinct on (cnpj) texto_ia from radar_comercial.ficha_cnpj_web
                        where cnpj = any(%s) and texto_ia is not null order by cnpj, consultado_em desc""",
                    (sorted(cnpjs),))
        return [r[0] for r in cur.fetchall()]
    except Exception:                                          # noqa: BLE001
        cur.connection.rollback()                              # antes da migracao da ficha
        return []


def _jpeg_leve(b, largura=LARGURA_FOTO, q=70):
    im = Image.open(io.BytesIO(bytes(b))).convert("RGB")
    if im.width > largura:
        im = im.resize((largura, int(im.height * largura / im.width)), Image.LANCZOS)
    s = io.BytesIO()
    im.save(s, "JPEG", quality=q)
    return s.getvalue()


#: OS MOTORES DA BUSCA WEB desde 13/09/2026: DuckDuckGo e Yahoo, e o Google so
#: na reserva (`buscar_web.py`). As buscas feitas DENTRO DO GOOGLE MAPS, de 12 a
#: 13/09, NAO ENTRAM: o Maps devolvia os lugares da regiao, de outras ruas e
#: numeros, e a IA chegou a aprovar por empresa do vizinho. So conta linha com
#: `resultados` (o formato novo, ja filtrado pelo endereco da instalacao).
MOTORES_DA_BUSCA = ("duckduckgo", "yahoo", "google")


def _texto_da_busca(cur, ligacao):
    """(consulta, texto): o que cada motor achou NO ENDERECO da instalacao. O texto
    ja vem filtrado de `buscar_web` — resultado de outro endereco nunca chega aqui."""
    cur.execute("""select distinct on (motor) motor, consulta, texto from radar_comercial.busca_web
                    where ligacao = %s and tipo = 'endereco' and motor = any(%s) and not bloqueado
                      and resultados is not null and texto is not null
                    order by motor, feito_em desc""", (str(ligacao), list(MOTORES_DA_BUSCA)))
    linhas = sorted(cur.fetchall(), key=lambda r: MOTORES_DA_BUSCA.index(r[0]))
    if not linhas:
        return None, None
    # A FONTE E A DATA DE CADA RESULTADO (15/09/2026): rede social, site de CNPJ, guia, iFood ou site proprio
    cidade, cep = fdb.cidade_e_cep(cur, ligacao)
    return linhas[0][1], "\n\n".join(fdb.anotar_texto(t, cidade, cep) for _m, _c, t in linhas)[:TEXTO_MAX]


def _resultados_web_da_ficha(cur, poi_ids):
    """{poi: "instagram.com › _pancacheia — PANÇA CHEIA LANCHES; ..."}: os "Resultados
    da Web" da ficha do Maps (migracao 0111). Texto curto: e reforco, nao prova."""
    if not poi_ids:
        return {}
    try:
        cur.execute("""select poi_id, resultados_web from radar_comercial.maps_data
                        where poi_id = any(%s) and jsonb_array_length(coalesce(resultados_web,'[]')) > 0""",
                    (list(poi_ids),))
    except Exception:                                          # noqa: BLE001
        cur.connection.rollback()                              # antes da migracao 0111
        return {}
    saida = {}
    for pid, cartoes in cur.fetchall():
        itens = ["%s — %s" % ((c.get("migalha") or c.get("url") or "")[:80], (c.get("titulo") or "")[:80])
                 for c in (cartoes or [])[:4] if isinstance(c, dict)]
        if itens:
            saida[pid] = "; ".join(itens)
    return saida


def montar(con, ligacao):
    """(dados_do_prompt, fotos_jpeg, rotulos, ids, n_fontes) ou (None, ...) sem POI."""
    return montar_com_refs(con, ligacao)[:5]


def montar_com_refs(con, ligacao, fotos_do_dossie=True):
    """O mesmo que `montar`, e mais DE QUEM E CADA FOTO: [{"poi", "tipo"}], na ordem
    em que foram para a IA. A tela SEEK marca no carrossel a foto que a IA disse
    que mostra o comercio (dono do produto, 13/09/2026)."""
    cur = con.cursor()
    cur.execute("""select coalesce(end_ligacao,''), coalesce(categoria,''), coalesce(nom_bairro,''),
                          coalesce(nro::text,'')
                     from resources_root.cadastro_corsan where num_ligacao = %s""",
                (int(ligacao) if str(ligacao).strip().isdigit() else -1,))  # pelo NUMERO: `::text` impede o indice sob a RLS (988 ms -> 0,09 ms, 18/09/2026)
    cad = cur.fetchone()
    if not cad:
        return None, [], [], [], 0, []
    end_l, cat, bairro, nro_inst = cad
    cur.execute("""select p.id, lower(coalesce(p.fonte,'')), coalesce(p.nome,''), coalesce(p.categoria,''),
                          coalesce(p.endereco,''), coalesce(p.telefone,''), coalesce(p.cnpj,''),
                          m.total_avaliacoes, rd.bruto->>'data_inicio', rd.complemento,
                          (select count(distinct l2.ligacao) from radar_comercial.ligacao_poi l2
                            where l2.poi_id = p.id and l2.descartado_em is null and l2.ligacao <> %s)
                     from radar_comercial.ligacao_poi lp
                     join radar_comercial.pois p on p.id = lp.poi_id
                     left join radar_comercial.maps_data m on m.poi_id = p.id
                     left join radar_comercial.receita_data rd on rd.poi_id = p.id
                    where lp.ligacao = %s and lp.descartado_em is null and p.fundido_em is null""",
                (str(ligacao), str(ligacao)))
    linhas_reg = cur.fetchall()
    # O NUMERO E AS PROVAS DATADAS DE CADA REGISTRO (14/09/2026): as mesmas que a
    # checagem confere depois — calculadas num lugar so (`provas_datadas`).
    provas = pdat.carregar(con, [r[0] for r in linhas_reg])
    web = _resultados_web_da_ficha(cur, [r[0] for r in linhas_reg])
    regs, ids, fontes = [], [], set()
    for pid, fonte, nome, catp, endp, tel, cnpj, aval, abertura, compl, outras in linhas_reg:
        ids.append(pid)
        fontes.add(fonte)
        info = provas.get(pid) or {}
        conf = pdat.numero_confere(info.get("numero"), nro_inst)
        numero = ("nº %s — mesmo número da instalação" % info.get("numero") if conf == "igual" else
                  "nº %s — NÚMERO DIFERENTE do da instalação (%s)" % (info.get("numero"), nro_inst)
                  if conf == "diferente" else "sem número publicado")
        partes = [x for x in (catp, endp, numero, ("complemento " + compl) if compl else "sem complemento",
                              ("tel " + tel) if tel else "", ("CNPJ " + cnpj) if cnpj else "",
                              ("aberto em %s/%s" % (abertura[4:6], abertura[:4])) if abertura and len(abertura) >= 6 else "",
                              ("%s avaliações no Google" % aval) if aval else "",
                              ("provas datadas: " + pdat.resumo(info)) if info.get("provas") else "sem prova datada",
                              ("na web (ficha do Google): " + web[pid]) if web.get(pid) else "",
                              ("candidato também de %d outra(s) instalação(ões)" % outras) if outras else "") if x]
        regs.append("#%s [%s] %s · %s" % (pid, fonte, nome, " · ".join(partes)))
    if not ids:
        return None, [], [], [], 0, []
    # AS FOTOS: as quatro de rua do POI mais proximo do medidor e a primeira
    # publicada — as mesmas que o dossie escolhe, sem o texto dele.
    # SEM AS FOTOS DO DOSSIE (`fotos_do_dossie=False`): o processo leve escolhe as proprias e nao le as
    # imagens duas vezes (15/09/2026: 40 julgamentos/min de ponta a ponta contra 64,5 da IA sozinha).
    _t, imgs, tipos, _r = (dl.montar(con, ligacao, ia, imagens, busca_web=None) if fotos_do_dossie
                           else ("", [], [], {}))
    sv = [(b, t) for b, t in zip(imgs, tipos) if t.startswith("sv_")][:4]
    pub = [(b, t) for b, t in zip(imgs, tipos) if not t.startswith("sv_")][:1]
    fotos = [_jpeg_leve(b) for b, t in sv + pub]
    # A DATA DA FOTO PUBLICADA vai no rotulo (14/09/2026): `foto_maps_1 (2025-08)`
    # vira "foto publicada no Google (ago/2025)"; sem data, o rotulo diz que nao ha.
    rot = [t if t.startswith("sv_") else
           ("foto publicada no Google (%s)" % pdat.mes_ano(pdat.data_do_rotulo(t)) if pdat.data_do_rotulo(t)
            else "foto publicada no Google (sem data)") for b, t in sv + pub]
    _r = _r or {}
    refs = [{"poi": _r.get("fonte_das_visadas"), "tipo": t.split(" ")[0]} for _b, t in sv] \
        + [{"poi": _r.get("fonte_das_fotos"), "tipo": "foto publicada"} for _b, _t2 in pub]
    consulta, texto = _texto_da_busca(cur, ligacao)
    dados = ("HOJE: %s\n\nINSTALAÇÃO: %s · número %s · categoria %s · bairro %s\n\nREGISTROS CANDIDATOS:\n%s\n\n"
             "FOTOS, nesta ordem:\n%s\n\nTEXTO DA BUSCA NA WEB%s:\n%s"
             % (pdat.hoje().strftime("%d/%m/%Y"), end_l, nro_inst or "sem número", cat, bairro, "\n".join(regs),
                "\n".join("%d. %s" % (i + 1, r) for i, r in enumerate(rot))
                or "(nenhuma foto)", (" (consulta \"%s\", DuckDuckGo e Yahoo)" % consulta) if consulta else "",
                texto or "(não houve busca na web para esta instalação)"))
    return dados, fotos, rot, ids, len(fontes), refs


def _metros(la1, lo1, la2, lo2):
    import math
    if None in (la1, lo1, la2, lo2):
        return 9e9
    dy = (float(la2) - float(la1)) * 111320
    dx = (float(lo2) - float(lo1)) * 111320 * math.cos(math.radians(float(la1)))
    return math.hypot(dx, dy)


def poi_da_foto_de_rua(cur, ligacao, ids):
    """(poi, metros, tem_nova, lat, lng, rua): o registro COM PIN DO MAPS mais perto do hidrometro, ate
    `dl.RAIO_DA_FOTO_M`. `tem_nova`: se ele tem a foto de rua de frente recapturada (fov 100).

    SO O PIN DO MAPS E LUGAR (dono do produto, 15/09/2026): a coordenada de origem do registro da Receita e o
    endereco geocodificado e cai na rua — em 15% das fotos a camera ficou a ate 4 m dela. Sem registro com pin
    a ate 60 m, a foto de rua e a do hidrometro."""
    # pelo indice: `num_ligacao::text = %s` varria a tabela, 0,1 a 0,3 s por ligacao (15/09/2026)
    cur.execute("""select cod_latitude::float, cod_longitude::float, nom_logradouro from resources_root.cadastro_corsan
                    where num_ligacao = %s""", (int(ligacao) if str(ligacao).isdigit() else -1,))
    la, lo, rua = cur.fetchone() or (None, None, None)
    # PIN E O LUGAR DO GOOGLE (place_id ChIJ): `maps_lat` tambem guarda a correcao pelo CNEFE e a geocodificacao
    # da base estadual, que continuam sendo endereco e nao lugar
    cur.execute("""select p.id, p.maps_lat, p.maps_lng from radar_comercial.pois p
                    where p.id = any(%s) and p.maps_lat is not null and p.maps_lng is not null
                      and coalesce(p.place_id, '') like 'ChIJ%%'""", (list(ids),))
    perto = sorted(((_metros(la, lo, pla, plo), pid, pla, plo) for pid, pla, plo in cur.fetchall()), key=lambda x: x[0])
    if not perto or perto[0][0] > dl.RAIO_DA_FOTO_M:
        return None
    d, pid, pla, plo = perto[0]
    cur.execute("""select 1 from radar_comercial.poi_evidencia where poi_id = %s and tipo = 'sv_frente' and fov = 100
                      and dados is not null and capturado_em >= %s""", (pid, FRENTE_NOVA_DESDE))
    return pid, d, cur.fetchone() is not None, pla, plo, rua


_ROTULO_DA_SETA = re.compile(r"\d{1,3}\s*m")


def _texto_da_leitura(leitura):
    """A leitura da foto de rua (`ler_fotos_de_rua`, 0115) em poucas linhas para o julgamento."""
    if not isinstance(leitura, dict):
        return None
    onde = {"no_imovel_da_seta": "no imóvel da seta", "colado_ao_imovel": "colado ao imóvel", "longe": "longe"}
    # o rotulo da distancia que a propria seta traz ("7 m") e a marca d'agua do Google nao sao texto do lugar
    ts = ['"%s" (%s, %s)' % (t.get("texto"), t.get("tipo") or "?", onde.get(t.get("onde"), t.get("onde") or "?"))
          for t in (leitura.get("textos") or [])[:15] if isinstance(t, dict) and t.get("texto")
          and not _ROTULO_DA_SETA.fullmatch(str(t.get("texto")).strip())
          and t.get("tipo") != "marca_dagua" and "google" not in str(t.get("texto")).lower()]
    ss = ["%s (%s)" % (x.get("sinal"), onde.get(x.get("onde"), x.get("onde") or "?"))
          for x in (leitura.get("sinais_sem_texto") or [])[:8] if isinstance(x, dict) and x.get("sinal")]
    return "\n".join(["LEITURA DA FOTO DE RUA (feita antes, só com a imagem em resolução maior):",
                       "textos: %s" % ("; ".join(ts) or "nenhum"),
                       "sinais sem texto: %s" % ("; ".join(ss) or "nenhum"),
                       "uso não residencial no imóvel, pela leitura: %s"
                       % ("sim" if leitura.get("uso_nao_residencial_no_imovel") else "não")])


def montar_leve(con, ligacao):
    """O processo leve: (dados, fotos, rotulos, ids, n_fontes, refs), o mesmo formato de `montar_com_refs`.
    Os registros saem de `montar_com_refs` sem as fotos do dossie; a foto de rua e SO a captura nova."""
    dados, _fotos, _rot, ids, n_fontes, _refs = montar_com_refs(con, ligacao, fotos_do_dossie=False)
    if dados is None:
        return None, [], [], [], 0, []
    cabeca = dados.split("\n\nFOTOS, nesta ordem:")[0]
    # O CEP DO REGISTRO DIFERENTE DO DA INSTALACAO (auditoria das 40, 15/09/2026): o iFood da 320537 era da Rua
    # Esperanca de outro CEP — rua homonima
    m_cep = re.search(r"INSTALAÇÃO:[^\n]*?CEP:?\s*(\d{5})-?(\d{3})", cabeca)
    if m_cep:
        cep_inst = m_cep.group(1) + m_cep.group(2)

        def _marca_cep(mm):
            linha = mm.group(0)
            ceps = {a + b for a, b in re.findall(r"(?<![\d-])(\d{5})-(\d{3})(?![\d-])", linha)}
            if ceps and cep_inst not in ceps:
                return linha + " · CEP %s DIFERENTE do da instalação (%s-%s): pode ser rua homônima" % (
                    "/".join(sorted("%s-%s" % (c[:5], c[5:]) for c in ceps)), cep_inst[:5], cep_inst[5:])
            return linha
        cabeca = re.sub(r"(?m)^#\d+ \[[^\]]+\][^\n]*$", _marca_cep, cabeca)
    cur = con.cursor()
    fotos, rot, refs = [], [], []
    leitura_rua = mira_rua = None
    escolha = poi_da_foto_de_rua(cur, ligacao, ids)
    if escolha and escolha[2]:
        cur.execute("""select dados, data_imagem, distancia_m, leitura, mira_x from radar_comercial.poi_evidencia
                        where poi_id = %s and tipo = 'sv_frente'""", (escolha[0],))
        b, data, dist, leitura_rua, mira_rua = cur.fetchone()
        fotos.append(_jpeg_768(b, largura=LARGURA_RUA))
        rot.append("foto de rua de frente (%s): Street View, seta no pin do Maps do registro, câmera a %s m"
                   % (pdat.mes_ano(pdat.data_de_texto(data)), round(dist or 0)))
        refs.append({"poi": escolha[0], "tipo": "sv_frente"})
    elif not escolha:
        # NENHUM REGISTRO A ATE 60 M: a foto de rua tirada no hidrometro (0114, 15/09/2026)
        cur.execute("""select dados, data_imagem, distancia_m, leitura, mira_x from radar_comercial.ligacao_evidencia
                        where ligacao = %s and tipo = 'sv_frente' and dados is not null""", (str(ligacao),))
        x = cur.fetchone()
        if x:
            leitura_rua, mira_rua = x[3], x[4]
            fotos.append(_jpeg_768(x[0], largura=LARGURA_RUA))
            rot.append("foto de rua de frente para o hidrômetro (%s): Street View, seta na coordenada do hidrômetro desta "
                       "instalação (nenhum registro com pin do Maps a até 60 m), câmera a %s m" % (pdat.mes_ano(pdat.data_de_texto(x[1])), round(x[2] or 0)))
            refs.append({"poi": None, "ligacao": str(ligacao), "tipo": "sv_hidrometro"})
    # A FOTO DO GOOGLE: do registro com mais avaliacoes que tenha foto do proprio lugar, a mais recente com data
    cur.execute("""select p.id from radar_comercial.pois p left join radar_comercial.maps_data m on m.poi_id = p.id
                    where p.id = any(%s) order by coalesce(m.total_avaliacoes, 0) desc, p.id""", (list(ids),))
    for (pid,) in cur.fetchall():
        pub = ia._fotos_do_maps_datadas(cur, pid)[:1]
        if pub:
            b0, d0 = pub[0]
            fotos.append(_jpeg_768(b0))
            rot.append("foto publicada no Google do lugar (%s)" % (pdat.mes_ano(pdat.data_de_texto(d0)) if d0 else "sem data"))
            refs.append({"poi": pid, "tipo": "foto publicada"})
            break
    coments = pdat.comentarios_recentes(con, ids)
    cur.execute("select id, coalesce(cnpj, '') from radar_comercial.pois where id = any(%s)", (list(ids),))
    cnpjs = {c for _i, c in cur.fetchall() if len(c) == 14}
    blocos = []
    if isinstance(leitura_rua, dict) and leitura_rua.get("fachadas") is not None:
        # A PLACA DO VIZINHO SO COM O NOME EM OUTRA FONTE (15/09/2026): a fachada da seta sai da ponta da seta,
        # e a foto em que nada vale para a instalacao leva a marca no rotulo — a checagem tira ela das provas
        texto_rua, vale_rua = fds.para_julgamento(leitura_rua, mira_rua, *fds.fontes_de_nome(cur, ligacao, ids))
        blocos.append(texto_rua)
        if rot and rot[0].startswith("foto de rua"):
            rot[0] = fds.marcar_rotulo(rot[0], vale_rua)
            if fds.aluga_na_seta(leitura_rua, mira_rua):
                rot[0] = "%s · %s" % (rot[0], cv.MARCA_ALUGA_NA_RUA)
    elif _texto_da_leitura(leitura_rua):
        blocos.append(_texto_da_leitura(leitura_rua))
    if coments:
        blocos.append("COMENTÁRIOS RECENTES DE CLIENTES NO GOOGLE (até 2 anos):\n" + "\n".join(
            "#%s %s" % (pid, c) for pid in ids for c in coments.get(pid, [])))
    fichas = _fichas_cnpj_em_texto(cur, cnpjs)
    consulta, texto = _texto_da_busca(cur, ligacao)
    if texto:
        fichas.append("RESULTADO DA BUSCA NA WEB PELO ENDEREÇO%s:\n%s"
                      % ((" (consulta \"%s\")" % consulta) if consulta else "", texto))
    redes = fdb.redes_sociais_no_endereco(cur, ligacao, ids)
    if redes:
        fichas.append("REDES SOCIAIS NO ENDEREÇO (fonte própria, independente da Receita; o código conferiu o endereço e o nome "
                      "do registro no post):\n" + "\n".join(
                          "- %s, registro #%s, %s: %s" % (x["rede"], x["poi"], ("post de %s" % pdat.mes_ano(pdat.data_de_texto(x["data"])))
                                                         if x.get("data") else "sem data do post", x["texto"]) for x in redes))
    if fichas:
        blocos.append("FICHAS E BUSCA, EM TEXTO:\n\n" + "\n\n".join(fichas))
    dados = cabeca + ("\n\n" + "\n\n".join(blocos) if blocos else "") + "\n\nIMAGENS, nesta ordem:\n" + (
        "\n".join("%d. %s" % (i + 1, r) for i, r in enumerate(rot)) or "(nenhuma imagem)")
    return dados, fotos, rot, ids, n_fontes, refs


PROCESSO_SEM_STREET_VIEW = "revisão humana direta (sem cobertura do Street View, 15/09/2026)"
MOTIVO_SEM_STREET_VIEW = ("Sem cobertura do Street View perto do alvo da foto de rua (%s: %s). Por decisão do dono do "
                          "produto em 15/09/2026, a ligação sem panorama perto vai direto para revisão humana, sem "
                          "parecer da IA.")


def sem_street_view(con, ligacao, ids):
    """(alvo, motivo) quando o alvo da foto de rua desta ligacao — o pin do Maps (`poi_da_foto_de_rua`) ou o
    hidrometro — esta em `sem_street_view` (0116); senao None."""
    with con.cursor() as cur:
        escolha = poi_da_foto_de_rua(cur, ligacao, ids) if ids else None
        alvo = "poi:%s" % escolha[0] if escolha else "ligacao:%s" % ligacao
        cur.execute("select motivo from radar_comercial.sem_street_view where alvo = %s", (alvo,))
        x = cur.fetchone()
    return (alvo, x[0]) if x else None


#: A IA SÓ JULGA O QUE FOI MARCADO (dono do produto, 17/09/2026). `--qualificacoes` traz as qualificações que a IA
#: pode julgar, marcadas no processo (validação ou extração); a ligação coletada com outra qualificação vai direto
#: para revisão humana, sem parecer da IA — como a sem Street View. None (sem o argumento) é o de antes: julga todas.
QUALIFICACOES_AUTORIZADAS = None
#: a qualificação de cada ligação do lote, lida uma vez em `rodar`
QUALIFICACAO_DA_LIGACAO = {}
#: a validação de onde veio a marcação (`--validacao`), para o motivo
VALIDACAO = None
#: os rótulos da tela (`validacao.ROTULO_QUALIFICACAO`), preenchidos em `main`
ROTULO_QUALIFICACAO = {}
PROCESSO_IA_NAO_AUTORIZADA = "revisão humana direta (IA não autorizada para a qualificação, 17/09/2026)"
MOTIVO_IA_NAO_AUTORIZADA = ("A IA não foi autorizada a julgar ligações com qualificação %s %s; a marcação está gravada "
                            "%s. Vai para revisão humana sem parecer da IA.")


def uma(poco, ligacao, modelo, placar, trava, aplicar):
    t0 = time.time()
    with poco.pegar() as con:
        dados, fotos, rot, ids, n_fontes, refs = (montar_leve if LEVE else montar_com_refs)(con, ligacao)
    prompt = PROMPT_LEVE if LEVE else PROMPT
    processo = PROCESSO_LEVE if LEVE else PROCESSO
    if dados is None:
        with trava:
            placar["sem_poi"] += 1
        return
    # A IA SÓ JULGA O QUE FOI MARCADO (dono do produto, 17/09/2026): a ligação cuja qualificação não está entre as
    # marcadas no processo vai direto para revisão humana, sem gastar a IA. A percepção guarda a qualificação e as
    # autorizadas: `validacao.ligacoes_para_validar` não conta esta revisão como julgamento, e a validação seguinte
    # com a caixa marcada a julga.
    if QUALIFICACOES_AUTORIZADAS is not None:
        qual = QUALIFICACAO_DA_LIGACAO.get(str(ligacao))
        if qual not in QUALIFICACOES_AUTORIZADAS:
            motivo = MOTIVO_IA_NAO_AUTORIZADA % (
                ROTULO_QUALIFICACAO.get(qual, qual or "(sem qualificação)"),
                ("nesta validação (#%s)" % VALIDACAO) if VALIDACAO else "neste processo",
                "na validação" if VALIDACAO else "no processo")
            r = {"veredito": "revisao_humana", "motivo": motivo, "justificativa": motivo,
                 "aderentes": [], "nao_combinam": [], "pois_de_outro_endereco": []}
            percepcao = {"processo": PROCESSO_IA_NAO_AUTORIZADA, "prioridade": "normal", "dados": dados, "fotos": rot,
                         "fotos_ref": refs, "resposta": r, "ids": ids, "ia_nao_autorizada": qual or "sem qualificação",
                         "qualificacoes_autorizadas": list(QUALIFICACOES_AUTORIZADAS), "validacao": VALIDACAO}
            if aplicar:
                with poco.pegar() as con:
                    al.gravar(con, ligacao, "revisao_humana", r, percepcao,
                              {"pois": len(ids), "fontes": n_fontes, "ids": ids},
                              "nenhum (IA não autorizada)", len(fotos), time.time() - t0)
            with trava:
                placar["revisao_humana"] += 1
                placar["ia_nao_autorizada"] += 1
                al._log("   %-10s %-15s %d POIs · qualificação %s sem autorização para a IA: revisão direta, sem IA"
                        % (ligacao, "revisao_humana", len(ids), qual or "(nenhuma)"))
            return
    if al.PULAR_SEM_IMAGEM and not fotos:
        with trava:
            placar["sem_imagem_fica_para_o_fim"] += 1
        return
    # SEM COBERTURA DO STREET VIEW (dono do produto, 15/09/2026): sem foto de rua porque nao ha panorama perto do
    # alvo, a ligacao vai direto para revisao humana, com prioridade baixa e sem gastar a IA
    if LEVE and not any(str(x).startswith("foto de rua") for x in rot):
        with poco.pegar() as con:
            sem_sv = sem_street_view(con, ligacao, ids)
        if sem_sv:
            motivo = MOTIVO_SEM_STREET_VIEW % sem_sv
            r = {"veredito": "revisao_humana", "motivo": motivo, "justificativa": motivo,
                 "aderentes": [], "nao_combinam": [], "pois_de_outro_endereco": []}
            percepcao = {"processo": PROCESSO_SEM_STREET_VIEW, "prioridade": "baixa", "dados": dados, "fotos": rot,
                         "fotos_ref": refs, "resposta": r, "ids": ids, "sem_street_view": sem_sv[0]}
            if aplicar:
                with poco.pegar() as con:
                    al.gravar(con, ligacao, "revisao_humana", r, percepcao, {"pois": len(ids), "fontes": n_fontes, "ids": ids},
                              "nenhum (sem Street View)", len(fotos), time.time() - t0)
            with trava:
                placar["revisao_humana"] += 1
                placar["sem_street_view_direto"] += 1
                al._log("   %-10s %-15s %d POIs · sem Street View (%s): revisão direta, sem IA"
                        % (ligacao, "revisao_humana", len(ids), sem_sv[0]))
            return
    # O TETO CRESCE COM OS REGISTROS: a lista de aderentes de um predio grande
    # passa dos 900 tokens. A base subiu para 1.600 em 13/09/2026: o motivo
    # deixou de ter limite de frases, e as fotos e a busca ganharam campo.
    # 14/09/2026: com o "por" de cada aderente sem limite, 33 respostas vieram
    # cortadas no meio do JSON (4.683 caracteres com 1.600 + 80 por POI).
    teto = min(16000, 3000 + 200 * len(ids))  # predio de 140 POIs cortava o JSON em 3.000 (12/09/2026)
    # O CONTEXTO DA SPARK E DE 32.768 TOKENS (prompt + resposta): passar disso o vLLM
    # recusa com 400. 1,6 CARACTERE POR TOKEN: medido pelo /tokenize da Spark em
    # 14/09/2026, a lista de registros (densa de CNPJ, telefone e numero) da 1,82 a
    # 1,92, e os predios de 130 a 177 POIs passavam do contexto com 2,8 e com 2,0.
    # A folga de 1.000 cobre o molde do chat. 1.000 por foto.
    teto = max(1500, min(teto, 32768 - int(len(prompt + dados) / 1.6) - 1000 * len(fotos) - 1000))
    try:
        # O TEMPO SEGUE O TAMANHO DA RESPOSTA (14/09/2026): com 120 julgamentos ao
        # mesmo tempo a Spark gera ~3 tokens/s para cada um, e com o motivo sem
        # limite o predio passava dos 40 s por POI e morria em "timed out".
        r = di._chat_local(modelo, prompt + dados, [base64.b64encode(b).decode() for b in fotos],
                           max_tokens=teto, timeout=max(al.TIMEOUT, min(7200, max(40 * len(ids), int(teto / 2.5)))))
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha"] += 1
            al._log("   %-10s FALHOU: %s" % (ligacao, str(e)[:70]))
        return
    r = dict(r or {})
    v = str(r.get("veredito") or "").strip().lower()
    if v not in VEREDITOS:
        with trava:
            placar["fora_da_escala"] += 1
        v = "reprovado"
    # QUEM NAO COMBINA vira "de outro endereco": o descarte e o revinculo sao os
    # mesmos do processo anterior (`marcar_intrusos`, que nunca descarta todos).
    r["pois_de_outro_endereco"] = [{"poi": x.get("poi"), "porque": x.get("por")}
                                   for x in (r.get("nao_combinam") or []) if isinstance(x, dict)]
    r["justificativa"] = r.get("motivo")
    # A CHECAGEM DO CODIGO (dono do produto, 12/09/2026): registro que nao vale
    # nao aprova, e a aprovada so por MEI vai para revisao humana. A regra de
    # um POI por instalacao roda no fim da rodada, sobre todas as aprovadas.
    checagem = None
    if v in ("aprovado", "revisao_humana"):
        with poco.pegar() as con:
            v, checagem = cv.checar_uma(con, ligacao, v, r, ids, processo=processo, fotos=rot)
        if checagem:
            checagem["justificativa_ia"] = r.get("justificativa")
            if v != "aprovado":
                r["justificativa"] = "[checagem: %s] %s" % (checagem.get("porque"), r.get("motivo") or "")
    # PRIORIDADE BAIXA (15/09/2026): revisao so pela Receita e sem sinal nas imagens vai para o fim da fila da SEEK
    prioridade = None
    if LEVE and v == "revisao_humana":
        fs = cv.fontes_confirmadas(r, rot)
        prioridade = "baixa" if set(fs) <= {"receita"} and not cv.imagem_tem_sinal(r) else "normal"
    # A CLASSE DO CASO (dono do produto, 16/09/2026): o ramo do negócio e o que a IA viu nas imagens, gravados
    # no veredito para a SEEK filtrar por eles.
    classe = None
    try:
        import classificacao as cl
        with poco.pegar() as con:
            classe = cl.classificar(con, ligacao, r, rot, (checagem or {}).get("validos") or ids)
    except Exception as e:                                     # noqa: BLE001
        al._log("   %-10s classe FALHOU: %s" % (ligacao, str(e)[:60]))
    percepcao = {"processo": processo, "prioridade": prioridade, "dados": dados, "fotos": rot, "fotos_ref": refs, "resposta": r,
                 "ids": ids, "classe": classe}
    if checagem:
        percepcao["checagem"] = checagem
    resumo = {"pois": len(ids), "fontes": n_fontes, "ids": ids}
    fora = 0
    if SAIDA:
        with trava:
            with open(os.path.join(SAIDA, "resultado.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"ligacao": ligacao, "veredito": v, "resposta": r, "dados": dados,
                                    "fotos": rot, "fotos_ref": refs, "segundos": round(time.time() - t0, 1)},
                                   ensure_ascii=False) + "\n")
            for i, b in enumerate(fotos):
                open(os.path.join(SAIDA, "%s_%d.jpg" % (ligacao, i + 1)), "wb").write(b)
    if aplicar:
        with poco.pegar() as con:
            al.gravar(con, ligacao, v, r, percepcao, resumo, modelo, len(fotos), time.time() - t0)
            fora = al.marcar_intrusos(con, ligacao, r, set(ids), modelo)
    with trava:
        placar[v] += 1
        placar["poi_de_outro_endereco"] += fora
        al._log("   %-10s %-15s %d POIs · %d fotos · %.0fs · %s"
                % (ligacao, v, len(ids), len(fotos), time.time() - t0, str(r.get("motivo") or "")[:70]))


def feitas_na_rodada(placar):
    return sum(placar[v] for v in VEREDITOS) > 0


def rodar(limite, aplicar, trabalhadores, modelo, ligacoes, cidade, exigir_busca, vinculo_novo,
          adiar_grandes=0, so_grandes=0, julgar_sem_foto=False):
    # JULGAR SEM FOTO (dono do produto, 14/09/2026): a ligacao cujo registro mais
    # perto fica a mais de `dossie_ligacao.RAIO_DA_FOTO_M` do hidrometro nunca tera
    # foto no dossie — capturar a foto do POI nao resolve. Com a flag, ela e julgada
    # so com registros e busca, em vez de ficar para o fim para sempre.
    al.PULAR_SEM_IMAGEM = bool(exigir_busca) and not julgar_sem_foto
    con = bc.conectar()
    alvos = al.fila(con, 0 if so_grandes else limite, False, ligacoes, sem_catalogo=True,
                    exigir_busca=exigir_busca, vinculo_novo=vinculo_novo, cidade=cidade,
                    adiar_grandes=adiar_grandes)
    if so_grandes:
        # O LACO DOS PREDIOS: so as ligacoes com mais de `so_grandes` POIs.
        cur = con.cursor()
        cur.execute("""select lp.ligacao, count(*) from radar_comercial.ligacao_poi lp
                         join radar_comercial.pois p on p.id = lp.poi_id
                        where lp.ligacao = any(%s) and lp.descartado_em is null and p.fundido_em is null
                        group by 1 having count(*) > %s""", ([str(x) for x in alvos], so_grandes))
        grandes_ = {str(r[0]) for r in cur.fetchall()}
        alvos = [x for x in alvos if str(x) in grandes_]
        if limite:
            alvos = alvos[:limite]
    con.close()
    al._log("▶ avaliação ENXUTA — " + ("LEVE: foto de rua de frente e foto do Google em 768 px, fichas e busca "
                                        "em texto, comentários, saída curta" if LEVE else
                                        "até 5 fotos, a página da busca em texto, prompt curto"))
    al._log("   %d ligação(ões) na fila" % len(alvos))
    if not alvos:
        return {"alvos": 0}
    placar = collections.Counter()
    trava = threading.Lock()
    poco = al.Poco(al.CONEXOES)
    fila_ = list(alvos)
    trava_fila = threading.Lock()
    # QUANTOS POIS CADA UMA TEM, de uma vez: e o que separa a grande da pequena.
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""select lp.ligacao, count(*) from radar_comercial.ligacao_poi lp
                     join radar_comercial.pois p on p.id = lp.poi_id
                    where lp.ligacao = any(%s) and lp.descartado_em is null and p.fundido_em is null
                    group by 1""", ([str(x) for x in fila_],))
    npoi = {str(l): n for l, n in cur.fetchall()}
    if QUALIFICACOES_AUTORIZADAS is not None:
        # A QUALIFICAÇÃO DAS LIGAÇÕES DO LOTE, de uma vez (17/09/2026): `uma()` decide por ela se a IA julga
        cur.execute("""select num_ligacao::text, qualificacao from resources_root.cadastro_corsan
                        where num_ligacao = any(%s::bigint[])""", ([int(x) for x in fila_ if str(x).isdigit()],))
        QUALIFICACAO_DA_LIGACAO.update({str(l): q for l, q in cur.fetchall()})
        al._log("   a IA pode julgar: %s · %d da fila com outra qualificação vão para revisão humana sem IA"
                % (", ".join(QUALIFICACOES_AUTORIZADAS),
                   sum(1 for x in fila_ if QUALIFICACAO_DA_LIGACAO.get(str(x)) not in QUALIFICACOES_AUTORIZADAS)))
    con.close()
    grandes = [0]
    t0 = time.time()

    def trabalhador():
        while True:
            with trava_fila:
                if not fila_:
                    return
                i = 0
                if grandes[0] >= MAX_GRANDES:
                    i = next((k for k, x in enumerate(fila_) if npoi.get(str(x), 0) <= al.GRANDE), 0)
                lig = fila_.pop(i)
                grande = npoi.get(str(lig), 0) > al.GRANDE
                if grande:
                    grandes[0] += 1
            try:
                uma(poco, lig, modelo, placar, trava, aplicar)
            except Exception as e:                             # noqa: BLE001
                with trava:
                    placar["erro"] += 1
                    al._log("   %-10s ERRO: %s: %s" % (lig, type(e).__name__, str(e)[:80]))
            finally:
                if grande:
                    with trava_fila:
                        grandes[0] -= 1

    ts = [threading.Thread(target=trabalhador, daemon=True) for _ in range(max(1, trabalhadores))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    dt = time.time() - t0
    if aplicar and feitas_na_rodada(placar):
        # UM POI, UMA INSTALACAO: precisa de todas as aprovadas, entao roda aqui.
        con = bc.conectar()
        try:
            # SO AS DA FILA (16/09/2026): o rejulgamento das SIM em analise nao mexe nas aprovadas e reprovadas de fora
            cv.revisar(con, aplicar=True, log=al._log,
                       so_ligacoes={str(x) for x in alvos} if CHECAGEM_SO_DA_FILA else None)
        finally:
            con.close()
    al._log("")
    for k, n in sorted(placar.items()):
        al._log("   %-26s %d" % (k, n))
    feitas = sum(placar[v] for v in VEREDITOS)
    al._log("   %d ligação(ões) em %.1f min · %.1f por minuto" % (feitas, dt / 60, feitas * 60 / max(dt, 1)))
    return dict(placar)


def ligacoes_do_prompt_antigo(cidade, limite=0):
    """As ligacoes com veredito de OUTRO processo que nao `PROCESSO`, na cidade.
    Dois conjuntos cruzados em Python, e nao `exists` por linha."""
    con = bc.conectar()
    try:
        cur = con.cursor()
        cur.execute("""select ligacao, id_empresa from radar_comercial.ligacao_veredito
                        where coalesce(percepcao::jsonb->>'processo', '') <> %s""", (PROCESSO_LEVE if LEVE else PROCESSO,))
        velhas = {}
        for l, emp in cur.fetchall():
            velhas.setdefault(str(emp), set()).add(str(l))
        ligs = []
        for emp, conj in velhas.items():
            cur.execute("""select num_ligacao::text from resources_root.cadastro_corsan
                            where id_empresa = %s and num_ligacao = any(%s::bigint[])"""
                        + (" and upper(cidade) = upper(%s)" if cidade else ""),
                        (emp, [int(x) for x in conj if x.isdigit()]) + ((cidade,) if cidade else ()))
            ligs += [r[0] for r in cur.fetchall()]
    finally:
        con.close()
    ligs.sort(key=int)
    al._log("   %d ligação(ões) julgadas por outro processo%s" % (len(ligs), " em " + cidade if cidade else ""))
    return ligs[:limite] if limite else ligs


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=80)
    p.add_argument("--modelo", default=al.MODELO_PADRAO)
    p.add_argument("--ligacao", action="append")
    p.add_argument("--cidade", default=None)
    p.add_argument("--exigir-busca-web", dest="exigir_busca", action="store_true")
    p.add_argument("--vinculo-novo", dest="vinculo_novo", action="store_true")
    p.add_argument("--saida", default=None, help="pasta para gravar cada julgamento e as fotos")
    p.add_argument("--adiar-grandes", dest="adiar_grandes", type=int, default=0,
                   help="a fila tira as ligacoes com mais de N POIs (elas vao para o laco dos predios)")
    p.add_argument("--so-grandes", dest="so_grandes", type=int, default=0,
                   help="o laco dos predios: so as ligacoes com mais de N POIs")
    p.add_argument("--ligacoes-arquivo", dest="ligacoes_arquivo", default=None,
                   help="arquivo com uma ligacao por linha (a reavaliacao das 22 mil nao cabe na linha de comando)")
    p.add_argument("--julgar-sem-foto", dest="julgar_sem_foto", action="store_true",
                   help="julga tambem a ligacao sem foto a ate 60 m do hidrometro, so com registros e busca")
    p.add_argument("--prompt-antigo", dest="prompt_antigo", action="store_true",
                   help="rejulga as ligacoes (da --cidade) cujo veredito veio de outro processo que nao o atual")
    p.add_argument("--listar-fila", dest="listar_fila", default=None,
                   help="so escreve a fila (a mesma que o julgamento pegaria) neste arquivo e sai — o lote do laco "
                        "recaptura ficha, fotos, foto de rua e ficha do CNPJ dessas ligacoes antes de julgar")
    p.add_argument("--leve", action="store_true",
                   help="o processo leve de 15/09/2026: foto de rua de frente, fichas em texto, comentarios, saida curta")
    p.add_argument("--checagem-so-da-fila", dest="checagem_so_da_fila", action="store_true",
                   help="a checagem do fim da rodada so grava as ligacoes desta fila (rejulgamento de um recorte)")
    p.add_argument("--qualificacoes", default=None,
                   help="as qualificações que a IA pode julgar, separadas por vírgula (a marcação do processo, "
                        "17/09/2026); a ligação de outra vai direto para revisão humana. Sem ela, a IA julga todas")
    p.add_argument("--validacao", type=int, default=None,
                   help="a validação de onde veio a marcação (vai no motivo da revisão direta)")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    global SAIDA, LEVE, CHECAGEM_SO_DA_FILA
    global QUALIFICACOES_AUTORIZADAS, VALIDACAO
    if a.qualificacoes is not None:
        import validacao as va
        try:
            QUALIFICACOES_AUTORIZADAS = va.ler_qualificacoes(a.qualificacoes)
        except ValueError as e:
            p.error(str(e))
        ROTULO_QUALIFICACAO.update(va.ROTULO_QUALIFICACAO)
    VALIDACAO = a.validacao
    LEVE = a.leve
    CHECAGEM_SO_DA_FILA = a.checagem_so_da_fila
    if a.saida:
        os.makedirs(a.saida, exist_ok=True)
        SAIDA = a.saida
    if a.listar_fila:
        con = bc.conectar()
        try:
            alvos = al.fila(con, a.limite, False, None, sem_catalogo=True, exigir_busca=a.exigir_busca,
                            vinculo_novo=a.vinculo_novo, cidade=a.cidade, adiar_grandes=a.adiar_grandes)
        finally:
            con.close()
        with open(a.listar_fila, "w") as f:
            f.write("".join("%s\n" % x for x in alvos))
        al._log("   fila: %d ligação(ões) em %s" % (len(alvos), a.listar_fila))
        return 0
    ligs = list(a.ligacao or [])
    if a.ligacoes_arquivo:
        ligs += [x.strip() for x in open(a.ligacoes_arquivo) if x.strip()]
    if a.prompt_antigo:
        ligs += ligacoes_do_prompt_antigo(a.cidade, a.limite)
        if not ligs:
            al._log("nenhuma ligação julgada por outro processo")
            return 0
    r = rodar(a.limite, a.aplicar, a.trabalhadores, a.modelo, ligs or None, a.cidade, a.exigir_busca,
              a.vinculo_novo, a.adiar_grandes, a.so_grandes, a.julgar_sem_foto)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
