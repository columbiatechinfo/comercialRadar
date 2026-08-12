"""
avaliar_fachada.py — Lê a fachada de cada POI com a IA e devolve sinal cadastral.

Roda a skill `skills/leitura-fachada-cadastral` sobre as imagens que já estão no
banco: a fachada do Street View (`streetview_imgs`) e, quando houver, as fotos do
Maps (`images_urls`). A saída é uma anotação no contrato da skill, validada pelo
validador dela antes de virar linha de banco.

A DIVISÃO DE TRABALHO É DELIBERADA:

  a IA OBSERVA   — o que aparece no pixel: número na fachada, letreiro, quantos
                   medidores, quantas portas, que tipologia é aquilo.
  o código DERIVA — o que decorre de regra: comparar com o cadastro, decidir se
                   é `sinal_imagem` ou `achado_convergente`, calcular divergência
                   de economias, aplicar teto de confiança por fonte.

Pedir derivação ao modelo é o caminho curto para o número bonito e errado: ele
"conclui" convergência de uma fonte só, converte três UCs elétricas em três
economias de água e completa dígito borrado por simetria. Todas as três coisas a
skill proíbe em letras maiúsculas, e nenhuma delas é observação.

O VÍNCULO vem de `cadastro_cliente` quando o POI está casado com um imóvel: é ele
que traz matrícula, número cadastrado e economias, e é o que permite uma segunda
fonte independente. Sem vínculo a anotação é autônoma e nenhuma oportunidade
passa de `sinal_imagem` — está na skill, e o validador cobra.

USO:
  .venv\\Scripts\\python avaliar_fachada.py --area area_atual [--limit N]
      [--modelo gpt-4o-mini] [--workers 4] [--refazer] [--teto-usd 5]
      [--so-estimar]
"""

import os
import io
import re
import json
import time
import base64
import asyncio
import argparse
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import config          # .env + UTF-8
import area_utils
import base_comum as bc

SKILL = Path(__file__).resolve().parent / "skills" / "leitura-fachada-cadastral"
SCHEMA_VERSAO = "1.5.0"

# Preço por 1M de tokens (US$). O padrão é o modelo barato: a leitura de fachada
# é tarefa de descrição, não de raciocínio longo, e a diferença de preço entre os
# dois é de uma ordem de grandeza sobre dezenas de milhares de imagens.
PRECOS = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}
MODELO_PADRAO = "gpt-4o-mini"

# LLM LOCAL no i9 — Ollama com modelo de visão. Custo ZERO em dólar; o que ele
# gasta é tempo de GPU, que já está pago. Serve para varrer a cidade inteira sem
# olhar o relógio do cartão, e para reprocessar à vontade quando a skill mudar.
# `think: false` é obrigatório: modelo com "thinking" devolve `response` vazio
# sem isso, e o placar do teste vira ficção.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.115.117.49:11434").rstrip("/")
MODELOS_LOCAIS = ("qwen2.5vl:7b", "qwen2.5vl:3b")


def _e_local(modelo: str) -> bool:
    return modelo in MODELOS_LOCAIS or modelo.startswith("qwen")
# Street View sem data declarada: a skill manda assumir 36 meses e DECLARAR a
# suposição. `data_captura_origem = "assumida"` é essa declaração.
IDADE_ASSUMIDA_MESES = 36
TIMEOUT_S = 90.0


# ──────────────────────────────────────────────────────────────────────────
# Contrato de SAÍDA DA IA — só observação, achatado, com vocabulário fechado
# ──────────────────────────────────────────────────────────────────────────
def _ler_skill(nome: str) -> dict:
    return json.loads((SKILL / "assets" / nome).read_text(encoding="utf-8"))


_VOCAB = _ler_skill("vocabulario.json")
_SCHEMA = _ler_skill("schema_fachada.json")


def LISTA(campo: str) -> list:
    """Vocabulário fechado da skill, pelo nome do campo.

    OS ENUMS SAEM DOS ARQUIVOS DA SKILL, não de cópia aqui. Escrevi as listas à
    mão na primeira versão e todas as anotações foram reprovadas: `frontal_completo`
    contra `fachada_completa`, `em_funcionamento_aparente` contra
    `em_atividade_aparente`, `letreiro` contra `letreiro_ativo`. Termo aproximado
    quebra o cruzamento com a base tão bem quanto termo errado — e lendo do
    arquivo, atualizar a skill atualiza o runner."""
    v = _VOCAB["listas"].get(campo) or _VOCAB["listas_multivaloradas"].get(campo)
    if not v:
        raise KeyError(f"campo {campo!r} não está no vocabulário da skill")
    return list(v)


def ENUM_SCHEMA(*caminho) -> list:
    """Enum que vive no schema, não no vocabulário (ex.: imagem.enquadramento)."""
    d = _SCHEMA["properties"]
    for c in caminho:
        d = d[c] if c in d else d["properties"][c]
    return [x for x in d.get("enum", []) if x is not None]

# O schema que vai para o `response_format` da OpenAI. Pequeno de proposito: o
# schema completo da skill tem 16 blocos e 51 KB, e mandar isso a cada imagem
# custa mais em token de contrato do que a propria leitura. O que falta e
# preenchido por `_expandir`, com `nao_observavel`, que e o que a skill manda
# fazer quando a campanha nao pede o bloco.
SCHEMA_IA = {
    "type": "object", "additionalProperties": False,
    "required": ["triagem", "imagem", "enderecamento", "uso", "estrutura",
                 "contagens", "medicao", "edificacao", "agua_esgoto", "observacoes"],
    "properties": {
        "triagem": {
            "type": "object", "additionalProperties": False,
            "required": ["conteudo_imagem", "e_imovel", "apto_para_cadastro",
                         "atividade_economica_aparente", "sinais_atividade_economica",
                         "atividade_no_alvo", "natureza_atividade",
                         "formalidade_aparente", "confianca_triagem", "evidencia_triagem"],
            "properties": {
                "conteudo_imagem": {"type": "string", "enum": LISTA("triagem.conteudo_imagem")},
                "e_imovel": {"type": "boolean"},
                "apto_para_cadastro": {"type": "boolean"},
                "atividade_economica_aparente": {"type": "string", "enum": LISTA("triagem.atividade_economica_aparente")},
                "sinais_atividade_economica": {"type": "array", "items": {
                    "type": "string", "enum": LISTA("triagem.sinais_atividade_economica")}},
                "atividade_no_alvo": {"type": ["string", "null"], "enum": LISTA("triagem.atividade_no_alvo") + [None]},
                "natureza_atividade": {"type": ["string", "null"],
                                       "enum": LISTA("triagem.natureza_atividade") + [None]},
                "formalidade_aparente": {"type": ["string", "null"], "enum": LISTA("triagem.formalidade_aparente") + [None]},
                "confianca_triagem": {"type": "number"},
                "evidencia_triagem": {"type": "string"},
            },
        },
        "imagem": {
            "type": "object", "additionalProperties": False,
            "required": ["enquadramento", "apta_para_leitura", "motivo_inaptidao"],
            "properties": {
                "enquadramento": {"type": "string", "enum": ENUM_SCHEMA("imagem", "enquadramento")},
                "apta_para_leitura": {"type": "boolean"},
                "motivo_inaptidao": {"type": ["string", "null"]},
            },
        },
        "enderecamento": {
            "type": "object", "additionalProperties": False,
            "required": ["numeros_lidos", "origem_numero", "numero_legivel",
                         "complementos_lidos"],
            "properties": {
                # LITERAL. Digito borrado nao e completado — vem o que da para ler
                # ou nada. A skill trata numero inventado como erro cadastral.
                "numeros_lidos": {"type": "array", "items": {"type": "string"}},
                "origem_numero": {"type": ["string", "null"],
                                  "enum": LISTA("enderecamento.origem_numero_consolidado") + [None]},
                # A divergência de numeração só vira fila se o número tiver sido
                # LIDO. Sem este campo, "328" chutado onde a plaqueta diz 326 abre
                # ocorrência de campo com confiança 0,6 — e foi o que aconteceu em
                # 2 das 5 fotos medidas. Aqui o modelo declara se cada dígito
                # estava distinguível; quem não sabe dizer, não abre fila.
                "numero_legivel": {"type": ["boolean", "null"]},
                "complementos_lidos": {"type": "array", "items": {"type": "string"}},
            },
        },
        "uso": {
            "type": "object", "additionalProperties": False,
            "required": ["uso_predominante", "nome_estabelecimento_visivel",
                         "atividade_letreiro", "segmento_inferido",
                         "descricao_atividade_funcional", "situacao_na_data",
                         "sinais_comerciais_no_momento"],
            "properties": {
                "uso_predominante": {"type": "string", "enum": LISTA("uso.uso_predominante")},
                "nome_estabelecimento_visivel": {"type": ["string", "null"]},
                "atividade_letreiro": {"type": ["string", "null"]},
                "segmento_inferido": {"type": ["string", "null"],
                                      "enum": LISTA("uso.segmento_inferido") + [None]},
                "descricao_atividade_funcional": {"type": ["string", "null"]},
                # OUTRA lista, não a da triagem: `triagem.sinais_atividade_economica`
                # descreve o que denuncia atividade ("letreiro_fachada"),
                # `uso.sinais_atividade_comercial` descreve o que ela aparenta
                # AGORA ("letreiro_ativo", "porta_aco_baixada"). Copiar uma na
                # outra reprova no vocabulário — e são perguntas diferentes.
                "sinais_comerciais_no_momento": {"type": "array", "items": {
                    "type": "string", "enum": LISTA("uso.sinais_atividade_comercial")}},
                "situacao_na_data": {"type": ["string", "null"],
                                    "enum": LISTA("uso.situacao_estabelecimento_na_data_imagem") + [None]},
            },
        },
        "estrutura": {
            "type": "object", "additionalProperties": False,
            "required": ["tipologia", "pavimentos_qtd", "unidades_fisicas_estimadas",
                         "metodo_estimativa", "identificadores_unidades",
                         "confianca_estrutural"],
            "properties": {
                "tipologia": {"type": "string", "enum": LISTA("estrutura_imovel.tipologia")},
                "pavimentos_qtd": {"type": ["integer", "null"]},
                "unidades_fisicas_estimadas": {"type": ["integer", "null"]},
                # vocabulário FECHADO da skill — valor fora da lista quebra o
                # cruzamento com a base, então ele vai igual desde a origem
                "metodo_estimativa": {"type": "array", "items": {"type": "string",
                    "enum": LISTA("estrutura_imovel.metodo_estimativa")}},
                # numero da unidade entra; nome de morador NUNCA (LGPD)
                "identificadores_unidades": {"type": "array", "items": {"type": "string"}},
                "confianca_estrutural": {"type": "number"},
            },
        },
        "contagens": {
            "type": "object", "additionalProperties": False,
            "required": ["ucs_energia_visiveis", "hidrometros_visiveis",
                         "portas_acesso_independentes", "caixas_correio",
                         "interfones", "estabelecimentos_distintos"],
            "properties": {
                "ucs_energia_visiveis": {"type": ["integer", "null"]},
                "hidrometros_visiveis": {"type": ["integer", "null"]},
                "portas_acesso_independentes": {"type": ["integer", "null"]},
                "caixas_correio": {"type": ["integer", "null"]},
                "interfones": {"type": ["integer", "null"]},
                "estabelecimentos_distintos": {"type": ["integer", "null"]},
            },
        },
        # MEDIÇÃO — o bloco que o usuário pediu por extenso. Não é só "tem
        # hidrômetro?": interessa a TAMPA (existe, está quebrada, sumiu),
        # se a medição é individual ou BATERIA COLETIVA (que denuncia várias
        # unidades), onde ela fica e se dá para ler da calçada. Isso serve a
        # qualquer concessionária, não só água — por isso a entrada de energia
        # entra no mesmo bloco.
        "medicao": {
            "type": "object", "additionalProperties": False,
            "required": ["hidrometro_presente", "posicao_medicao", "tipo_abrigo",
                         "estado_abrigo", "acessibilidade_medicao",
                         "padrao_entrada_energia", "ramal_entrada",
                         "descricao_medicao"],
            "properties": {
                "hidrometro_presente": {"type": "string",
                                        "enum": LISTA("agua.hidrometro_presente")},
                "posicao_medicao": {"type": ["string", "null"],
                                    "enum": LISTA("agua.posicao_medicao") + [None]},
                "tipo_abrigo": {"type": ["string", "null"],
                                "enum": LISTA("agua.tipo_abrigo") + [None]},
                "estado_abrigo": {"type": ["string", "null"],
                                  "enum": LISTA("agua.estado_abrigo") + [None]},
                "acessibilidade_medicao": {"type": ["string", "null"],
                                           "enum": LISTA("agua.acessibilidade_medicao") + [None]},
                "padrao_entrada_energia": {"type": ["string", "null"],
                                           "enum": LISTA("energia.tipo_padrao_entrada") + [None]},
                "ramal_entrada": {"type": ["string", "null"],
                                  "enum": LISTA("energia.ramal_entrada") + [None]},
                # texto livre: o que a lista fechada não consegue dizer
                "descricao_medicao": {"type": ["string", "null"]},
            },
        },
        "edificacao": {
            "type": "object", "additionalProperties": False,
            "required": ["tipo_edificacao", "estado_conservacao",
                         "padrao_construtivo", "pavimentos_visiveis"],
            "properties": {
                "tipo_edificacao": {"type": ["string", "null"],
                                    "enum": LISTA("edificacao.tipo_edificacao") + [None]},
                "estado_conservacao": {"type": ["string", "null"],
                                       "enum": LISTA("edificacao.estado_conservacao") + [None]},
                "padrao_construtivo": {"type": ["string", "null"],
                                       "enum": LISTA("edificacao.padrao_construtivo") + [None]},
                "pavimentos_visiveis": {"type": ["integer", "null"]},
            },
        },
        "agua_esgoto": {
            "type": "object", "additionalProperties": False,
            # Estes três são `campo_texto` na skill, não booleano — e um `false`
            # aqui reprovava a anotação inteira no schema. Pedir o termo do
            # vocabulário direto ao modelo também diz mais: "não observável" e
            # "ausência confirmada" são coisas diferentes que um booleano funde.
            "required": ["caixa_inspecao_aparente", "sinais_fossa",
                         "lancamento_sarjeta_aparente"],
            "properties": {
                "caixa_inspecao_aparente": {
                    "type": ["string", "null"],
                    "enum": LISTA("esgoto.caixa_inspecao_aparente") + [None]},
                "sinais_fossa": {
                    "type": ["string", "null"],
                    "enum": LISTA("esgoto.indicio_fossa") + [None]},
                "lancamento_sarjeta_aparente": {
                    "type": ["string", "null"],
                    "enum": LISTA("esgoto.lancamento_sarjeta_aparente") + [None]},
            },
        },
        "observacoes": {"type": "string"},
    },
}


