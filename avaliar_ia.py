# -*- coding: utf-8 -*-
"""avaliar_ia.py — a IA da Spark julga o POI a partir da evidência capturada.

O QUE ESTE MÓDULO DECIDE, E O QUE ELE NÃO DECIDE

Ele responde uma pergunta só, e ela é comercial: **este imóvel, que a Corsan
fatura como RESIDENCIAL, tem atividade econômica?** Não descobre POI, não
corrige endereço, não mexe em vínculo. Entra com as imagens que o
`capturar_evidencia.py` e o `capturar_pagina.py` gravaram em `poi_evidencia`,
sai com uma linha em `poi_veredito`.

DUAS CHAMADAS, E ELAS NÃO SE MISTURAM — é o padrão que tornou o veredito visual
confiável neste projeto e a razão dele é medida, não teórica:

    1 · PERCEPÇÃO CEGA   só as imagens. O modelo não vê nome, categoria,
                         endereço nem fonte. Se vir, papagaia: pedir "descreva"
                         junto com "o cadastro diz padaria" faz a descrição
                         nascer com padaria dentro, e aí o julgamento está
                         confirmando a si mesmo.
    2 · JULGAMENTO       só texto: a descrição da etapa 1 mais o que o cadastro
                         afirma. Sem imagem. O modelo compara duas afirmações
                         em vez de olhar e opinar ao mesmo tempo.

QUATRO VEREDITOS, e a escala é do dono do produto:

    aprovado_exato       o estabelecimento do cadastro está ali, identificado
    aprovado_comercial   não é aquele, mas o imóvel tem comércio visível
    revisao_humana       há indício, não há prova
    reprovado            nenhuma atividade econômica relevante

DUAS PERGUNTAS DE CLASSIFICAÇÃO, SEPARADAS — também decisão do dono do produto.
A ESPÉCIE do CNEFE (1 a 8) diz o que é a EDIFICAÇÃO; a SEÇÃO da CNAE (A a U)
diz qual é a ATIVIDADE. São eixos diferentes e uma não deriva da outra.

O AIRBNB TEM PROMPT PRÓPRIO. Ele não vai ao Street View — não há endereço exato
para mirar —, e o que prova atividade num anúncio de hospedagem é a página:
calendário com datas, avaliações datadas, anfitrião ativo. A escala de
veredito é a MESMA para não criar duas réguas.

Uso:
    python avaliar_ia.py --area area_atual --limite 10        # ensaio
    python avaliar_ia.py --area area_atual --aplicar
    python avaliar_ia.py --poi 87876 --aplicar --refazer
"""
from __future__ import annotations

import argparse
import base64
import json
import threading
import time

import area_utils
import base_comum as bc

# O CLIENTE DA SPARK É O QUE JÁ EXISTE. `descrever_imagens._chat_local` fala o
# protocolo da OpenAI, lê `VLLM_URL` do ambiente e já carrega a nota sobre por
# que `num_ctx` sumiu. Reescrevê-lo aqui criaria dois clientes para manter.
import descrever_imagens as di
import imagens

# O MODELO É O QUE A SPARK SERVE HOJE, e o nome vem do `.env` — não do padrão
# histórico do `descrever_imagens`, que ainda aponta para `qwen3vl-moe`. Medido
# em 04/09/2026: `GET /v1/models` devolve `ia-principal` e
# `qwen3.5-35b-a3b`, ambos com raiz `Qwen/Qwen3.5-35B-A3B-FP8` e janela de
# 131.072; `qwen3vl-moe` não está mais no ar. Pedir um modelo que não existe dá
# 404 em toda chamada, e o placar viria zerado sem dizer por quê.
#
# E ELE ENXERGA: sondado no mesmo dia com uma `sv_frente` real, respondeu em
# 1,7 s descrevendo a casa, o telhado e as próprias marcações verdes que o
# `capturar_evidencia` desenha — 577 tokens de prompt para uma imagem de
# 934×621, o que quer dizer que o servidor reamostra antes de olhar.
import os
MODELO_PADRAO = (os.environ.get("SPARK_MODELO")
                 or os.environ.get("MODELO_VISAO") or "ia-principal")
TIMEOUT = 480

# A ORDEM DAS IMAGENS É A ORDEM DA LEITURA, e ela não é arbitrária: o satélite
# primeiro dá o enquadramento (onde fica, quantas construções), a fachada
# depois responde a pergunta, o fundo por último dá o contexto do quarteirão.
ORDEM_RUA = ["sv_frente", "sv_lado_a", "sv_fundo", "sv_lado_b"]
ORDEM_PAGINA = ["pagina_airbnb"]

VEREDITOS = ("aprovado_exato", "aprovado_comercial", "revisao_humana", "reprovado")

ESPECIES = """1 domicílio particular · 2 domicílio coletivo (pensão, hotel, \
alojamento) · 3 estabelecimento agropecuário · 4 estabelecimento de ensino · \
5 estabelecimento de saúde · 6 estabelecimento de outras finalidades (comércio, \
serviço, indústria, escritório) · 7 edificação em construção ou reforma · \
8 estabelecimento religioso"""


def _log(m):
    print(m, flush=True)


# ── 1 · a percepção cega ───────────────────────────────────────────────────
#
# NENHUM DADO DO CADASTRO ENTRA AQUI. Nem o nome, nem a categoria, nem a rua.
# O que o modelo devolve tem de poder ser conferido só olhando as imagens.
# ── a percepção: UMA chamada, as QUATRO visadas ────────────────────────────
#
# ISTO JÁ FOI UMA CHAMADA POR IMAGEM, e voltou a ser uma só porque A PERGUNTA
# MUDOU. Antes se pedia "descreva a fachada sob a mira", e aí a foto do outro
# lado da rua era contaminação pura: o modelo atribuía ao alvo a loja do
# vizinho (POI 99207, "Black Style"). Agora se pede ACHAR O ESTABELECIMENTO,
# apareça ele em qual visada aparecer — e para isso o modelo precisa ver as
# quatro juntas, senão não há como dizer "está na esquina à direita".
#
# O QUE SUBSTITUI A SEPARAÇÃO FÍSICA é a resposta ser POR VISADA: cada bloco
# diz o que há NAQUELA foto, e um campo à parte diz em qual delas o alvo foi
# encontrado. Assim o julgamento continua sabendo distinguir "na frente, sob a
# mira" de "do outro lado da rua" — que é a distinção que decide o veredito.
#
# DUAS CHAMADAS POR POI, e não quatro: percepção e julgamento. É o que o dono
# do produto pediu em 04/09/2026, e o custo cai de ~16 s para ~9 s por POI.

VISTA_ROTULO = {
    "sv_frente": "FRENTE - a câmera encara a coordenada. Uma mira verde aberta "
                 "marca o imóvel do endereço.",
    "sv_lado_a": "LADO DIREITO - a mesma câmera girada 90°.",
    "sv_fundo": "ATRÁS - a mesma câmera girada 180°, o outro lado da rua.",
    "sv_lado_b": "LADO ESQUERDO - a mesma câmera girada 270°.",
}

IGNORAR = """IGNORE, e nunca transcreva como letreiro: a marca d'água do \
Google, placas de trânsito e nomes de rua. A mira verde foi desenhada por nós \
sobre a foto para apontar o imóvel do endereço — ela não existe no local."""

PROMPT_QUATRO = """Você recebe %(n)d fotos de rua do MESMO ponto, tiradas do \
mesmo lugar girando a câmera. Nesta ordem:

%(lista)s

SUA TAREFA É ACHAR ESTABELECIMENTO — comércio, serviço, oficina, igreja, \
escola, depósito, qualquer atividade que não seja só moradia. Ele pode estar em \
QUALQUER uma das fotos, e não só na da mira.

Descreva o que vê. Não julgue, não conclua, e não invente nome que não esteja \
escrito. %(ignorar)s

REGRAS DE LEITURA
- Transcreva letreiro, placa, toldo, faixa e adesivo EXATAMENTE como estão \
escritos. Ilegível é "ilegível" — não complete.
- Diga SEMPRE em qual foto viu cada coisa. É a única forma de separar o imóvel \
do endereço dos vizinhos.
- Um mesmo prédio pode ter mais de um estabelecimento, inclusive nos andares \
de cima.
- MEDIDORES: conte as caixas de medidor de energia ou água NO IMÓVEL DA MIRA \
(foto 1). Se não der para contar, use null — nunca zero por desencargo.

Responda SOMENTE um JSON:
{
 "imovel_da_mira": {
   "tipo": "casa|sobrado|predio|loja_terrea|galpao|terreno_vago|em_obra|indefinido",
   "andares": <int|null>, "medidores": <int|null>,
   "vitrine": true|false, "porta_comercial": true|false, "toldo": true|false,
   "letreiros": ["<texto lido NO IMÓVEL DA MIRA>", ...],
   "conservacao": "conservado_habitado|demolido_ou_nao_construido|\
mal_conservado_habitado|mal_conservado_desabitado|indefinido",
   "descricao": "<até 40 palavras>"},
 "estabelecimentos": [
   {"nome": "<lido, ou null>", "ramo_aparente": "<o que parece ser>",
    "onde": "frente|lado_direito|atras|lado_esquerdo",
    "no_imovel_da_mira": true|false,
    "evidencia": "<o que se vê: letreiro, vitrine, mercadoria, cliente>"}],
 "numeros_visiveis": ["<número de porta lido, e em qual foto>", ...],
 "carater_do_quarteirao": "residencial|misto|comercial|industrial|indefinido",
 "achou_estabelecimento": true|false
}"""


PROMPT_PAGINA = """Você recebe UMA imagem: a página inteira de um anúncio de \
hospedagem, capturada de cima a baixo.

DESCREVA O QUE ESTÁ NA PÁGINA. Não julgue e não invente o que não estiver \
escrito. Onde não houver o dado, use null.

Responda SOMENTE um JSON com estas chaves:
{
 "titulo": "<título do anúncio>",
 "tipo": "<espaço inteiro, quarto, etc., como está escrito>",
 "cidade": "<cidade que a página informa>",
 "nota": <número|null>, "avaliacoes": <int|null>,
 "avaliacao_mais_recente": "<mês e ano da avaliação mais recente visível|null>",
 "calendario_visivel": true|false,
 "mes_do_calendario": "<mês e ano mostrados no calendário|null>",
 "datas_disponiveis": true|false|null,
 "anfitriao": "<nome>", "anos_hospedando": <int|null>, "superhost": true|false,
 "quartos": <int|null>, "hospedes": <int|null>,
 "mapa_visivel": true|false,
 "descricao": "<até 45 palavras do que o anúncio oferece>"
}"""


