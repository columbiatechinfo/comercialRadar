"""
descrever_imagens.py — Análise visual dos POIs por IA (Ollama qwen2.5vl na GPU).

Arquitetura em 2 etapas (mais confiável que jogar tudo no prompt):
  ETAPA 1 (modelo): OBSERVA as imagens e descreve o que vê — cenário, ramo visto,
                    nome no letreiro (lido às cegas), nº de andares, porte.
  ETAPA 2 (código): DECIDE veredito/confere/motivo/construção/lead a partir da
                    percepção + comparação de nome em código (fuzzy).

A percepção é 100% CEGA (o modelo não recebe nenhum dado do cadastro — nem nome, nem
categoria), pra não "papaguear". O casamento com o cadastro acontece depois: nome por
fuzzy em código; ramo por pergunta ISOLADA ao mesmo modelo (só texto — inline o
julgamento saía raso; isolado o qwen acerta, validado 10/10).

Captura incremental (CP6): se a 1ª visão levaria a REPROVAR, busca prints de OUTROS
panoramas/posições (câmeras a ~15 m encarando o POI) e reavalia com todas as imagens
antes de reprovar de fato. Só reprova como último recurso.

Regras calibradas com o cliente: scratchpad/calibracao_prompt.md.
Modelo roda 100% na GPU do servidor remoto (ver [[ollama-gpu-servidor]]).

USO:
  .venv\\Scripts\\python descrever_imagens.py [--limit N] [--workers 3]
      [--largura 1024] [--refazer] [--ids 1644,31861]
      [--incremental] [--extras 2]      # busca outros panoramas antes de reprovar
"""

import io
import re
import os
import math
import json
import time
import base64
import argparse
import threading
import unicodedata
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

import realtime_ingest
import endpoints

BASE = Path(__file__).resolve().parent
SV_DIR = BASE / "streetview"
EXTRAS_DIR = SV_DIR / "extras"          # espelho em disco dos shots 360/deslocados

RECENCIA_MESES = 12                     # janela p/ "recomendar visita" (coment/foto/SV)

# ── A IA MORA NA SPARK, E FALA O PROTOCOLO DA OPENAI (24/08/2026) ───────────
#
# Este módulo falava o protocolo NATIVO do Ollama (`/api/generate`, com
# `images` em base64 puro e `options.num_ctx`). O destino é o vLLM da Spark —
# `Qwen3-VL-30B-A3B` servido como `qwen3vl-moe` — que fala o protocolo da
# OpenAI, o mesmo do `avaliar_fachada.py` e do `leitura_fachada.py`.
#
# Um protocolo só, e a máquina vira endereço. `LOCAL_URL` aponta para a Spark;
# apontar para `http://100.115.117.49:11434/v1` usa o Ollama do i9 pelo MESMO
# código, porque ele também expõe `/v1` — foi assim que este caminho pôde ser
# exercitado antes de a Spark subir.
LOCAL_URL = os.environ.get(
    "VLLM_URL", endpoints.VLLM
).rstrip("/")
MODELO_PADRAO = os.environ.get("MODELO_VISAO", "qwen3vl-moe")

# `num_ctx` SUMIU, e some-se o que isso significa.
#
# Os 4.096 fixos não eram escolha de qualidade: eram o teto do i9, onde pedir
# janela diferente forçava reload do modelo e derrubava para CPU (bug de
# 06/07/2026 — ctx 1024 pendurava 370 s TODO o pipeline). O vLLM serve com
# `--max-model-len 131072` e não recarrega por requisição; a janela deixou de
# ser algo que o cliente negocia.
#
# ATENÇÃO ao herdar disso uma mudança de lógica: a "segunda olhada" é foto a
# foto PORQUE só duas fotos cabiam em 4.096. Com a janela grande isso deixa de
# ser necessário — mas juntar as fotos MUDA O VEREDITO, e mudar veredito sem
# medir é trocar um viés conhecido por um desconhecido. A mudança de transporte
# vem primeiro; a de lógica, depois de comparar lado a lado.