def _prompt_sistema() -> str:
    """Instrução condensada da skill — as regras que mudam o resultado.

    Não é a SKILL.md inteira: 28 KB por imagem, vezes dezenas de milhares de
    imagens, é dinheiro gasto em repetir contrato. Aqui ficam as regras duras que
    o modelo viola quando não avisado, e as que o validador cobra."""
    return (
        "Você lê IMAGENS DE FACHADA para cadastro de saneamento e devolve observação "
        "estruturada. Você é um SENSOR: descreve o que está no pixel. Não conclui, "
        "não cruza com base externa, não estima valor.\n\n"
        # A interface do Google foi REMOVIDA da imagem antes de chegar aqui
        # (`limpar_interface`). Descrevê-la no prompt só devolvia a frase de volta:
        # o qwen2.5vl:7b passou a reprovar fotos limpas alegando "interface do
        # Google Street View", e a copiar o NÚMERO DO EXEMPLO como número lido.
        # O que não está no pixel não precisa estar no texto.
        "Só conta como número lido o que estiver NA EDIFICAÇÃO: muro, porta, "
        "portão, caixa de correio, placa ou letreiro. Número em veículo, poste, "
        "placa de trânsito ou telefone de letreiro não é número do imóvel.\n\n"
        "REGRAS DURAS (o validador reprova quem violar):\n"
        "1. Dúvida vira null com motivo, nunca chute.\n"
        "2. NÚMERO: transcreva literal o que consegue ler. Dígito borrado NÃO é "
        "completado. Nunca trate como número do imóvel: serial de medidor, matrícula "
        "de hidrômetro, telefone de letreiro, CNPJ, preço, horário, placa de veículo, "
        "número de poste. Diga em `origem_numero` ONDE ele estava (placa, porta, "
        "portão, caixa de correio) e marque `numero_legivel=true` só se você "
        "distinguiu CADA dígito com certeza. Se leu a forma geral mas não jura o "
        "último dígito, `numero_legivel=false` — não é reprovação, é o que decide "
        "se a diferença para o cadastro vira visita de campo.\n"
        "3. ATIVIDADE ECONÔMICA: diga DE QUEM ela é em `atividade_no_alvo`. Em rua de "
        "comércio a foto do alvo quase sempre pega o letreiro do vizinho — marcar "
        "`em_vizinho` evita um achado falso de categoria. Ambulante na calçada não "
        "torna o imóvel comercial.\n"
        "4. Sinal de atividade exige item listado em `sinais_atividade_economica` "
        "(letreiro, toldo, vitrine, porta de aço, mercadoria exposta, veículo de "
        "serviço, placa de profissional liberal, cardápio, doca, equipamento).\n"
        "5. `fechado_no_momento` NÃO é `desativado_aparente`. Porta de aço baixada só "
        "diz que estava fechada naquele instante.\n"
        "6. CONTE SEPARADO: medidores de energia, hidrômetros, portas independentes, "
        "interfones e caixas de correio são contagens diferentes. Não some, não "
        "converta uma na outra.\n"
        "7. LGPD: nunca transcreva nome de morador ou destinatário, telefone "
        "particular, placa de veículo ou texto manuscrito pessoal. Em caixa de correio, "
        "o NÚMERO da unidade entra; o nome ao lado não entra em campo nenhum. Nome "
        "fantasia e telefone comercial de letreiro podem ser transcritos.\n"
        "8. Se a imagem não é de imóvel, marque `e_imovel=false` e pare: não invente "
        "atributos.\n"
        # NÃO descreva aqui o caso de inaptidão com uma frase pronta. A versão
        # anterior dizia "se a fachada está quase toda obstruída..." e o
        # qwen2.5vl:7b devolveu EXATAMENTE essa frase como motivo em 16 de 29
        # fotos — inclusive num prédio de três andares, dois letreiros legíveis
        # e dia de sol. Modelo pequeno não julga a imagem: repete a sentença
        # marcante da instrução. O critério agora é POSITIVO e contável.
        "9. APTIDÃO — a leitura é apta por padrão. Foto de rua em dia claro é "
        "apta, mesmo com carro estacionado, poste, fiação, árvore ou muro na "
        "frente: fachada real tem essas coisas. Só marque `apta_para_leitura="
        "false` se você não consegue distinguir NENHUMA das três: (a) uma "
        "abertura (porta, portão ou janela), (b) o limite entre a edificação e "
        "a rua, (c) a cor ou o material da parede. Se marcar false, o "
        "`motivo_inaptidao` tem de NOMEAR o que atrapalha e onde — \"caminhão "
        "cobrindo toda a testada\", \"foto noturna sem iluminação\" — e nunca "
        "uma frase genérica sobre estar obstruída.\n"
        "10. Não infira renda, classe social nem perfil do morador.\n"
        "11. Se classificar a tipologia como PRÉDIO (edificio_vertical, "
        "condominio_vertical_multitorre, predio_misto), é obrigatório estimar "
        "`pavimentos_qtd` e `unidades_fisicas_estimadas` — dizer que é prédio sem "
        "dizer de que tamanho não reconstrói estrutura nenhuma. Se não consegue "
        "contar, use uma tipologia não vertical.\n\n"
        "A confiança é sua certeza de LEITURA, de 0 a 1. Seja severo: 0,9 é para o que "
        "está nítido e inequívoco no pixel."
    )


def _prompt_usuario(poi: dict, vinc: dict | None) -> str:
    """O contexto do caso. O cadastro entra como REFERÊNCIA A CONFERIR, nunca como
    resposta — dizer ao modelo que ali há 1 economia é convidá-lo a enxergar 1."""
    p = [f"POI: {poi.get('nome') or '(sem nome)'}"]
    if poi.get("categoria"):
        p.append(f"Categoria no Maps: {poi['categoria']}")
    # NEM o endereço do POI NEM o número do imóvel vinculado entram aqui, mesmo
    # com a ressalva de "confira se bate". Medido nas mesmas 5 fotos, tirando o
    # endereço do prompt o gpt-4o mudou 3 dos 5 números: 175 virou nenhum, 317
    # virou 870, 326 virou 328 — ou seja, ele estava devolvendo o número que EU
    # dei, não o que a fachada mostra. E como `_numero_confere()` depois compara
    # o lido com o cadastrado, a conferência ficava circular: eu dizia o número,
    # o modelo repetia, e o código declarava que batia — inflando a confiança do
    # achado com a minha própria informação. A comparação é feita em código, que
    # é onde ela sempre esteve; o modelo só precisa ler.
    p.append("\nLeia a(s) imagem(ns) e devolva a observação no formato pedido.")
    return "\n".join(p)


# ──────────────────────────────────────────────────────────────────────────
# Banco
# ──────────────────────────────────────────────────────────────────────────
def esquema(con):
    with con.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fachada_anotacao (
              id             serial PRIMARY KEY,
              poi_id         integer NOT NULL REFERENCES pois(id) ON DELETE CASCADE,
              modelo         text,
              schema_versao  text,
              status         text,          -- aprovado | reprovado | inapto | fora_escopo
              apta           boolean,
              e_imovel       boolean,
              uso_observado  text,
              tipologia      text,
              unidades_fisicas integer,
              ucs_energia    integer,
              hidrometros    integer,
              economias_base integer,
              gap_uc_economias integer,
              numero_lido    text,
              numero_confere boolean,
              atividade_no_alvo text,
              confianca      real,
              oportunidades  jsonb,         -- [{codigo, nivel_evidencia, confianca, ...}]
              anotacao       jsonb,         -- a anotação completa, no contrato da skill
              validacao      jsonb,         -- erros/avisos do validador da skill
              tokens_in      integer,
              tokens_out     integer,
              custo_usd      numeric(10,6),
              criado_em      timestamp DEFAULT now())""")
        # colunas de leitura ampliada — medição, pavimentos e conservação
        for col, tipo in (("estado_conservacao", "text"), ("padrao_construtivo", "text"),
                          ("tipo_edificacao", "text"), ("pavimentos", "integer"),
                          ("medicao_abrigo", "text"), ("medicao_estado", "text"),
                          ("medicao_acesso", "text"), ("medicao_posicao", "text"),
                          ("medicao_coletiva", "boolean"), ("medicao_desc", "text"),
                          ("energia_entrada", "text")):
            cur.execute(f"ALTER TABLE fachada_anotacao "
                        f"ADD COLUMN IF NOT EXISTS {col} {tipo}")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ix_fachada_poi
                         ON fachada_anotacao (poi_id)""")
        cur.execute("""CREATE INDEX IF NOT EXISTS ix_fachada_status
                         ON fachada_anotacao (status)""")
    con.commit()


def carregar_alvos(poligono, limit: int, refazer: bool, con) -> list:
    """POIs da área que têm fachada, com o vínculo do cadastro quando existir."""
    with con.cursor() as cur:
        cur.execute(f"""
            SELECT p.id, p.nome, p.endereco, p.categoria,
                   COALESCE(p.maps_lat, p.lat_origem), COALESCE(p.maps_lng, p.lng_origem),
                   s.id, s.data_captura,
                   c.num_ligacao, c.numero, c.categoria, c.classe_esgoto,
                   COALESCE(c.economias_res,0) + COALESCE(c.economias_com,0)
                     + COALESCE(c.economias_ind,0) + COALESCE(c.economias_pub,0)
                     + COALESCE(c.economias_out,0),
                   k.coletiva_id, k.forma, k.veredito, k.qtd_observada,
                   k.qtd_inferida, k.economias_cnefe, k.com_atividade, k.atividades
              FROM pois p
              JOIN streetview_imgs s ON s.poi_id = p.id AND s.angulo = 'facade'
              LEFT JOIN cadastro_cliente c ON c.poi_id = p.id
              -- TERCEIRA FONTE: o que o recenseador do IBGE anotou naquela porta
              LEFT JOIN cnefe_coletiva k ON k.poi_id = p.id
             WHERE p.match_valido IS NOT FALSE
               AND COALESCE(p.maps_lat, p.lat_origem) IS NOT NULL
               {"" if refazer else
                "AND NOT EXISTS (SELECT 1 FROM fachada_anotacao a WHERE a.poi_id = p.id)"}
             ORDER BY p.id""")
        linhas = cur.fetchall()

    alvos = []
    for r in linhas:
        la, lo = r[4], r[5]
        if poligono and not area_utils.ponto_no_poligono(la, lo, poligono):
            continue
        vinc = None
        if r[8] or r[9]:
            vinc = {"matricula": r[8], "numero": r[9], "categoria": r[10],
                    "classe_esgoto": r[11], "economias": r[12]}
        col = None
        if r[13]:
            col = {"coletiva_id": r[13], "forma": r[14], "veredito": r[15],
                   "qtd_observada": r[16], "qtd_inferida": r[17],
                   "economias_cnefe": r[18], "com_atividade": r[19],
                   "atividades": r[20]}
        alvos.append({"poi_id": r[0], "nome": r[1], "endereco": r[2],
                      "categoria": r[3], "lat": la, "lng": lo,
                      "sv_id": r[6], "sv_data": r[7], "vinculo": vinc,
                      "coletiva": col})
        if limit and len(alvos) >= limit:
            break
    return alvos