# ── 2 · o julgamento, sem imagem ───────────────────────────────────────────
PROMPT_JULGAR = """Você decide se um imóvel tem ATIVIDADE ECONÔMICA.

O contexto: a companhia de água fatura este imóvel como RESIDENCIAL, mas alguma \
base indica que existe um estabelecimento nele. Se houver comércio, a ligação \
está com a tarifa errada — é isso que se procura.

Você recebe (a) a DESCRIÇÃO das imagens, feita por outro observador que NÃO \
sabia nada do cadastro, e (b) o que o CADASTRO afirma. Compare as duas coisas.

DESCRIÇÃO DAS IMAGENS:
%(percepcao)s

O QUE O CADASTRO AFIRMA:
%(cadastro)s

ESCOLHA UM VEREDITO:
- "aprovado_exato": a descrição identifica O estabelecimento do cadastro — o \
letreiro traz o nome, ou o ramo visto é inequivocamente o mesmo do cadastro.
- "aprovado_comercial": não dá para dizer que é AQUELE, mas o imóvel tem \
atividade comercial visível (vitrine, letreiro de outro negócio, mercadoria, \
porta de loja). A visita se justifica do mesmo jeito.
- "revisao_humana": há indício e não há prova. Ex.: porta que pode ser de loja \
mas está fechada; quarteirão comercial e imóvel ambíguo; imagem antiga demais \
para o que o cadastro afirma; mais de um medidor numa casa aparentemente comum.
- "reprovado": nada indica atividade econômica. Casa residencial sem qualquer \
sinal, terreno vago, obra.

REGRAS QUE NÃO SE NEGOCIAM
- A DESCRIÇÃO VEM DAS QUATRO VISADAS, e cada estabelecimento traz "onde" foi \
visto e se está "no_imovel_da_mira". Só aprova como "aprovado_exato" ou \
"aprovado_comercial" o que está NO IMÓVEL DA MIRA. Estabelecimento visto na \
lateral ou atrás informa o caráter do quarteirão e mais nada.
- Comércio do LADO OPOSTO da rua NÃO aprova o imóvel. Ele só informa o caráter \
do quarteirão, e caráter de quarteirão sozinho é, no máximo, revisao_humana. \
Tudo que estiver em "lado_oposto" — inclusive "letreiros_do_outro_lado" — é da \
calçada de frente, não do imóvel avaliado: se o único letreiro do conjunto \
estiver ali, o imóvel continua sem letreiro.
- Só aprova o que estiver em "fachada".
- Nome parecido não é nome igual. "Silva Alimentos" não confirma "Mercado Silva".
- Ausência de letreiro não reprova por si só: muitos negócios de bairro operam \
sem fachada. O que reprova é a ausência de QUALQUER sinal.

OS TRÊS ENDEREÇOS TÊM DE SER O MESMO LUGAR, e conferir isso é parte do seu
trabalho. Você recebe (a) o endereço do estabelecimento, (b) a coordenada de \
onde as fotos foram tiradas e (c) o endereço e a coordenada de cada ligação de \
água. Se a rua do estabelecimento não for a rua da ligação, ou se a ligação \
estiver a centenas de metros do ponto fotografado, então as fotos podem ser de \
OUTRO IMÓVEL — e nesse caso nada do que se vê nelas serve para aprovar nem para \
reprovar este cadastro.

Quando perceber essa divergência: escolha "revisao_humana" e DIGA na \
justificativa qual é a discordância, com os números. Não aprove por uma foto \
que pode ser de outro lugar, e não reprove um comércio por não aparecer numa \
foto que talvez nem seja dele. Distância de algumas dezenas de metros é normal \
— a ligação fica na calçada e o ponto no meio do lote; o que acusa é centena \
de metros, ou rua com nome diferente.

PROVA DE PLATAFORMA VENCE FACHADA MUDA. Se o cadastro disser que a loja \
estava ATIVA numa plataforma (iFood disponível, anúncio de hospedagem com \
avaliação recente, AVALIAÇÃO DE CLIENTE NO GOOGLE), isso é prova de atividade \
econômica FUNCIONANDO, com data. \
Fachada sem vitrine não a desmente: delivery de comida, doceria, marmita e \
salão de casa operam sem porta de loja — é o negócio mais comum do bairro. \
Nesse caso o piso é "aprovado_comercial"; use "revisao_humana" só se a foto \
contradisser a plataforma (terreno vago, imóvel demolido, obra). NUNCA \
reprove um estabelecimento que a plataforma dá como ativo.

AVALIAÇÃO DE CLIENTE NO GOOGLE É PROVA DE TERCEIRO, e você recebe a data de \
cada uma. Quem escreveu esteve lá: "levei meu carro", "cortei o cabelo com \
ele", "encomendei o bolo" descrevem um serviço prestado NAQUELE endereço, e \
descrevem melhor do que a fachada, porque a fachada não muda quando o negócio \
fecha e a avaliação para de aparecer.

Leia as avaliações e pese pela DATA e pelo que elas contam:
- avaliação de até um ano descrevendo serviço prestado: o piso é \
"aprovado_comercial", mesmo que a casa não tenha vitrine, letreiro nem toldo. \
Barbeiro, manicure, doceira, costureira e oficina de fundo de quintal são o \
negócio mais comum do bairro e nenhum deles põe placa.
- avaliação entre um e três anos: sustenta "revisao_humana", não reprovação. \
O negócio existiu ali; falta saber se ainda opera.
- avaliação com mais de três anos e nada mais: não sustenta nada sozinha. \
Decida pela imagem.
- horário de funcionamento declarado no Google (por exemplo "Abre seg. às \
08:00") é o dono dizendo que atende — vale como indício, não como prova.

E NÃO INVERTA A REGRA: ausência de avaliação não reprova nada. A maior parte \
dos negócios de bairro não tem uma linha escrita sobre eles.

A FOTO DE RUA TEM IDADE, e o cadastro diz qual. O Google não refotografa a \
cidade todo ano: um terço das fachadas deste projeto é de 2024. Pese assim:
- foto do último ano: o que ela mostra vale como está.
- foto de dois anos ou mais CONTRA uma avaliação recente: acredite na \
avaliação. A fachada não muda quando o negócio abre; a casa da foto pode já \
ter virado loja. Nesse caso o piso é "revisao_humana", nunca "reprovado".
- foto de dois anos ou mais e nada mais: decida pela foto, mas diga a idade \
dela na justificativa, para quem for à porta saber o que esperar.

QUANDO REPROVAR DIRETO, SEM PASSAR POR REVISÃO. "revisao_humana" é para dúvida \
REAL, e não para desconforto de decidir. Se a descrição das quatro fotos diz \
casa residencial, sem vitrine, sem porta comercial, sem toldo, sem letreiro, \
sem mercadoria e sem movimento — não há dúvida a resolver, e mandar isso para \
uma pessoa é gastar o olhar dela com o que a foto já respondeu. REPROVE.

Só use "revisao_humana" quando houver um sinal CONCRETO E AMBÍGUO que você \
possa nomear: porta que pode ser de loja e está fechada; toldo sem letreiro; \
mercadoria empilhada no pátio; dois ou mais medidores numa casa aparentemente \
comum; imagem antiga demais para o que o cadastro afirma. Se você não consegue \
escrever qual é o sinal ambíguo, não é revisão — é reprovado.

CLASSIFIQUE TAMBÉM, e são duas perguntas independentes:
- ESPÉCIE DA EDIFICAÇÃO (código do CNEFE): %(especies)s
- SEÇÃO DA ATIVIDADE (letra da CNAE): %(secoes)s
  Use null na seção quando o veredito for "reprovado" ou quando não houver \
atividade identificável.

Responda SOMENTE um JSON:
{"veredito": "<um dos quatro>", "especie_cnefe": <1-8|null>,
 "secao_cnae": "<letra|null>", "medidores": <int|null>,
 "justificativa": "<um parágrafo, até 60 palavras, dizendo o que na descrição \
sustenta o veredito. Cite o que foi visto, não o que se supõe.>"}"""


PROMPT_JULGAR_HOSPEDAGEM = """Você decide se um imóvel tem ATIVIDADE ECONÔMICA.

O contexto: a companhia de água fatura este imóvel como RESIDENCIAL, e ele está \
anunciado como hospedagem por temporada. Hospedagem remunerada É atividade \
econômica — um apartamento alugado por diária consome como comércio, não como \
moradia.

NÃO HÁ FOTO DA FACHADA, e isso é do desenho: a plataforma não publica o \
endereço exato. A prova aqui é a PRÓPRIA PÁGINA — o anúncio no ar, o calendário \
respondendo, as avaliações datadas.

DESCRIÇÃO DA PÁGINA:
%(percepcao)s

O QUE O CADASTRO AFIRMA:
%(cadastro)s

ESCOLHA UM VEREDITO, na mesma escala dos demais:
- "aprovado_exato": o anúncio está ativo E tem prova de uso recente — avaliação \
nos últimos 12 meses, ou calendário com datas disponíveis e anfitrião ativo.
- "aprovado_comercial": o anúncio existe e é de hospedagem remunerada, mas sem \
sinal de uso recente (sem avaliação datada, sem calendário legível).
- "revisao_humana": a página não deixa claro se é hospedagem remunerada, ou os \
dados estão incompletos a ponto de não sustentar conclusão.
- "reprovado": a página não é de hospedagem, ou o anúncio está claramente \
desativado.

CLASSIFICAÇÃO: hospedagem é espécie 2 do CNEFE (domicílio coletivo) quando o \
imóvel é operado como hospedagem, e a seção da CNAE é I (alojamento e \
alimentação). Use esses valores salvo se a página disser outra coisa.

Responda SOMENTE um JSON:
{"veredito": "<um dos quatro>", "especie_cnefe": <1-8|null>,
 "secao_cnae": "<letra|null>", "medidores": null,
 "justificativa": "<um parágrafo, até 60 palavras, citando o que na página \
sustenta o veredito — data da avaliação, mês do calendário, nota.>"}"""


