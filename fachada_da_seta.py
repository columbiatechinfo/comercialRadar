# -*- coding: utf-8 -*-
"""fachada_da_seta.py — qual fachada e a da seta, e a placa do vizinho so com o nome em outra fonte (15/09/2026).

Decisao do dono do produto: "ta tendo vereditos com base em vizinho novamente, isso so deve ocorrer se alguma
outra fonte trouxer o nome do estabelecimento visto nos vizinhos". A leitura antiga pedia a IA para dizer se a
placa estava "no imovel da seta" ou "colada", e ela punha a AGROPECUARIA GAUCHO e a MR DISTRIBUIDORA do lado
como "no imovel da seta" (ligacoes 353327 e a do POI 385691).

Agora a divisao do trabalho e:
  - a IA (`PROMPT`, uma imagem, uma tarefa) lista CADA FACHADA com a caixa, os textos e os sinais dela;
  - o CODIGO escolhe a fachada da seta: a caixa que contem a ponta (`mira_x` gravado na captura e a altura da
    ponta em `desenho_seta.PONTA_REL`), e entre varias a menor;
  - placa de fachada vizinha so vale se o NOME ou o TELEFONE aparece em outra fonte: nome dos registros, ficha do
    Serasa, busca na web. Desde 16/09/2026 QUEM CONFERE E A IA (o codigo so aponta onde o nome parece aparecer);
    sinal sem texto de vizinho nunca vale — nao ha nome para conferir.

Sem banco aqui alem de `fontes_de_nome`; o julgamento (`avaliar_enxuto.montar_leve`) monta o texto e marca o
rotulo da foto quando nada nela vale para a instalacao, e a checagem (`checagem_veredito`) tira essa foto das
provas.
"""
import re
import unicodedata

from checagem_veredito import MARCA_ALUGA_NA_RUA, MARCA_SEM_SINAL_NA_RUA
from desenho_seta import PONTA_REL

PROMPT = """Esta é uma foto de rua (Street View). Ignore a seta verde, o rótulo de distância e o quadro escuro no canto inferior direito (uma planta vista de cima): não fazem parte da cena.

Identifique CADA FACHADA de imóvel visível na cena — casa, sobrado, loja, prédio, galpão, ou o muro/portão da frente de um lote —, da esquerda para a direita. Para cada uma:
- a caixa que envolve a fachada que aparece, em coordenadas relativas de 0 a 1000 [x1, y1, x2, y2] (0,0 no canto superior esquerdo da foto). A caixa vai da divisa esquerda à divisa direita do lote daquela fachada (muro, parede ou cerca de divisa), inclusive a parte baixa ou recuada do imóvel; caixas de lotes vizinhos se encostam na divisa, sem se sobrepor. Não junte dois lotes numa caixa só; placa em cima de uma fachada é dela;
- uma descrição curta;
- os TEXTOS escritos NELA — placa, letreiro, faixa, banner, adesivo, anúncio pintado, toldo, telefone, placa de aluga/vende —, transcritos; no máximo 8 por fachada; texto longo, só o essencial em até 8 palavras; número da casa e nome de rua não;
- os sinais de atividade não residencial SEM texto nela: vitrine com mercadoria, porta de loja aberta, balcão, mesas de bar, oficina com carros ou peças em serviço, pátio com caminhões ou máquinas, material à venda, sucata, marcador de estabelecimento do Google. Portão de garagem, porta fechada de casa e carro na garagem NÃO são sinal;
- se ela mostra uso não residencial (sinal_comercial). Placa de aluga-se ou vende-se é do tipo aluga_vende e, sozinha, NÃO é uso não residencial;
- se está encoberta (árvore, poste, veículo ou muro escondendo boa parte dela).

Responda SOMENTE um JSON:
{"fachadas": [{"n": 1, "caixa": [x1, y1, x2, y2], "descricao": "<até 8 palavras>", "textos": [{"texto": "<o que está escrito>", "tipo": "letreiro|placa|faixa|pintura|adesivo|toldo|aluga_vende|outro"}], "sinais_sem_texto": ["<o que se vê>"], "sinal_comercial": true|false, "encoberta": true|false}],
 "resumo": "<até 20 palavras>"}"""

