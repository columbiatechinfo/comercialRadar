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

BASE = Path(__file__).resolve().parent
SV_DIR = BASE / "streetview"
EXTRAS_DIR = SV_DIR / "extras"          # espelho em disco dos shots 360/deslocados

RECENCIA_MESES = 12                     # janela p/ "recomendar visita" (coment/foto/SV)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.115.117.49:11434")
API = OLLAMA_HOST.rstrip("/") + "/api/generate"
MODELO_PADRAO = "qwen2.5vl:7b"
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
    payload = json.dumps({
        "model": modelo, "prompt": PROMPT_RAMO.format(visto=visto, categoria=categoria),
        "stream": False, "format": "json", "keep_alive": "15m",
        # num_ctx IGUAL ao da percepção (4096): pedir ctx diferente força reload do
        # modelo e o Ollama trava com a VRAM no limite (bug caçado em 06/07/2026 —
        # ctx 1024 pendurava 370s TODO o pipeline; ctx 4096 responde em 0.8s)
        "options": {"num_ctx": 4096, "num_predict": 30, "temperature": 0},
    }).encode()
    for retry in (False, True):
        try:
            req = urllib.request.Request(API, data=payload,
                                         headers={"Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=180).read())
            return _tf(json.loads(resp.get("response", "{}")).get("mesmo_ramo"))
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


def _imgs_b64(imgs: list, largura: int) -> list:
    """1ª imagem (street view, com máscara de UI) na largura cheia; fotos de apoio
    menores e mais comprimidas (o link até o servidor é relay Tailscale)."""
    return [_resize_b64(b, largura, 80, mascarar=True) if i == 0
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
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT dados FROM images_urls
                           WHERE poi_id = %s AND dados IS NOT NULL ORDER BY id LIMIT %s""",
                        (poi_id, limit))
            return [bytes(r[0]) for r in cur.fetchall()]
    except Exception:
        return []


def _observar(modelo: str, imgs_b64: list) -> dict:
    # ctx 4096 FIXO: é o que carrega 100% GPU (5.4GB); pedir mais força reload e
    # derruba pra CPU. 1 SV 1024px (~1.1k tok) + 3 fotos 768px (~0.6k cada) ~ 3.3k ✓.
    # Se um POI estourar (aspecto raro), o caller reduz as fotos e retenta.
    payload = json.dumps({
        "model": modelo, "prompt": PROMPT_SISTEMA, "images": imgs_b64,
        "stream": False, "format": "json", "keep_alive": "15m",
        "options": {"num_ctx": 4096, "num_predict": 480, "temperature": 0},
    }).encode()
    req = urllib.request.Request(API, data=payload, headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read())
    return json.loads(resp.get("response", "{}"))


def _observar_seguro(modelo: str, imgs: list, largura: int) -> dict:
    """Observa com degradação progressiva (contexto) e retry em timeout (fila GPU)."""
    import socket
    import urllib.error
    tentativas = (
        _imgs_b64(imgs, largura),                                   # SV cheia + fotos 768
        [_resize_b64(b, 512, mascarar=(i == 0))                     # TODAS a 512 (mantém
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


def _tf(x):
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() in ("true", "sim", "1") if x is not None else False


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
    import psycopg2
    with conn, conn.cursor() as cur:
        cur.execute("DELETE FROM streetview_imgs WHERE poi_id=%s AND angulo=%s",
                    (poi_id, angulo))
        cur.execute("""INSERT INTO streetview_imgs (poi_id, dados, bytes_tam, lat, lng,
                                                    angulo, data_captura)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (poi_id, psycopg2.Binary(raw), len(raw), lat, lng, angulo, data_captura))


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


def _analisar_poi(modelo: str, imgs: list, largura: int, row: dict, conn=None) -> dict:
    """Percepção + decisão; se divergente MAS há fotos do próprio POI, faz uma
    2ª olhada SÓ nas fotos (CP4/CP10): elas são do estabelecimento em si — confiança
    maior que o street view, que pode estar mostrando o letreiro do vizinho.

    Avalia cada foto ISOLADA (não concatenada): as listagens do Maps misturam fotos
    do ponto com ruído (ex.: pizza + deck de chalé trocado) e concatenar contamina a
    leitura. Cada foto é evidência independente — aprova na 1ª que casar."""
    percep = _observar_seguro(modelo, imgs, largura)
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
                res2["equivalencia"] = ("Foto do próprio ponto confirma o ramo "
                                        "(street view mostrava o vizinho/fachada).")
                res2["_percepcao"]["_2look"] = True
                return res2
    return res


def carregar_alvos(limit: int, refazer: bool, ids: list, so_reprovados: bool = False) -> list:
    conn = realtime_ingest.conectar()
    try:
        with conn.cursor() as cur:
            base_cols = ("id, nome, nome_fantasia, razao_social, categoria, cnae, "
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
                    res = _analisar_poi(modelo, imgs, largura, row, conn)
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
                            imgs = imgs + rot
                            res = _analisar_poi(modelo, imgs, largura, row, conn)
                        if res["veredito"] == "reprovado" and extras_n > 0:
                            extra_imgs = cap.extras(row["lat"], row["lng"], cam, extras_n)
                            for k, shot in enumerate(extra_imgs):
                                _arquivar_shot(conn, row["id"], shot, f"p{k + 1}",
                                               row["lat"], row["lng"], data_pano)
                            if extra_imgs:
                                escalou = True
                                imgs = imgs + extra_imgs
                                res = _analisar_poi(modelo, imgs, largura, row, conn)
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