# ── quem entra ─────────────────────────────────────────────────────────────
# A FILA E ESCRITA EM `exists`, E NAO EM `join`. A primeira versao juntava
# `pois` com `ligacao_poi`, `cadastro_corsan`, `categoria_catalogo` e
# `poi_evidencia` e colapsava tudo com `distinct`. Funcionava enquanto havia o
# `not exists (poi_veredito)` no fim — sem perceber, era ELE que podava a
# consulta cedo.
#
# Com `--refazer` esse filtro sai, e a mesma consulta ficou 4 MINUTOS parada em
# 04/09/2026, sem devolver linha nenhuma: o produto do join sobre 301 mil POIs
# e 65 mil vinculos e grande, e o `distinct` so limpa DEPOIS de montar tudo.
#
# Em `exists` o resultado e identico — a chave continua sendo `p.id` —, mas o
# planejador pode partir das 431 linhas de `poi_evidencia`, que e o lado
# pequeno, e cada condicao vira um teste de existencia que para no primeiro
# acerto. Nenhuma linha e duplicada para ser removida depois.
SQL_ALVO = """
    select p.id, coalesce(p.nome,''), coalesce(p.fonte,''),
           coalesce(p.categoria,''), coalesce(p.endereco,''),
           coalesce(p.cidade,''), coalesce(p.uf,''),
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry)
      from radar_comercial.pois p
     where (exists (select 1 from radar_comercial.poi_evidencia e
                     where e.poi_id = p.id and e.dados is not null)
            -- A FILA SEGUE A LEITURA. `evidencia()` cai para
            -- `streetview_imgs` quando nao ha `poi_evidencia`; exigir so a
            -- primeira aqui deixaria de fora justamente os POIs que a captura
            -- de fachada ja cobriu — 13.736 em Canoas quando isto foi escrito.
            or exists (select 1 from radar_comercial.streetview_imgs s
                        where s.poi_id = p.id and s.storage_path is not null)
            -- iFOOD E AIRBNB ENTRAM SEM FOTO. Ver
            -- `FONTES_QUE_DISPENSAM_IMAGEM`: neles quem prova e a ficha da
            -- plataforma, e 1.040 pontos ja provados estavam parados aqui
            -- esperando uma fotografia que nao mudaria o veredito.
            or p.fonte in ('ifood', 'airbnb'))
       and exists (select 1 from radar_comercial.categoria_catalogo cc
                    where cc.fonte = p.fonte
                      and cc.valor = btrim(p.categoria) and cc.avaliar)
       and exists (select 1
                     from radar_comercial.ligacao_poi lp
                     join resources_root.cadastro_corsan l
                          on l.num_ligacao::text = lp.ligacao
                    where lp.poi_id = p.id
                      and upper(l.categoria) = 'RESIDENCIAL'
                      and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA')
       %(filtro)s
     -- A ORDEM E A CONFIANCA DO VINCULO, e nao o `id`.
     --
     -- `order by p.id` parecia neutro e nao era: o id conta QUANDO o POI foi
     -- criado, e as fontes entraram em epocas diferentes. Medido em
     -- 07/09/2026, com 6.668 vereditos ja dados: o Maps tinha 3.035 POIs
     -- prontos e apenas 34 julgados, porque seus ids sao os mais altos —
     -- criados por ultimo — e a fila ainda varria a base estadual.
     --
     -- Ordenar pela confianca do VINCULO poe na frente o par ligacao x POI que
     -- bate rua e numero, que e o que tem mais chance de virar cobranca. Quem
     -- tem so proximidade espera. O denominador nao muda — tudo e julgado no
     -- fim —, mas os primeiros resultados passam a ser os melhores.
     -- A FONTE VEM ANTES DA CONFIANCA DO VINCULO.
     --
     -- Ordenar so pela confianca parecia certo e tinha um vies escondido: o
     -- endereco do CNPJ bate rua e numero EXATO, porque e o endereco de
     -- REGISTRO. Registro nao e operacao — MEI e empresa de fundo de quintal
     -- declaram a propria casa. A precisao do dado enganava o criterio, e a
     -- Receita subia na fila justamente por isso.
     --
     -- MEDIDO em 07/09/2026, com 7.744 vereditos dados:
     --
     --     fonte      a julgar   %% da fila   reprovados
     --     receita      13.917        44%%        51,7%%
     --     ibge         10.326        33%%        18,3%%
     --     estadual      3.852        12%%        21,9%%
     --     maps          3.432        11%%        10,9%%   (21,5%% aprovado exato)
     --
     -- A Receita ocupa quase metade do trabalho e reprova metade do que
     -- recebe; o Maps, que e a melhor fonte, e um nono da fila. Nada deixa de
     -- ser julgado — o denominador e o mesmo —, mas os primeiros resultados
     -- passam a vir de quem tem mais chance de virar cobranca.
     --
     -- iFood e Airbnb vem na frente de tudo porque sao decididos pela ficha da
     -- fonte, em milissegundos: adiar quem nao consome modelo nao economiza
     -- nada e so atrasa o resultado.
     -- O MAPS ABRE A FILA. Pedido direto do usuario em 07/09/2026, e a
     -- medicao concorda: 10,9%% de reprovacao contra 51,7%% da Receita, e
     -- 21,5%% de aprovado exato, a maior taxa de todas as fontes.
     --
     -- iFood e Airbnb vem logo atras, e nao na frente: sao decididos pela
     -- ficha em milissegundos, entao a posicao deles quase nao muda o relogio
     -- — sao 95 itens, algo como seis segundos de fila.
     order by case p.fonte
                when 'maps'     then 1
                when 'ifood'    then 2
                when 'airbnb'   then 2
                when 'estadual' then 3
                when 'cadastur' then 3
                when 'ibge'     then 4
                when 'receita'  then 5
                else 6
              end,
              (select max(lp2.confianca) from radar_comercial.ligacao_poi lp2
                where lp2.poi_id = p.id) desc nulls last, p.id
"""

SEM_VEREDITO = """
       and not exists (select 1 from radar_comercial.poi_veredito v
                        where v.poi_id = p.id)
"""

# `--poi` NÃO PASSA PELA FILA, e essa é a razão de ser dele: quem digitou o id
# já escolheu o ponto. Exigir dele categoria marcada, vínculo residencial e
# área desenhada devolveria "não achei" para um POI que está ali na tela — que
# foi o defeito corrigido em 27b0e41 e que a reescrita da fila em `exists`
# apagou por descuido. A única exigência que fica é ter imagem: sem ela não há
# o que julgar.
SQL_POR_ID = """
    select p.id, coalesce(p.nome,''), coalesce(p.fonte,''),
           coalesce(p.categoria,''), coalesce(p.endereco,''),
           coalesce(p.cidade,''), coalesce(p.uf,''),
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry)
      from radar_comercial.pois p
     where p.id = any(%s)
       and exists (select 1 from radar_comercial.poi_evidencia e
                    where e.poi_id = p.id and e.dados is not null)
     order by p.id
"""


# ══════════════════════════════════════════════════════════════════════════
# QUANDO A FONTE JA PROVA, A IMAGEM SO REFORCA
#
# Medido em 26 POIs de Canoas (06/09/2026), julgados so pela rua: 11 foram
# reprovados como "residencia sem comercio" e, nos ONZE, o observador nao tinha
# visto letreiro nenhum. Nao era o julgamento descartando evidencia — nao havia
# evidencia na rua. Uma cozinha de delivery e uma hospedagem por temporada
# funcionam DENTRO DE CASA.
#
# E NO IFOOD A INVERSAO E COMPLETA: "parece uma casa" e prova A FAVOR da tese.
# O produto procura comercio pagando tarifa residencial — a cozinha na casa E o
# achado. Reprovar por isso e descartar o alvo por ele parecer com o alvo.
#
# Isto roda ANTES das duas chamadas ao modelo, e economiza as duas.
# ══════════════════════════════════════════════════════════════════════════

#: Ate quando um comentario de hospede conta como "esta operando".
MESES_HOSPEDE_RECENTE = 12

#: iFood — a ficha existe, com CNPJ ou nota.
IFOOD_BASE = 0.55
#: iFood — a loja esta NO AR agora (`bruto.disponivel`). E o sinal mais forte
#: que esta fonte oferece: nao e "ja existiu", e "aceita pedido hoje".
IFOOD_NO_AR = 0.35
IFOOD_COM_CNPJ = 0.05
IFOOD_COM_AVALIACAO = 0.05
#: iFood — teto de quem esta FORA DO AR. A loja existe e pode voltar, mas nao
#: merece a frente da fila: o fiscal nao deve sair para uma cozinha fechada.
IFOOD_TETO_FORA_DO_AR = 0.50

#: Airbnb — o anuncio esta no ar, com preco publicado.
AIRBNB_BASE = 0.40
#: Airbnb — o passo que a RECENCIA vale por inteiro, decaindo um doze avos por
#: mes ate zerar em doze meses. Decisao do dono do produto (06/09/2026).
#: Linear de proposito: o Airbnb data o comentario por MES, sem dia, e uma
#: curva daria precisao aparente sobre um dado grosso.
PESO_RECENCIA = 0.50
AIRBNB_MUITAS_AVALIACOES = 0.10
AIRBNB_AVALIACOES_MUITAS = 20