#: palavras que qualquer comercio tem: nao identificam o estabelecimento
GENERICAS = set("""
agropecuaria distribuidora distribuidor conveniencia padaria confeitaria mercado minimercado supermercado mercearia
armazem acougue loja lojas comercio comercial servico servicos oficina mecanica auto autopecas pecas center centro
lanches lancheria restaurante pizzaria bar bares cafe cafeteria salao beleza estetica cabeleireiro cabeleireira
barbearia farmacia drogaria material materiais construcao ferragem ferragens casa pet shop moda modas roupas
calcados tele entrega teleentrega delivery temos aberto aberta vende vendese aluga alugase aluguel telefone
whatsapp fone contato ltda eireli epp cia grupo express gaucho gaucha brasil sul nova novo santa santo sao bom boa
silva santos souza oliveira pereira costa rodrigues almeida lima ferreira alves carvalho gomes martins ribeiro
delicia delicias caseira caseiro sabor sabores arte artes top mais total prime master mega super mini
atendimento qualidade melhor melhores preco precos promocao oferta ofertas horario funcionamento segunda sexta
sabado domingo feriado cartao cartoes pix aceitamos venda vendas compra compras troca trocas conserto consertos
instalacao instalacoes manutencao reforma reformas pintura pinturas criacao impressao grafica produtos produto
facebook instagram tiktok youtube serralheria vidracaria marcenaria
""".split())
_FONE = re.compile(r"(?:\(?\d{2}\)?[\s.-]*)?9?\d{4}[\s.-]?\d{4}")