def _chat_local(modelo: str, prompt: str, imgs_b64: list | None = None,
                max_tokens: int = 480, timeout: int = 180) -> dict:
    """Uma chamada de visão pelo protocolo da OpenAI. Devolve o JSON já parseado.

    `urllib` e não a biblioteca `openai`: este módulo é síncrono e roda dentro de
    um pool de threads; trazer um cliente assíncrono aqui obrigaria a reescrever
    o laço inteiro para ganhar nada.
    """
    import urllib.request

    conteudo = [{"type": "text", "text": prompt}]
    for b64 in (imgs_b64 or []):
        conteudo.append({"type": "image_url",
                         "image_url": {"url": "data:image/jpeg;base64," + b64}})
    corpo = json.dumps({
        "model": modelo, "temperature": 0, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": conteudo}],
        # `json_object` e não `json_schema`: o esquema de cada chamada aqui é
        # descrito no PRÓPRIO prompt, e pedir schema estrito exigiria declará-lo
        # duas vezes — em dois lugares que podem divergir.
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(f"{LOCAL_URL}/chat/completions", data=corpo,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer local"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return json.loads(txt or "{}")
IMG_LARGURA = 1024
MAPS_FOTO_LARGURA = 768     # fotos de apoio menores (economia de tokens de visão)
MAX_FOTOS_MAPS = 3
TIMEOUT = 480               # fila do GPU: rajadas de POIs de 4 imagens atrasam a vez

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
CONSENT_COOKIES = [
    {"name": "CONSENT", "value": "YES+cb.20220419-08-p0.pt+FX+410", "domain": ".google.com", "path": "/"},
    {"name": "SOCS", "value": "CAESHAgBEhJnd3NfMjAyMjA0MTktMF9SQzEaAnB0IAEaBgiAo_KTBg", "domain": ".google.com", "path": "/"},
]
_RE_CAM = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+),3a")

# ── Percepção 100% CEGA (o modelo não recebe NADA do cadastro) ──────────────────
# Qualquer dado cadastral mostrado aqui contamina a descrição (o modelo "papagaia"
# nome/categoria em nome_visto/atividade_real). Ele descreve às cegas; o casamento
# com o cadastro acontece depois, em chamada isolada + fuzzy de nome.
PROMPT_SISTEMA = """Você OBSERVA imagens de um local (Google Street View da fachada e, \
quando houver, fotos do ponto) e DESCREVE fielmente o que vê.

Olhe TODAS as imagens e o QUADRO INTEIRO. Um mesmo prédio pode abrigar VÁRIOS \
estabelecimentos — no TÉRREO e nos ANDARES DE CIMA (ex.: farmácia no térreo e clínica \
no 1º andar). LEIA placas, letreiros e vitrines em TODOS os pavimentos e liste CADA \
estabelecimento identificável em "estabelecimentos". O alvo pode ser o de CIMA, não o \
do térreo — por isso não descarte os letreiros dos andares superiores.

REGRAS DE LEITURA (importantes):
- Transcreva APENAS texto que você lê com CERTEZA na imagem. NUNCA complete, adivinhe \
ou "corrija" palavras parcialmente visíveis, borradas ou cobertas — na dúvida, deixe \
"". Um nome inventado é pior que nenhum nome.
- Algumas imagens podem ser do INTERIOR do estabelecimento, de produtos ou de \
serviços prestados (sem placa nenhuma). Nesses casos DEDUZA o ramo pelo conteúdo: \
prateleiras lotadas de mercadoria + caixas registradoras = supermercado/mercearia; \
cabelo tratado/cadeira de corte = salão de beleza; pratos de comida = restaurante; \
consultório/dentes = clínica odontológica; peças de carro = autopeças; roupas em \
araras = loja de roupas; prateleiras de óculos/armações/lentes = ótica; piscina com \
crianças e instrutor/raias = escola de natação; balcão com jalecos/microscópios/tubos \
de exame = laboratório; salão com mesas decoradas/buffet/palco = espaço de eventos; \
altar/crucifixo/imagens de santos/púlpito/bancos de igreja = igreja/templo. \
A foto do INTERIOR vale MAIS que a fachada quando divergirem.

PORTE E FUNCIONÁRIOS (estime pelo tamanho FÍSICO, não chute baixo):
- Avalie o tamanho real: largura da frente, profundidade, nº de pavimentos, de portas/\
vitrines, e se é quiosque/box, loja de rua ou GALPÃO/atacarejo.
- "funcionarios_estimados" = faixa provável de PESSOAS QUE TRABALHAM ali (funcionários, \
não clientes), coerente com o tamanho E o ramo. Referências: quiosque/box 1-2; loja de \
rua pequena 2-5; farmácia/padaria/salão médio 4-10; supermercado/atacarejo/galpão/loja \
grande 15-50; hipermercado/rede 50+. Um GALPÃO ou loja ampla NUNCA tem só "2 a 4".
- "porte": micro (quiosque, 1 pessoa) | pequeno (loja de rua) | medio (loja ampla, \
vários funcionários) | grande (galpão, supermercado, rede).

Responda SOMENTE um JSON válido, sem texto fora dele, com EXATAMENTE estas chaves:
{
  "cenario": "comercio|residencia|area_aberta|vago",
  "estabelecimentos": [{"nome": "letreiro lido ou \\"\\"", "ramo": "ramo específico", "andar": "terreo|superior"}],
  "ramo_visto": "o ramo do estabelecimento PRINCIPAL/mais provável do ponto, o MAIS ESPECÍFICO possível (ex: padaria, loja de cosméticos, oficina mecânica, supermercado, gráfica); \\"\\" se não houver",
  "nome_visto": "letreiro do estabelecimento principal lido NA IMAGEM, ou \\"\\"",
  "andares": 1,
  "porte": "micro|pequeno|medio|grande",
  "funcionarios_estimados": "faixa de funcionários coerente com o porte, ex '15 a 30'",
  "atividade_real": "o que de fato parece funcionar ali (1 frase)"
}
Definições:
- "cenario": "comercio" = loja/empresa/serviço ATIVO ou prédio/complexo residencial \
(condomínio); "residencia" = casa/moradia comum sem comércio; "area_aberta" = praça, \
terreno, muro, área verde; "vago" = ponto comercial vazio com placa ALUGA/VENDE.
- "estabelecimentos": TODOS os pontos comerciais que conseguir ler/deduzir nas imagens \
(térreo e andares); use [] se nenhum. Não invente — só o que enxerga.
- "ramo_visto"/"atividade_real": baseados SÓ nas imagens, nunca em suposições.
- "andares": nº de pavimentos visíveis do prédio principal (1, 2, 3...)."""

CAMPOS_DADOS_EXTRA = [
    ("cnae", "CNAE"), ("natureza_juridica", "Natureza jurídica"),
    ("preco_medio", "Preço médio"), ("avaliacao", "Nota Google"),
]

# ── Comparação de ramo: pergunta ISOLADA ao mesmo modelo (só texto) ─────────────
# Inline junto da percepção o julgamento saía raso (dizia true p/ quase tudo);
# como pergunta única e focada o mesmo qwen acerta (validado 10/10 na calibração).
PROMPT_RAMO = """Você compara atividades comerciais. Diga se as duas atividades abaixo \
são da MESMA família de negócio.

Atividade vista na imagem: "{visto}"
Atividade informada no cadastro: "{categoria}"

Pense na FAMÍLIA de negócio, não nas palavras. Uma variação ou especialização dentro da \
mesma família conta como MESMO ramo. Exemplos que contam como iguais:
- Alimentação (serviço): restaurante, lanchonete, padaria, empadaria, pastelaria, \
pizzaria, cafeteria, bar, sorveteria, açaí — todos são serviço de alimentação.
- Varejo de alimentos: mercearia, mercadinho, armazém, supermercado, atacarejo, \
empório, hortifruti — todos são venda de alimentos/mercado.
- Automotivo: oficina, autopeças, funilaria, lava-jato, concessionária.
- Beleza: salão, barbearia, estética, esmalteria, cabeleireiro.
- Sinônimos e variações também contam (academia = sala de fitness; empadaria = padaria).

Compare o PRODUTO/SERVIÇO específico. Palavras genéricas (comércio, varejista, loja, \
venda, serviço) NÃO tornam ramos diferentes em iguais: "plantas e flores" não é \
"alimentos" nem "eletrônicos"; "supermercado" não é "floricultura".

Responda false quando forem famílias/produtos claramente diferentes (padaria não é loja \
de móveis; farmácia não é oficina; floricultura não é assistência de celular; \
supermercado não é loja de flores).

Responda SOMENTE JSON: {{"mesmo_ramo": true ou false}}"""


# prefixos genéricos de categoria/CNAE que fazem o modelo casar qualquer varejo
# ("Comércio varejista de plantas" ~ "supermercado" pelo "comércio varejista")
_GEN_CAT = re.compile(
    r"^(?:com[eé]rcio\s+(?:varejista|atacadista)\s+de\s+|com[eé]rcio\s+de\s+|"
    r"venda\s+de\s+|revenda\s+de\s+|lojas?\s+de\s+|loja\s+|"
    r"presta[cç][aã]o\s+de\s+servi[cç]os?\s+de\s+|servi[cç]os?\s+de\s+|"
    r"fabrica[cç][aã]o\s+de\s+|escolas?\s+de\s+|academias?\s+de\s+|"
    r"cursos?\s+de\s+|ateli[eê]\s+de\s+)", re.I)


def _essencia_categoria(cat: str) -> str:
    """Remove prefixos genéricos encadeados e devolve o produto/serviço específico
    (ex.: 'Comércio varejista de plantas e flores' -> 'plantas e flores')."""
    c = (cat or "").strip()
    prev = None
    while c and c != prev:
        prev = c
        c = _GEN_CAT.sub("", c).strip()
    return c or (cat or "").strip()


def _mesmo_ramo(modelo: str, visto: str, categoria: str) -> bool:
    # atalho em código: se o produto/serviço específico já coincide no texto (após
    # tirar genéricos), é o MESMO ramo — sem chamar o modelo, que às vezes erra em
    # casos triviais ('natação'/'natação' e 'igreja'/'Igreja católica' -> False).
    # Usa _STOP_RAMO (mínimo): tipo de negócio conta como sinal (roupas≠móveis mas
    # 'loja' some, então o overlap exige o termo distintivo).
    tv, tc = set(_norm_tokens(visto, _STOP_RAMO)), set(_norm_tokens(categoria, _STOP_RAMO))
    if tv and tc and len(tv & tc) / min(len(tv), len(tc)) >= 0.5:
        return True
    prompt = PROMPT_RAMO.format(visto=visto, categoria=categoria)
    for retry in (False, True):
        try:
            # 30 tokens: a resposta é `{"mesmo_ramo": true}`. Teto baixo aqui não
            # é economia, é freio — sem ele um modelo em laço escreve até o
            # contexto acabar e devolve JSON cortado no meio.
            return _tf(_chat_local(modelo, prompt, max_tokens=30, timeout=180)
                       .get("mesmo_ramo"))
        except Exception:
            if retry:
                raise
            time.sleep(10)                     # congestão do relay: respira e retenta

# tokens de nome que não ajudam a identificar (sufixos societários, cidade, genéricos).
# Inclui PALAVRAS DE TIPO DE NEGÓCIO: elas descrevem o ramo, não a identidade — sem
# elas o casamento exige um token DISTINTIVO ("Escola Mística" deixa de casar com
# "Escola Náutica" só pelo "escola"; mas "Escola Municipal Cândido" ainda casa por
# "municipal/candido"). Ver [[calibracao-2look-multiloja]].
_STOP = {"lj", "loja", "lojas", "matriz", "filial", "ltda", "me", "epp", "eireli",
         "cia", "e", "de", "da", "do", "dos", "das", "parnaiba", "piaui", "pi", "sa",
         # tipos de negócio genéricos:
         "escola", "colegio", "creche", "faculdade", "universidade", "instituto",
         "clinica", "consultorio", "laboratorio", "hospital", "posto", "farmacia",
         "drogaria", "bar", "restaurante", "lanchonete", "padaria", "pizzaria",
         "mercado", "mercadinho", "mercantil", "supermercado", "armazem", "comercial",
         "casa", "centro", "salao", "studio", "espaco", "atelie", "academia",
         "hotel", "pousada", "motel", "igreja", "associacao", "condominio",
         "edificio", "residencial", "auto", "oficina", "empresa", "servicos",
         "servico", "grupo", "rede", "super", "ponto", "depósito", "deposito"}

# Para casar RAMO (não nome) as palavras de tipo SÃO o sinal distintivo (igreja,
# escola, farmácia): stop mínimo só com genéricos-container e sufixos societários.
# ('igreja' vs 'Igreja católica' precisa casar; 'Escola Mística' vs 'Escola Náutica'
# NÃO — mas esse é o caminho de NOME, que usa _STOP cheio.)
_STOP_RAMO = {"lj", "loja", "lojas", "matriz", "filial", "ltda", "me", "epp", "eireli",
              "cia", "e", "de", "da", "do", "dos", "das", "parnaiba", "piaui", "pi",
              "sa", "comercio", "comercial", "varejista", "atacadista", "servico",
              "servicos", "venda"}


def _norm_tokens(s, stop=_STOP) -> list:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s).lower()
    return [t for t in s.split() if len(t) >= 3 and t not in stop]


def _limpar_lead(s) -> str | None:
    """Apara o texto do lead: corta a partir de telefone/slogan e limita tamanho."""
    s = (s or "").strip()
    s = re.sub(r"\b(?:TEL|SAC|FONE|WHATS(?:APP)?|CEL)\b.*", "", s, flags=re.I)
    s = re.sub(r"\(?\d{2,}\)?[\s.-]*9?\d{4}[\s.-]*\d{4}.*", "", s)   # nº de telefone
    s = s.strip(" -–|,.")
    return s[:60].strip() or None


def _nome_util(s) -> str:
    """Aplica o filtro CP8: descarta fragmento curto, placa de trânsito e marca
    d'água do Google. Retorna o nome limpo ou "" (não serve como letreiro)."""
    s = (s or "").strip()
    if (len(s) < 4
            or s.upper() in ("PARE", "STOP", "PROIBIDO", "DEVAGAR", "LOMBADA", "PEDESTRE")
            or re.search(r"google|street\s?view", s, re.I)):
        return ""
    return s


def _nome_bate(visto, candidatos) -> bool:
    """True se o letreiro lido casa (fuzzy) com algum nome cadastrado."""
    vt = set(_norm_tokens(visto))
    if not vt:
        return False
    for c in candidatos:
        ct = set(_norm_tokens(c))
        if ct and (vt & ct) and len(vt & ct) / min(len(vt), len(ct)) >= 0.5:
            return True
    return False


def _bearing(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _dest_point(lat, lng, bearing_deg, dist_m):
    R = 6371000.0
    br = math.radians(bearing_deg); la = math.radians(lat); lo = math.radians(lng)
    dr = dist_m / R
    la2 = math.asin(math.sin(la) * math.cos(dr) + math.cos(la) * math.sin(dr) * math.cos(br))
    lo2 = lo + math.atan2(math.sin(br) * math.sin(dr) * math.cos(la),
                          math.cos(dr) - math.sin(la) * math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)


def _dados_prompt(row: dict) -> tuple:
    """Retorna (categoria, bloco_extra) — SEM o nome, pra não papaguear."""
    extra = []
    for campo, rotulo in CAMPOS_DADOS_EXTRA:
        v = row.get(campo)
        if v not in (None, "", 0):
            extra.append(f"- {rotulo}: {str(v)[:120]}")
    return (row.get("categoria") or "(não informada)"), ("\n".join(extra))


def _mascarar_ui(im: Image.Image) -> Image.Image:
    """Cobre os overlays do print do Street View (painel de endereço no topo-esq.
    e minimapa no rodapé-esq.) — o modelo LIA esses textos como se fossem placas
    da fachada (leads falsos tipo '78 R. Castelo' e nomes vindos do minimapa)."""
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    w, h = im.size
    sx, sy = w / 1280.0, h / 656.0            # prints são clips 1280x656
    d.rectangle([0, 0, int(380 * sx), int(155 * sy)], fill=(128, 128, 128))
    d.rectangle([0, int(540 * sy), int(270 * sx), h], fill=(128, 128, 128))
    return im


def _imgs_b64(imgs: list, largura: int, eh_streetview=None) -> list:
    """Street View com máscara de UI e largura cheia; fotos de apoio, menores.

    A MÁSCARA SEGUE A NATUREZA DA IMAGEM, E NÃO A POSIÇÃO DELA.

    Era `if i == 0`, de quando o street view era um só e vinha na frente. Hoje
    não é: o protocolo 360 acrescenta os giros DEPOIS das fotos do Maps
    (`imgs = imgs + rot`), e a captura de fachada já grava três ângulos. Todas
    são prints do Maps, todas trazem o painel de endereço no topo-esquerdo e o
    rótulo do minimapa no rodapé — e todas menos a primeira iam CRUAS.

    O modelo lê esse texto como se fosse placa. Medido em 06/09/2026, no POI
    84240 (Chapa Quente Lanches): o giro `g90` mostrava o rótulo do minimapa
    "Padaria Confeitaria e Cafeteria Sabor Do Trigo", e o modelo devolveu isso
    como `nome_visto` e `ramo_visto` — um estabelecimento VIZINHO, lido da
    interface do Google. O veredito acertou por outro motivo (a ficha do iFood),
    mas a evidência estava inventada.

    `eh_streetview` é a lista de índices que levam máscara. Sem ela, mantém o
    comportamento antigo — a primeira — para não quebrar quem ainda não passa.
    """
    sv = set(range(len(imgs))) if eh_streetview is True else \
        ({0} if eh_streetview is None else set(eh_streetview))
    return [_resize_b64(b, largura, 80, mascarar=True) if i in sv
            else _resize_b64(b, min(largura, MAPS_FOTO_LARGURA), 70)
            for i, b in enumerate(imgs)]


def _resize_b64(raw: bytes, largura: int, qualidade: int = 80, mascarar: bool = False) -> str:
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if mascarar:
        im = _mascarar_ui(im)
    if im.width > largura:
        r = largura / im.width
        im = im.resize((largura, int(im.height * r)))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=qualidade)
    return base64.b64encode(b.getvalue()).decode()


def _fotos_maps_bytes(poi_id: int, conn, limit: int = MAX_FOTOS_MAPS) -> list:
    # Storage desde 12/08/2026, com queda para a coluna `dados` enquanto existir.
    try:
        import imagens
        return imagens.fotos_do_poi(poi_id, conn, limite=limit)
    except Exception:
        return []


def _observar(modelo: str, imgs_b64: list) -> dict:
    """A percepção cega: só as imagens, nenhum dado do cadastro.

    O teto de contexto que existia aqui era do i9 (4.096, o que carregava 100%
    em GPU com 8 GB de VRAM). No vLLM da Spark a janela é do SERVIDOR
    (`--max-model-len 131072`) e não se negocia por requisição — o degradar
    progressivo do `_observar_seguro` continua valendo para o caso raro, mas
    deixou de ser a regra.
    """
    return _chat_local(modelo, PROMPT_SISTEMA, imgs_b64,
                       max_tokens=480, timeout=TIMEOUT)


def _observar_seguro(modelo: str, imgs: list, largura: int,
                     eh_streetview=None) -> dict:
    """Observa com degradação progressiva (contexto) e retry em timeout (fila GPU)."""
    import socket
    import urllib.error
    _sv = set(range(len(imgs))) if eh_streetview is True else \
        ({0} if eh_streetview is None else set(eh_streetview))
    tentativas = (
        _imgs_b64(imgs, largura, eh_streetview),                    # SV cheia + fotos 768
        [_resize_b64(b, 512, mascarar=(i in _sv))                   # TODAS a 512 (mantém
         for i, b in enumerate(imgs)],                              # o giro 360 no jogo)
        [_resize_b64(imgs[0], largura, mascarar=True)],             # só o street view
    )
    for i, b64s in enumerate(tentativas):
        for retry in (False, True):
            try:
                return _observar(modelo, b64s)
            except urllib.error.HTTPError as e:
                if e.code == 400 and i < len(tentativas) - 1:
                    break                      # degrada as imagens
                raise
            except (socket.timeout, urllib.error.URLError, TimeoutError):
                if retry:                      # 2ª vez que estoura: desiste
                    raise
                time.sleep(15)                 # deixa a fila do GPU esvaziar


#: A REGUA DA FACHADA — mesma forma da de `cruzar_ligacao.confianca_de`:
#: uma BASE pelo motivo, mais REFORCOS pelo que sustenta a leitura.
#:
#: Nem todo veredito por imagem vale o mesmo, e tratar todos como binarios
#: apagava diferencas reais. "Letreiro casou com o cadastro" e quase certeza;
#: "residencia sem comercio" e a leitura mais fragil que existe aqui — foi
#: justamente ela que, medida em 26 POIs de Canoas, apareceu 11 vezes sem que o
#: modelo tivesse visto UM letreiro. E "imagem insuficiente" mal e um veredito:
#: e a confissao de que nao deu para julgar.
#:
#: A confianca vale para os DOIS sentidos: e o quanto o veredito se sustenta,
#: aprovando ou reprovando.
CONF_FACHADA = {
    "ok": 0.70,                       # ramo compativel, pela pergunta isolada
    "sem_estabelecimento": 0.75,      # area aberta: a cena e clara
    "ponto_vago": 0.70,               # imovel vazio: clara, mas muda rapido
    "atividade_divergente": 0.60,     # ve outro ramo — pode ser o vizinho
    "residencia_sem_comercio": 0.45,  # a mais fragil: negocio dentro de casa
    "imagem_insuficiente": 0.20,      # mal e veredito
}
#: Reforco de quem casou o NOME no letreiro: e a evidencia mais forte da etapa,
#: porque nome proprio nao se repete por acaso na esquina.
CONF_NOME_CASOU = 0.25
#: Reforco de quem passou pela 2a olhada e voltou confirmado nas fotos DO
#: PROPRIO ponto — evidencia melhor que o street view, que pode mostrar o
#: vizinho.
CONF_2LOOK = 0.10


def _confianca_da_fachada(motivo: str, nome_ok: bool, dois_look: bool = False,
                          n_imgs: int = 0) -> float:
    v = CONF_FACHADA.get(motivo, 0.40)
    if nome_ok:
        v += CONF_NOME_CASOU
    if dois_look:
        v += CONF_2LOOK
    if n_imgs >= 5:
        # MAIS ANGULOS, MAIS CHAO. O protocolo 360 existe para nao reprovar por
        # ter olhado de um lado so; quem passou por ele decidiu vendo mais.
        v += 0.05
    return round(min(1.0, v), 2)


def _tf(x):
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() in ("true", "sim", "1") if x is not None else False


#: Ate quando um comentario de hospede conta como "esta operando".
MESES_HOSPEDE_RECENTE = 12

# ══════════════════════════════════════════════════════════════════════════
# A REGUA DE CONFIANCA DO VEREDITO — 0 a 1, a mesma de `ligacao_poi`.
#
# Existir e estar funcionando sao coisas diferentes, e o rotulo `aprovado`
# nao distinguia as duas. Uma loja com CNPJ que esta FORA DO AR no iFood
# existe; uma hospedagem cujo ultimo hospede foi ha onze meses tambem. Nenhuma
# das duas merece a mesma posicao na lista do operador que a loja aberta agora
# ou a hospedagem com hospede no mes passado.
#
# As duas escalas tem a mesma forma: uma BASE, que e "a fonte registra este
# estabelecimento", mais um PASSO DE ATUALIDADE, que e "e ele esta operando".
# ══════════════════════════════════════════════════════════════════════════

#: iFood — a ficha existe, com CNPJ ou nota.
IFOOD_BASE = 0.55
#: iFood — a loja esta NO AR agora (`bruto.disponivel`). E o sinal mais forte
#: que esta fonte oferece: nao e "ja existiu", e "aceita pedido hoje".
IFOOD_NO_AR = 0.35
#: iFood — reforcos menores de identidade.
IFOOD_COM_CNPJ = 0.05
IFOOD_COM_AVALIACAO = 0.05
#: iFood — teto de quem esta FORA DO AR. A loja existe e pode voltar, mas
#: mandar o fiscal a uma cozinha fechada e o desperdicio que a regua evita.
IFOOD_TETO_FORA_DO_AR = 0.50

#: Airbnb — o anuncio esta no ar, com preco publicado.
AIRBNB_BASE = 0.40
#: Airbnb — o passo que a RECENCIA vale por inteiro, e que decai um doze avos
#: por mes de distancia ate zerar em doze meses. Decisao do dono do produto
#: (06/09/2026): "caindo a cada mes ate zerar quando passar de 1 ano".
#:
#: Decai LINEAR de proposito. Uma curva daria mais precisao aparente sobre um
#: dado que ja e grosso — o Airbnb data o comentario por MES, sem dia.
PESO_RECENCIA = 0.50
#: Airbnb — reforco de historico: anuncio com muitos hospedes ao longo do
#: tempo e operacao estabelecida, mesmo que o ultimo comentario nao seja de
#: ontem.
AIRBNB_MUITAS_AVALIACOES = 0.10
AIRBNB_AVALIACOES_MUITAS = 20


def _passo_recencia(datas) -> tuple:
    """(passo, meses, ultimo) a partir das datas de comentario, a mais nova.

    `datas` sao pares (ano, mes) ja ordenados do mais novo para o mais velho.
    Devolve o passo de confianca — cheio no mes corrente, zero a partir de
    `MESES_HOSPEDE_RECENTE`.
    """
    import datetime as _dt
    if not datas:
        return 0.0, None, None
    hoje = _dt.date.today()
    meses = (hoje.year * 12 + hoje.month) - (datas[0][0] * 12 + datas[0][1])
    meses = max(0, meses)
    if meses >= MESES_HOSPEDE_RECENTE:
        return 0.0, meses, datas[0]
    return round(PESO_RECENCIA * (1.0 - meses / float(MESES_HOSPEDE_RECENTE)),
                 3), meses, datas[0]

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


def _prova_da_fonte(row: dict, conn) -> dict | None:
    """O veredito que o REGISTRO DA FONTE ja decide, sem olhar fachada.

    POR QUE ISTO EXISTE. Medido em 06/09/2026, 26 POIs de Canoas julgados por
    fachada: 11 foram reprovados como "residencia sem comercio" e, nos ONZE, o
    modelo nao tinha visto letreiro nenhum. Nao era o codigo descartando
    evidencia — nao havia evidencia na fachada. Uma cozinha de delivery e uma
    hospedagem por temporada funcionam DENTRO DE CASA; fotografar a casa e
    concluir "sem comercio" e responder a pergunta errada.

    E no iFood a inversao e completa: "parece uma casa" e prova A FAVOR da
    tese, e nao contra. O produto procura comercio pagando tarifa
    residencial — a cozinha na casa E o achado.

    Devolve o veredito quando a fonte decide, ou None para seguir pela imagem.
    """
    fonte = (row.get("fonte") or "").strip().lower()
    if conn is None or fonte not in ("ifood", "airbnb"):
        return None

    if fonte == "ifood":
        # A FICHA DO MERCHANT E MAIS FORTE QUE QUALQUER FACHADA. Em Canoas:
        # 100% das 950 lojas com CNPJ, 100% com rua, 100% com nota, 96,7% com
        # avaliacoes. Uma loja anunciada no iFood, com CNPJ e nota, e um
        # estabelecimento — a foto da rua nao acrescenta nada a isso.
        with conn.cursor() as k:
            k.execute("""select m.cnpj, m.nota, m.avaliacoes, m.categoria,
                                (m.bruto->>'disponivel')
                           from radar_comercial.ifood_merchant m
                          where m.poi_id = %s limit 1""", (row["id"],))
            r = k.fetchone()
        if not r:
            return None
        cnpj, nota, aval, cat, disp = r
        if not (cnpj or nota is not None):
            return None

        no_ar = (str(disp).lower() == "true") if disp is not None else None
        conf = IFOOD_BASE
        if cnpj:
            conf += IFOOD_COM_CNPJ
        if aval:
            conf += IFOOD_COM_AVALIACAO
        if no_ar:
            conf += IFOOD_NO_AR
        if no_ar is False:
            # FORA DO AR NAO REPROVA — a loja existe e o CNPJ e real. Mas
            # perde a frente da fila: o fiscal nao deve sair para uma cozinha
            # que o proprio iFood diz estar fechada.
            conf = min(conf, IFOOD_TETO_FORA_DO_AR)
        conf = round(min(1.0, conf), 2)

        estado = ("no ar agora" if no_ar
                  else "FORA DO AR no iFood" if no_ar is False
                  else "sem informação de disponibilidade")
        return {
            "confere": True,
            "veredito": "aprovado" if no_ar is not False else "revisao_humana",
            "motivo": "ok" if no_ar is not False else "loja_fora_do_ar",
            "confianca": conf,
            "equivalencia": ("Loja no iFood com %s%s, %s — a ficha da fonte "
                             "prova o estabelecimento; a fachada apenas reforça."
                             % ("CNPJ" if cnpj else "nota",
                                " e %d avaliações" % aval if aval else "",
                                estado)),
            "veredito_motivo": cat or None, "atividade_real": cat or None,
            "porte": None, "pessoas_estimadas": None,
            "tipo_construcao": None, "outro_estabelecimento": None,
            "_percepcao": {"prova_da_fonte": "ifood", "disponivel": no_ar},
            "_nome_ok": True,
        }

    # AIRBNB: A FACHADA NEM E DELE. O site desloca o pino de proposito — 34 dos
    # 44 POIs de Canoas com `coord_exata` falso —, entao o predio fotografado
    # pode ser outro. Julgar isso por imagem nao e conservador, e ruido.
    #
    # O que prova operacao comercial e o proprio anuncio: esta com preco (no ar)
    # e recebeu hospede no ultimo ano. Decisao do dono do produto, 06/09/2026.
    with conn.cursor() as k:
        k.execute("""select a.preco_total is not null, a.avaliacoes, a.avaliacoes_qtd
                       from radar_comercial.airbnb_anuncio a
                      where a.poi_id = %s limit 1""", (row["id"],))
        r = k.fetchone()
    if not r:
        return None
    anunciado, avals, qtd = r
    datas = sorted([d for d in (_mes_ano((x or {}).get("data"))
                                for x in (avals or []) if isinstance(x, dict)) if d],
                   reverse=True)
    passo, meses, ultimo = _passo_recencia(datas)

    conf = (AIRBNB_BASE if anunciado else 0.0) + passo
    if qtd and qtd >= AIRBNB_AVALIACOES_MUITAS:
        conf += AIRBNB_MUITAS_AVALIACOES
    conf = round(min(1.0, conf), 2)

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
            "confere": True, "veredito": "aprovado", "motivo": "ok",
            "confianca": conf,
            "equivalencia": ("Anúncio no ar, %s — hospedagem em operação. A "
                             "fachada não julga: o Airbnb desloca o pino de "
                             "propósito." % quando),
            "veredito_motivo": "hospedagem", "atividade_real": "hospedagem",
            "porte": None, "pessoas_estimadas": None, "tipo_construcao": None,
            "outro_estabelecimento": None,
            "_percepcao": {"prova_da_fonte": "airbnb", "meses_desde_hospede": meses},
            "_nome_ok": True,
        }
    return {
        "confere": False, "veredito": "revisao_humana",
        "motivo": "sem_sinal_de_operacao", "confianca": conf,
        # NAO TER COMENTARIO NAO E O MESMO QUE TER UM ANTIGO. O primeiro e
        # falta de dado — em Canoas, 25 dos 55 anuncios estavam com o detalhe
        # PENDENTE e por isso sem avaliacao nenhuma. O segundo e sinal. Dizer
        # "o passo zerou porque passou de 12 meses" no caso da falta seria
        # inventar uma medicao que nao foi feita.
        "equivalencia": ("Anúncio %s, %s — %s. Não reprovado pela fachada, que "
                         "no Airbnb aponta para o prédio errado."
                         % ("no ar" if anunciado else "fora do ar", quando,
                            "sem como medir atualidade" if ultimo is None else
                            "o passo de atualidade zerou (mais de %d meses)"
                            % MESES_HOSPEDE_RECENTE)),
        "veredito_motivo": "hospedagem", "atividade_real": "hospedagem",
        "porte": None, "pessoas_estimadas": None, "tipo_construcao": None,
        "outro_estabelecimento": None,
        "_percepcao": {"prova_da_fonte": "airbnb", "meses_desde_hospede": meses},
        "_nome_ok": False,
    }