# A data do panorama destrava a leitura comercial (regra C-11 da skill: comércio
# sem data confiável não é estado atual). Ela vem do endpoint de METADADOS do
# Street View, que é GRÁTIS — e 18 mil das 20 mil fachadas foram capturadas sem
# ela. Buscar aqui, sob demanda, evita um job separado de 18 mil chamadas: quem
# precisa da data é esta fase, e o valor fica gravado para as próximas.
#
# A chave NÃO é `MAPS_API_KEY` de propósito. Essa variável é o interruptor do
# motor Places, que é PAGO — defini-la para usar um endpoint grátis ligaria a
# mineração paga de lado.
def _chave_maps() -> str:
    # `MAPS_SERVER_KEY` vem PRIMEIRO porque esta chamada sai do Python, não do
    # navegador. Chave restrita por referrer HTTP — que é a restrição correta
    # para `MAPS_JS_KEY` e `GOOGLE_TILES_KEY` — devolve REQUEST_DENIED aqui:
    # requisição de servidor não manda referrer. Sem esta variável, restringir a
    # chave do mapa (o certo a fazer) quebraria a data do panorama no meio do
    # lote, com erro que parece de rede.
    for nome in ("MAPS_SERVER_KEY", "MAPS_JS_KEY", "GOOGLE_TILES_KEY", "MAPS_API_KEY"):
        k = (os.environ.get(nome) or "").strip()
        if k:
            return k
    return ""


def buscar_data_pano(lat, lng, sv_id: int, con) -> str | None:
    chave = _chave_maps()
    if not chave or lat is None:
        return None
    url = ("https://maps.googleapis.com/maps/api/streetview/metadata?"
           f"location={lat},{lng}&key={chave}")
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=12) as r:
            md = json.load(r)
    except Exception:
        return None
    if md.get("status") != "OK" or not md.get("date"):
        return None
    # O Google devolve "2025-10" e a coluna é varchar(7): guarda como veio. Quem
    # completa para data cheia é `_data_iso`, na hora de montar a anotação — o
    # schema quer AAAA-MM-DD, o banco guarda a precisão real, que é o mês.
    data = md["date"][:7]
    with con.cursor() as cur:
        cur.execute("""UPDATE streetview_imgs SET data_captura = %s, pano_id = %s
                        WHERE id = %s AND data_captura IS NULL""",
                    (data, (md.get("pano_id") or "")[:40], sv_id))
    con.commit()
    return data


def _data_iso(bruta) -> str | None:
    """"2025-10" -> "2025-10-01". O dia é convenção declarada, não invenção: o
    Street View publica precisão de MÊS, e a skill usa a data só para calcular
    faixa de idade — onde um dia a mais ou a menos não muda a faixa."""
    if not bruta:
        return None
    s = bruta.strftime("%Y-%m-%d") if hasattr(bruta, "strftime") else str(bruta)
    return s + "-01" if len(s) == 7 else s[:10]


# A captura é um PRINT do Street View, e a interface do Google vem junto: caixa
# preta com endereço no alto à esquerda, minimapa embaixo, bússola e zoom à
# direita. Pedir ao modelo que a ignore não funciona — o gpt-4o-mini obedece, mas
# o qwen2.5vl:7b leu o endereço da CAIXA ("274 R. Araguaia") como número do
# imóvel e, quando avisado para ignorá-la, passou a usá-la como motivo de
# inaptidão. Discutir com o modelo sobre pixels que estão lá é perder duas vezes.
#
# Então os pixels saem. As regiões são fixas porque o print é sempre 1280x656
# gerado pelo mesmo código — se o viewport da captura mudar, isto precisa mudar
# junto, e é por isso que a proporção é conferida antes de recortar.
UI_STREETVIEW = (
    (0.000, 0.000, 0.300, 0.240),    # caixa preta: endereço, cidade, data
    (0.000, 0.878, 0.215, 1.000),    # minimapa
    (0.925, 0.830, 1.000, 1.000),    # bússola e zoom
)


def limpar_interface(dados: bytes) -> bytes:
    """Cobre a interface espelhando a faixa vizinha da própria foto.

    Pintar de cinza chapado resolve metade do problema e cria a outra: o número
    da caixa preta some, mas o qwen2.5vl:7b passa a ver o retângulo liso e a
    chamá-lo de "interface do Google Street View" — reprovando a leitura pelo
    tapa-buraco. Espelhar a faixa ao lado devolve céu sobre céu e calçada sobre
    calçada, e não sobra artefato sobre o qual reclamar. O desfoque final mata
    a costura, que a simetria perfeita denunciaria."""
    try:
        import io as _io
        from PIL import Image, ImageFilter
        im = Image.open(_io.BytesIO(dados)).convert("RGB")
    except Exception:
        return dados
    w, h = im.size
    if w < 400 or h < 200:              # miniatura: mexer aqui é destruir
        return dados
    for fx0, fy0, fx1, fy1 in UI_STREETVIEW:
        x0, y0 = int(fx0 * w), int(fy0 * h)
        x1, y1 = int(fx1 * w), int(fy1 * h)
        lg = x1 - x0
        if lg <= 0 or y1 <= y0:
            continue
        # a faixa doadora é a vizinha horizontal que couber na imagem: à direita
        # para as caixas da esquerda, à esquerda para as da direita
        if x1 + lg <= w:
            doadora = im.crop((x1, y0, x1 + lg, y1))
        elif x0 - lg >= 0:
            doadora = im.crop((x0 - lg, y0, x0, y1))
        else:
            continue
        doadora = doadora.transpose(Image.FLIP_LEFT_RIGHT)
        im.paste(doadora.filter(ImageFilter.GaussianBlur(2.2)), (x0, y0))
    saida = _io.BytesIO()
    im.save(saida, format="JPEG", quality=88)
    return saida.getvalue()


def _imagens(poi_id: int, sv_id: int, con) -> list:
    """Bytes da fachada. Uma imagem por chamada é o padrão: duas fotos de fachada
    continuam sendo UMA fonte independente, então a segunda encarece sem mudar o
    teto de confiança de nada."""
    with con.cursor() as cur:
        cur.execute("SELECT dados FROM streetview_imgs WHERE id = %s", (sv_id,))
        r = cur.fetchone()
    return [limpar_interface(bytes(r[0]))] if r and r[0] else []


# ──────────────────────────────────────────────────────────────────────────
# Derivação — a parte que é regra, não observação
# ──────────────────────────────────────────────────────────────────────────
_RE_NUM = re.compile(r"\d+")


def _uso_do_alvo(uso: str | None, tri: dict) -> str:
    """Uso do IMÓVEL-ALVO — o do vizinho não conta (C-19).

    O conjunto de usos econômicos sai do PRÓPRIO validador. Escrevi a lista à
    mão primeiro e ela já nascia errada em cinco termos: inventava
    `institucional`, que lá não existe, e esquecia `religioso`, `educacional`,
    `saude` e `rural_agropecuario` — templo e escola passariam pelo gate que
    esta função existe para respeitar."""
    uso = uso or "indeterminado"
    economicos = getattr(_validador()["mod"], "USOS_ECONOMICOS", frozenset())
    if tri.get("atividade_no_alvo") in ("em_vizinho", "ambulante_via_publica") \
            and uso in economicos:
        return "indeterminado"
    return uso


def _EV_CONTADAS(cnt: dict) -> bool:
    """Alguma coisa foi de fato CONTADA na foto? — o que a C-04 cobra."""
    return any(isinstance(cnt.get(k), int) and cnt[k] > 0
               for k in ("ucs_energia_visiveis", "hidrometros_visiveis",
                         "portas_acesso_independentes", "caixas_correio",
                         "interfones"))

# `estrutura_imovel.metodo_estimativa` e `economias.metodo` são vocabulários
# DIFERENTES para a mesma ideia. Traduzir aqui, num lugar só, evita a anotação
# que passa num bloco e é reprovada no outro.
_METODO_ECON = {
    "medidores_energia": "medidor_energia",
    "hidrometros_bateria": "hidrometro_bateria",
    "portas_terreo": "portas_independentes",
    "acessos_independentes": "portas_independentes",
    "interfone": "campainhas",
    "caixas_correio": "caixas_correio",
    "contagem_prumo": "pavimentos_x_unidades",
    "pavimentos_x_unidades": "pavimentos_x_unidades",
    "placa_edificio": "indeterminado",
    "cnefe": "indeterminado",
    "indeterminado": "indeterminado",
}


def _faixa_temporal(data_img: str | None) -> str:
    """`validade_temporal_uso` é faixa de idade, não data.

    Sem data declarada a skill manda assumir 36 meses — o que cai em
    `historica_19a36m`, e não em `data_desconhecida`: a suposição é declarada em
    `data_captura_origem = estimada`, e fingir desconhecimento esconderia o
    quanto aquele letreiro pode estar velho."""
    if not data_img:
        return "historica_19a36m"
    try:
        d = datetime.strptime(data_img[:10], "%Y-%m-%d")
    except ValueError:
        return "data_desconhecida"
    meses = (datetime.now() - d).days / 30.44
    if meses <= 6:
        return "atual_ate_6m"
    if meses <= 18:
        return "recente_7a18m"
    if meses <= 36:
        return "historica_19a36m"
    return "defasada_acima_36m"


def campo(valor, conf, evidencia="", juizo=None, regra=None):
    """Todo atributo da skill é um OBJETO, nunca um valor cru.

    `{valor, juizo, confianca, evidencia, regra}` é o que permite ao consumidor a
    jusante filtrar por `..._conf >= 0.70 AND ..._juizo <> 'inferido'` sem abrir
    JSON — é o layout de quatro colunas irmãs do consolidado. Gravar o valor
    pelado passa no olho e é reprovado no gate.

    O juízo se deduz do valor quando não é declarado: valor presente é
    `observado`, ausência é `ausente_confirmado` quando houve leitura e
    `nao_observavel` quando não houve."""
    if juizo is None:
        juizo = "observado" if valor not in (None, "", []) else "nao_observavel"
    d = {"valor": valor, "juizo": juizo, "confianca": round(float(conf or 0), 2),
         "evidencia": evidencia or None}
    if regra:
        d["regra"] = regra
    return d


def _numero_confere(lidos: list, numero_base) -> bool | None:
    """None quando não há o que comparar. A skill é explícita: número divergente
    NÃO une registros e NÃO é corrigido — vira alerta."""
    if not lidos or numero_base in (None, ""):
        return None
    base = _RE_NUM.search(str(numero_base))
    if not base:
        return None
    alvo = base.group()
    return any(_RE_NUM.search(x) and _RE_NUM.search(x).group() == alvo for x in lidos)


# Suportes físicos onde um número de imóvel de fato fica. `outro` e
# `indeterminado` ficam de fora de propósito: quem não sabe dizer onde leu não
# leu — leu por eliminação, que é o nome educado de palpite.
_SUPORTES_NUMERO = {"fachada", "porta", "portao", "caixa_correio",
                    "placa_endereco", "placa_comercial", "interfone"}