#: A confianca de cada veredito quando quem decide e o MODELO, e nao a ficha.
#: Mesma regua 0..1 de `ligacao_poi.confianca`.
#: AS FONTES QUE NAO PRECISAM DE FOTO DE RUA PARA SEREM JULGADAS.
#:
#: Nelas o veredito sai da FICHA DA PLATAFORMA — loja no ar no iFood, anuncio
#: ativo no Airbnb com comentario recente —, que e prova datada de atividade
#: economica. A foto de rua nao acrescenta prova nenhuma a isso: uma casa sem
#: vitrine nao desmente um pedido aceito ontem.
#:
#: O QUE ESTA REGRA CORRIGE, medido em 07/09/2026:
#:
#:     Airbnb  267 POIs, 247 SEM imagem nenhuma  ->  so 7 julgados
#:     iFood 1.348 POIs, 797 SEM imagem nenhuma  ->  488 julgados
#:
#: Eram ~1.040 pontos ja provados pela plataforma, parados na fila esperando
#: uma fotografia que nao mudaria o veredito. A exigencia existia por um
#: raciocinio que parecia solido — "a ficha prova que o negocio existe, e nao
#: que ele esta NAQUELE endereco" — mas o preco dela era alto demais: o
#: endereco ja e conferido no cruzamento, que e onde ele deve ser conferido, e
#: a `ligacao_poi.confianca` diz quanto vale aquele par.
#:
#: A foto continua sendo capturada e continua indo para o dossie da visita.
#: Ela deixa de ser CONDICAO para o julgamento nestas duas fontes — decisao do
#: dono do produto em 07/09/2026: "as imagens servem para compor, nao para
#: comprovar; o que e decisivo sao os metadados".
FONTES_QUE_DISPENSAM_IMAGEM = ("ifood", "airbnb")

CONF_VEREDITO = {
    "aprovado_exato": 0.90,       # o letreiro traz o nome do cadastro
    "aprovado_comercial": 0.70,   # ha comercio no imovel, mas nao AQUELE
    "revisao_humana": 0.40,       # indicio sem prova
    "reprovado": 0.55,            # nada indica atividade — e uma leitura fragil
}

_MESES_PT = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
             "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
             "outubro": 10, "novembro": 11, "dezembro": 12}


def _mes_ano(txt):
    """'junho de 2026' -> (2026, 6). O Airbnb data o comentario assim."""
    if not txt:
        return None
    ano = mes = None
    for x in str(txt).lower().replace(" de ", " ").split():
        if x in _MESES_PT:
            mes = _MESES_PT[x]
        elif x.isdigit() and len(x) == 4:
            ano = int(x)
    return (ano, mes) if ano and mes else None


def _passo_recencia(datas):
    """(passo, meses, ultimo) — cheio no mes corrente, zero a partir de 12."""
    import datetime as _dt
    if not datas:
        return 0.0, None, None
    hoje = _dt.date.today()
    meses = max(0, (hoje.year * 12 + hoje.month)
                - (datas[0][0] * 12 + datas[0][1]))
    if meses >= MESES_HOSPEDE_RECENTE:
        return 0.0, meses, datas[0]
    return (round(PESO_RECENCIA * (1.0 - meses / float(MESES_HOSPEDE_RECENTE)),
                  3), meses, datas[0])


def _prova_da_fonte(con, alvo):
    """O veredito que o REGISTRO da fonte ja decide. None para seguir na imagem."""
    fonte = (alvo.get("fonte") or "").strip().lower()
    if fonte not in ("ifood", "airbnb"):
        return None

    if fonte == "ifood":
        # A FICHA DO MERCHANT E MAIS FORTE QUE QUALQUER FACHADA. Em Canoas,
        # das 950 lojas: 100% com CNPJ, 100% com rua, 100% com nota, 96,7%
        # com avaliacoes. Uma loja anunciada com CNPJ e nota E um
        # estabelecimento; a foto da rua nao acrescenta nada a isso.
        with con.cursor() as k:
            k.execute("""select m.cnpj, m.nota, m.avaliacoes, m.categoria,
                                (m.bruto->>'disponivel')
                           from radar_comercial.ifood_merchant m
                          where m.poi_id = %s limit 1""", (alvo["id"],))
            r = k.fetchone()
        if not r:
            return None
        cnpj, nota, aval, cat, disp = r
        if not (cnpj or nota is not None):
            return None
        no_ar = (str(disp).lower() == "true") if disp is not None else None
        conf = IFOOD_BASE + (IFOOD_COM_CNPJ if cnpj else 0) \
            + (IFOOD_COM_AVALIACAO if aval else 0) + (IFOOD_NO_AR if no_ar else 0)
        if no_ar is False:
            conf = min(conf, IFOOD_TETO_FORA_DO_AR)
        estado = ("no ar agora" if no_ar else "FORA DO AR no iFood"
                  if no_ar is False else "sem informação de disponibilidade")
        return {
            "veredito": "aprovado_exato" if no_ar is not False else "revisao_humana",
            "confianca": round(min(1.0, conf), 2),
            "especie_cnefe": 6,
            "justificativa": ("Loja no iFood com %s%s, %s — a ficha da fonte "
                              "prova o estabelecimento; a rua apenas reforça."
                              % ("CNPJ" if cnpj else "nota",
                                 " e %d avaliações" % aval if aval else "",
                                 estado)),
            "_fonte": "ifood", "_categoria": cat, "_no_ar": no_ar,
        }

    # AIRBNB — a rua nem e dele: o site desloca o pino de proposito (34 dos 44
    # POIs de Canoas com `coord_exata` falso). O que prova operacao e o proprio
    # anuncio: no ar, e com hospede no ultimo ano.
    with con.cursor() as k:
        k.execute("""select a.preco_total is not null, a.avaliacoes,
                            a.avaliacoes_qtd
                       from radar_comercial.airbnb_anuncio a
                      where a.poi_id = %s limit 1""", (alvo["id"],))
        r = k.fetchone()
    if not r:
        return None
    anunciado, avals, qtd = r
    datas = sorted([d for d in (_mes_ano((x or {}).get("data"))
                                for x in (avals or []) if isinstance(x, dict))
                    if d], reverse=True)
    passo, meses, ultimo = _passo_recencia(datas)
    conf = (AIRBNB_BASE if anunciado else 0.0) + passo
    if qtd and qtd >= AIRBNB_AVALIACOES_MUITAS:
        conf += AIRBNB_MUITAS_AVALIACOES

    if ultimo is None:
        quando = "sem comentário colhido"
    elif meses == 0:
        quando = "hóspede neste mês"
    elif meses == 1:
        quando = "último hóspede há 1 mês (%d/%02d)" % ultimo
    else:
        quando = "último hóspede há %d meses (%d/%02d)" % (meses, ultimo[0],
                                                           ultimo[1])
    if anunciado and passo > 0:
        return {
            "veredito": "aprovado_exato", "confianca": round(min(1.0, conf), 2),
            "especie_cnefe": 2,
            "justificativa": ("Anúncio no ar, %s — hospedagem em operação. A "
                              "rua não julga: o Airbnb desloca o pino de "
                              "propósito." % quando),
            "_fonte": "airbnb", "_meses": meses,
        }
    return {
        "veredito": "revisao_humana", "confianca": round(min(1.0, conf), 2),
        "especie_cnefe": 2,
        # NAO TER COMENTARIO NAO E O MESMO QUE TER UM ANTIGO. O primeiro e
        # falta de dado; o segundo e sinal.
        "justificativa": ("Anúncio %s, %s — %s. Não reprovado pela rua, que no "
                          "Airbnb aponta para o prédio errado."
                          % ("no ar" if anunciado else "fora do ar", quando,
                             "sem como medir atualidade" if ultimo is None else
                             "o passo de atualidade zerou (mais de %d meses)"
                             % MESES_HOSPEDE_RECENTE)),
        "_fonte": "airbnb", "_meses": meses,
    }


def alvos(con, poligono, limite, pois, refazer):
    cur = con.cursor()
    if pois:
        cur.execute(SQL_POR_ID, (list(pois),))
    else:
        cur.execute(SQL_ALVO % {"filtro": "" if refazer else SEM_VEREDITO})
    saida, fora = [], 0
    for (pid, nome, fonte, cat, endereco, cidade, uf, la, lo) in cur.fetchall():
        if poligono and la is not None \
                and not area_utils.ponto_no_poligono(la, lo, poligono):
            fora += 1
            continue
        saida.append({"id": pid, "nome": nome, "fonte": fonte, "categoria": cat,
                      "endereco": endereco, "cidade": cidade, "uf": uf,
                      # A COORDENADA VIAJA COM O ALVO. Ela já vinha do SQL e
                      # era descartada aqui; sem ela não há como medir a
                      # distância até a ligação, que é o número que denuncia a
                      # foto tirada no lugar errado.
                      "lat": la, "lng": lo})
        if limite and len(saida) >= limite:
            break
    return saida, fora


#: DE ONDE VEM A IMAGEM QUANDO NAO HA `poi_evidencia`.
#:
#: Ha duas capturas de rua no repositorio, herdadas de duas geracoes:
#:
#:   capturar_evidencia.py -> poi_evidencia    4 visadas, borda ja cortada, mira
#:   streetview_capture.py -> streetview_imgs  3 visadas, borda crua, sem mira
#:
#: Sao quase a mesma foto do mesmo ponto, e manter as duas rodando significaria
#: fotografar cada POI duas vezes para dois destinos. Em 06/09/2026 a captura de
#: fachada ja estava em 49% dos 27.470 POIs de Canoas quando a duplicidade
#: apareceu; jogar isso fora para recapturar do zero seria desperdicio maior que
#: a diferenca entre as duas.
#:
#: Entao a leitura aceita as duas. `poi_evidencia` continua sendo a preferida —
#: tem o fundo e a mira —, e `streetview_imgs` entra como queda, com a borda
#: cortada AQUI, na leitura, ja que a captura dela nao corta.
DA_FACHADA = {"facade": "sv_frente", "g90": "sv_lado_a",
              "g180": "sv_fundo", "g270": "sv_lado_b"}