def _decidir(p: dict, row: dict, modelo: str) -> dict:
    """Decide a partir da percepção; o casamento ramo↔categoria é uma 2ª chamada
    focada ao mesmo modelo (só texto), e nome↔cadastro é fuzzy em código."""
    cenario = (p.get("cenario") or "").strip().lower()
    ramo = (p.get("ramo_visto") or "").strip()
    # CP8 (via _nome_util): descarta fragmento/placa de trânsito/marca d'água Google —
    # o '© Google' e banners "mais avaliada do Google" já viraram falsa aprovação.
    nome_visto = _nome_util(p.get("nome_visto"))
    # TODOS os letreiros lidos (térreo + andares de cima): num prédio multi-loja o
    # alvo pode ser o letreiro de cima, não o principal. Casar nome contra todos eles
    # resgata o co-localizado (ex.: clínica "+Sorriso" sobre a drogaria do térreo).
    estabs = p.get("estabelecimentos") if isinstance(p.get("estabelecimentos"), list) else []
    nomes_vistos = [nome_visto] + [_nome_util((e or {}).get("nome"))
                                   for e in estabs if isinstance(e, dict)]
    nomes_vistos = list(dict.fromkeys(n for n in nomes_vistos if n))   # únicos, sem ""
    try:
        andares = int(float(p.get("andares") or 1))
    except (ValueError, TypeError):
        andares = 1

    cand_cad = [row.get("nome"), row.get("nome_fantasia"), row.get("razao_social")]
    nome_ok = any(_nome_bate(n, cand_cad) for n in nomes_vistos)

    if cenario == "area_aberta":
        construcao = "area_aberta"
    elif andares >= 2:
        construcao = "predio"
    elif cenario == "residencia":
        construcao = "casa"
    else:
        construcao = "loja_terrea"

    categoria = (row.get("categoria") or "").strip()
    outro = None
    mesmo = False
    if cenario == "area_aberta":
        conf, veredito, motivo = False, "reprovado", "sem_estabelecimento"
    elif cenario == "vago":
        conf, veredito, motivo = False, "reprovado", "ponto_vago"
    elif nome_ok:                                # letreiro casa com o cadastro
        conf, veredito, motivo = True, "aprovado", "ok"
    elif cenario == "residencia":
        conf, veredito, motivo = False, "reprovado", "residencia_sem_comercio"
    elif not categoria:
        # sem categoria cadastrada não há com o que comparar: só o NOME batendo
        # aprova (já tratado acima); comércio genérico em volta não é evidência
        # do POI em si (ex.: condomínio "aprovado" pela loja vizinha no giro 360)
        conf, veredito, motivo = False, "reprovado", "imagem_insuficiente"
    else:
        # comércio sem nome casado → pergunta focada: ramo visto = categoria?
        # SÓ o ramo entra na comparação (nome não descreve atividade — comparar
        # nome×categoria gerou "2 Google"≈"Empresa de Software" aprovado);
        # nunca usar atividade_real (campo descritivo, sujeito a eco)
        if not ramo:                             # sem ramo legível → não dá pra julgar
            conf, veredito, motivo = False, "reprovado", "imagem_insuficiente"
        else:
            mesmo = _mesmo_ramo(modelo, ramo, _essencia_categoria(categoria))
            if mesmo:
                conf, veredito, motivo = True, "aprovado", "ok"
            else:                                # ramo diferente → lead (CP2)
                conf, veredito, motivo = False, "reprovado", "atividade_divergente"
                outro = _limpar_lead(nome_visto or ramo)

    if nome_ok:
        base = "nome no letreiro casou"
    elif motivo == "imagem_insuficiente":
        base = "sem nome nem ramo legíveis — impossível confrontar"
    elif mesmo:
        base = "ramo compatível"
    elif motivo == "atividade_divergente":
        base = f"ramo visto ({ramo or cenario}) diverge"
    else:
        base = f"cenário '{cenario}' incompatível"
    return {
        "confere": conf, "veredito": veredito, "motivo": motivo,
        "confianca": _confianca_da_fachada(motivo, nome_ok),
        "equivalencia": f"{base} com a categoria '{row.get('categoria') or '(vazia)'}'.",
        "veredito_motivo": p.get("atividade_real"),
        "porte": p.get("porte"),
        "pessoas_estimadas": p.get("funcionarios_estimados") or p.get("pessoas_estimadas"),
        "atividade_real": p.get("atividade_real"), "tipo_construcao": construcao,
        "outro_estabelecimento": outro, "_percepcao": p, "_nome_ok": nome_ok,
    }