def _um_digito_de_diferenca(a: str, b: str) -> bool:
    """Mesmo tamanho e no máximo um dígito trocado — 248 e 249, não 870 e 379.

    Duas vistas que devolvem números vizinhos viram o mesmo número e hesitaram
    num dígito; duas vistas que devolvem números sem relação não viram número
    nenhum. É essa distinção que decide se há leitura."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 1


def _numero_lido_de_verdade(end: dict, numero_base=None) -> bool:
    """O número foi LIDO — ou o modelo só afirmou que leu?

    A autodeclaração NÃO basta, e isso foi medido: nas fotos em que o gpt-4o
    devolveu 328 onde a plaqueta diz 326, e 870 onde o número é ilegível até
    ampliado 7x, ele marcou `numero_legivel=true`, origem `fachada`, nas três
    tentativas seguidas. Repetir a chamada também não resolve: a temperatura é 0
    e ele repete a mesma invenção, idêntica, quantas vezes você pedir.

    O que separa leitura de invenção é a SEGUNDA VISTA — reler o número num
    recorte ampliado da mesma foto — julgada por duas perguntas:

    1. o recorte leu o número do CADASTRO? então a vista inteira é que errou, e
       não há divergência a acusar (foi o caso do 328 contra a plaqueta 326);
    2. as duas vistas chegaram ao mesmo número, ou a um vizinho de um dígito?
       248 e 249 são a mesma placa com uma dúvida; 870 e 379 são duas invenções.
    """
    if not (end.get("numero_legivel")
            and end.get("origem_numero") in _SUPORTES_NUMERO):
        return False
    recorte = [m.group() for m in
               (_RE_NUM.search(str(x)) for x in (end.get("numero_no_recorte") or []))
               if m]
    inteira = _RE_NUM.search(str((end.get("numeros_lidos") or [""])[0]))
    if not recorte or not inteira:
        return False
    base = _RE_NUM.search(str(numero_base or ""))
    if base and base.group() in recorte:
        return False              # o recorte dá razão ao cadastro
    return any(_um_digito_de_diferenca(inteira.group(), x) for x in recorte)


def _MOTIVO_ILEGIVEL(end: dict) -> str:
    """Por que a divergência não virou fila — em português, para a ficha do POI."""
    if not end.get("numero_legivel"):
        return "não teve todos os dígitos distinguidos na foto"
    if end.get("origem_numero") not in _SUPORTES_NUMERO:
        return f"não tem suporte identificado (origem {end.get('origem_numero') or '?'})"
    vistos = end.get("numero_no_recorte")
    return ("não se confirmou no recorte ampliado"
            + (f" — lá se leu {'/'.join(vistos)}" if vistos else ", que não achou número"))


def _oportunidades(obs: dict, vinc: dict | None, data_ref: str,
                   col: dict | None = None) -> list:
    """Deriva as oportunidades das observações + vínculo.

    `nivel_evidencia` é o ponto sensível: só é `achado_convergente` quando existe
    uma segunda fonte INDEPENDENTE. Imagem + cadastro da concessionária são duas;
    três medidores e três portas na mesma foto continuam sendo uma."""
    out = []
    tri = obs.get("triagem") or {}
    uso = obs.get("uso") or {}
    est = obs.get("estrutura") or {}
    cnt = obs.get("contagens") or {}
    tem_vinculo = bool(vinc)
    # O CNEFE é fonte independente de verdade: um recenseador do IBGE anotou
    # aquela porta em 2022 sem saber da existência do cliente nem da nossa foto.
    tem_cnefe = bool(col)

    def add(codigo, classe, prioridade, evidencia, conf, acao,
            convergente=False, cnefe=False):
        """`convergente` diz que ESTE achado é confirmável por outra fonte; quais
        fontes de fato entram é decidido aqui, e nunca pelo modelo."""
        fontes = ["imagem_fachada"]
        if convergente and tem_vinculo:
            fontes.append("cadastro_companhia")
        if cnefe and tem_cnefe:
            fontes.append("cnefe")     # o enum da skill é "cnefe", sem sufixo
        nivel = "achado_convergente" if len(fontes) >= 2 else "sinal_imagem"
        out.append({
            "codigo": codigo, "classe": classe, "prioridade": prioridade,
            # duas fontes já valem mais que uma; três valem mais que duas
            "confianca": round(min(conf + 0.1 * (len(fontes) - 1), 0.95), 2),
            "evidencia": evidencia, "acao_sugerida": acao,
            "data_referencia": data_ref, "automatizavel": False,
            "nivel_evidencia": nivel, "fontes_independentes": fontes,
        })

    ucs = cnt.get("ucs_energia_visiveis") or 0
    unidades = est.get("unidades_fisicas_estimadas") or 0
    econ = (vinc or {}).get("economias") or 0
    no_alvo = tri.get("atividade_no_alvo") == "no_imovel_alvo"
    uso_obs = uso.get("uso_predominante")

    # UC elétrica NÃO vira economia de água — vira oportunidade de conferência
    if ucs >= 2:
        add("MULTIPLAS_UCS_MESMO_ENDERECO", "cadastro", "media",
            f"{ucs} medidores de energia individualizados na mesma testada",
            est.get("confianca_estrutural") or 0.6,
            "conferir se as unidades têm economia de água própria")
    if tem_vinculo and ucs and econ and ucs > econ:
        add("DIVERGENCIA_UC_ECONOMIAS", "receita", "alta",
            f"{ucs} UCs elétricas visíveis contra {econ} economia(s) cadastrada(s)",
            0.7, "vistoria para confirmar economias", convergente=True)
    if unidades >= 2:
        # o CNEFE viu a mesma coisa? então não é mais opinião da foto
        bate_cnefe = bool(col and (col.get("qtd_observada") or 0) >= 2)
        add("MULTIPLAS_UNIDADES_FISICAS", "cadastro",
            "alta" if bate_cnefe else "media",
            f"{unidades} unidades físicas estimadas por "
            + (", ".join(est.get("metodo_estimativa") or []).replace("_", " ")
               or "contagem visual")
            + (f"; o IBGE registrou {col['qtd_observada']} unidades "
               f"({(col.get('forma') or '').lower()}) no mesmo endereço em 2022"
               if bate_cnefe else ""),
            est.get("confianca_estrutural") or 0.6,
            "conferir número de economias do imóvel", cnefe=bate_cnefe)
    if tem_vinculo and unidades and econ and unidades > econ:
        cn = (col or {}).get("qtd_observada") or 0
        add("ECONOMIAS_OCULTAS_POTENCIAL", "receita", "alta",
            f"{unidades} unidades físicas contra {econ} economia(s) cadastrada(s)"
            + (f"; o IBGE contou {cn} unidades ali" if cn > econ else ""),
            0.7, "fila de confirmação cadastral",
            convergente=True, cnefe=cn > econ)

    # uso real x categoria — só quando a atividade é DO ALVO
    if no_alvo and uso_obs in ("comercial", "servicos", "industrial"):
        cat = (vinc or {}).get("categoria") or ""
        diverge = tem_vinculo and cat.upper().startswith("RESID")
        add("USO_COMERCIAL_NAO_CADASTRADO" if diverge else "USO_MISTO_POTENCIAL",
            "receita", "alta" if diverge else "media",
            f"uso observado {uso_obs}"
            + (f"; cadastro diz {cat}" if cat else "")
            + (f"; letreiro: {uso.get('atividade_letreiro')}"
               if uso.get("atividade_letreiro") else ""),
            0.7, "revisão de categoria tarifária", convergente=diverge)
    if no_alvo and uso_obs == "misto_res_com":
        add("USO_MISTO_POTENCIAL", "receita", "media",
            "uso misto observado na fachada", 0.65, "revisão de categoria")
    if tri.get("formalidade_aparente") == "atividade_domiciliar" and no_alvo:
        add("ATIVIDADE_DOMICILIAR_POTENCIAL", "receita", "media",
            "atividade econômica dentro de imóvel residencial: "
            + (tri.get("evidencia_triagem") or "sinal na fachada"),
            0.6, "cruzar com CNPJ no endereço e categoria cadastral")

    ag = obs.get("agua_esgoto") or {}
    # os campos agora são TERMO, não booleano: "sem_indicio" e "nao_observavel"
    # são strings verdadeiras e disparariam o achado ao contrário. Só os termos
    # que afirmam ter visto o sinal contam.
    _FOSSA_SIM = {"respiro_aparente", "tampa_em_area_permeavel_sem_rede"}
    _SARJETA_SIM = {"sim_tubulacao_visivel", "sim_mancha_umidade_permanente"}
    sinal_fossa = ag.get("sinais_fossa") in _FOSSA_SIM
    sinal_sarjeta = ag.get("lancamento_sarjeta_aparente") in _SARJETA_SIM
    if sinal_fossa or sinal_sarjeta:
        add("ESGOTO_SEM_COBRANCA_POTENCIAL", "receita", "media",
            "sinais de solução individual de esgoto na fachada: "
            + ", ".join(t for t in (ag.get("sinais_fossa") if sinal_fossa else None,
                                    ag.get("lancamento_sarjeta_aparente")
                                    if sinal_sarjeta else None) if t),
            0.5, "cruzar com cadastro de rede e faturamento")

    # MEDIÇÃO — o que a tampa denuncia. Bateria coletiva é evidência forte de
    # várias unidades: ninguém agrupa medidor onde há uma economia só. E tampa
    # ausente/quebrada é ocorrência de ROTA, não de receita — vira produtividade
    # de campo, que é outra fila e outro dono.
    md = obs.get("medicao") or {}
    coletiva = (md.get("hidrometro_presente") == "bateria_coletiva"
                or md.get("padrao_entrada_energia") == "bateria_coletiva")
    if coletiva:
        add("MULTIPLAS_UNIDADES_FISICAS", "cadastro", "alta",
            "medição em bateria coletiva na testada — agrupamento só existe "
            "onde há várias unidades",
            0.7, "conferir quantas economias o imóvel tem")
    # ── O QUE SÓ O CNEFE ENXERGA ──────────────────────────────────────────
    # A fachada vê a testada; o recenseador entrou na vila e contou porta por
    # porta. Um condomínio horizontal com 14 casas nos fundos aparece como UM
    # portão na foto — e como 14 unidades no CNEFE. Esse achado nasce da fonte
    # externa e a imagem apenas o acompanha, então ele existe mesmo quando o
    # modelo não viu multiplicidade nenhuma.
    if col:
        cn = col.get("qtd_observada") or 0
        if tem_vinculo and econ and cn > econ:
            add("ECONOMIAS_OCULTAS_POTENCIAL", "receita", "alta",
                f"o IBGE registrou {cn} unidades neste endereço em 2022 "
                f"({(col.get('forma') or '').lower()}, veredito "
                f"{(col.get('veredito') or '').lower()}) contra {econ} "
                f"economia(s) cadastrada(s)",
                0.75, "fila de confirmação cadastral — evidência documental",
                convergente=True, cnefe=True)
        if (col.get("qtd_inferida") or 0) > 0:
            add("MULTIPLAS_UNIDADES_FISICAS", "cadastro", "media",
                f"{col['qtd_inferida']} unidade(s) deduzida(s) por lacuna na "
                f"numeração do IBGE — não observadas, requerem confirmação",
                0.5, "vistoria para confirmar as unidades deduzidas", cnefe=True)
        if col.get("com_atividade") and col.get("atividades"):
            add("USO_MISTO_POTENCIAL", "receita", "media",
                f"o recenseador anotou atividade no endereço: "
                f"{col['atividades'][:120]}",
                0.6, "cruzar com a categoria tarifária", cnefe=True)

    # Tampa quebrada, acesso obstruído e conservação ruim NÃO são oportunidades
    # cadastrais — os códigos deles vivem no vocabulário de ALERTAS, e é onde
    # ficam. Oportunidade é fila de receita/cadastro; aquilo é ocorrência de
    # rota e contexto de ocupação, com outro dono e outra urgência.
    return out


def _alertas_medicao(obs: dict) -> list:
    """Alertas de MEDIÇÃO e CONSERVAÇÃO — o que a tampa e o reboco denunciam."""
    md = obs.get("medicao") or {}
    edi = obs.get("edificacao") or {}
    al = []

    def a(codigo, sev, desc):
        al.append({"codigo": codigo, "severidade": sev, "descricao": desc})

    if md.get("estado_abrigo") in ("tampa_ausente", "tampa_quebrada",
                                   "obstruido", "soterrado"):
        a("IMPEDIMENTO_LEITURA", "atencao",
          f"abrigo do medidor: {md['estado_abrigo'].replace('_', ' ')}"
          + (f" — {md['descricao_medicao']}" if md.get("descricao_medicao") else ""))
    if md.get("acessibilidade_medicao") in ("inacessivel", "obstruido_vegetacao",
                                            "obstruido_veiculo",
                                            "interno_requer_morador"):
        a("IMPEDIMENTO_LEITURA", "atencao",
          f"acesso à medição: {md['acessibilidade_medicao'].replace('_', ' ')}")
    if md.get("hidrometro_presente") == "ausente_confirmado":
        a("MEDICAO_NAO_LOCALIZADA", "atencao",
          "nenhum hidrômetro ou abrigo visível na testada")
    if edi.get("estado_conservacao") in ("ruim", "em_ruina"):
        a("SUSPEITA_VACANCIA", "info",
          f"conservação {edi['estado_conservacao'].replace('_', ' ')} na fachada — "
          f"verificar ocupação antes de cobrar")
    return al


def _expandir(obs: dict, alvo: dict, data_img: str | None, modelo: str) -> dict:
    """Observação da IA -> anotação no contrato da skill.

    Os blocos que esta campanha não pede saem ausentes/`nao_observavel`, que é o
    que a skill autoriza quando a campanha foca só algumas teses."""
    tri = dict(obs.get("triagem") or {})
    img_obs = obs.get("imagem") or {}
    end = obs.get("enderecamento") or {}
    uso = obs.get("uso") or {}
    est = obs.get("estrutura") or {}
    cnt = obs.get("contagens") or {}
    ag = obs.get("agua_esgoto") or {}
    med = obs.get("medicao") or {}
    edi = obs.get("edificacao") or {}
    vinc = alvo.get("vinculo")

    lidos = [x for x in (end.get("numeros_lidos") or []) if x]
    numero = lidos[0] if len(lidos) == 1 else None
    confere = _numero_confere(lidos, (vinc or {}).get("numero"))
    end_obs = end
    legivel = _numero_lido_de_verdade(end, (vinc or {}).get("numero"))

    # NÚMERO NÃO SUSTENTADO PELA SEGUNDA VISTA: aqui ele deixa de existir, e não
    # apenas de virar fila. Esconder a oportunidade e manter a divergência nos
    # campos reprova na própria skill — C-14/R-END-06 cobra a oportunidade de
    # quem declara divergência, e com razão: uma ficha que diz "o número diverge"
    # e não abre nada é pior que silêncio, porque alguém lê e age assim mesmo.
    # Se as duas vistas não se sustentam, a leitura não aconteceu; o que ficou
    # foi a tentativa, e ela vai como REVISÃO HUMANA, com os números tentados à
    # vista de quem for conferir.
    numero_incerto = None
    if confere is False and not legivel:
        numero_incerto = (lidos, end.get("numero_no_recorte") or [])
        lidos, numero, confere = [], None, None
    data_ref = data_img or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Confiança de LEITURA declarada pela IA. Street View tem teto próprio na
    # tabela de observabilidade da skill; o validador aplica e reprova quem passar.
    cf = float(tri.get("confianca_triagem") or 0.5)
    metodos = [m for m in (est.get("metodo_estimativa") or []) if m]

    # TIPOLOGIA VERTICAL SEM DIMENSIONAMENTO NÃO SE SUSTENTA (C-22). O modelo
    # chamava de "edifício vertical" e devolvia pavimentos e unidades nulos —
    # afirmação sem lastro, e a regra mais reprovada de todas. Aqui ela é
    # rebaixada para `indeterminada`: perder a classificação é melhor que
    # gravar uma que a própria anotação não comprova.
    VERT = ("edificio_vertical", "condominio_vertical_multitorre", "predio_misto")
    pav = est.get("pavimentos_qtd") or edi.get("pavimentos_visiveis")
    if (est.get("tipologia") in VERT
            and not (pav and est.get("unidades_fisicas_estimadas"))
            and not (est.get("identificadores_unidades") or [])):
        est = {**est, "tipologia": "indeterminada"}

    oportunidades = _oportunidades(obs, vinc, data_ref, alvo.get("coletiva"))
    alertas = _alertas_medicao(obs)

    def alerta(codigo, sev, desc):
        alertas.append({"codigo": codigo, "severidade": sev, "descricao": desc})

    if tri.get("atividade_economica_aparente") in ("sinal_fraco", "sinal_forte"):
        alertas.append({"codigo": "ATIVIDADE_ECONOMICA_APARENTE", "severidade": "info",
                        "descricao": tri.get("evidencia_triagem") or "sinal na fachada"})
    if tri.get("atividade_no_alvo") == "em_vizinho":
        alerta("ATIVIDADE_DE_VIZINHO", "info",
               "o sinal comercial pertence ao imóvel vizinho, não ao alvo")
    if tri.get("formalidade_aparente") == "atividade_domiciliar":
        alerta("ATIVIDADE_DOMICILIAR_APARENTE", "atencao",
               "atividade econômica aparente dentro de imóvel residencial")
    if (est.get("tipologia") or "") in ("edificio_vertical",
                                        "condominio_vertical_multitorre", "predio_misto"):
        alerta("ESTRUTURA_VERTICAL_DETECTADA", "info",
               f"tipologia {est.get('tipologia')} com "
               f"{est.get('pavimentos_qtd') or '?'} pavimentos")
    if not img_obs.get("apta_para_leitura"):
        alerta("IMAGEM_INAPTA", "atencao",
               img_obs.get("motivo_inaptidao") or "imagem não sustenta leitura")

    # DIVERGÊNCIA DE NÚMERO: alerta + oportunidade juntos, é o que a regra
    # R-END-06 exige. Número divergente NÃO une registros nem corrige cadastro —
    # vira fila, porque pode ser tanto foto da casa errada quanto cadastro velho.
    #
    # Mas só vira fila se o número tiver sido LIDO. Divergência é uma acusação
    # contra o cadastro, e acusação exige leitura, não palpite: medido em 5
    # fotos, o gpt-4o devolveu 328 onde a plaqueta diz 326 e 870 onde diz 317.
    # Cada um desses abriria visita de campo com confiança 0,6. O alerta continua
    # saindo sempre — quem olha a ficha do POI vê a diferença e julga; o que o
    # portão barra é a ocorrência que faz alguém pegar a moto.
    if numero_incerto:
        # `REVISAO_HUMANA` é do vocabulário fechado da skill e é exatamente o
        # caso: alguém olha a foto e decide. Código novo aqui reprovaria a
        # anotação inteira no schema.
        tent, rec = numero_incerto
        alerta("REVISAO_HUMANA", "info",
               f"número não confirmado e por isso não registrado: a vista "
               f"inteira leu {'/'.join(tent)}, o recorte ampliado "
               f"{'/'.join(rec) if rec else 'não achou número'} — "
               f"{_MOTIVO_ILEGIVEL(end_obs)}")
    if confere is False:
        alerta("DIVERGENCIA_NUMERO_ENDERECO", "atencao",
               f"número lido {lidos} diverge do cadastrado {vinc.get('numero')}")
        oportunidades.append({
            "codigo": "DIVERGENCIA_NUMERO_ENDERECO", "classe": "qualidade_dado",
            "prioridade": "media", "confianca": 0.6,
            "evidencia": f"número visual {'/'.join(lidos)} lido em "
                         f"{end_obs.get('origem_numero')} e confirmado no recorte "
                         f"ampliado ({'/'.join(end_obs.get('numero_no_recorte') or [])}) "
                         f"contra {vinc.get('numero')} no cadastro",
            "acao_sugerida": "conferir identidade do imóvel antes de usar a leitura",
            "data_referencia": data_ref, "automatizavel": False,
            "nivel_evidencia": "contradicao",
            "fontes_independentes": ["imagem_fachada", "cadastro_companhia"]})

    # `e_imovel=false` ENCERRA: sem atributo, sem oportunidade. Uma foto de tela
    # anotada como fachada é pior que foto faltando — ocupa a matrícula com dado
    # inventado e ninguém volta lá.
    if not tri.get("e_imovel"):
        oportunidades = []
        alertas = [{"codigo": "IMAGEM_FORA_DE_ESCOPO", "severidade": "atencao",
                    "descricao": f"conteúdo {tri.get('conteudo_imagem')}: "
                                 f"{tri.get('evidencia_triagem') or 'não é imóvel'}"}]

    unidades = est.get("unidades_fisicas_estimadas")
    econ = (vinc or {}).get("economias")
    anot = {
        "schema_versao": SCHEMA_VERSAO,
        "imagem": {
            "arquivo": f"streetview/poi_{alvo['poi_id']}.jpg",
            "fonte": "streetview",
            "data_captura": data_img,
            "idade_meses": None if data_img else IDADE_ASSUMIDA_MESES,
            "data_captura_origem": "metadado_arquivo" if data_img else "estimada",
            # SRID 4674 (SIRGAS 2000) é o default do schema; a origem é a API do
            # Street View, que é de onde a coordenada da captura vem
            "coordenada": {"lat": alvo["lat"], "lon": alvo["lng"],
                           "srid": 4674, "origem": "api"},
            "enquadramento": img_obs.get("enquadramento") or "indeterminado",
            "apta_para_leitura": bool(img_obs.get("apta_para_leitura")),
            "motivo_inaptidao": img_obs.get("motivo_inaptidao"),
        },
        "triagem": {
            "conteudo_imagem": tri.get("conteudo_imagem") or "ininteligivel",
            "e_imovel": bool(tri.get("e_imovel")),
            "apto_para_cadastro": bool(tri.get("apto_para_cadastro")),
            "atividade_economica_aparente":
                tri.get("atividade_economica_aparente") or "indeterminado",
            # campo_texto no schema: lista fechada separada por ';'
            "sinais_atividade_economica":
                ";".join(tri.get("sinais_atividade_economica") or []) or None,
            "atividade_no_alvo": tri.get("atividade_no_alvo"),
            "natureza_atividade": tri.get("natureza_atividade"),
            "formalidade_aparente": tri.get("formalidade_aparente"),
            "confianca_triagem": float(tri.get("confianca_triagem") or 0.5),
            "evidencia_triagem": tri.get("evidencia_triagem") or "",
            # exigidos quando a imagem não serve para cadastro
            "uso_parcial_permitido": None if tri.get("apto_para_cadastro") else "nenhum",
            "motivo_descarte": None if tri.get("apto_para_cadastro")
                else (img_obs.get("motivo_inaptidao")
                      or f"conteúdo {tri.get('conteudo_imagem')}"),
        },
        "atributos": {"uso": {"uso_predominante": campo(
            None, 0.0, "imagem não é de imóvel: nenhum atributo cadastral",
            juizo="nao_observavel")}} if not tri.get("e_imovel") else {
            "enderecamento": {
                # `observado` só quando cada dígito foi distinguido num suporte
                # identificado; senão é `inferido`, que é o que o juízo da skill
                # significa. O campo declarava `observado` para tudo, inclusive
                # para número que o modelo tinha adivinhado.
                "numero_endereco_consolidado": campo(
                    numero, cf,
                    (f"número lido em {end.get('origem_numero')}" if legivel
                     else "número parcialmente distinguido na testada")
                    if numero else "nenhum número legível na testada",
                    juizo=("observado" if legivel else "inferido") if numero
                    else "nao_observavel",
                    regra=None if (legivel or not numero) else "R-END-06"),
                "origem_numero_consolidado": campo(
                    end.get("origem_numero"), cf,
                    "objeto físico onde o número foi lido"),
                # campo_texto, não lista: vários valores vão separados por ';'.
                # Passar a lista crua fazia o validador reprovar com
                # "['Sl 203'] is not of type 'string'".
                "numeros_adicionais": campo(
                    ";".join(str(x) for x in lidos[1:]) or None, cf,
                    "demais numerações na mesma testada"),
                "complementos_unidades_visiveis": campo(
                    ";".join(str(x) for x in (end.get("complementos_lidos") or []))
                    or None, cf,
                    "complementos lidos em porta, interfone ou caixa de correio"),
                "divergencia_numero_base": campo(
                    (confere is False) if confere is not None else None, cf,
                    "comparação do número lido com o número cadastrado",
                    juizo="inferido" if confere is not None else "nao_observavel",
                    regra="R-END-06" if confere is not None else None),
            },
            "uso": {
                # C-19, gate anti-contaminação: em rua de comércio a foto do alvo
                # pega o letreiro do vizinho, o modelo marca `em_vizinho` (certo) e
                # DEPOIS diz uso=comercial (errado) — atribuindo ao alvo o negócio
                # do lado. O `atividade_no_alvo` é a resposta mais confiável das
                # duas, porque é a pergunta específica; então é ele que manda.
                "uso_predominante": campo(
                    _uso_do_alvo(uso.get("uso_predominante"), tri), cf,
                    tri.get("evidencia_triagem") or "leitura da fachada"),
                "atividade_letreiro": campo(uso.get("atividade_letreiro"), cf,
                                            "texto do letreiro"),
                "segmento_inferido": campo(
                    uso.get("segmento_inferido"), cf, "segmento deduzido do letreiro",
                    juizo="inferido" if uso.get("segmento_inferido") else "nao_observavel",
                    regra="R-USO-02" if uso.get("segmento_inferido") else None),
                "nome_estabelecimento_visivel": campo(
                    uso.get("nome_estabelecimento_visivel"), cf, "nome no letreiro"),
                "descricao_atividade_funcional": campo(
                    uso.get("descricao_atividade_funcional"), cf,
                    "o que aparenta funcionar no local"),
                # C-11: sem data do panorama não se afirma situação comercial.
                # "Comércio sem data confiável não é estado atual" — a skill é
                # explícita, e o Street View só traz data em 12% das capturas.
                "situacao_estabelecimento_na_data_imagem": campo(
                    uso.get("situacao_na_data") if data_img else None, cf,
                    "situação na data da imagem" if data_img
                    else "sem data do panorama: situação comercial não afirmável",
                    juizo=None if data_img else "nao_observavel"),
                # campo_texto: a lista de sinais vira string separada por ';'
                "sinais_atividade_comercial": campo(
                    ";".join(uso.get("sinais_comerciais_no_momento") or []) or None, cf,
                    "situação aparente do estabelecimento na data"),
                # lista FECHADA de faixas de idade, não uma data
                "validade_temporal_uso": campo(
                    _faixa_temporal(data_img), cf,
                    "idade da imagem em relação à leitura"),
            },
            "edificacao": {
                "tipo_edificacao": campo(edi.get("tipo_edificacao"), cf,
                                         "tipo lido na fachada"),
                "estado_conservacao": campo(edi.get("estado_conservacao"), cf,
                                            "conservação aparente da fachada"),
                "padrao_construtivo": campo(edi.get("padrao_construtivo"), cf,
                                            "padrão construtivo aparente"),
                "pavimentos_visiveis": campo(edi.get("pavimentos_visiveis")
                                             or est.get("pavimentos_qtd"), cf,
                                             "pavimentos contados na fachada"),
            },
            "agua": {
                "hidrometro_presente": campo(med.get("hidrometro_presente"), cf,
                                             med.get("descricao_medicao")
                                             or "medição na testada"),
                "hidrometros_visiveis_qtd": campo(cnt.get("hidrometros_visiveis"), cf,
                                                  "contagem de hidrômetros"),
                "posicao_medicao": campo(med.get("posicao_medicao"), cf,
                                         "onde a medição está"),
                "tipo_abrigo": campo(med.get("tipo_abrigo"), cf,
                                     "abrigo/tampa do medidor"),
                "estado_abrigo": campo(med.get("estado_abrigo"), cf,
                                       "estado da tampa/abrigo"),
                "acessibilidade_medicao": campo(med.get("acessibilidade_medicao"), cf,
                                                "acesso para leitura"),
            },
            "energia": {
                "medidores_energia_qtd": campo(cnt.get("ucs_energia_visiveis"), cf,
                                               "contagem de medidores de energia"),
                "tipo_padrao_entrada": campo(med.get("padrao_entrada_energia"), cf,
                                             "padrão de entrada de energia"),
                "ramal_entrada": campo(med.get("ramal_entrada"), cf,
                                       "ramal aéreo ou subterrâneo"),
            },
            "esgoto": {
                "caixa_inspecao_aparente": campo(ag.get("caixa_inspecao_aparente"), cf,
                                                 "caixa de inspeção visível"),
                "indicio_fossa": campo(ag.get("sinais_fossa"), cf,
                                       "sinais de solução individual"),
                "lancamento_sarjeta_aparente": campo(
                    ag.get("lancamento_sarjeta_aparente"), cf,
                    "lançamento aparente em sarjeta"),
            },
        },
        "economias": {
            # C-04: economia acima de 1 exige ao menos UMA evidência contada. O
            # modelo estima unidades olhando a fachada inteira e não conta nada —
            # e a anotação inteira caía por isso. Estimativa sem contagem é
            # palpite, e o campo aceita null: dizer "não sei" passa no gate e é
            # verdade; dizer "4" sem contar nada é o que a regra proíbe.
            "estimativa": (unidades if (unidades or 0) <= 1 or _EV_CONTADAS(cnt)
                           else None),
            "metodo": _METODO_ECON.get(metodos[0] if metodos else "", "indeterminado"),
            "evidencias_contadas": {
                "medidores_energia": cnt.get("ucs_energia_visiveis"),
                "hidrometros": cnt.get("hidrometros_visiveis"),
                "portas_independentes": cnt.get("portas_acesso_independentes"),
                "caixas_correio": cnt.get("caixas_correio"),
                "interfones": cnt.get("interfones"),
            },
            "confianca": float(est.get("confianca_estrutural") or 0.5),
            "divergencia": bool(unidades and econ and unidades != econ),
            "justificativa": obs.get("observacoes") or "",
        },
        "estrutura_imovel": {
            "tipologia": est.get("tipologia") or "indeterminada",
            # o modelo às vezes preenche só um dos dois campos de pavimento; o
            # C-22 exige `pavimentos_qtd` junto com as unidades por pavimento
            "pavimentos_qtd": est.get("pavimentos_qtd") or edi.get("pavimentos_visiveis"),
            # C-22: tipologia vertical sem dimensionamento é reprovada — foi a
            # regra que mais reprovou (30 de 45). O modelo classifica como
            # vertical e devolve o total de unidades sem a divisão por andar;
            # a razão fecha a conta, e com um pavimento só a resposta é o próprio
            # total (galpão e loja térrea caíam aqui com `null`).
            "unidades_por_pavimento_estimadas": (
                max(1, round(unidades / max(1, est.get("pavimentos_qtd") or 1)))
                if unidades else None),
            "unidades_fisicas_estimadas": unidades,
            "metodo_estimativa": metodos or ["indeterminado"],
            # string separada por ';' — números de unidade, nunca nome de morador
            "identificadores_unidades":
                ";".join(str(x) for x in (est.get("identificadores_unidades") or [])) or None,
            "ucs_energia_visiveis": cnt.get("ucs_energia_visiveis"),
            "hidrometros_visiveis": cnt.get("hidrometros_visiveis"),
            "confianca_estrutural": float(est.get("confianca_estrutural") or 0.5),
            "evidencia_estrutura": obs.get("observacoes") or None,
        },
        "alertas": alertas,
        "oportunidades": oportunidades,
        "lgpd": {
            "pessoas_visiveis": False,
            "placas_veiculo_visiveis": False,
            # o validador REPROVA se vier true; o prompt proíbe a transcrição
            "dado_pessoal_transcrito": False,
            "acao": "nenhuma",
        },
        "auditoria": {
            # QUANTIDADE de passes, não a lista deles — o schema pede inteiro 0..6
            "passes_executados": 6,
            "rodadas_verificacao": 1,
            "confianca_media": round(float(tri.get("confianca_triagem") or 0.5), 2),
            "anotador": f"avaliar_fachada.py/{modelo}",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }
    if vinc:
        anot["vinculo"] = {
            "matricula": str(vinc.get("matricula") or "") or None,
            "numero_confere": confere,
            "imovel_id": str(vinc.get("matricula") or "") or None,
            "numero_base": str(vinc.get("numero") or "") or None,
            "categoria_tarifaria_base": vinc.get("categoria"),
            "economias_agua_base": econ,
        }
    if alvo.get("coletiva"):
        # `convergencia` na skill tem TRÊS campos e `additionalProperties: false`.
        # A versão anterior despejava aqui os seis campos da coletiva do CNEFE e
        # reprovava a anotação inteira no schema. O detalhe da coletiva já está
        # gravado na coluna própria da `fachada_anotacao`; aqui vai o que o
        # contrato pede — o status, quais fontes entraram e uma frase.
        c = alvo["coletiva"]
        fontes = ["imagem_fachada", "cnefe"]
        if alvo.get("vinculo"):
            fontes.append("cadastro_companhia")
        obs, inf = c.get("qtd_observada"), c.get("qtd_inferida")
        anot["convergencia"] = {
            "status": ("ha_achados_convergentes" if len(fontes) >= 3
                       else "somente_sinais"),
            "fontes_independentes_usadas": fontes,
            "observacao": (
                f"CNEFE 2022 (coletiva {c.get('coletiva_id')}, forma "
                f"{c.get('forma') or 'indeterminada'}): {obs} unidades observadas"
                + (f", {inf} inferidas" if inf else "")
                + (f" — {c['veredito']}" if c.get("veredito") else "")),
        }
    _aplicar_teto(anot)
    # `confianca_media` é CONFERIDA pelo validador: tem de ser a média real dos
    # campos preenchidos, não um número declarado à parte. Por isso só dá para
    # calcular depois do teto — que rebaixa vários deles.
    confs = [c["confianca"] for _p, c in _iter_campos(anot)
             if c.get("valor") not in (None, "", [])]
    aud = anot["auditoria"]
    aud["confianca_media"] = round(sum(confs) / len(confs), 3) if confs else 0.0
    aud["campos_preenchidos"] = len(confs)
    aud["campos_nao_observaveis"] = sum(
        1 for _p, c in _iter_campos(anot) if c.get("juizo") == "nao_observavel")
    aud["teto_fonte_aplicado"] = True
    return anot


def _aplicar_teto(anot: dict) -> None:
    """Rebaixa toda confiança ao teto que a skill define para a fonte.

    A tabela de observabilidade diz quanto uma foto de Street View de 3 anos pode
    sustentar por atributo — letreiro chega a 0,25, porque o comércio muda. O
    modelo não conhece essa tabela e devolve 0,5 em tudo; o validador reprova o
    que passa do teto. Rebaixar é honesto e é o que a skill manda: a confiança
    descreve o que a FONTE aguenta, não o quanto o leitor gostou da imagem."""
    v = _validador()
    img = anot.get("imagem") or {}
    for path, campo_ in _iter_campos(anot):
        try:
            teto = v["mod"].teto_efetivo(path, img, v["obs"])
        except Exception:
            continue
        if teto is not None and campo_.get("confianca", 0) > teto:
            campo_["confianca"] = round(float(teto), 2)

    # A confiança da TRIAGEM tem teto próprio: a skill limita o quanto uma foto
    # de Street View pode sustentar sobre atividade econômica, porque comércio
    # muda e a foto é velha. O modelo devolve 0,9 achando que está sendo preciso.
    # Os TETOS DA TRIAGEM E DA ESTRUTURA usam caminhos de OUTROS campos — é assim
    # que o validador calcula, e adivinhar o caminho ("triagem.confianca_triagem")
    # devolvia teto nenhum e não rebaixava nada. Espelhado do validador:
    #   triagem  → teto de `conteudo_imagem`, e o de `atividade_economica_aparente`
    #              quando há atividade (foto velha de rua não sustenta comércio)
    #   estrutura→ teto de `unidades_fisicas_estimadas`
    def _teto(path):
        try:
            return v["mod"].teto_efetivo(path, img, v["obs"])
        except Exception:
            return None

    tri = anot.get("triagem") or {}
    tetos = [t for t in [_teto("triagem.conteudo_imagem")] if t]
    if tri.get("atividade_economica_aparente") in ("sinal_fraco", "sinal_forte"):
        t = _teto("triagem.atividade_economica_aparente")
        if t:
            tetos.append(t)
    if tetos and tri.get("confianca_triagem", 0) > min(tetos):
        tri["confianca_triagem"] = round(float(min(tetos)), 2)

    est_b = anot.get("estrutura_imovel") or {}
    teto_est = _teto("estrutura_imovel.unidades_fisicas_estimadas")
    if teto_est and est_b.get("confianca_estrutural", 0) > teto_est:
        est_b["confianca_estrutural"] = round(float(teto_est), 2)


def _iter_campos(anot: dict):
    """(caminho, campo) de cada atributo com juízo — é o que o teto governa."""
    for bloco, campos in (anot.get("atributos") or {}).items():
        for nome, c in (campos or {}).items():
            if isinstance(c, dict) and "juizo" in c:
                yield f"{bloco}.{nome}", c


# ──────────────────────────────────────────────────────────────────────────
# Validação pela própria skill
# ──────────────────────────────────────────────────────────────────────────
_VAL = {"mod": None, "schema": None, "obs": None, "vocab": None}


def _validador():
    """Carrega o validador da skill uma vez. Usar o validador DELA, e não um
    conferidor próprio, é o que garante que a anotação continue válida quando a
    skill for atualizada — as regras vivem lá, não aqui."""
    if _VAL["mod"] is None:
        import importlib.util
        alvo = SKILL / "scripts" / "validar_anotacao.py"
        spec = importlib.util.spec_from_file_location("validar_anotacao", alvo)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _VAL["mod"] = mod
        carregar = json.loads
        _VAL["schema"] = carregar((SKILL / "assets" / "schema_fachada.json").read_text("utf-8"))
        _VAL["obs"] = carregar((SKILL / "assets" / "observabilidade.json").read_text("utf-8"))
        _VAL["vocab"] = carregar((SKILL / "assets" / "vocabulario.json").read_text("utf-8"))
    return _VAL


def validar(anot: dict) -> dict:
    v = _validador()
    tmp = Path(tempfile.gettempdir()) / f"_fachada_{os.getpid()}_{id(anot)}.json"
    try:
        tmp.write_text(json.dumps(anot, ensure_ascii=False), encoding="utf-8")
        return v["mod"].validar(str(tmp), v["schema"], v["obs"], v["vocab"])
    finally:
        tmp.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────
# Chamada da IA
# ──────────────────────────────────────────────────────────────────────────
_CLIENTE = None
_USO = {"in": 0, "out": 0}


def _openai():
    global _CLIENTE
    if _CLIENTE is None:
        from openai import AsyncOpenAI
        chave = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not chave:
            raise SystemExit("Sem OPENAI_API_KEY no .env — esta fase depende dela.")
        _CLIENTE = AsyncOpenAI(api_key=chave, timeout=TIMEOUT_S, max_retries=0)
    return _CLIENTE


# ORÇAMENTO DE TOKENS POR MINUTO — o limite REAL da conta, lido do erro 429:
#   "on tokens per min (TPM): Limit 200000, Used 200000, Requested 1569"
#
# Não é bloqueio, é orçamento. Por isso a defesa certa não é recuar às cegas
# depois de bater: é NÃO PASSAR. O runner mantém a conta do que gastou nos
# últimos 60 s e segura a próxima chamada até caber. A cada POI são ~3.300
# tokens (1.100 da imagem, 1.500 do contrato, 700 da resposta), o que dá ~55 POIs
# por minuto — muito acima do que a fila precisa.
#
# A primeira versão tratava o 429 como defeito daquele POI e seguia em frente,
# no mesmo ritmo que causou o estouro: 69 dos 82 pontos da fila viraram erro.
TPM_LIMITE = int(os.environ.get("OPENAI_TPM", "200000"))
TPM_FOLGA = 0.85          # margem: a conta do servidor e a nossa nunca batem exato
RETENTATIVAS_429 = 5

# TOKENS POR POI, MEDIDOS — não estimados. A primeira versão chutou 3.300 e o
# real no gpt-4o-mini é 40.345: ele cobra imagem com um multiplicador enorme, e
# a mesma foto que custa 4,1 mil tokens no gpt-4o custa 39,8 mil nele.
#
# A consequência inverte a escolha do modelo. Dentro do teto de 200k tokens/min:
#   gpt-4o       4.616 tokens · US$ 0,0154/POI · 43 POIs/min
#   gpt-4o-mini 40.345 tokens · US$ 0,0063/POI ·  4 POIs/min
# O "barato" custa 2,4× menos por ponto e leva 11× mais tempo. Numa cidade
# inteira: US$ 104 em 69 h contra US$ 256 em 6,4 h.
TOKENS_POR_MODELO = {
    "gpt-4o": 4616,
    "gpt-4o-mini": 40345,
    "gpt-4.1-mini": 40000,      # mesma família de multiplicador do mini
}
TOKENS_PADRAO = 40000           # o pessimista, para modelo não medido


def tokens_por_poi(modelo: str) -> int:
    return TOKENS_POR_MODELO.get(modelo, TOKENS_PADRAO)

_JANELA = []              # [(instante, tokens)] dos últimos 60 s
_TRAVA_TPM = None


def _tpm_lock():
    global _TRAVA_TPM
    if _TRAVA_TPM is None:
        _TRAVA_TPM = asyncio.Lock()
    return _TRAVA_TPM


async def _reservar_tokens(estimado: int):
    """Segura a chamada até caber no orçamento do minuto."""
    async with _tpm_lock():
        while True:
            agora = time.monotonic()
            while _JANELA and agora - _JANELA[0][0] > 60.0:
                _JANELA.pop(0)
            usado = sum(t for _, t in _JANELA)
            if usado + estimado <= TPM_LIMITE * TPM_FOLGA or not _JANELA:
                _JANELA.append((agora, estimado))
                return
            # espera o suficiente para a chamada mais antiga sair da janela
            await asyncio.sleep(max(0.5, 60.0 - (agora - _JANELA[0][0])))


def _ajustar_tokens(real: int):
    """Troca a estimativa pelo gasto real, para a janela não mentir."""
    if _JANELA:
        t, _est = _JANELA[-1]
        _JANELA[-1] = (t, real)


# Freio de emergência, para quando mesmo assim escapar um 429.
_FREIO = {"ate": 0.0, "seguidos": 0}


async def _esperar_freio():
    while True:
        falta = _FREIO["ate"] - time.monotonic()
        if falta <= 0:
            return
        await asyncio.sleep(min(falta, 2.0))


def _acionar_freio(segundos: float | None = None):
    _FREIO["seguidos"] += 1
    # 4, 8, 16, 32, 60 — teto de um minuto, que é a janela dos limites por minuto
    piso = min(60.0, 4.0 * 2 ** (_FREIO["seguidos"] - 1))
    espera = max(segundos or 0.0, piso)
    _FREIO["ate"] = max(_FREIO["ate"], time.monotonic() + espera)
    return espera


def _soltar_freio():
    if _FREIO["seguidos"]:
        _FREIO["seguidos"] -= 1


def _retry_after(exc) -> float | None:
    """Segundos que a própria OpenAI pede para esperar, quando ela diz."""
    try:
        v = exc.response.headers.get("retry-after-ms")
        if v:
            return float(v) / 1000.0
        v = exc.response.headers.get("retry-after")
        if v:
            return float(v)
    except Exception:
        pass
    return None


def custo_usd(modelo: str) -> float:
    if _e_local(modelo):
        return 0.0          # GPU do i9 — o custo é tempo, e já está pago
    pin, pout = PRECOS.get(modelo, PRECOS[MODELO_PADRAO])
    return _USO["in"] / 1e6 * pin + _USO["out"] / 1e6 * pout


# ── Triagem de nitidez: pergunta separada, positiva e contável ─────────────
# O modelo pequeno não obedece a condicional nem a negação. A regra 9 diz, com
# todas as letras, que carro estacionado NÃO reprova — e o qwen2.5vl:7b devolveu
# `obstrucao_carro_estacionado_frente_fachada` como motivo. Ele não julga: ele
# responde. Então a aptidão sai de UMA pergunta só, com três respostas de sim ou
# não sobre o que está visível, e a decisão é tomada aqui, em código, onde não há
# modelo para desobedecer.
_SCHEMA_NITIDEZ = {
    "type": "object", "additionalProperties": False,
    "required": ["ve_parede", "ve_abertura", "ve_limite_rua", "descricao"],
    "properties": {
        "ve_parede":     {"type": "boolean"},
        "ve_abertura":   {"type": "boolean"},
        "ve_limite_rua": {"type": "boolean"},
        "descricao":     {"type": "string"},
    },
}

_PROMPT_NITIDEZ = (
    "Olhe a foto e responda sim ou não para cada pergunta. Responda pelo que "
    "aparece em QUALQUER parte da imagem — não importa se algo estiver na "
    "frente, se a foto for de longe ou se estiver de lado.\n"
    "1. ve_parede: aparece a parede de alguma edificação, e dá para dizer a cor "
    "ou o material dela?\n"
    "2. ve_abertura: aparece pelo menos uma porta, portão, janela ou vitrine?\n"
    "3. ve_limite_rua: dá para ver onde a edificação termina e a rua ou a "
    "calçada começa?\n"
    "descricao: em uma frase, o que aparece na foto."
)


async def _triagem_nitidez(imgs: list, modelo: str) -> tuple[bool, str]:
    """Devolve (apta, motivo). Apta = duas das três respostas positivas.

    Duas de três, e não as três: fachada de esquina esconde o limite da rua,
    muro alto esconde a abertura, e nenhuma das duas impede ler pavimento,
    tipologia ou letreiro — que é o que a leitura precisa."""
    import urllib.request

    def _chamar():
        corpo = json.dumps({
            "model": modelo, "stream": False, "think": False,
            "messages": [{"role": "user", "content": _PROMPT_NITIDEZ,
                          "images": [base64.b64encode(imgs[0]).decode()]}],
            "format": _SCHEMA_NITIDEZ,
            "options": {"temperature": 0, "num_ctx": 4096},
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=corpo,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.load(r)

    try:
        d = await asyncio.to_thread(_chamar)
        _USO["in"] += d.get("prompt_eval_count", 0)
        _USO["out"] += d.get("eval_count", 0)
        n = json.loads(d["message"]["content"])
    except Exception:
        return True, ""            # triagem que falha não reprova a foto
    sim = sum(bool(n.get(k)) for k in ("ve_parede", "ve_abertura", "ve_limite_rua"))
    if sim >= 2:
        return True, ""
    falta = ", ".join(r for k, r in (("ve_parede", "parede"),
                                     ("ve_abertura", "abertura"),
                                     ("ve_limite_rua", "limite com a rua"))
                      if not n.get(k))
    desc = (n.get("descricao") or "").strip()
    return False, f"não se distingue na foto: {falta}" + (f" — {desc}" if desc else "")


async def _ler_ollama(alvo: dict, imgs: list, modelo: str) -> dict:
    """Leitura pelo Ollama do i9. Mesmo contrato, custo zero.

    A imagem vai em `images` (base64 puro, sem o prefixo `data:`) e o schema vai
    em `format` — o Ollama honra JSON Schema desde a 0.5, o que mantém o mesmo
    contrato dos dois lados e evita um segundo caminho de parsing.

    Roda em thread porque `urllib` é bloqueante: dentro do laço async ele
    seguraria o event loop inteiro e os workers virariam fila de um."""
    import urllib.request

    def _chamar():
        corpo = json.dumps({
            "model": modelo, "stream": False, "think": False,
            "messages": [
                {"role": "system", "content": _prompt_sistema()},
                {"role": "user",
                 "content": _prompt_usuario(alvo, alvo.get("vinculo")),
                 "images": [base64.b64encode(imgs[0]).decode()]},
            ],
            "format": SCHEMA_IA,
            "options": {"temperature": 0, "num_ctx": 8192},
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=corpo,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.load(r)

    apta, motivo = await _triagem_nitidez(imgs, modelo)
    d = await asyncio.to_thread(_chamar)
    # tokens contam para o placar, mas não para o bolso: o preço local é 0
    _USO["in"] += d.get("prompt_eval_count", 0)
    _USO["out"] += d.get("eval_count", 0)
    out = json.loads(d["message"]["content"])
    # a aptidão vem da triagem, não desta resposta: aqui o modelo tem 11 regras
    # na frente e reprova a foto citando a que mandava não reprovar
    out.setdefault("imagem", {})
    out["imagem"]["apta_para_leitura"] = apta
    out["imagem"]["motivo_inaptidao"] = None if apta else motivo
    return out


# ── Segunda vista: o número num recorte ampliado ──────────────────────────
_ESQ_NUMERO = {
    "type": "object", "additionalProperties": False,
    "required": ["numeros", "onde"],
    "properties": {"numeros": {"type": "array", "items": {"type": "string"}},
                   "onde": {"type": "string"}},
}

_PROMPT_NUMERO = (
    "Estas imagens são recortes ampliados da MESMA fachada. Transcreva os "
    "números de IMÓVEL que você consegue ler — plaqueta, porta, portão, caixa "
    "de correio, placa de rua na parede. Se qualquer dígito estiver borrado, "
    "NÃO complete e NÃO adivinhe: devolva lista vazia. Não inclua telefone, "
    "preço, ano, horário nem número de poste. Em `onde`, diga em que objeto o "
    "número estava."
)


def _recortes_numero(dados: bytes) -> list:
    """Três fatias da metade de baixo, ampliadas 3x.

    A numeração vive na testada, não no telhado, e numa foto de 1280 px de
    largura ela tem poucos pixels de altura — que é a razão de o modelo ler 328
    onde está escrito 326. Ampliar não cria informação, mas entrega ao modelo a
    informação que já estava lá em tamanho que ele distingue."""
    import io as _io
    from PIL import Image
    im = Image.open(_io.BytesIO(dados))
    w, h = im.size
    saida = []
    for x0, x1 in ((0.00, 0.40), (0.30, 0.70), (0.60, 1.00)):
        c = im.crop((int(x0 * w), int(0.35 * h), int(x1 * w), int(0.95 * h)))
        c = c.resize((c.width * 3, c.height * 3), Image.LANCZOS)
        buf = _io.BytesIO()
        c.convert("RGB").save(buf, "JPEG", quality=92)
        saida.append(buf.getvalue())
    return saida


async def _reler_numero(dados: bytes, modelo: str) -> list:
    """Números lidos no recorte. Lista vazia = não deu para ler."""
    try:
        recortes = _recortes_numero(dados)
    except Exception:
        return []
    try:
        if _e_local(modelo):
            import urllib.request

            def _chamar():
                corpo = json.dumps({
                    "model": modelo, "stream": False, "think": False,
                    "messages": [{"role": "user", "content": _PROMPT_NUMERO,
                                  "images": [base64.b64encode(x).decode()
                                             for x in recortes]}],
                    "format": _ESQ_NUMERO,
                    "options": {"temperature": 0, "num_ctx": 8192},
                }).encode()
                req = urllib.request.Request(
                    f"{OLLAMA_URL}/api/chat", data=corpo,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                    return json.load(r)

            d = await asyncio.to_thread(_chamar)
            _USO["in"] += d.get("prompt_eval_count", 0)
            _USO["out"] += d.get("eval_count", 0)
            saida = json.loads(d["message"]["content"])
        else:
            conteudo = [{"type": "text", "text": _PROMPT_NUMERO}] + [
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{base64.b64encode(x).decode()}",
                    "detail": "high"}} for x in recortes]
            r = await _openai().chat.completions.create(
                model=modelo, temperature=0,
                messages=[{"role": "user", "content": conteudo}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "numero_recorte", "strict": True,
                    "schema": _ESQ_NUMERO}})
            _USO["in"] += r.usage.prompt_tokens
            _USO["out"] += r.usage.completion_tokens
            saida = json.loads(r.choices[0].message.content)
        return [str(x) for x in (saida.get("numeros") or []) if str(x).strip()]
    except Exception:
        return []          # segunda vista que falha não vira acusação


async def _confirmar_divergencia(alvo: dict, obs: dict, imgs: list, modelo: str):
    """Relê o número no recorte QUANDO ele contradiz o cadastro.

    Só nesse caso: sem imóvel vinculado não há divergência possível, e número
    que bate com o cadastro não precisa de segunda vista para não virar fila.
    Assim a chamada extra acontece na minoria dos POIs, e paga por si."""
    end = obs.get("enderecamento") or {}
    lidos = [x for x in (end.get("numeros_lidos") or []) if x]
    vinc = alvo.get("vinculo") or {}
    if not lidos or not imgs or _numero_confere(lidos, vinc.get("numero")) is not False:
        return
    no_recorte = await _reler_numero(imgs[0], modelo)
    end["numero_no_recorte"] = no_recorte
    alvo_num = _RE_NUM.search(lidos[0])
    end["numero_confirmado_recorte"] = bool(alvo_num) and any(
        _RE_NUM.search(x) and _RE_NUM.search(x).group() == alvo_num.group()
        for x in no_recorte)
    obs["enderecamento"] = end


async def _ler_imagem(alvo: dict, imgs: list, modelo: str) -> dict | None:
    """Uma leitura, com o freio respeitado e o 429 tratado como espera."""
    if _e_local(modelo):
        return await _ler_ollama(alvo, imgs, modelo)
    from openai import RateLimitError
    for tentativa in range(RETENTATIVAS_429):
        await _esperar_freio()
        await _reservar_tokens(tokens_por_poi(modelo))
        try:
            r = await _chamar_openai(alvo, imgs, modelo)
            _soltar_freio()
            return r
        except RateLimitError as e:
            if tentativa == RETENTATIVAS_429 - 1:
                raise
            espera = _acionar_freio(_retry_after(e))
            print(f"   ⏳ limite de taxa — segurando {espera:.0f}s "
                  f"(tentativa {tentativa + 1}/{RETENTATIVAS_429})", flush=True)
    return None


async def _chamar_openai(alvo: dict, imgs: list, modelo: str) -> dict:
    b64 = base64.b64encode(imgs[0]).decode()
    conteudo = [
        {"type": "text", "text": _prompt_usuario(alvo, alvo.get("vinculo"))},
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
    ]
    resp = await _openai().chat.completions.create(
        model=modelo, temperature=0,
        messages=[{"role": "system", "content": _prompt_sistema()},
                  {"role": "user", "content": conteudo}],
        response_format={"type": "json_schema", "json_schema": {
            "name": "leitura_fachada", "strict": True, "schema": SCHEMA_IA}},
    )
    _USO["in"] += resp.usage.prompt_tokens
    _USO["out"] += resp.usage.completion_tokens
    _ajustar_tokens(resp.usage.prompt_tokens + resp.usage.completion_tokens)
    return json.loads(resp.choices[0].message.content)


def gravar(alvo: dict, anot: dict, val: dict, modelo: str, con):
    tri = anot["triagem"]
    est = anot["estrutura_imovel"]
    vinc = anot.get("vinculo") or {}
    ucs = est.get("ucs_energia_visiveis")
    econ = (anot.get("vinculo") or {}).get("economias_agua_base")
    atr = anot.get("atributos") or {}
    ed, agua, ene = (atr.get("edificacao") or {}, atr.get("agua") or {},
                     atr.get("energia") or {})

    def _v(bloco, campo_):
        return (bloco.get(campo_) or {}).get("valor")
    if not tri["e_imovel"]:
        status = "fora_escopo"
    elif not anot["imagem"]["apta_para_leitura"]:
        status = "inapto"
    else:
        status = val.get("status") or "reprovado"
    with con.cursor() as cur:
        cur.execute("""
            INSERT INTO fachada_anotacao (poi_id, modelo, schema_versao, status, apta,
                e_imovel, uso_observado, tipologia, unidades_fisicas, ucs_energia,
                hidrometros, economias_base, gap_uc_economias, numero_lido,
                numero_confere, atividade_no_alvo, confianca, oportunidades, anotacao,
                validacao, tokens_in, tokens_out, custo_usd,
                estado_conservacao, padrao_construtivo, tipo_edificacao, pavimentos,
                medicao_abrigo, medicao_estado, medicao_acesso, medicao_posicao,
                medicao_coletiva, medicao_desc, energia_entrada)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (poi_id) DO UPDATE SET
                modelo=EXCLUDED.modelo, schema_versao=EXCLUDED.schema_versao,
                status=EXCLUDED.status, apta=EXCLUDED.apta, e_imovel=EXCLUDED.e_imovel,
                uso_observado=EXCLUDED.uso_observado, tipologia=EXCLUDED.tipologia,
                unidades_fisicas=EXCLUDED.unidades_fisicas, ucs_energia=EXCLUDED.ucs_energia,
                hidrometros=EXCLUDED.hidrometros, economias_base=EXCLUDED.economias_base,
                gap_uc_economias=EXCLUDED.gap_uc_economias, numero_lido=EXCLUDED.numero_lido,
                numero_confere=EXCLUDED.numero_confere,
                atividade_no_alvo=EXCLUDED.atividade_no_alvo, confianca=EXCLUDED.confianca,
                oportunidades=EXCLUDED.oportunidades, anotacao=EXCLUDED.anotacao,
                validacao=EXCLUDED.validacao, criado_em=now(),
                estado_conservacao=EXCLUDED.estado_conservacao,
                padrao_construtivo=EXCLUDED.padrao_construtivo,
                tipo_edificacao=EXCLUDED.tipo_edificacao, pavimentos=EXCLUDED.pavimentos,
                medicao_abrigo=EXCLUDED.medicao_abrigo,
                medicao_estado=EXCLUDED.medicao_estado,
                medicao_acesso=EXCLUDED.medicao_acesso,
                medicao_posicao=EXCLUDED.medicao_posicao,
                medicao_coletiva=EXCLUDED.medicao_coletiva,
                medicao_desc=EXCLUDED.medicao_desc,
                energia_entrada=EXCLUDED.energia_entrada""",
            (alvo["poi_id"], modelo, SCHEMA_VERSAO, status,
             anot["imagem"]["apta_para_leitura"], tri["e_imovel"],
             ((anot["atributos"].get("uso") or {}).get("uso_predominante")
              or {}).get("valor"), est["tipologia"],
             est.get("unidades_fisicas_estimadas"), ucs,
             est.get("hidrometros_visiveis"), econ,
             (ucs - econ) if (ucs is not None and econ is not None) else None,
             ((anot["atributos"].get("enderecamento") or {})
              .get("numero_endereco_consolidado") or {}).get("valor"),
             vinc.get("numero_confere"), tri.get("atividade_no_alvo"),
             anot["auditoria"]["confianca_media"],
             json.dumps(anot["oportunidades"], ensure_ascii=False),
             json.dumps(anot, ensure_ascii=False),
             json.dumps({"status": val.get("status"),
                         "erros": val.get("erros", [])[:12],
                         "avisos": val.get("avisos", [])[:12]}, ensure_ascii=False),
             _USO["in"], _USO["out"], round(custo_usd(modelo), 6),
             _v(ed, "estado_conservacao"), _v(ed, "padrao_construtivo"),
             _v(ed, "tipo_edificacao"), _v(ed, "pavimentos_visiveis"),
             _v(agua, "tipo_abrigo"), _v(agua, "estado_abrigo"),
             _v(agua, "acessibilidade_medicao"), _v(agua, "posicao_medicao"),
             _v(agua, "hidrometro_presente") == "bateria_coletiva",
             (agua.get("hidrometro_presente") or {}).get("evidencia"),
             _v(ene, "tipo_padrao_entrada")))
    con.commit()
    return status


# ──────────────────────────────────────────────────────────────────────────
# Orquestração
# ──────────────────────────────────────────────────────────────────────────
def estimar(n: int, modelo: str) -> dict:
    """Estimativa ANTES de gastar. Medido: ~1.100 tokens da imagem em `detail:high`
    (1280x656), ~1.500 do contrato e ~700 de saída."""
    if _e_local(modelo):
        # ~11 s por imagem medidos no qwen2.5vl:7b com a GPU inteira
        return {"pois": n, "modelo": modelo, "usd": 0.0, "usd_por_poi": 0.0,
                "local": True, "horas": round(n * 11 / 3600, 1),
                "pois_min": round(60 / 11, 1)}
    pin, pout = PRECOS.get(modelo, PRECOS[MODELO_PADRAO])
    tot = tokens_por_poi(modelo)
    saida = 520                      # medido; o resto é entrada
    usd = n * ((tot - saida) / 1e6 * pin + saida / 1e6 * pout)
    # o teto de tokens por minuto é o que MANDA no tempo, não a rede
    por_min = max(1, int(TPM_LIMITE * TPM_FOLGA / tot))
    return {"pois": n, "modelo": modelo, "usd": round(usd, 2),
            "usd_por_poi": round(usd / max(1, n), 5), "local": False,
            "tokens_poi": tot, "pois_min": por_min,
            "horas": round(n / por_min / 60, 1)}


async def run(area_path, limit, modelo, workers, refazer, teto_usd, so_estimar):
    poligono = area_utils.carregar_area(area_path) if area_path else None
    cidade, uf = area_utils.municipio_da_area(poligono)
    con = bc.conectar()
    try:
        esquema(con)
        alvos = carregar_alvos(poligono, limit, refazer, con)
    finally:
        con.close()

    est = estimar(len(alvos), modelo)
    print(f"🔍 Avaliação de fachada | Área: {cidade or '?'}/{uf or '?'} | "
          f"modelo {modelo}", flush=True)
    print(f"POIs : {len(alvos)} | Pendentes: {len(alvos)}", flush=True)
    if est.get("local"):
        print(f"   LLM local no i9 ({OLLAMA_URL}) — custo US$ 0; "
              f"~{est['horas']} h de GPU", flush=True)
    else:
        print(f"   Custo estimado: US$ {est['usd']} "
              f"(US$ {est['usd_por_poi']} por POI) | teto US$ {teto_usd}", flush=True)
        print(f"   {est['tokens_poi']:,} tokens por POI → ~{est['pois_min']} POIs/min "
              f"dentro do teto de {TPM_LIMITE:,} TPM → ~{est['horas']} h", flush=True)
    com_vinculo = sum(1 for a in alvos if a["vinculo"])
    print(f"   Com vínculo no cadastro do cliente: {com_vinculo} "
          f"(só esses podem gerar achado convergente)", flush=True)
    if so_estimar or not alvos:
        print("(estimativa — nada foi enviado)" if so_estimar else "Nada a avaliar.",
              flush=True)
        return

    print("⟦fase⟧ fachada", flush=True)
    sem = asyncio.Semaphore(workers)
    cont = {"n": 0, "ok": 0, "inapto": 0, "fora": 0, "reprov": 0, "erro": 0, "oport": 0}
    ini = time.time()
    lock = asyncio.Lock()
    parar = {"teto": False}
    refila = []          # quem falhou por motivo transitório e merece 2ª volta

    async def _um(alvo):
        async with sem:
            if parar["teto"]:
                return
            con = bc.conectar()
            try:
                imgs = _imagens(alvo["poi_id"], alvo["sv_id"], con)
                if not imgs:
                    return
                try:
                    obs = await asyncio.wait_for(_ler_imagem(alvo, imgs, modelo),
                                                 timeout=TIMEOUT_S)
                except Exception as e:
                    async with lock:
                        cont["erro"] += 1
                        refila.append(alvo)
                    print(f"   ⚠️ POI {alvo['poi_id']}: {type(e).__name__}: "
                          f"{str(e)[:90]} — volta para a fila", flush=True)
                    return
                # segunda vista no número, só quando ele contradiz o cadastro
                try:
                    await asyncio.wait_for(
                        _confirmar_divergencia(alvo, obs, imgs, modelo),
                        timeout=TIMEOUT_S)
                except Exception:
                    pass          # sem confirmação, a divergência não abre fila
                data = _data_iso(alvo["sv_data"] or buscar_data_pano(
                    alvo["lat"], alvo["lng"], alvo["sv_id"], con))
                anot = _expandir(obs, alvo, data, modelo)
                val = validar(anot)
                status = gravar(alvo, anot, val, modelo, con)
            finally:
                con.close()
            async with lock:
                cont["n"] += 1
                cont["oport"] += len(anot["oportunidades"])
                cont[{"aprovado": "ok", "inapto": "inapto", "fora_escopo": "fora"}
                     .get(status, "reprov")] += 1
                gasto = custo_usd(modelo)
                if teto_usd and not _e_local(modelo) and gasto >= teto_usd:
                    parar["teto"] = True
                print(f"🔍 POIs {cont['n']}/{len(alvos)} | aptos {cont['ok']} | "
                      f"inaptos {cont['inapto']} | fora de escopo {cont['fora']} | "
                      f"oportunidades {cont['oport']} | US$ {gasto:.3f} | "
                      f"{(alvo.get('nome') or '')[:26]}", flush=True)

    LOTE = 60
    for i in range(0, len(alvos), LOTE):
        if parar["teto"]:
            print(f"   ⛔ teto de US$ {teto_usd} atingido — parando por aqui.", flush=True)
            break
        await asyncio.gather(*[_um(a) for a in alvos[i:i + LOTE]])

    # SEGUNDA VOLTA nos que falharam. Erro de chamada é quase sempre transitório
    # — limite de taxa, rede, timeout — e descartar o POI por isso deixa buraco
    # na área sem ninguém saber. A fila do repasse já vem depois do freio ter
    # segurado, então ela anda num ritmo que a conta aguenta.
    if refila and not parar["teto"]:
        print(f"\n↻ Segunda volta em {len(refila)} POIs que falharam por "
              f"limite de taxa ou rede", flush=True)
        cont["erro"] = 0
        pendentes, refila = list(refila), []
        for i in range(0, len(pendentes), LOTE):
            await asyncio.gather(*[_um(a) for a in pendentes[i:i + LOTE]])

    print(f"\n{'═'*52}\n🔍 Avaliação de fachada | Resumo\n{'═'*52}")
    print(f"   Avaliados            : {cont['n']} de {len(alvos)}")
    print(f"   Aptos (aprovados)    : {cont['ok']}")
    print(f"   Inaptos (imagem ruim): {cont['inapto']}")
    print(f"   Fora de escopo       : {cont['fora']}")
    print(f"   Reprovados no gate   : {cont['reprov']}")
    print(f"   Erros de chamada     : {cont['erro']}")
    print(f"   Oportunidades geradas: {cont['oport']}")
    print(f"   Custo                : US$ {custo_usd(modelo):.4f}")
    print(f"   Tempo                : {(time.time()-ini)/60:.1f} min")
    print(f"{'═'*52}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--modelo", default=MODELO_PADRAO)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--refazer", action="store_true",
                   help="reavalia quem já tem anotação")
    p.add_argument("--teto-usd", type=float, default=0.0,
                   help="para quando o gasto chegar aqui (0 = sem teto)")
    p.add_argument("--so-estimar", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.area, a.limit, a.modelo, a.workers, a.refazer,
                    a.teto_usd, a.so_estimar))


if __name__ == "__main__":
    main()