def _da_fachada(cur, poi_id):
    """`{tipo: bytes}` a partir de `streetview_imgs`, com a borda cortada."""
    import imagens as _im
    try:
        from capturar_evidencia import _cortar_interface
    except Exception:                                          # noqa: BLE001
        def _cortar_interface(x):
            return x
    cur.execute("""select angulo, storage_path from radar_comercial.streetview_imgs
                    where poi_id = %s and storage_path is not null
                    order by id""", (poi_id,))
    linhas = cur.fetchall()
    # SOLTA A TRANSACAO ANTES DE BAIXAR.
    #
    # `_im.baixar` fala HTTP com o Storage e leva segundos por imagem. Com a
    # transacao aberta durante isso, a sessao fica `idle in transaction`
    # segurando um slot do pooler — que aceita 20 NO TOTAL, entre todas as
    # maquinas e servicos.
    #
    # Medido em 07/09/2026: catorze sessoes presas exatamente nesta consulta,
    # e a captura de evidencia nao conseguia abrir a dela — morria com
    # `EMAXCONNSESSION` antes do primeiro POI. O sintoma apontava para a
    # captura, e a causa estava aqui.
    #
    # Ler tudo, fechar, e so entao baixar. O `select` custa milissegundos; o
    # download, segundos. Nao ha razao para os dois dividirem uma transacao.
    try:
        cur.connection.commit()
    except Exception:                                          # noqa: BLE001
        pass
    saida = {}
    for ang, caminho in linhas:
        tipo = DA_FACHADA.get(ang)
        if not tipo or tipo in saida:
            continue
        b = _im.baixar(caminho)
        if b:
            # A BORDA SAI NA LEITURA porque a captura de fachada nao a tira. O
            # painel do Maps no alto e o rotulo do minimapa embaixo sao lidos
            # como se fossem placa: medido em 06/09/2026, um giro trouxe
            # "Padaria Confeitaria e Cafeteria Sabor Do Trigo" — o nome do
            # VIZINHO, escrito pela interface — e virou `nome_visto`.
            saida[tipo] = _cortar_interface(b)
    return saida


def evidencia(con, poi_id):
    """As imagens do POI, na ordem em que a IA deve lê-las.

    Devolve `(forma, imagens, tipos)` — `tipos` na mesma ordem das imagens, e é
    dela que sai a lista que o prompt anuncia.
    """
    cur = con.cursor()
    # OS BYTES PODEM ESTAR EM DOIS LUGARES.
    #
    # `poi_evidencia` sempre teve `dados` e `storage_path`; ate 07/09/2026 so o
    # primeiro era usado, e a tabela virou a maior do banco — 53 GB de 104 GB.
    # As imagens adequadas da captura antiga entram pelo Storage, e as 63.308
    # antigas continuam em bytea. `imagens._de_linha` resolve os dois: prefere o
    # caminho e cai no bytea quando nao ha caminho ou o download falha.
    cur.execute("""select tipo, dados, storage_path
                     from radar_comercial.poi_evidencia
                    where poi_id = %s
                      and (dados is not null or storage_path is not null)""",
                (poi_id,))
    por_tipo = {}
    for tp, d, sp in cur.fetchall():
        b = imagens._de_linha(sp, d)
        if b:
            por_tipo[tp] = b
    if por_tipo.get("pagina_airbnb"):
        return "pagina", [por_tipo["pagina_airbnb"]], ["pagina_airbnb"]
    if not any(t in por_tipo for t in ORDEM_RUA):
        por_tipo = _da_fachada(cur, poi_id)
    tipos = [t for t in ORDEM_RUA if t in por_tipo]
    if not tipos:
        return None, [], []
    return "rua", [por_tipo[t] for t in tipos], tipos


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


import datetime as _dt
import re as _re

# NOME DE MEI NÃO É NOME DE PORTA, e mandar procurá-lo torna o diagnóstico
# injusto — decisão do dono do produto em 04/09/2026.
#
# A Receita registra o microempreendedor como "59.245.003 SABRINA DE FATIMA
# SMOLA" ou "NATIELI DE OLIVEIRA SANTOS 01973901021": é o CNPJ colado no nome
# da pessoa. Isso NUNCA está escrito numa fachada. Pedir à IA que confirme
# esse nome garante que ela não confirme nada, e o ponto cai em revisão ou
# reprovação por um motivo que não é do imóvel — é do cadastro.
#
# O que vai no lugar é a ATIVIDADE (a descrição do CNAE). A pergunta deixa de
# ser "existe uma placa escrito SABRINA?" e passa a ser "há sinal de lanchonete
# aqui?", que é a pergunta que a foto pode responder.
_DIGITOS = _re.compile(r"\d[\d./-]{4,}\d")


def nome_procuravel(nome: str):
    """Devolve o nome se ele puder estar numa placa; None se for razão social.

    O teste é a corrida de dígitos: CNPJ, CPF ou NIRE embutidos no nome. Um
    nome fantasia de verdade não carrega seis dígitos seguidos.
    """
    n = (nome or "").strip()
    if not n:
        return None
    if _DIGITOS.search(n):
        return None
    return n


import math as _math


def _metros(a, b, c, d):
    r = 6371000.0
    p1, p2 = _math.radians(a), _math.radians(c)
    dp, dl = _math.radians(c - a), _math.radians(d - b)
    h = (_math.sin(dp / 2) ** 2
         + _math.cos(p1) * _math.cos(p2) * _math.sin(dl / 2) ** 2)
    return 2 * r * _math.asin(min(1.0, _math.sqrt(h)))


def ligacoes_texto(con, alvo) -> str:
    """As ligações de água do ponto: endereço, coordenada e distância ao POI.

    POR QUE ISTO VAI À IA — pedido do dono do produto em 04/09/2026, e a razão
    é um defeito medido: o geocodificador casa endereço só pelo CEP, e num CEP
    de bairro inteiro o "número 43" vira o 43 de qualquer rua. O POI 91794 diz
    "AVENIDA RIO GRANDE DO SUL, 43" e recebeu a coordenada do "BECO DEODORO DA
    FONSECA, 43". A foto sai do lugar errado, e a IA julgava a casa de um
    terceiro sem ter como desconfiar.
    """
    cur = con.cursor()
    cur.execute("""
        select lp.ligacao, coalesce(l.categoria,''), coalesce(l.sit_ligacao,''),
               coalesce(l.nom_logradouro,''), coalesce(l.nro,''),
               coalesce(l.nom_bairro,''),
               l.cod_latitude::float8, l.cod_longitude::float8,
               coalesce(l.qtd_eco_res,0), coalesce(l.qtd_eco_com,0),
               coalesce(l.qtd_eco_ind,0)
          from radar_comercial.ligacao_poi lp
          left join resources_root.cadastro_corsan l
                 on l.num_ligacao::text = lp.ligacao
         where lp.poi_id = %s
         order by (upper(coalesce(l.categoria,'')) = 'RESIDENCIAL') desc,
                  lp.ligacao
         limit 6
    """, (alvo["id"],))
    linhas = []
    for (lig, cat, sit, logr, nro, bairro, la, lo,
         eres, ecom, eind) in cur.fetchall():
        # O BAIRRO VAI ROTULADO, e nao colado por virgula.
        #
        # Escrito como "R. CONCORDIA 985, NITEROI", o julgamento lia NITEROI
        # como CIDADE e anunciava divergencia de municipio — quando Niteroi e
        # bairro de Canoas. MEDIDO em 07/09/2026: 182 dos 2.808 casos em
        # revisao humana citam divergencia junto com um bairro conhecido de
        # Canoas (Niteroi, Igara, Guajuviras, Mathias Velho, Harmonia,
        # Estancia). Sao 6,5% das revisoes penduradas numa divergencia que
        # nao existe.
        #
        # A palavra "bairro" resolve porque o modelo nao tem como saber, de um
        # nome sozinho depois de uma virgula, se aquilo e bairro ou municipio —
        # e varios bairros brasileiros tem nome de cidade.
        onde = ("%s %s" % (logr, nro)).strip()
        if bairro:
            onde = ("%s — bairro %s" % (onde, bairro)) if onde else (
                "bairro %s" % bairro)
        dist = ""
        if la is not None and lo is not None and alvo.get("lat") is not None:
            dist = " · a %.0f m do ponto fotografado" % _metros(
                alvo["lat"], alvo["lng"], la, lo)
        eco = []
        if eres:
            eco.append("%d residencial" % eres)
        if ecom:
            eco.append("%d comercial" % ecom)
        if eind:
            eco.append("%d industrial" % eind)
        linhas.append("  - ligação %s (%s, %s) em %s%s%s"
                      % (lig, cat or "?", sit or "?", onde or "endereço vazio",
                         dist, (" · economias: " + ", ".join(eco)) if eco else ""))
    return "\n".join(linhas)


#: QUANTOS COMENTARIOS VAO NO PROMPT. Os mais RECENTES, porque o que decide
#: tarifa e se o negocio opera AGORA — comentario de sete anos prova que existiu.
COMENTARIOS_NO_PROMPT = 6

#: Como o Google escreve "quando". Medido em 07/09/2026 sobre 75.563
#: comentarios: 74 formas distintas, todas cobertas por estes sete padroes.
_UNIDADE_EM_DIAS = {"minuto": 0, "hora": 0, "dia": 1, "semana": 7,
                    "mes": 30, "mês": 30, "ano": 365}


def _dias_atras(texto):
    """'5 meses atrás' -> 150. Devolve None quando nao reconhece.

    A DATA VEM RELATIVA e nao absoluta: o Google escreve "5 meses atrás" na
    pagina, e e isso que a coleta guardou. Para virar data de calendario falta
    a ancora, que e `pois.detalhado_em` — quando a pagina foi lida.
    """
    if not texto:
        return None
    s = str(texto).strip().lower()
    if s.startswith("hoje") or s.startswith("agora"):
        return 0
    n = 1
    m = _re.match(r"^(\d+)", s)
    if m:
        n = int(m.group(1))
    elif not _re.match(r"^(um|uma)\b", s):
        return None
    for unidade, dias in _UNIDADE_EM_DIAS.items():
        if unidade in s:
            return n * dias
    return None


def _quando_em_palavras(dias, ancora):
    """Diz a data em calendario, e nao so 'ha tanto tempo'."""
    if dias is None:
        return "quando não se sabe"
    if ancora is None:
        return "há %d dia(s) quando a página foi lida" % dias
    d = ancora - _dt.timedelta(days=dias)
    meses = ["jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez"]
    return "%s/%d" % (meses[d.month - 1], d.year)