def _arquivar_shot(conn, poi_id: int, raw: bytes, angulo: str, lat=None, lng=None,
                   data_captura=None):
    """Grava o shot no banco (streetview_imgs.angulo distingue fachada/g90/g180/
    g270/p1/p2) e espelha em disco. Idempotente: refazer substitui o mesmo ângulo."""
    EXTRAS_DIR.mkdir(parents=True, exist_ok=True)
    (EXTRAS_DIR / f"{poi_id}_{angulo}.jpg").write_bytes(raw)
    # Bytes no Storage, caminho no banco (12/08/2026).
    import imagens
    imagens.gravar_streetview(poi_id, raw, lat, lng, conn,
                              angulo=angulo, data_captura=data_captura)


def _recomendar_visita(conn, poi_id: int, aprovado: bool) -> tuple:
    """Pós-aprovação: recomenda visita se há sinal de vida RECENTE (comentário,
    foto com EXIF ou street view dentro de RECENCIA_MESES). Retorna (bool, motivo)."""
    if not aprovado:
        return False, "não aprovado nos critérios anteriores"
    evid = []
    with conn.cursor() as cur:
        # comentários do Google usam data relativa ("3 semanas atrás"); qualquer
        # coisa sem "ano" está dentro dos últimos 12 meses
        cur.execute("""SELECT COUNT(*) FROM comentarios
                       WHERE poi_id=%s AND COALESCE(data,'')<>'' AND data !~* 'ano'""",
                    (poi_id,))
        n = cur.fetchone()[0]
        if n:
            evid.append(f"{n} comentário(s) recente(s)")
        cur.execute("""SELECT COUNT(*) FROM images_urls
                       WHERE poi_id=%s AND data_imagem >= to_char(now() - interval '%s months','YYYY-MM-DD')""",
                    (poi_id, RECENCIA_MESES))
        n = cur.fetchone()[0]
        if n:
            evid.append(f"{n} foto(s) recente(s)")
        cur.execute("""SELECT COUNT(*) FROM streetview_imgs
                       WHERE poi_id=%s AND data_captura >= to_char(now() - interval '%s months','YYYY-MM')""",
                    (poi_id, RECENCIA_MESES))
        n = cur.fetchone()[0]
        if n:
            evid.append("street view recente")
    if evid:
        return True, "aprovado + " + ", ".join(evid)
    return False, f"aprovado, mas sem sinal de vida nos últimos {RECENCIA_MESES} meses"


