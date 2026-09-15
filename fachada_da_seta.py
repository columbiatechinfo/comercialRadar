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
  - placa de fachada vizinha so vale se o NOME (palavra distintiva, nao "padaria" nem "distribuidora") ou o
    TELEFONE aparece em outra fonte: nome dos registros, ficha do Serasa, busca na web. Sinal sem texto de
    vizinho nunca vale — nao ha nome para conferir.

Sem banco aqui alem de `fontes_de_nome`; o julgamento (`avaliar_enxuto.montar_leve`) monta o texto e marca o
rotulo da foto quando nada nela vale para a instalacao, e a checagem (`checagem_veredito`) tira essa foto das
provas.
"""
import re
import unicodedata

from checagem_veredito import MARCA_SEM_SINAL_NA_RUA
from desenho_seta import PONTA_REL

PROMPT = """Esta é uma foto de rua (Street View). Ignore a seta verde, o rótulo de distância e o quadro escuro no canto inferior direito (uma planta vista de cima): não fazem parte da cena.

Identifique CADA FACHADA de imóvel visível na cena — casa, sobrado, loja, prédio, galpão, ou o muro/portão da frente de um lote —, da esquerda para a direita. Para cada uma:
- a caixa que envolve a fachada que aparece, em coordenadas relativas de 0 a 1000 [x1, y1, x2, y2] (0,0 no canto superior esquerdo da foto). Não junte duas fachadas numa caixa só; placa em cima de uma fachada é dela;
- uma descrição curta;
- os TEXTOS escritos NELA — placa, letreiro, faixa, banner, adesivo, anúncio pintado, toldo, telefone, placa de aluga/vende —, transcritos; no máximo 8 por fachada; texto longo, só o essencial em até 8 palavras; número da casa e nome de rua não;
- os sinais de atividade não residencial SEM texto nela: vitrine com mercadoria, porta de loja aberta, balcão, mesas de bar, oficina com carros ou peças em serviço, pátio com caminhões ou máquinas, material à venda, sucata, marcador de estabelecimento do Google. Portão de garagem, porta fechada de casa e carro na garagem NÃO são sinal;
- se ela mostra uso não residencial (sinal_comercial);
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
    excluir = set()
    if str(ligacao).isdigit():
        cur.execute("""select concat_ws(' ', nom_logradouro, nom_bairro, cidade) from resources_root.cadastro_corsan
                        where num_ligacao = %s""", (int(ligacao),))
        r = cur.fetchone()
        excluir = set(_normal(r[0] if r else "").split()) | {"canoas", "rua", "avenida", "travessa", "estrada"}
    return pecas, excluir


def casar(texto, pecas, excluir=()):
    """A fonte em que o nome (palavra distintiva de 4+ letras) ou o telefone da placa aparece, ou None."""
    fones = _fones(texto)
    for fonte, t in pecas:
        if fones and fones & _fones(t):
            return fonte
    # o nome inteiro, mesmo feito so de palavras genericas ("Agropecuaria Gaucho" na ficha do Serasa)
    frase = " ".join(w for w in _normal(texto).split() if not w.isdigit())
    if len(frase) >= 10 and " " in frase:
        for fonte, t in pecas:
            if (" %s " % frase) in (" %s " % _normal(t)):
                return fonte
    distintivas = [w for w in _normal(texto).split()
                   if len(w) >= 4 and not w.isdigit() and w not in GENERICAS and w not in excluir]
    if not distintivas:
        return None
    for fonte, t in pecas:
        palavras = set(_normal(t).split())
        if any(w in palavras for w in distintivas):
            return fonte
    return None


def _textos(f):
    return [t for t in (f.get("textos") or []) if isinstance(t, dict) and str(t.get("texto") or "").strip()
            and t.get("tipo") != "marca_dagua" and "google" not in str(t.get("texto")).lower()
            and not re.fullmatch(r"\d{1,3}\s*m", str(t.get("texto")).strip())]


def para_julgamento(leitura, mira_x, pecas, excluir):
    """(texto, vale): a leitura para o julgamento e se ALGUM sinal da foto vale para esta instalacao — o da
    fachada da seta, ou placa de vizinho com o nome em outra fonte."""
    alvo = escolher(leitura, mira_x)
    linhas = ["LEITURA DA FOTO DE RUA (feita antes, só com a imagem; qual fachada é a da seta foi decidido pela posição "
              "da ponta da seta, não pela IA):"]
    vale = False
    if alvo:
        c = _caixa(alvo)
        ts = ['"%s" (%s)' % (t["texto"], t.get("tipo") or "?") for t in _textos(alvo)]
        ss = [str(s) for s in (alvo.get("sinais_sem_texto") or []) if str(s).strip()]
        estado = [e for e, ok in (("cortada pela borda da foto", c[0] <= 5 or c[2] >= 995),
                                  ("encoberta", bool(alvo.get("encoberta")))) if ok]
        linhas.append("fachada da seta: %s%s · textos: %s · sinais sem texto: %s · uso não residencial visível: %s"
                      % (alvo.get("descricao") or "?", (" (%s)" % ", ".join(estado)) if estado else "",
                         "; ".join(ts) or "nenhum", "; ".join(ss) or "nenhum",
                         "sim" if alvo.get("sinal_comercial") else "não"))
        vale = bool(alvo.get("sinal_comercial") and (ts or ss))
    else:
        linhas.append("fachada da seta: a ponta da seta não cai sobre nenhuma fachada identificada")
    com_nome, sem_nome = [], []
    for f in fachadas(leitura):
        if f is alvo:
            continue
        for t in _textos(f):
            if t.get("tipo") == "aluga_vende":
                continue
            fonte = casar(t["texto"], pecas, excluir)
            (com_nome if fonte else sem_nome).append('"%s"%s' % (t["texto"], (" — o nome aparece em: %s" % fonte) if fonte else ""))
        sem_nome += [str(s) for s in (f.get("sinais_sem_texto") or []) if str(s).strip()]
    if com_nome:
        linhas.append("placas de VIZINHOS com o nome em outra fonte (valem como sinal desta instalação): " + "; ".join(com_nome))
        vale = True
    if sem_nome:
        linhas.append("placas e sinais de VIZINHOS sem o nome em nenhuma outra fonte (NÃO são sinal desta instalação): "
                      + "; ".join(sem_nome))
    linhas.append("sinal na foto de rua que vale para esta instalação: %s" % ("sim" if vale else "não"))
    return "\n".join(linhas), vale


def marcar_rotulo(rotulo, vale):
    return rotulo if vale else "%s · %s" % (rotulo, MARCA_SEM_SINAL_NA_RUA)