def _sinal_do_maps(con, alvo) -> list:
    """Nota, horario e comentarios — a prova que o Google ja tinha.

    POR QUE ISTO EXISTE, medido em 07/09/2026: dos 542 POIs de Maps que a IA
    REPROVOU, 356 tinham comentario de cliente no Google e 369 tinham nota. Um
    deles e uma barbearia com cinco pessoas descrevendo o corte de cabelo,
    reprovada porque a fachada e uma casa — que e exatamente o negocio que este
    projeto procura: comercio sem vitrine em ligacao residencial.

    Comentario datado e prova de TERCEIRO sobre atividade em curso, da mesma
    natureza da loja no ar no iFood. A fachada muda nao desmente nenhum dos
    dois: barbeiro, manicure, oficina de fundo de quintal e doceira operam sem
    porta de loja, e sao a maior parte do que se procura aqui.
    """
    linhas = []
    cur = con.cursor()
    cur.execute("""select avaliacao, total_avaliacoes, status_horario,
                          resumo_avaliacoes
                     from radar_comercial.maps_data where poi_id = %s""",
                (alvo["id"],))
    r = cur.fetchone()
    if r and (r[0] is not None or r[1]):
        linhas.append("- Google Maps: nota %s de 5, com %s avaliação(ões)"
                      % (r[0] if r[0] is not None else "?",
                         r[1] if r[1] is not None else "?"))
    if r and r[2]:
        linhas.append("- horário declarado no Google: %s" % str(r[2]).strip())
    if r and r[3]:
        linhas.append("- o que o Google resume das avaliações: %s"
                      % str(r[3])[:400])

    cur.execute("""select detalhado_em from radar_comercial.pois
                    where id = %s""", (alvo["id"],))
    a = cur.fetchone()
    ancora = a[0] if a and a[0] else None

    cur.execute("""select autor, data, nota, texto
                     from radar_comercial.comentarios
                    where poi_id = %s""", (alvo["id"],))
    coments = []
    for autor, quando, nota, texto in cur.fetchall():
        coments.append((_dias_atras(quando), autor, quando, nota, texto))
    if not coments:
        return linhas

    # OS MAIS NOVOS PRIMEIRO. Quem nao teve a data entendida vai para o fim, e
    # nao para o comeco: sem data ele nao serve para dizer "opera agora".
    coments.sort(key=lambda c: (c[0] is None, c[0] if c[0] is not None else 0))
    recentes = coments[:COMENTARIOS_NO_PROMPT]
    linhas.append("- avaliações de clientes no Google (%d no total, as %d mais "
                  "recentes abaixo, com a data em que foram escritas):"
                  % (len(coments), len(recentes)))
    for dias, autor, quando, nota, texto in recentes:
        linhas.append("  · %s, nota %s: %s"
                      % (_quando_em_palavras(dias, ancora),
                         "?" if nota is None else ("%.0f" % nota),
                         (str(texto or "(sem texto, só a nota)")
                          .replace("\n", " ")[:180])))
    return linhas


def _cadastro_texto(con, alvo) -> str:
    """O que o cadastro afirma, em texto — inclusive o que o iFood já sabe.

    A LOJA DO IFOOD ENTRA AQUI, e não como imagem: a página da loja é protegida
    por desafio anti-robô, mas o endpoint `/extra` já entregou, de graça, o que
    interessa — se a loja estava disponível, quantas avaliações tem e quando foi
    vista. É prova de atividade mais forte que um print do cardápio, porque diz
    QUANDO.
    """
    nome = nome_procuravel(alvo["nome"])
    linhas = [
        ("- nome fantasia: %s" % nome) if nome else
        ("- SEM NOME FANTASIA. O cadastro traz apenas a razão social de "
         "microempreendedor (CNPJ + nome do titular), que não aparece em "
         "fachada. NÃO procure nome: procure a ATIVIDADE abaixo."),
        "- atividade declarada: %s" % (alvo.get("categoria_nome")
                                       or alvo["categoria"]
                                       or "(sem categoria)"),
        "- fonte do dado: %s" % alvo["fonte"],
        "- endereço: %s — %s/%s" % (alvo["endereco"] or "(sem endereço)",
                                    alvo["cidade"], alvo["uf"]),
        # DUAS FRASES QUE EVITAM DIVERGENCIA INVENTADA.
        #
        # A primeira: o Google devolve o BAIRRO dentro do endereço, e muitos
        # bairros brasileiros têm nome de cidade. Sem esta linha o julgamento
        # escrevia "o cadastro cita R. Quaraí em Canoas, mas as fotos foram
        # tiradas em Niterói" — e Niterói é bairro de Canoas. Medido em
        # 07/09/2026: 182 dos 2.808 casos em revisão humana citavam
        # divergência ao lado de um bairro conhecido da própria cidade.
        #
        # A segunda: a foto sai do ponto de rua mais próximo, não da porta.
        # Algumas dezenas de metros são o normal de um quarteirão urbano, e o
        # julgamento estava lendo 23 m como indício de imóvel errado.
        "- o MUNICÍPIO é o declarado nesta linha. Qualquer outro nome de lugar "
        "que apareça no endereço ou nas ligações é BAIRRO, não cidade — e não "
        "é divergência.",
        "- a foto é tirada do ponto de rua mais próximo, e não da porta: "
        "dezenas de metros entre a foto e a ligação são o normal de um "
        "quarteirão. Só desconfie da distância acima de uns 60 m.",
    ]
    cur = con.cursor()
    if alvo["fonte"] == "ifood":
        cur.execute("""select nota, avaliacoes, cnpj, telefone, visto_em,
                              bruto->>'disponivel'
                         from radar_comercial.ifood_merchant
                        where poi_id = %s limit 1""", (alvo["id"],))
        r = cur.fetchone()
        if r:
            linhas.append(
                "- iFood em %s: loja %s, nota %s, %s avaliação(ões)%s%s"
                % (r[4].date() if r[4] else "?",
                   "DISPONÍVEL" if r[5] == "true" else "não disponível",
                   r[0] if r[0] is not None else "?",
                   r[1] if r[1] is not None else "?",
                   ", CNPJ %s" % r[2] if r[2] else "",
                   ", telefone %s" % r[3] if r[3] else ""))
    elif alvo["fonte"] == "airbnb":
        cur.execute("""select nota, avaliacoes_qtd, anfitriao, hospedes, quartos
                         from radar_comercial.airbnb_anuncio
                        where poi_id = %s limit 1""", (alvo["id"],))
        r = cur.fetchone()
        if r:
            linhas.append("- Airbnb: nota %s, %s avaliação(ões), anfitrião %s, "
                          "%s hóspede(s), %s quarto(s)"
                          % tuple("?" if x is None else x for x in r))
    cur.execute("""select url from radar_comercial.poi_link
                    where poi_id = %s and ativo order by fonte limit 4""",
                (alvo["id"],))
    for (u,) in cur.fetchall():
        linhas.append("- link: %s" % u)

    # A COORDENADA FOTOGRAFADA, DITA EM NÚMERO. Sem ela a IA não tem como
    # comparar o que a foto mostra com o que o cadastro afirma.
    if alvo.get("lat") is not None:
        linhas.append("- coordenada de onde as fotos foram tiradas: %.6f, %.6f"
                      % (alvo["lat"], alvo["lng"]))
    # QUANDO O GOOGLE FOTOGRAFOU A RUA. Ver a migracao 0077: um terco das
    # fachadas julgadas e de 2024, e o modelo estava lendo aquilo como se fosse
    # hoje. Uma casa fotografada ha dois anos pode ter virado loja depois — e
    # tambem o contrario, uma loja pode ter fechado. Sem a data, o modelo nao
    # tem como pesar isso.
    cur.execute("""select min(data_imagem), max(data_imagem)
                     from radar_comercial.poi_evidencia
                    where poi_id = %s and data_imagem is not null""",
                (alvo["id"],))
    _dt_img = cur.fetchone()
    if _dt_img and _dt_img[1]:
        if _dt_img[0] == _dt_img[1]:
            linhas.append("- as fotos de rua sao de %s" % _dt_img[1])
        else:
            linhas.append("- as fotos de rua sao de %s a %s"
                          % (_dt_img[0], _dt_img[1]))

    # O SINAL DO GOOGLE ENTRA AQUI, junto do que o iFood e o Airbnb ja
    # entregavam — e pela mesma razao: e prova de terceiro sobre atividade,
    # com data, que a foto de rua nao tem como dar.
    if alvo["fonte"] == "maps":
        linhas.extend(_sinal_do_maps(con, alvo))

    ligs = ligacoes_texto(con, alvo)
    if ligs:
        linhas.append("- ligações de água vinculadas a este ponto:")
        linhas.append(ligs)
    return "\n".join(linhas)


def _secoes_texto(con) -> str:
    cur = con.cursor()
    cur.execute("select letra, nome from radar_comercial.cnae_secao order by letra")
    return " · ".join("%s %s" % (l, n) for l, n in cur.fetchall())


def _nomes_de_cnae(con, alvos_lista):
    """A descrição do CNAE, para todos de uma vez.

    O código cru ("5611201") não diz nada nem para a IA nem para quem vai à
    porta. A tradução vem da `rf_cnaes` numa consulta só, e não uma por POI.
    """
    codigos = sorted({(a["categoria"] or "").strip() for a in alvos_lista
                      if (a["categoria"] or "").strip().isdigit()})
    if not codigos:
        return
    with con.cursor() as k:
        k.execute("select codigo, descricao from resources_root.rf_cnaes "
                  "where codigo = any(%s)", (codigos,))
        m = dict(k.fetchall())
    for a in alvos_lista:
        a["categoria_nome"] = m.get((a["categoria"] or "").strip())