FOTOS_2LOOK = 3           # 2ª olhada: avalia até 3 fotos do próprio ponto, UMA A UMA


def _analisar_poi(modelo: str, imgs: list, largura: int, row: dict, conn=None,
                  eh_streetview=None) -> dict:
    """Percepção + decisão; se divergente MAS há fotos do próprio POI, faz uma
    2ª olhada SÓ nas fotos (CP4/CP10): elas são do estabelecimento em si — confiança
    maior que o street view, que pode estar mostrando o letreiro do vizinho.

    Avalia cada foto ISOLADA (não concatenada): as listagens do Maps misturam fotos
    do ponto com ruído (ex.: pizza + deck de chalé trocado) e concatenar contamina a
    leitura. Cada foto é evidência independente — aprova na 1ª que casar."""
    # A FONTE DECIDE PRIMEIRO, QUANDO ELA PODE DECIDIR.
    #
    # Para iFood e Airbnb a fachada nao e evidencia do estabelecimento — ver
    # `_prova_da_fonte`. A percepcao continua rodando, porque ela REFORCA: o
    # letreiro que confirma o ramo vira nota no veredito. O que ela nao faz
    # mais e reprovar quem a propria fonte ja prova.
    veredito_da_fonte = _prova_da_fonte(row, conn)

    percep = _observar_seguro(modelo, imgs, largura, eh_streetview)
    if veredito_da_fonte is not None:
        veredito_da_fonte["_percepcao"] = dict(percep,
                                               prova_da_fonte=row.get("fonte"))
        ramo = (percep.get("ramo_visto") or "").strip()
        if ramo and veredito_da_fonte["veredito"] == "aprovado":
            veredito_da_fonte["equivalencia"] += (
                " Fachada mostra %s, o que reforça." % ramo)
        return veredito_da_fonte

    res = _decidir(percep, row, modelo)
    # dispara também em residencia/sem_estabelecimento: a fachada murada ou o terreno
    # podem esconder o negócio que as fotos do PRÓPRIO ponto revelam (ex.: creche com
    # muro-fachada de casa, mas a foto do Maps é o letreiro/mural da escola).
    if res.get("motivo") in ("atividade_divergente", "imagem_insuficiente",
                             "residencia_sem_comercio", "sem_estabelecimento"):
        fotos = (_fotos_maps_bytes(row["id"], conn, FOTOS_2LOOK) if conn is not None
                 else imgs[1:1 + FOTOS_2LOOK])
        for b in fotos:                           # uma foto por vez (evita foto-ruído)
            try:
                p2 = _observar(modelo, [_resize_b64(b, 640, 72)])
            except Exception:
                continue                          # 400/timeout nessa foto: tenta a próxima
            res2 = _decidir(p2, row, modelo)
            if res2.get("veredito") == "aprovado":
                res2["confianca"] = _confianca_da_fachada(
                    res2.get("motivo"), res2.get("_nome_ok"), dois_look=True)
                res2["equivalencia"] = ("Foto do próprio ponto confirma o ramo "
                                        "(street view mostrava o vizinho/fachada).")
                res2["_percepcao"]["_2look"] = True
                return res2
    return res