def _normal(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _fones(t):
    saida = set()
    for m in _FONE.findall(str(t or "")):
        d = re.sub(r"\D", "", m)
        if len(d) >= 8:
            saida.add(d[-8:])
    return saida


def _caixa(f):
    c = f.get("caixa") if isinstance(f, dict) else None
    if not isinstance(c, (list, tuple)) or len(c) != 4:
        return None
    try:
        c = [float(v) for v in c]
    except (TypeError, ValueError):
        return None
    if max(c) <= 1.0:
        c = [v * 1000 for v in c]
    x1, x2 = sorted((c[0], c[2]))
    y1, y2 = sorted((c[1], c[3]))
    return [max(0, x1), max(0, y1), min(1000, x2), min(1000, y2)]


def _na_planta(c):
    """A caixa que a IA pos na planta do canto inferior direito."""
    return c[0] >= 770 and c[1] >= 620


def fachadas(leitura):
    return [f for f in ((leitura or {}).get("fachadas") or []) if _caixa(f) and not _na_planta(_caixa(f))]


def escolher(leitura, mira_x, ponta_y=PONTA_REL):
    """A fachada da seta: a caixa que contem a ponta; sem nenhuma, a da coluna da ponta mais perto dela na
    vertical; entre varias, a menor. None se a ponta nao cai em fachada identificada."""
    fs = fachadas(leitura)
    if mira_x is None or not fs:
        return None
    x, y = float(mira_x) * 1000, float(ponta_y) * 1000

    def area(f):
        c = _caixa(f)
        return max(1.0, c[2] - c[0]) * max(1.0, c[3] - c[1])
    com_ponta = [f for f in fs if _caixa(f)[0] <= x <= _caixa(f)[2] and _caixa(f)[1] <= y <= _caixa(f)[3]]
    if com_ponta:
        return min(com_ponta, key=area)
    coluna = [f for f in fs if _caixa(f)[0] <= x <= _caixa(f)[2]]
    if coluna:
        return min(coluna, key=lambda f: (min(abs(_caixa(f)[1] - y), abs(_caixa(f)[3] - y)), area(f)))
    return None


#: a ponta a menos disto (em milesimos da largura) da borda da caixa, com outra fachada tambem perto, e DIVISA:
#: as caixas da IA erram 3 a 6% nas bordas (319537 e 2615312, 15/09/2026)
DIVISA = 70

# ESCOLHER A OU B COM CERTEZA (15/09/2026): com a opcao "divisa" a IA respondeu divisa nas 19 de 19, ate onde a linha
# cortava a casa azul em cheio (319537). Forcando A ou B, olhando o nivel do chao: 11 com certeza alta, 8 media.
PROMPT_DESEMPATE = """Recorte de uma foto de rua (Street View). A linha vertical VERMELHA marca uma direção; ignore a seta verde por cima dela.
Olhe onde a linha vermelha cruza a FRENTE DOS LOTES NO NÍVEL DO CHÃO — o muro, a grade, o portão ou a parede da frente —, e não o céu nem o telhado.
Os dois imóveis dos lados dela são:
A (à esquerda): {a}
B (à direita): {b}
Nesse ponto, a linha vermelha está na frente de qual imóvel? Escolha A ou B mesmo que seja perto da divisa, e diga a certeza: "alta" se a linha cai claramente dentro de um deles, "media" se cai perto da divisa mas do lado dele, "baixa" se não dá para ver (árvore, poste ou carro na frente, ou exatamente na divisa).
Responda SOMENTE um JSON: {{"imovel": "A" ou "B", "certeza": "alta" ou "media" ou "baixa", "por": "<até 12 palavras>"}}"""


def _borda(f, x):
    c = _caixa(f)
    return 0.0 if c[0] <= x <= c[2] else min(abs(c[0] - x), abs(c[2] - x))


def na_divisa(leitura, mira_x):
    """[esquerda, direita] quando a ponta cai na divisa entre duas fachadas (a escolhida tem a borda a menos de
    DIVISA da ponta e outra fachada tambem esta a menos de DIVISA), senao None."""
    if mira_x is None:
        return None
    x = float(mira_x) * 1000
    alvo = escolher(leitura, mira_x)
    fs = fachadas(leitura)
    if alvo is not None:
        c = _caixa(alvo)
        if min(x - c[0], c[2] - x) > DIVISA:
            return None
        outras = [f for f in fs if f is not alvo and _borda(f, x) <= DIVISA]
        if not outras:
            return None
        par = [alvo, min(outras, key=lambda f: _borda(f, x))]
    else:
        perto = sorted((f for f in fs if _borda(f, x) <= DIVISA), key=lambda f: _borda(f, x))[:2]
        if len(perto) < 2:
            return None
        par = perto
    return sorted(par, key=lambda f: _caixa(f)[0] + _caixa(f)[2])


def recorte_com_linha(jpeg, mira_x, meia_largura=0.2):
    """O recorte em volta da ponta com a linha vermelha vertical, em JPEG (para `PROMPT_DESEMPATE`)."""
    import cv2
    import numpy as np
    arr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = arr.shape[:2]
    x = int(float(mira_x) * w)
    x0, x1 = max(0, x - int(meia_largura * w)), min(w, x + int(meia_largura * w))
    rec = arr[:int(h * 0.8), x0:x1].copy()
    cv2.line(rec, (x - x0, 0), (x - x0, rec.shape[0] - 1), (0, 0, 255), max(3, w // 500))
    return cv2.imencode(".jpg", rec, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


def desempatar(leitura, mira_x, jpeg, perguntar):
    """Na divisa, a pergunta a parte com o recorte: devolve o dict gravado em `leitura["desempate"]`, ou None.
    `perguntar(prompt, jpeg) -> dict` e a chamada a IA (quem chama escolhe o modelo)."""
    par = na_divisa(leitura, mira_x)
    if not par:
        return None
    a, b = par
    r = perguntar(PROMPT_DESEMPATE.format(a=a.get("descricao") or "?", b=b.get("descricao") or "?"),
                  recorte_com_linha(jpeg, mira_x)) or {}
    esc = str(r.get("imovel") or "").strip().upper()
    certeza = str(r.get("certeza") or "").strip().lower()
    # certeza baixa (arvore, poste, carro na frente, ou em cima da divisa) fica indefinida
    n = None if certeza not in ("alta", "media", "média") else a.get("n") if esc == "A" else b.get("n") if esc == "B" else None
    return {"entre": [a.get("n"), b.get("n")], "imovel": esc, "certeza": certeza, "n": n, "por": r.get("por")}


def escolher_final(leitura, mira_x):
    """(alvo, divisa): a fachada da seta depois do desempate; na divisa sem desempate (ou "divisa"), alvo None e
    divisa com as duas fachadas."""
    par = na_divisa(leitura, mira_x)
    if not par:
        return escolher(leitura, mira_x), None
    d = (leitura or {}).get("desempate") or {}
    if d.get("n") is not None and d.get("entre") == [par[0].get("n"), par[1].get("n")]:
        return next(f for f in par if f.get("n") == d["n"]), None
    return None, par


def fontes_de_nome(cur, ligacao, ids):
    """([(fonte, texto)], palavras do endereco): onde procurar o nome visto na placa — nome e telefone dos
    registros, a ficha do Serasa, a busca na web pelo endereco."""
    pecas, cnpjs = [], set()
    cur.execute("""select lower(coalesce(p.fonte, '')), regexp_replace(coalesce(p.cnpj, ''), '\\D', '', 'g'),
                          (select string_agg(value, ' ') from jsonb_each_text(to_jsonb(p) - 'geom')
                            where key ~ '(nome|fantasia|razao|telefone|fone|site|instagram)')
                     from radar_comercial.pois p where p.id = any(%s)""", (list(ids or []),))
    for fonte, cnpj, texto in cur.fetchall():
        pecas.append(({"maps": "ficha do Google Maps", "receita": "Receita"}.get(fonte, fonte or "registro"), texto or ""))
        if len(cnpj) == 14:
            cnpjs.add(cnpj)
    if cnpjs:
        cur.execute("""select texto_ia from radar_comercial.ficha_cnpj_web
                        where cnpj = any(%s) and texto_ia is not null and not bloqueado""", (sorted(cnpjs),))
        pecas += [("Serasa", t) for (t,) in cur.fetchall()]
    cur.execute("""select texto from radar_comercial.busca_web
                    where ligacao = %s and tipo = 'endereco' and not bloqueado and resultados is not null
                      and texto is not null""", (str(ligacao),))
    pecas += [("busca na web", t) for (t,) in cur.fetchall()]
    return pecas, palavras_do_endereco(cur, ligacao)


def palavras_do_endereco(cur, ligacao):
    """Rua, bairro e cidade da ligacao: nao identificam o negocio (placa e post trazem o endereco)."""
    excluir = {"canoas", "rua", "avenida", "travessa", "estrada"}
    if str(ligacao).isdigit():
        cur.execute("""select concat_ws(' ', nom_logradouro, nom_bairro, cidade) from resources_root.cadastro_corsan
                        where num_ligacao = %s""", (int(ligacao),))
        r = cur.fetchone()
        excluir |= set(_normal(r[0] if r else "").split())
    return excluir


def casar(texto, pecas, excluir=()):
    """A fonte em que o nome (palavra distintiva de 4+ letras) ou o telefone da placa aparece, ou None."""
    fones = _fones(texto)
    for fonte, t in pecas:
        if fones and fones & _fones(t):
            return fonte
    # o nome inteiro, mesmo feito so de palavras genericas ("Agropecuaria Gaucho" na ficha do Serasa)
    frase = " ".join(w for w in _normal(texto).split() if not w.isdigit())
    # POR FRASE, NAO POR PALAVRA SOLTA (dono do produto, 16/09/2026): "De Deus para" casava com a busca na web pela
    # palavra "deus" e "FERRAGEM MULTI+" por "multi", e a placa do vizinho aprovava a casa da seta. Uma palavra so
    # vale se for longa e distintiva ("AngolaBrasil"); duas ou mais, a frase inteira.
    # NOME DE RUA NAO E NOME DE LOJA (16/09/2026): "R. Cidade de Santa Fe" casava com a busca na web pelo endereco
    if not [w for w in frase.split() if len(w) >= 4 and w not in excluir]:
        return None
    if " " in frase:
        casa = len(frase) >= 10
    else:
        casa = len(frase) >= 8 and frase not in GENERICAS and frase not in excluir
    if casa:
        for fonte, t in pecas:
            if (" %s " % frase) in (" %s " % _normal(t)):
                return fonte
    return None


def _textos(f):
    return [t for t in (f.get("textos") or []) if isinstance(t, dict) and str(t.get("texto") or "").strip()
            and t.get("tipo") != "marca_dagua" and "google" not in str(t.get("texto")).lower()
            and not re.fullmatch(r"\d{1,3}\s*m", str(t.get("texto")).strip())]


_ALUGA = re.compile(r"\b(aluga|alugo|alugase|aluga se|para alugar|aluguel|vende se|vendese|vendo|a venda|locacao)\b")


def _e_aluga(t):
    return t.get("tipo") == "aluga_vende" or bool(_ALUGA.search(_normal(t.get("texto"))))


def _so_contato(t):
    """Texto que e so telefone, site ou nome de imobiliaria: com placa de aluga/vende, e do anuncio."""
    if re.search(r"www|http|[.]com|[.]br|@", str(t.get("texto") or "").lower()):
        return True
    x = _normal(t.get("texto"))
    sem = re.sub(r"\b(www|com|br|net|imobiliaria|imoveis|corretor|creci|tel|fone|whatsapp)\b|\d+", " ", x)
    return not sem.strip() or "imobiliaria" in x or "creci" in x


def aluga_na_seta(leitura, mira_x):
    """Os textos de aluga/vende na fachada da seta (auditoria das 40, 310148)."""
    alvo, _divisa = escolher_final(leitura, mira_x)
    return [t["texto"] for t in _textos(alvo)] if alvo and any(_e_aluga(t) for t in _textos(alvo)) and \
        [t for t in _textos(alvo) if _e_aluga(t)] else []


def seta_tem_sinal(alvo):
    """A fachada da seta mostra uso: sinal comercial com texto que nao e so aluga/vende, ou sinal sem texto."""
    if not alvo:
        return False
    aluga = [t["texto"] for t in _textos(alvo) if _e_aluga(t)]
    ts_uso = [t for t in _textos(alvo) if not _e_aluga(t) and not (aluga and _so_contato(t))]
    ss = [str(s) for s in (alvo.get("sinais_sem_texto") or []) if str(s).strip()]
    return bool(alvo.get("sinal_comercial") and (ts_uso or ss))


def para_julgamento(leitura, mira_x, pecas, excluir):
    """(texto, vale): a leitura para o julgamento e se ALGUM sinal da foto vale para esta instalacao — o da
    fachada da seta, ou placa de vizinho com o nome em outra fonte."""
    alvo, divisa = escolher_final(leitura, mira_x)
    linhas = ["LEITURA DA FOTO DE RUA (feita antes, só com a imagem; qual fachada é a da seta foi decidido pela posição "
              "da ponta da seta, não pela IA):"]
    vale = False
    if divisa:
        linhas.append("fachada da seta: INDEFINIDA — a ponta cai na divisa entre \"%s\" e \"%s\"; placas das duas só valem "
                      "se você confirmar o nome ou o telefone em outra fonte" % (divisa[0].get("descricao") or "?", divisa[1].get("descricao") or "?"))
    elif alvo:
        c = _caixa(alvo)
        ts = ['"%s" (%s)' % (t["texto"], t.get("tipo") or "?") for t in _textos(alvo)]
        aluga = [t["texto"] for t in _textos(alvo) if _e_aluga(t)]
        ts_uso = [t for t in _textos(alvo) if not _e_aluga(t) and not (aluga and _so_contato(t))]
        ss = [str(s) for s in (alvo.get("sinais_sem_texto") or []) if str(s).strip()]
        estado = [e for e, ok in (("cortada pela borda da foto", c[0] <= 5 or c[2] >= 995),
                                  ("encoberta", bool(alvo.get("encoberta")))) if ok]
        linhas.append("fachada da seta: %s%s · textos: %s · sinais sem texto: %s · uso não residencial visível: %s"
                      % (alvo.get("descricao") or "?", (" (%s)" % ", ".join(estado)) if estado else "",
                         "; ".join(ts) or "nenhum", "; ".join(ss) or "nenhum",
                         "sim" if alvo.get("sinal_comercial") else "não"))
        vale = seta_tem_sinal(alvo)
        if aluga:
            linhas.append("PLACA DE ALUGA/VENDE NA FACHADA DA SETA: %s — sinal de imóvel vago ou à venda, NÃO de uso"
                          % "; ".join('"%s"' % x for x in aluga))
    else:
        linhas.append("fachada da seta: a ponta da seta não cai sobre nenhuma fachada identificada")
    # A PLACA DO VIZINHO E AVALIADA PELA IA (dono do produto, 16/09/2026): "tem que ser avaliado pela IA e nao estrito
    # assim". O codigo lista as placas dos vizinhos e aponta onde o nome parece aparecer; quem confere se a placa e deste
    # estabelecimento e a IA, com os registros, a ficha do Serasa e a busca na web. Sinal sem texto de vizinho nunca vale.
    placas, sinais = [], []
    for f in fachadas(leitura):
        if f is alvo:
            continue
        for t in _textos(f):
            if t.get("tipo") == "aluga_vende":
                continue
            fonte = casar(t["texto"], pecas, excluir)
            placas.append('"%s"%s' % (t["texto"], (" — o nome parece aparecer em: %s" % fonte) if fonte else ""))
        sinais += [str(s) for s in (f.get("sinais_sem_texto") or []) if str(s).strip()]
    if placas:
        linhas.append("placas de VIZINHOS (só são sinal desta instalação se VOCÊ confirmar o nome ou o telefone nos "
                      "registros, na ficha do Serasa ou na busca na web): " + "; ".join(placas))
        vale = True
    if sinais:
        linhas.append("sinais sem texto de VIZINHOS (NÃO são sinal desta instalação): " + "; ".join(sinais))
    linhas.append("sinal na foto de rua que pode valer para esta instalação: %s" % ("sim" if vale else "não"))
    return "\n".join(linhas), vale


def marcar_rotulo(rotulo, vale):
    return rotulo if vale else "%s · %s" % (rotulo, MARCA_SEM_SINAL_NA_RUA)