def um_poi(poco, alvo, modelo, secoes, placar, trava, aplicar) -> None:
    """Um POI, com a conexao EMPRESTADA POR TOQUE — nunca pela rodada inteira.

    O DEFEITO QUE ISTO CORRIGE, medido em 07/09/2026: `obreiro` embrulhava esta
    funcao inteira num `with poco.pegar()`, e a funcao passa 28,9 s dos seus
    29 s esperando o modelo. Ou seja, cada trabalhador segurava uma das 20
    sessoes do pooler por 29 segundos para usa-la por milissegundos.

    Com `CONEXOES = 4` e seis trabalhadores, dois ficavam PARADOS na fila do
    poco o tempo todo. A medicao: 94 vereditos em 650 s, 28,9 s por POI — 4,1
    trabalhadores efetivos de 6 configurados. O poco era o teto, e nao o
    modelo: a vLLM da Spark mostrava 2,4 requisicoes em curso e ZERO na espera.

    Os cinco toques no banco aqui somam milissegundos: `evidencia`,
    `_prova_da_fonte`, `_cadastro_texto` e as duas gravacoes. Emprestando por
    toque, quatro conexoes atendem dez ou vinte trabalhadores — como ja
    acontece em `capturar_evidencia`, que empresta por gravacao.
    """
    t0 = time.time()

    # A FONTE DECIDE PRIMEIRO, QUANDO ELA PODE DECIDIR — e isso economiza as
    # DUAS chamadas ao modelo. Ver `_prova_da_fonte`: para iFood e Airbnb a rua
    # nao e evidencia do estabelecimento, e insistir nela produzia o erro que a
    # medicao de 06/09/2026 mostrou.
    #
    # A IMAGEM DEIXOU DE SER CONDICAO onde a ficha decide. Ver
    # `FONTES_QUE_DISPENSAM_IMAGEM`: em iFood e Airbnb o veredito sai do
    # metadado, e exigir foto antes deixava ~1.040 pontos provados parados na
    # fila. Nas outras fontes a exigencia continua de pe — la a imagem E a
    # evidencia, e sem ela nao ha o que julgar.
    with poco.pegar() as con:
        forma, imgs, tipos = evidencia(con, alvo["id"])
    if not imgs and alvo["fonte"] not in FONTES_QUE_DISPENSAM_IMAGEM:
        with trava:
            placar["sem_evidencia"] += 1
        return

    with poco.pegar() as con:
        da_fonte = _prova_da_fonte(con, alvo)
    if da_fonte is not None:
        v = da_fonte["veredito"]
        percep = {"_imagens": tipos, "prova_da_fonte": da_fonte.get("_fonte"),
                  "_sem_modelo": True}
        if aplicar:
            with poco.pegar() as con:
                gravar(con, alvo["id"], v, da_fonte, percep, modelo, len(imgs),
                       time.time() - t0)
        with trava:
            placar[v] += 1
            placar["decidido_pela_fonte"] += 1
            _log("   %8d %-26s %-19s [%s] %s"
                 % (alvo["id"], alvo["nome"][:26], v,
                    da_fonte.get("_fonte"),
                    (da_fonte.get("justificativa") or "")[:52]))
        return

    # SEM IMAGEM E SEM FICHA NAO HA JULGAMENTO. So chega aqui um POI das
    # fontes que dispensam imagem cuja ficha nao pode decidir — loja que sumiu
    # do iFood, anuncio de Airbnb sem data de comentario. Sem foto e sem
    # metadado nao ha material: sai como veio, contado no placar.
    if not imgs:
        with trava:
            placar["sem_evidencia"] += 1
        return

    prompt_jul = (PROMPT_JULGAR_HOSPEDAGEM if forma == "pagina"
                  else PROMPT_JULGAR)

    # 1 · percepção cega — uma chamada POR IMAGEM (ver a nota nos prompts)
    percepcao = {}
    try:
        if forma == "pagina":
            percepcao = di._chat_local(modelo, PROMPT_PAGINA, [_b64(imgs[0])],
                                       max_tokens=900, timeout=TIMEOUT)
        else:
            # A LISTA ANUNCIADA É A QUE FOI ENVIADA. Prometer quatro fotos a
            # quem recebeu duas é pedir que o modelo descreva as que faltam.
            lista = "\n".join("%d. %s" % (i + 1, VISTA_ROTULO[tp])
                               for i, tp in enumerate(tipos))
            percepcao = di._chat_local(
                modelo,
                PROMPT_QUATRO % {"n": len(tipos), "lista": lista,
                                 "ignorar": IGNORAR},
                [_b64(b) for b in imgs], max_tokens=1100, timeout=TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha_percepcao"] += 1
            _log("   %8d percepção FALHOU %s: %s"
                 % (alvo["id"], type(e).__name__, str(e)[:70]))
        return

    # QUAIS IMAGENS ENTRARAM, gravado junto da percepção. Sem isto, ler depois
    # que a fachada veio nula não distingue "não havia foto" de "o modelo não
    # soube descrever" — e são erros de natureza oposta.
    if isinstance(percepcao, dict):
        percepcao["_imagens"] = tipos

    # O CADASTRO SAI DO BANCO ANTES DA CHAMADA, e nao dentro dela: montado no
    # meio do argumento, ele obrigaria a segurar a conexao durante os segundos
    # em que o modelo escreve.
    with poco.pegar() as con:
        cadastro = _cadastro_texto(con, alvo)

    # 2 · julgamento, só texto
    try:
        veredito = di._chat_local(
            modelo,
            prompt_jul % {"percepcao": json.dumps(percepcao, ensure_ascii=False,
                                                  indent=1),
                          "cadastro": cadastro,
                          "especies": ESPECIES, "secoes": secoes},
            None, max_tokens=400, timeout=TIMEOUT)
    except Exception as e:                                     # noqa: BLE001
        with trava:
            placar["falha_julgamento"] += 1
            _log("   %8d julgamento FALHOU %s: %s"
                 % (alvo["id"], type(e).__name__, str(e)[:70]))
        return

    v = (veredito.get("veredito") or "").strip()
    if v not in VEREDITOS:
        # VEREDITO FORA DA ESCALA NÃO VIRA "reprovado" NEM SOME. Ele vira
        # revisão humana e a resposta crua fica guardada: transformar resposta
        # inválida em reprovação inventaria uma decisão que o modelo não tomou.
        with trava:
            placar["fora_da_escala"] += 1
        percepcao["_veredito_cru"] = veredito
        v = "revisao_humana"

    # SEGUNDA LEITURA DO QUE SERIA DESCARTADO. Ver `RECONFERIR_REPROVADO`.
    #
    # A percepcao NAO e refeita: as imagens ja foram lidas e a descricao e a
    # mesma. O que se repete e so o julgamento, que e a chamada curta — e e ele
    # que estava balancando.
    if RECONFERIR_REPROVADO and v == "reprovado":
        segundo = None
        try:
            segundo = di._chat_local(
                modelo,
                prompt_jul % {"percepcao": json.dumps(percepcao,
                                                      ensure_ascii=False,
                                                      indent=1),
                              "cadastro": cadastro, "especies": ESPECIES,
                              "secoes": secoes},
                None, max_tokens=400, timeout=TIMEOUT)
        except Exception:                                      # noqa: BLE001
            # A reconferencia que falha NAO derruba o POI: fica valendo a
            # primeira leitura, que foi valida. Transformar erro de rede em
            # revisao humana encheria a fila de gente com defeito de rede.
            segundo = None
        v2 = ((segundo or {}).get("veredito") or "").strip()
        if segundo is not None and v2 in VEREDITOS and v2 != "reprovado":
            with trava:
                placar["reconferido_virou_revisao"] = placar.get(
                    "reconferido_virou_revisao", 0) + 1
            percepcao["_reconferencia"] = {
                "primeira": "reprovado", "segunda": v2,
                "justificativa_da_segunda":
                    (segundo.get("justificativa") or "")[:400]}
            veredito["justificativa"] = (
                "RECONFERIDO: a primeira leitura reprovou e a segunda disse "
                "'%s'. Duas leituras da MESMA descrição discordaram, então a "
                "decisão não é estável o bastante para descartar o imóvel. "
                "Primeira leitura: %s"
                % (v2, (veredito.get("justificativa") or "")[:200]))
            v = "revisao_humana"
            veredito["veredito"] = v

    # A CONFIANCA DO QUE O MODELO DECIDIU. Ele nao devolve numero, e pedir um
    # seria pedir que ele estimasse a propria certeza — coisa que modelo de
    # linguagem faz mal. O numero sai do VEREDITO, que e o que ele de fato
    # escolheu, na mesma regua 0..1 de `ligacao_poi.confianca`.
    #
    # `reprovado` nao fica no fundo da escala de proposito: "nada indica
    # atividade" e uma leitura fragil — foi ela que, medida em 26 POIs, apareceu
    # 11 vezes sem o observador ter visto um letreiro sequer.
    veredito.setdefault("confianca", CONF_VEREDITO.get(v, 0.50))

    dt = time.time() - t0
    if aplicar:
        with poco.pegar() as con:
            gravar(con, alvo["id"], v, veredito, percepcao, modelo, len(imgs),
                   dt)
    with trava:
        placar[v] += 1
        _log("   %8d %-26s %-19s %s"
             % (alvo["id"], alvo["nome"][:26], v,
                (veredito.get("justificativa") or "")[:64]))


def gravar(con, poi_id, v, veredito, percepcao, modelo, n_imgs, dt):
    esp = veredito.get("especie_cnefe")
    sec = (veredito.get("secao_cnae") or "").strip().upper()[:1] or None
    med = veredito.get("medidores")
    try:
        esp = int(esp) if esp is not None else None
        if esp is not None and not (1 <= esp <= 8):
            esp = None
    except (TypeError, ValueError):
        esp = None
    try:
        med = int(med) if med is not None else None
    except (TypeError, ValueError):
        med = None
    with con.cursor() as k:
        k.execute("""
            insert into radar_comercial.poi_veredito
                (poi_id, veredito, justificativa, especie_cnefe, secao_cnae,
                 medidores, percepcao, modelo, imagens, segundos, confianca)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (id_empresa, poi_id) do update set
                veredito = excluded.veredito,
                justificativa = excluded.justificativa,
                especie_cnefe = excluded.especie_cnefe,
                secao_cnae = excluded.secao_cnae,
                medidores = excluded.medidores,
                percepcao = excluded.percepcao,
                modelo = excluded.modelo,
                imagens = excluded.imagens,
                confianca = excluded.confianca,
                segundos = excluded.segundos,
                avaliado_em = now()
        """, (poi_id, v, veredito.get("justificativa"), esp, sec, med,
              json.dumps(percepcao, ensure_ascii=False), modelo, n_imgs,
              round(dt, 2),
              # A CONFIANCA VEM DOS DOIS CAMINHOS. Pela ficha da fonte ela
              # mede atualidade — loja no ar, hospede recente; pela fachada,
              # o quanto a leitura se sustenta. Mesma regua 0..1 de
              # `ligacao_poi.confianca`, para as duas caberem na mesma tela.
              veredito.get("confianca")))
    con.commit()


#: QUANTAS CONEXOES, independentemente de quantos trabalhadores.
#:
#: O pooler da porta 7100 aceita 20 sessoes NO TOTAL — entre todas as maquinas,
#: a API, o painel e as capturas. Este arquivo abria UMA POR TRABALHADOR: com
#: seis, mais as seis da captura de evidencia, mais os servicos, o pooler
#: saturava e ninguem mais conectava. Aconteceu duas vezes em 07/09/2026, e da
#: segunda nem uma consulta de diagnostico entrava.
#:
#: Quatro bastam porque o trabalho aqui e ESPERA DE MODELO, nao de banco: cada
#: POI faz duas chamadas ao vLLM, de segundos, e algumas consultas de
#: milissegundos. `capturar_evidencia` chegou a mesma conclusao com o mesmo
#: numero.
CONEXOES = 4


class Poco:
    """Emprestimo de conexao. Quem nao pega, espera — nao abre outra."""

    def __init__(self, n):
        import queue
        self.fila = queue.Queue()
        for _ in range(n):
            self.fila.put(bc.conectar())

    def pegar(self):
        import contextlib

        @contextlib.contextmanager
        def _emprestimo():
            con = self.fila.get()
            # CONEXAO MORTA SE TROCA, e nao se entrega assim mesmo.
            #
            # Em 07/09/2026 o banco derrubou as sessoes dos dois processos de
            # uma vez. O poco continuou entregando os mesmos objetos mortos, e
            # cada POI seguinte estourava `InterfaceError: connection already
            # closed` em milissegundos. Sem esta troca, uma soluco de segundos
            # no pooler consome a fila inteira sem julgar nada.
            if getattr(con, "closed", 0):
                con = bc.conectar()
            try:
                yield con
            finally:
                # DEVOLVE LIMPA. Uma transacao aberta esquecida aqui vira
                # `idle in transaction` e segura o slot do pooler — foi
                # exatamente o que aconteceu com o download de imagem.
                try:
                    con.rollback()
                except Exception:                              # noqa: BLE001
                    # Nao deu nem para desfazer: a conexao ja se foi. Devolver
                    # este objeto ao poco seria devolver o defeito.
                    try:
                        con.close()
                    except Exception:                          # noqa: BLE001
                        pass
                    con = None
                self.fila.put(con if con is not None else bc.conectar())
        return _emprestimo()

    def fechar(self):
        while not self.fila.empty():
            try:
                self.fila.get_nowait().close()
            except Exception:                                  # noqa: BLE001
                pass


#: QUANTAS FALHAS SEGUIDAS ANTES DE PARAR.
#:
#: Falha isolada e normal — imagem corrompida, modelo que devolve texto fora do
#: formato. Falha em SERIE nao e: significa que o problema nao esta no POI, e
#: sim no banco ou no modelo. Sem este freio, o laco varre a fila inteira
#: falhando em milissegundos por item e termina "sem erro", com a fila zerada e
#: nenhum veredito gravado — que e o pior desfecho possivel, porque parece
#: sucesso.
FALHAS_SEGUIDAS_LIMITE = 25

#: RECONFERIR O VEREDITO QUE DESCARTA.
#:
#: MEDIDO em 07/09/2026, com 40 POIs julgados tres vezes cada: a MESMA pergunta,
#: com o MESMO prompt e temperatura 0, muda de resposta em 8% dos casos. Nao e
#: amostragem aleatoria — `_chat_local` ja manda `temperature: 0`. E
#: nao-determinismo de lote: a vLLM junta requisicoes em lotes continuos, a
#: composicao do lote nunca se repete, muda a ordem das reducoes em ponto
#: flutuante, e num caso de fronteira o token escolhido muda.
#:
#: Os dois casos de ruido que deu para observar cairam os dois na mesma
#: fronteira:
#:
#:     375957  A=reprovado       B=revisao_humana
#:      86874  A=revisao_humana  B=reprovado
#:
#: E a pior fronteira que existe aqui. `reprovado` arquiva o POI para sempre;
#: `revisao_humana` manda uma pessoa olhar. A diferenca entre perder um caso de
#: subfaturamento e investiga-lo estava saindo de arredondamento.
#:
#: Entao so o veredito que DESCARTA e reconferido — e nao todos. Reconferir
#: tudo custaria ~15% do tempo e devolveria ~8% para a pilha de revisao, que
#: acabou de cair de 48% para 32%. Reconferir so os reprovados custa ~3%,
#: porque reprovado e um quinto dos casos e o julgamento e a chamada curta
#: (2 a 8 s contra os 20 s da percepcao).
#:
#: Discordancia nao vira aprovacao: vira `revisao_humana`, que e o balde certo
#: para "o modelo nao sabe".
RECONFERIR_REPROVADO = True

#: A ORDEM DAS FONTES vive no `order by` de `SQL_ALVO`, e nao aqui — ela precisa
#: ser SQL para caber no mesmo plano da consulta. Este nome existe so para quem
#: procurar "PESO_DA_FONTE" achar o lugar certo.
PESO_DA_FONTE = "ver o `case p.fonte` em SQL_ALVO"


def rodar(area, limite, aplicar, trabalhadores, modelo, pois, refazer):
    # ÁREA PEDIDA E INEXISTENTE É ERRO, e não 'sem filtro'. Com
    # `--poi` não há área a exigir: o id já é o recorte.
    poligono = None if pois else (area_utils.exigir_area(area)
                                  if area else None)
    con = bc.conectar()
    lista, fora = alvos(con, poligono, limite, pois, refazer)
    _log("   %d POI(s) com evidência na fila" % len(lista))
    if fora:
        _log("   %d fora do desenho" % fora)
    if not lista:
        _log("   nada a avaliar. Capture evidência antes "
             "(capturar_evidencia.py / capturar_pagina.py).")
        con.close()
        return {"alvos": 0}
    for a in lista[:5]:
        _log("      %8d %-34s %s" % (a["id"], a["nome"][:34], a["fonte"]))
    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")
        con.close()
        return {"alvos": len(lista), "avaliados": 0}
    secoes = _secoes_texto(con)
    _nomes_de_cnae(con, lista)
    con.close()

    placar = {k: 0 for k in VEREDITOS}
    # `decidido_pela_fonte` PRECISA existir aqui.
    #
    # O caminho da ficha (iFood e Airbnb) grava o veredito e SO DEPOIS soma no
    # placar. Sem a chave, o `+=` estourava `KeyError` com a linha ja gravada:
    # o veredito ia para o banco e o log dizia FALHOU. Passou despercebido
    # enquanto a falha era so uma linha de log — os 402 vereditos de ficha ja
    # gravados estao corretos.
    #
    # O que revelou foi o freio de 07/09/2026: como agora a falha devolve o POI
    # a fila e conta serie, e a nova ordem poe iFood e Airbnb NA FRENTE, deram
    # 28 falhas seguidas na largada e a rodada parou — corretamente, dizendo
    # que o problema nao era o POI.
    placar.update({"sem_evidencia": 0, "falha_percepcao": 0,
                   "falha_julgamento": 0, "fora_da_escala": 0,
                   "decidido_pela_fonte": 0,
                   "reconferido_virou_revisao": 0})
    trava = threading.Lock()
    t0 = time.time()

    import concurrent.futures
    fila = list(lista)

    poco = Poco(min(CONEXOES, max(1, trabalhadores)))

    # O freio e compartilhado: quem zera e quem conta sao trabalhadores
    # diferentes, e o que interessa e a serie do CONJUNTO, nao a de cada um.
    freio = {"seguidas": 0, "parar": False}

    def obreiro(_n):
        while True:
            with trava:
                if freio["parar"] or not fila:
                    return
                a = fila.pop(0)
            try:
                # QUEM EMPRESTA E `um_poi`, e nao este laco. Embrulhar a
                # chamada inteira aqui era o que segurava uma sessao do pooler
                # durante os 29 s de modelo — ver a docstring de `um_poi`.
                um_poi(poco, a, modelo, secoes, placar, trava, aplicar)
                with trava:
                    freio["seguidas"] = 0
            except Exception as e:                             # noqa: BLE001
                with trava:
                    freio["seguidas"] += 1
                    _log("   %8d FALHOU %s: %s"
                         % (a["id"], type(e).__name__, str(e)[:70]))
                    # O POI VOLTA PARA A FILA. Ele nao tem culpa de o banco ter
                    # caido, e sem isto uma soluco de rede vira buraco
                    # silencioso na cobertura — o item sai da fila sem veredito
                    # e ninguem fica sabendo.
                    # UMA segunda chance, nao infinitas: um POI que
                    # falha sempre — JSON que nunca fecha, imagem corrompida —
                    # voltaria para sempre e a fila nunca esvaziaria.
                    a["_tentativas"] = a.get("_tentativas", 0) + 1
                    if a["_tentativas"] < 2:
                        fila.append(a)
                    if freio["seguidas"] >= FALHAS_SEGUIDAS_LIMITE:
                        freio["parar"] = True
                        _log("   PARANDO: %d falhas seguidas. Nao e o POI, e o "
                             "banco ou o modelo. A fila fica com %d itens "
                             "intactos." % (freio["seguidas"], len(fila)))
                time.sleep(1.0)

    try:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, trabalhadores)) as piscina:
            list(piscina.map(obreiro, range(max(1, trabalhadores))))
    finally:
        poco.fechar()

    dt = time.time() - t0
    _log("")
    for k in VEREDITOS:
        _log("   %-20s %5d" % (k, placar[k]))
    for k in ("sem_evidencia", "falha_percepcao", "falha_julgamento",
              "fora_da_escala"):
        if placar[k]:
            _log("   %-20s %5d" % (k, placar[k]))
    _log("   %d POI(s) em %.1f min · %.1f s por POI"
         % (len(lista), dt / 60, dt / max(len(lista), 1)))
    return {"alvos": len(lista), **placar, "segundos": dt}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=3)
    p.add_argument("--modelo", default=MODELO_PADRAO)
    p.add_argument("--poi", action="append", type=int,
                   help="repetível; avalia estes POIs ignorando a fila")
    p.add_argument("--refazer", action="store_true",
                   help="reavalia quem já tem veredito")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ veredito da IA — percepção cega, julgamento isolado (%s)" % a.modelo)
    r = rodar(a.area, a.limite, a.aplicar, a.trabalhadores, a.modelo,
              a.poi, a.refazer)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