def carregar_alvos(limit: int, refazer: bool, ids: list, so_reprovados: bool = False) -> list:
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            # `fonte` E NECESSARIA PARA JULGAR. Ver `_prova_da_fonte`: ha
            # fontes cujo proprio registro ja prova o estabelecimento, e nelas
            # a fachada nao pode reprovar.
            base_cols = ("id, fonte, nome, nome_fantasia, razao_social, categoria, cnae, "
                         "natureza_juridica, preco_medio, avaliacao, streetview_path, "
                         "COALESCE(maps_lat, lat_origem) AS lat, "
                         "COALESCE(maps_lng, lng_origem) AS lng")
            cond = ["streetview_path IS NOT NULL", "streetview_path <> 'NA'",
                    "COALESCE(maps_lat, lat_origem) IS NOT NULL"]
            params = []
            if ids:
                cond.append("id = ANY(%s)")
                params.append(ids)
            elif so_reprovados:
                # re-passe: só reprovados que ainda não passaram pelo protocolo 360
                cond.append("""EXISTS (SELECT 1 FROM analise_ia a
                                       WHERE a.poi_id = pois.id
                                         AND a.veredito = 'reprovado'
                                         AND NOT jsonb_exists(a.resposta_json, '_360'))""")
            elif not refazer:
                cond.append("NOT EXISTS (SELECT 1 FROM analise_ia a WHERE a.poi_id = pois.id)")
            if not ids:
                # pula os pile-ups alucinados (coord lixo, não dá pra analisar por SV)
                cond.append("NOT COALESCE(revisar_manual, false)")
            cur.execute(f"SELECT {base_cols} FROM pois WHERE {' AND '.join(cond)} ORDER BY id", params)
            nomes = [d[0] for d in cur.description]
            rows = [dict(zip(nomes, r)) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows[:limit] if limit else rows


def _gravar(row: dict, res: dict, modelo: str, n_imgs: int, conn):
    outro = (res.get("outro_estabelecimento") or "").strip() or None
    aprovado = str(res.get("veredito", "")).lower().startswith("aprov")
    rec, rec_motivo = _recomendar_visita(conn, row["id"], aprovado)
    res["recomendar_visita"] = rec
    with conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO analise_ia (poi_id, confere, equivalencia, porte, pessoas_estimadas,
                                    atividade_real, veredito, veredito_motivo, motivo,
                                    outro_estabelecimento, tipo_construcao, resposta_json,
                                    modelo, imagem_fonte, n_imagens,
                                    recomendar_visita, recomendacao_motivo)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (poi_id) DO UPDATE SET
                confere=EXCLUDED.confere, equivalencia=EXCLUDED.equivalencia,
                porte=EXCLUDED.porte, pessoas_estimadas=EXCLUDED.pessoas_estimadas,
                atividade_real=EXCLUDED.atividade_real, veredito=EXCLUDED.veredito,
                veredito_motivo=EXCLUDED.veredito_motivo, motivo=EXCLUDED.motivo,
                outro_estabelecimento=EXCLUDED.outro_estabelecimento,
                tipo_construcao=EXCLUDED.tipo_construcao, resposta_json=EXCLUDED.resposta_json,
                modelo=EXCLUDED.modelo, imagem_fonte=EXCLUDED.imagem_fonte,
                n_imagens=EXCLUDED.n_imagens, criado_em=now(),
                recomendar_visita=EXCLUDED.recomendar_visita,
                recomendacao_motivo=EXCLUDED.recomendacao_motivo
        """, (row["id"], res.get("confere"), res.get("equivalencia"), res.get("porte"),
              res.get("pessoas_estimadas"), res.get("atividade_real"), res.get("veredito"),
              res.get("veredito_motivo"), res.get("motivo"), outro, res.get("tipo_construcao"),
              json.dumps(res, ensure_ascii=False), modelo, row.get("streetview_path"), n_imgs,
              rec, rec_motivo))


def _linha(cont, total, row, res, n, escalou):
    tag = " ↑extra" if escalou else ""
    print(f"  {cont['n']}/{total} #{row['id']} {str(row.get('nome',''))[:22]:22} "
          f"| {res.get('veredito','?'):9} | {str(res.get('motivo','')):22} "
          f"| {res.get('tipo_construcao','?'):11} | {n}img{tag}", flush=True)


# ── Captura incremental de outros panoramas/posições (Playwright sync) ──────────
class SVCapturador:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.br = self._pw.chromium.launch(headless=True)
        self.ctx = self.br.new_context(locale="pt-BR", user_agent=UA)
        self.ctx.add_cookies(CONSENT_COOKIES)
        self.page = self.ctx.new_page()
        self.page.set_viewport_size({"width": 1280, "height": 800})

    def _abrir(self, lat, lng, heading=None):
        q = (f"https://www.google.com/maps/@?api=1&map_action=pano"
             f"&viewpoint={lat},{lng}&hl=pt-BR")
        if heading is not None:
            q += f"&heading={heading:.0f}&pitch=5&fov=80"
        try:
            self.page.goto(q, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            return None
        fim = time.time() + 12
        while time.time() < fim:
            m = _RE_CAM.search(self.page.url)
            if m:
                return float(m.group(1)), float(m.group(2))
            self.page.wait_for_timeout(400)
        return None

    _MESES = {"jan": "01", "fev": "02", "mar": "03", "abr": "04", "mai": "05",
              "jun": "06", "jul": "07", "ago": "08", "set": "09", "out": "10",
              "nov": "11", "dez": "12"}

    def data_pano(self) -> str | None:
        """Lê a data do panorama do painel do Maps ('ago. de 2022' → '2022-08').
        A API de metadata está com a chave negada; o DOM entrega a mesma data."""
        try:
            txt = self.page.evaluate(
                "() => (document.body.innerText.match("
                "/(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\\.? de (\\d{4})/i)"
                " || []).slice(1,3).join(' ')")
            if txt:
                mes, ano = txt.split()
                return f"{ano}-{self._MESES[mes.lower()[:3]]}"
        except Exception:
            pass
        return None

    def _shot(self) -> bytes:
        """Print já com a máscara de UI aplicada (painel de endereço + minimapa)."""
        self.page.wait_for_timeout(2500)
        raw = self.page.screenshot(type="jpeg", quality=80,
                                   clip={"x": 0, "y": 64, "width": 1280, "height": 656})
        im = _mascarar_ui(Image.open(io.BytesIO(raw)).convert("RGB"))
        b = io.BytesIO()
        im.save(b, "JPEG", quality=80)
        return b.getvalue()

    def facade(self, lat, lng):
        """Print encarando a fachada. Retorna (bytes, cam) ou (None, None)."""
        cam = self._abrir(lat, lng, None)
        if not cam:
            return None, None
        h = _bearing(cam[0], cam[1], lat, lng)
        if self._abrir(lat, lng, h) is None:
            self._abrir(lat, lng, None)
        return self._shot(), cam

    def giro360(self, lat, lng, cam) -> list:
        """CP9: gira o MESMO panorama 360° (fachada +90°, +180°, +270°) pra olhar
        todo o entorno antes de qualquer reprovação."""
        out = []
        if not cam:
            return out
        base = _bearing(cam[0], cam[1], lat, lng)
        for delta in (90, 180, 270):
            if self._abrir(lat, lng, (base + delta) % 360) is not None:
                out.append(self._shot())
        return out

    def extras(self, lat, lng, cam, n=2) -> list:
        """Prints de OUTROS panoramas: desloca ~15 m ao longo da rua (perpendicular
        à linha câmera→POI), encarando o POI de ângulos diferentes."""
        out = []
        if not cam:
            return out
        b = _bearing(cam[0], cam[1], lat, lng)
        for sign in (1, -1):
            if len(out) >= n:
                break
            plat, plng = _dest_point(lat, lng, (b + 90 * sign) % 360, 15)
            cam2 = self._abrir(plat, plng, None)
            if not cam2:
                continue
            h2 = _bearing(cam2[0], cam2[1], lat, lng)
            if self._abrir(plat, plng, h2) is None:
                continue
            out.append(self._shot())
        return out

    def close(self):
        for fn in (self.br.close, self._pw.stop):
            try:
                fn()
            except Exception:
                pass


def run(limit, workers, modelo, largura, refazer, ids, incremental, extras_n,
        so_reprovados=False):
    alvos = carregar_alvos(limit, refazer, ids, so_reprovados)
    total = len(alvos)
    modo = f"incremental (+{extras_n} panoramas)" if incremental else "direto"
    print(f"🔎 Análise IA: {total} POIs | {modelo} | img {largura}px | modo {modo}", flush=True)
    if not total:
        print("Nada a analisar.")
        return

    cont = {"ok": 0, "aprov": 0, "reprov": 0, "leads": 0, "escal": 0, "erro": 0, "n": 0}
    lock = threading.Lock()
    ini = time.time()

    def _contabiliza(row, res, n, escalou):
        with lock:
            cont["ok"] += 1; cont["n"] += 1
            if escalou:
                cont["escal"] += 1
            v = str(res.get("veredito", "")).lower()
            cont["aprov" if "aprov" in v else "reprov"] += 1
            if (res.get("outro_estabelecimento") or "").strip():
                cont["leads"] += 1
            _linha(cont, total, row, res, n, escalou)

    # ── modo incremental: 1 navegador, sequencial, busca panoramas extras ──
    if incremental:
        cap = SVCapturador()
        conn = realtime_ingest.conectar()
        try:
            for row in alvos:
                try:
                    facade, cam = cap.facade(row["lat"], row["lng"])
                    if facade is None:
                        fp = SV_DIR / (row.get("streetview_path") or "")
                        facade = fp.read_bytes() if fp.exists() else None
                    if facade is None:
                        with lock:
                            cont["erro"] += 1; cont["n"] += 1
                        continue
                    data_pano = cap.data_pano() if cam else None
                    if cam:
                        _arquivar_shot(conn, row["id"], facade, "facade",
                                       row["lat"], row["lng"], data_pano)
                    imgs = [facade] + _fotos_maps_bytes(row["id"], conn)
                    # QUAIS SAO PRINTS DO MAPS. A lista cresce por tras (giros
                    # e panoramas deslocados entram no fim), e por isso os
                    # indices vao explicitos em vez de "o primeiro".
                    sv = [0]
                    res = _analisar_poi(modelo, imgs, largura, row, conn, sv)
                    escalou = False
                    # CP9: NUNCA reprovar sem antes girar o panorama 360° e olhar
                    # o entorno; se ainda reprovar, tenta panoramas deslocados.
                    if res["veredito"] == "reprovado" and cam:
                        rot = cap.giro360(row["lat"], row["lng"], cam)
                        for k, shot in enumerate(rot):
                            _arquivar_shot(conn, row["id"], shot, f"g{90 * (k + 1)}",
                                           row["lat"], row["lng"], data_pano)
                        if rot:
                            escalou = True
                            sv += list(range(len(imgs), len(imgs) + len(rot)))
                            imgs = imgs + rot
                            res = _analisar_poi(modelo, imgs, largura, row, conn, sv)
                        if res["veredito"] == "reprovado" and extras_n > 0:
                            extra_imgs = cap.extras(row["lat"], row["lng"], cam, extras_n)
                            for k, shot in enumerate(extra_imgs):
                                _arquivar_shot(conn, row["id"], shot, f"p{k + 1}",
                                               row["lat"], row["lng"], data_pano)
                            if extra_imgs:
                                escalou = True
                                sv += list(range(len(imgs),
                                                 len(imgs) + len(extra_imgs)))
                                imgs = imgs + extra_imgs
                                res = _analisar_poi(modelo, imgs, largura, row,
                                                    conn, sv)
                        res["_360"] = True         # marca: passou pelo protocolo 360
                    _gravar(row, res, modelo, len(imgs), conn)
                    _contabiliza(row, res, len(imgs), escalou)
                except Exception as e:
                    with lock:
                        cont["erro"] += 1; cont["n"] += 1
                    print(f"  #{row.get('id')} erro: {str(e)[:70]}", flush=True)
        finally:
            cap.close(); conn.close()

    # ── modo direto: usa o print já salvo, paralelo ──
    else:
        _local = threading.local()

        def _conn():
            if not hasattr(_local, "c") or _local.c.closed:
                _local.c = realtime_ingest.conectar()
            return _local.c

        def _um(row):
            try:
                fp = SV_DIR / (row.get("streetview_path") or "")
                if not fp.exists():
                    with lock:
                        cont["erro"] += 1; cont["n"] += 1
                    return
                conn = _conn()
                imgs = [fp.read_bytes()] + _fotos_maps_bytes(row["id"], conn)
                res = _analisar_poi(modelo, imgs, largura, row, conn)
                _gravar(row, res, modelo, len(imgs), conn)
                _contabiliza(row, res, len(imgs), False)
            except Exception as e:
                with lock:
                    cont["erro"] += 1; cont["n"] += 1
                print(f"  #{row.get('id')} erro: {str(e)[:70]}", flush=True)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_um, alvos))

    dt = time.time() - ini
    print(f"\n{'═'*58}")
    print(f"🔎 Análise IA | Resumo")
    print(f"{'═'*58}")
    print(f"   Analisados : {cont['ok']}/{total}  (erro: {cont['erro']})")
    print(f"   Aprovados  : {cont['aprov']}   Reprovados: {cont['reprov']}   Escalados: {cont['escal']}")
    print(f"   Leads novos (outro estabelecimento): {cont['leads']}")
    print(f"   Tempo      : {dt/60:.1f} min  ({dt/max(cont['ok'],1):.1f}s/POI)")
    print(f"{'═'*58}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--modelo", default=MODELO_PADRAO)
    p.add_argument("--largura", type=int, default=IMG_LARGURA)
    p.add_argument("--refazer", action="store_true")
    p.add_argument("--ids", default="")
    p.add_argument("--incremental", action="store_true",
                   help="Busca outros panoramas/posições antes de reprovar (CP6)")
    p.add_argument("--extras", type=int, default=2, help="Qtd de panoramas extras")
    p.add_argument("--so-reprovados", action="store_true",
                   help="Re-passa só os reprovados que ainda não viram imagens extras")
    a = p.parse_args()
    ids = [int(x) for x in a.ids.split(",") if x.strip()] if a.ids else []
    run(a.limit, a.workers, a.modelo, a.largura, a.refazer, ids, a.incremental, a.extras,
        a.so_reprovados)


if __name__ == "__main__":
    main()
