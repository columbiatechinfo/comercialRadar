# -*- coding: utf-8 -*-
"""capturar_evidencia.py — as quatro visadas de rua que a IA vai julgar.

O CONJUNTO, E POR QUE ELE MUDOU

    sv_frente   o panorama ENCARANDO a coordenada
    sv_lado_a   a mesma câmera, 90° à direita
    sv_fundo    a mesma câmera, 180°
    sv_lado_b   a mesma câmera, 270°

QUATRO VISADAS, E NÃO UMA — decisão do dono do produto em 04/09/2026, depois de
ver o resultado de três imagens. O objetivo deixou de ser "descreva a fachada
sob a mira" e passou a ser ACHAR O ESTABELECIMENTO, apareça ele em qual visada
aparecer. Um comércio de bairro fica com frequência na esquina, no fundo do
lote ou na lateral, e a visada única fechava a pergunta antes de olhar.

O SATÉLITE SAIU. Ele mostrava telhado e mais nada: nem letreiro, nem vitrine,
nem porta. Trazia o custo de uma imagem por POI e não decidia nenhum veredito.

O MARCADOR NÃO PRECISA DE PROJEÇÃO, e essa é a parte que quase virou trabalho
inútil. O `heading` que se pede ao Maps é o ângulo CÂMERA→POI — então o alvo
fica no centro horizontal da imagem por construção. Desenhar o marcador no
centro é exato; calcular onde ele cairia seria refazer a conta que já foi feita
para escolher o heading.

OS METADADOS VÊM ANTES DO NAVEGADOR, e isso é herança de um defeito caro. Pedir
o panorama pela coordenada do POI falha em 25 de 30 casos — a coordenada está
sobre a loja, e o Maps quer o panorama praticamente em cima do ponto. O
endpoint de metadados diz de graça se existe foto, QUAL é (`pano_id`) e ONDE a
câmera está; com isso o mesmo POI abre em 10 de 10.

    python capturar_evidencia.py --area area_atual --limite 20
    python capturar_evidencia.py --area area_atual --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io as _io
import math
import os
import time

import area_utils
import base_comum as bc
import streetview_geo as sv

LARG, ALT = 1280, 900

#: QUANTOS PIXELS POR PONTO DE TELA. Era 1 — o padrão — e por isso a visada
#: gravada tinha 934x567 depois do corte da interface.
#:
#: NESSA RESOLUÇÃO A IA NÃO LÊ LETREIRO. Apontado pelo dono do produto em
#: 10/09/2026: "não está vendo banners de empresas por conta da qualidade baixa
#: das imagens enviadas". Ele está certo, e o número explica: a fachada de um
#: comércio ocupa talvez 200 pixels de largura numa imagem de 934, e o nome
#: escrito nela, uns 60. Não há o que ler ali.
#:
#: `device_scale_factor` é a alavanca CERTA, e não aumentar `LARG`. Um viewport
#: maior mostra MAIS RUA no mesmo espaço — cada imóvel fica do mesmo tamanho
#: relativo e nada melhora. A densidade mantém o enquadramento e dobra o pixel:
#: a mesma fachada passa a ter 400 pixels de largura, e o letreiro 120.
#:
#: O PREÇO É REAL e vai dito: quatro vezes mais pixel significa arquivo maior,
#: mais banda para o modelo e mais tempo por chamada. A alternativa é continuar
#: gastando a chamada inteira numa imagem que não responde à pergunta.
DENSIDADE = 2

# O SATÉLITE TEM JANELA PRÓPRIA, e menor de propósito.
#
# A 1280×900 no zoom 21 a vista cobre ~120 m — o quarteirão inteiro, com o
# imóvel virando um detalhe e o marcador virando um ponto. O que a IA precisa
# ver é o TELHADO do ponto e os vizinhos imediatos. Uma janela de 720×720 no
# mesmo zoom cobre ~52 m: a construção enquadrada, com contexto suficiente para
# dizer se ela é maior ou menor que as ao lado.
#
# Fechar mais o zoom não resolveria: acima de 21 o Google reamostra o mesmo
# tile, e a imagem fica maior sem ficar mais nítida.
ZOOM_SAT = 21
LARG_SAT, ALT_SAT = 720, 720
CHAVE = os.environ.get("MAPS_JS_KEY", "").strip()

ARGS_NAV = ["--disable-http2", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"]

# AS QUATRO VISADAS, e o giro de cada uma a partir do rumo câmera→coordenada.
# A ordem importa: a frente vem primeiro porque é onde o alvo está por
# construção, e as outras três dão a volta no sentido horário.
VISADAS = (
    ("sv_frente", 0),
    ("sv_lado_a", 90),
    ("sv_fundo", 180),
    ("sv_lado_b", 270),
)

# O campo de visão das três visadas de entorno. 100° é o mais aberto que o Maps
# entrega sem distorcer as bordas a ponto de o letreiro deixar de ser legível.
FOV_ENTORNO = 100

# A vista de satélite com o marcador. `mapTypeId:'satellite'` e não 'hybrid':
# rótulo de rua por cima do telhado atrapalha justamente o que se quer ver.
SAT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>*{margin:0;padding:0}html,body,#map{width:%(l)dpx;height:%(a)dpx}</style>
</head><body><div id="map"></div><script>
function initMap(){
  const p = {lat:%(lat)s, lng:%(lng)s};
  const map = new google.maps.Map(document.getElementById('map'), {
    center:p, zoom:%(zoom)d, mapTypeId:'satellite', tilt:0,
    disableDefaultUI:true, clickableIcons:false});
  new google.maps.Marker({position:p, map:map,
    icon:{path:google.maps.SymbolPath.CIRCLE, scale:16,
          fillColor:'#22c55e', fillOpacity:0.18,
          strokeColor:'#22c55e', strokeWeight:4}});
  new google.maps.Marker({position:p, map:map, clickable:false,
    icon:{path:google.maps.SymbolPath.CIRCLE, scale:3.5,
          fillColor:'#052e16', fillOpacity:1,
          strokeColor:'#eaffea', strokeWeight:2}});
  google.maps.event.addListenerOnce(map,'idle',()=>{window.__pronto=true;});
}
</script>
<script src="https://maps.googleapis.com/maps/api/js?key=%(chave)s&callback=initMap&loading=async" async defer></script>
</body></html>"""


def _log(m):
    print(m, flush=True)


# QUANTO CORTAR DA BORDA DO PRINT DO STREET VIEW.
#
# O Maps de consumidor desenha por cima do panorama: o cartão do local no canto
# superior esquerdo, os botões de compartilhar e fechar no direito, o minimapa
# no inferior esquerdo, a bússola e o zoom na borda direita. Nada disso é a
# cena, e tudo isso a IA lê como se fosse.
#
# O corte é por PROPORÇÃO e não por seletor de CSS: o Maps troca as classes a
# cada implantação, e um seletor que some faz a interface voltar sem avisar. As
# margens abaixo foram medidas nos prints de 04/09/2026 e cobrem os quatro
# cantos com folga.
#
# O ALVO CONTINUA CENTRADO depois do corte — o `heading` mira nele, e cortar
# margens iguais não move o centro.
# Medido duas vezes: com topo=0.14 sobrava uma tarja preta do cartão do
# Maps no canto superior esquerdo. 0.20 a elimina, e o que se perde é céu.
CORTE = {"esq": 0.15, "dir": 0.12, "topo": 0.20, "baixo": 0.17}


#: QUANDO O GOOGLE FOTOGRAFOU AQUELA RUA.
#:
#: `capturado_em` responde "quando NOSSA rodada tirou o print", que nao e a
#: pergunta que decide nada. A que decide e a idade da imagem: um terco das
#: fachadas deste projeto e de 2024, e ha panorama de 2022. Uma casa
#: fotografada ha dois anos pode ter virado loja depois — e a IA estava lendo
#: aquilo como se fosse hoje, sem ter como desconfiar.
#:
#: A FONTE E A API DE METADADOS DO STREET VIEW, e ela NAO E COBRADA pelo
#: Google: metadata request e de graca, ao contrario da imagem. Uma chamada por
#: POI, com o `pano_id` que a captura ja tem na mao, devolve `date` no formato
#: AAAA-MM.
_CACHE_DATA_PANO = {}


def _data_do_pano(pano_id: str) -> str | None:
    """AAAA-MM do panorama, ou None. Guarda em memoria: panorama se repete."""
    if not pano_id:
        return None
    if pano_id in _CACHE_DATA_PANO:
        return _CACHE_DATA_PANO[pano_id]
    import json as _json
    import urllib.parse
    import urllib.request
    # A CHAVE TEM TRES NOMES POSSIVEIS neste projeto, e o container usa o
    # terceiro: `MAPS_JS_KEY`. Procurar so os dois primeiros fazia a funcao
    # devolver None em silencio, que e o pior jeito de falhar — a data
    # simplesmente nao apareceria e nada diria por que.
    chave = (os.environ.get("MAPS_API_KEY") or os.environ.get("MAPS_KEY")
             or os.environ.get("MAPS_JS_KEY") or "")
    if not chave:
        return None
    url = ("https://maps.googleapis.com/maps/api/streetview/metadata?"
           + urllib.parse.urlencode({"pano": pano_id, "key": chave}))
    data = None
    try:
        md = _json.loads(urllib.request.urlopen(url, timeout=12).read())
        if md.get("status") == "OK":
            data = md.get("date")
    except Exception:                                          # noqa: BLE001
        # DATA E ENFEITE UTIL, NAO CONDICAO. Falhar aqui nao pode custar a
        # captura: a foto vale sem a data, e a data se preenche depois.
        data = None
    _CACHE_DATA_PANO[pano_id] = data
    return data


def _para_webp(png: bytes, qualidade: int = 93) -> bytes:
    """PNG -> WebP, SEM MEXER EM PIXEL.

    `_cortar_interface` e `_marcar_centro` trabalham com `cv2.imencode(".png")`,
    que grava sem perda e sem compressao util para fotografia. MEDIDO em
    07/09/2026: as visadas gravadas assim ocupam de 837 a 998 KB cada; as mesmas
    imagens, nos mesmos pixels, ficam perto de 85 KB em WebP.

    O preco disso era cobrado duas vezes. Em disco: `poi_evidencia` virou a
    maior tabela do banco, 53 GB de 104 GB. E em tempo de modelo: a percepcao
    recebe quatro visadas mais duas fotos do Google, e 3,9 MB de imagem por
    chamada arrastam a fila inteira.

    A resolucao NAO muda — isso foi medido em 07/09/2026 e reprovado: reduzir
    largura faz o modelo ler letreiro errado, e num caso trocou o comercio por
    outro que nao existia. O que muda e so o formato.
    """
    try:
        from PIL import Image
        im = Image.open(_io.BytesIO(png)).convert("RGB")
        b = _io.BytesIO()
        im.save(b, "WEBP", quality=qualidade, method=4)
        d = b.getvalue()
        return d if len(d) < len(png) else png
    except Exception:                                          # noqa: BLE001
        return png                 # formato e economia, nao condicao


def _cortar_interface(png: bytes) -> bytes:
    """Tira as bordas onde o Maps desenha a própria interface."""
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return png
        h, w = arr.shape[:2]
        x1, x2 = int(w * CORTE["esq"]), int(w * (1 - CORTE["dir"]))
        y1, y2 = int(h * CORTE["topo"]), int(h * (1 - CORTE["baixo"]))
        rec = arr[y1:y2, x1:x2]
        if rec.size == 0:
            return png
        ok, buf = cv2.imencode(".png", rec)
        return buf.tobytes() if ok else png
    except Exception:                                          # noqa: BLE001
        return png


# ── o marcador desenhado no centro ─────────────────────────────────────────
def _marcar_centro(png: bytes, rotulo: str) -> bytes:
    """Desenha a mira no centro da imagem — onde o alvo está por construção.

    A mira é ABERTA (um losango de cantos, não um círculo cheio): marcador
    opaco no centro esconderia justamente a fachada que a IA precisa ler.
    """
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return png
        h, w = arr.shape[:2]
        cx, cy = w // 2, int(h * 0.52)
        r = int(min(w, h) * 0.13)
        for cor, esp in (((0, 0, 0), 7), ((0, 255, 90), 3)):
            for a0, a1 in ((225, 315), (45, 135), (135, 225), (315, 405)):
                cv2.ellipse(arr, (cx, cy), (r, r), 0, a0, a0 + 40, cor, esp)
            cv2.line(arr, (cx, cy - 14), (cx, cy + 14), cor, esp - 1)
            cv2.line(arr, (cx - 14, cy), (cx + 14, cy), cor, esp - 1)
        if rotulo:
            (tw, th), _ = cv2.getTextSize(rotulo, cv2.FONT_HERSHEY_SIMPLEX, .6, 2)
            x, y = cx - tw // 2, cy + r + 30
            cv2.rectangle(arr, (x - 8, y - th - 8), (x + tw + 8, y + 8),
                          (255, 255, 255), -1)
            cv2.rectangle(arr, (x - 8, y - th - 8), (x + tw + 8, y + 8),
                          (20, 20, 20), 2)
            cv2.putText(arr, rotulo, (x, y), cv2.FONT_HERSHEY_SIMPLEX, .6,
                        (20, 20, 20), 2, cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", arr)
        return buf.tobytes() if ok else png
    except Exception:                                          # noqa: BLE001
        return png                 # marcador é enfeite; imagem sem ele serve


# ── quem entra ─────────────────────────────────────────────────────────────
SQL_ALVO = """
    select distinct p.id, st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
           coalesce(p.nome,''), coalesce(p.fonte,''), coalesce(p.categoria,'')
      from radar_comercial.pois p
      left join radar_comercial.categoria_catalogo cc
            on cc.fonte = p.fonte and cc.valor = btrim(p.categoria)
     where p.pt_geo is not null
       -- O CATALOGO PODE SER DESLIGADO, e a decisao e a mesma do julgamento.
       --
       -- Ele exclui CNAE de quem trabalha de casa ou na rua — transporte de
       -- carga, servicos domesticos, alvenaria — e a exclusao e pensada. Mas
       -- ela some sem log: o POI fora do catalogo nunca e fotografado e nunca
       -- e julgado, e ninguem ve por que.
       --
       -- Decisao do dono do produto em 10/09/2026: julgar TODOS os
       -- candidatos. Se o julgamento passa a ver esses POIs, a captura tem de
       -- ver tambem — senao a IA os recebe sem foto nenhuma, que e o pior dos
       -- dois mundos.
       and (%(sem_catalogo)s::int = 1 or coalesce(cc.avaliar, false))
       -- TER LIGACAO RESIDENCIAL ATIVA, OU — se a regra estiver destravada —
       -- NAO TER LIGACAO NENHUMA.
       --
       -- A primeira metade e a regra de sempre, e o motivo dela e bom: sem
       -- saber qual imovel a ligacao serve, a foto nao vira cobranca. Mas ela
       -- deixa 20.905 pontos invisiveis duas vezes — sem vinculo e, por causa
       -- disso, sem foto —, e entre eles estao 2.100 do Maps, a fonte de
       -- menor reprovacao medida.
       --
       -- A segunda metade abre por `radar_comercial.regra`, e nao por
       -- constante no codigo: destravar custa 11 h de proxy e a fila da IA
       -- depois, e essa e decisao de quem responde pelo produto, tomada na
       -- tela e nao num deploy. Ver a migracao 0080.
       --
       -- OS DOIS JOINS VIRARAM `exists` de proposito. Como `or`, eles
       -- multiplicariam as linhas do POI por cada ligacao candidata antes de o
       -- `distinct` limpar — e o produto de 300 mil POIs por 65 mil vinculos
       -- ja custou 4 minutos parados na fila da IA, em 04/09/2026. Em
       -- `exists` cada condicao para no primeiro acerto e nada e duplicado.
       -- SO QUEM TEVE CRUZAMENTO VALIDO, e so onde o cliente disse SIM.
       --
       -- Duas condicoes que faltavam, e cada uma tirava trabalho inutil:
       --
       --   `lp.descartado_em is null` — o vinculo tem de estar VIVO. Um POI
       --   cujo unico vinculo caiu na regra de endereco nao pertence a
       --   ligacao nenhuma; fotografa-lo e gastar proxy para alimentar um
       --   julgamento que nao vai acontecer.
       --
       --   `l.apta_cruzamento` — a coluna gerada de `qualificacao`, que cobre
       --   SIM e SIM_COM_ANALISE_HUMANA. Decisao do dono do produto: "apenas
       --   quem tem sim ou sim com verificacao humana devem rodar a coleta".
       --   Vazio nao enriquece, e enriquecimento comeca na foto.
       --
       -- Medido em 10/09/2026: sem elas a fila da captura tinha ~29.700 POIs;
       -- com elas, 15.884. Metade do proxy ia para POI que ninguem julgaria.
       and (exists (select 1
                      from radar_comercial.ligacao_poi lp
                      join resources_root.cadastro_corsan l
                           on l.num_ligacao::text = lp.ligacao
                     where lp.poi_id = p.id
                       and lp.descartado_em is null
                       and l.apta_cruzamento
                       and upper(l.categoria) = 'RESIDENCIAL'
                       and upper(coalesce(l.sit_ligacao,'')) = 'ATIVA')
            or (%(sem_ligacao)s::int = 1
                and not exists (select 1
                                  from radar_comercial.ligacao_poi lp0
                                 where lp0.poi_id = p.id)))
       -- SO QUANDO NENHUMA FONTE TROUXE IMAGEM PARA A LIGACAO.
       --
       -- Regra do dono do produto em 10/09/2026: "se a mesma ligacao tem 3
       -- fontes de POI e 1 deles ja tem as imagens de street view e alguma
       -- outra, essa instalacao ja pode ir pra avaliacao".
       --
       -- A LISTA E MONTADA UMA VEZ, numa temporaria, e NAO por POI.
       --
       -- A primeira versao disto perguntava, para cada POI, "alguma ligacao
       -- deste POI tem alguma outra fonte com imagem?" — uma subconsulta
       -- aninhada sobre `ligacao_poi` dentro do laco dos 300 mil POIs. Ela
       -- rodou 33 MINUTOS sem terminar, e eu ainda subi quatro copias dela
       -- antes de ir olhar `pg_stat_activity`. O sintoma parecia "a captura
       -- travou"; a causa era a consulta que monta a fila.
       --
       -- `radar_comercial.tmp_lig_com_imagem` e preenchida antes, num
       -- comando so, e aqui vira um `not exists` contra chave primaria.

       -- QUEM JA TEM FOTO NAO VOLTA. Quem FALHOU volta, e essa distincao
       -- custou 17 POIs de 143 na primeira corrida do bloco de Canoas.
       --
       -- A condicao era `not exists (... tipo = 'sv_frente')`, sem olhar se
       -- havia byte na linha. So que a captura grava linha TAMBEM quando
       -- falha, para registrar o motivo — entao um tile lento carimbava o POI
       -- como feito, e ele nunca mais era tentado. Falha transitoria virando
       -- permanente, em silencio.
       --
       -- Duas falhas sao DEFINITIVAS e continuam fora: o Google confirmando
       -- que nao ha panorama no ponto, e a chave ausente. Repetir essas duas e
       -- gastar navegador para receber a mesma resposta.
       and not exists (select 1 from radar_comercial.poi_evidencia e
                        where e.poi_id = p.id and e.tipo = 'sv_frente'
                          -- `storage_path` CONTA COMO FOTO TIRADA.
                          --
                          -- A adequacao de 07/09/2026 gravou 65.316 imagens no
                          -- Storage, com `dados` NULO. Sem esta linha a fila
                          -- leria "sem bytes" e mandaria refotografar os 21.787
                          -- POIs que acabaram de ser adequados — abrindo o
                          -- Street View de graca para obter o que ja esta la.
                          and (e.dados is not null
                               or e.storage_path is not null
                          -- OS POR-CENTO SAO LITERAIS DO `LIKE`, E VAO
                          -- DOBRADOS. Enquanto esta consulta rodava sem
                          -- parametro nenhum — `cur.execute(SQL_ALVO)` —, o
                          -- psycopg nao olhava a string e um por-cento solto
                          -- passava batido. Agora ela recebe `sem_ligacao`, o
                          -- driver interpreta a string inteira, e o solitario
                          -- vira "argument formats can't be mixed": um erro
                          -- que fala de formato de argumento e nao menciona
                          -- LIKE nenhum, entao quem o ler vai procurar no
                          -- lugar errado.
                               or e.motivo_falha like 'o Google confirma%%'
                               or e.motivo_falha like 'MAPS_JS_KEY%%'))
-- A CONDICAO QUE OLHAVA `streetview_imgs` SAIU em 07/09/2026.
       --
       -- Ela existia para nao refotografar quem ja tinha a captura antiga.
       -- Depois da adequacao nao ha mais ninguem so naquela tabela: as 65.316
       -- imagens viraram linhas de `poi_evidencia`, e a condicao acima ja as
       -- enxerga pelo `storage_path`. Medido: zero POIs dependendo so da
       -- tabela antiga.
     order by p.id
"""


SQL_POR_ID = """
    select distinct p.id, st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
           coalesce(p.nome,''), coalesce(p.fonte,''), coalesce(p.categoria,'')
      from radar_comercial.pois p
     where p.pt_geo is not null and p.id = any(%s)
     order by p.id
"""


#: O QUE VALE SE A TABELA DE REGRAS NAO RESPONDER.
#:
#: Fechado. Uma trava que abre sozinha porque o banco piscou nao e trava: o
#: custo de nao capturar e adiar; o de capturar 19 mil pontos sem ninguem ter
#: pedido sao 11 h de proxy pago do usuario.
CAPTURAR_SEM_LIGACAO_PADRAO = False


def regra_ativa(con, chave: str) -> bool:
    """A regra `chave` esta destravada? Ver a migracao 0080."""
    try:
        with con.cursor() as k:
            k.execute("select ativo from radar_comercial.regra "
                      "where chave = %s", (chave,))
            r = k.fetchone()
            if r is not None:
                return bool(r[0])
    except Exception as e:                                     # noqa: BLE001
        _log("   nao consegui ler a regra %s (%s) — valendo o padrao"
             % (chave, str(e)[:60]))
    return CAPTURAR_SEM_LIGACAO_PADRAO


def alvos(con, poligono, limite, pois=None, sem_catalogo=False,
          fatia=""):
    """A fila. Com `--poi` a lista é EXATAMENTE a pedida, sem filtro nenhum.

    Recapturar uma amostra escolhida a dedo é o caso de todo teste de método:
    passar pela fila normal excluiria justamente quem já tem evidência, que é
    quem se quer refazer.
    """
    cur = con.cursor()
    # O RAMO `--poi` NAO MONTA OS INDICES, e por isso eles nascem vazios.
    #
    # `com_imagem` e `ligs_do_poi` sao construidos so no ramo da fila normal.
    # Com `--poi` o laco de filtro os usava mesmo assim e morria com
    # `UnboundLocalError` — recapturar um POI escolhido a dedo e justamente o
    # caso em que nao se quer filtro nenhum: quem pediu, pediu.
    com_imagem, ligs_do_poi = set(), {}
    if pois:
        cur.execute(SQL_POR_ID, (list(pois),))
    else:
        sem = regra_ativa(con, "capturar_sem_ligacao")
        if sem:
            _log("   regra `capturar_sem_ligacao` DESTRAVADA: entram também "
                 "os POIs sem ligação vinculada")
        # A LISTA VAI PARA A MEMORIA, e nao para uma tabela.
        #
        # A versao anterior materializava as ligacoes com imagem numa tabela
        # `unlogged` compartilhada. Com DUAS MAQUINAS na mesma fila isso vira
        # estado mutavel compartilhado: o notebook chamava `truncate` enquanto
        # o i9 ainda lia dali, ficava preso num `Lock: relation` por minutos —
        # e, se o truncate tivesse passado, teria apagado a lista que o outro
        # estava usando no meio da consulta.
        #
        # Sao 24 mil ligacoes: um `set` em Python custa poucos MB e nao tem
        # dono, nao tem lock e nao precisa de GRANT. Mesma licao de
        # `nao-usar-sql-caro-tem-py`: trazer os dois lados e cruzar aqui.
        _log("   lendo as ligações que já têm imagem...")
        cur.execute("""
            select distinct lp.ligacao
              from radar_comercial.ligacao_poi lp
              join radar_comercial.pois p on p.id = lp.poi_id
             where lp.descartado_em is null and p.fundido_em is null
               and (exists (select 1 from radar_comercial.poi_evidencia e
                             where e.poi_id = p.id and e.tipo like 'sv_%'
                               and (e.dados is not null
                                    or e.storage_path is not null))
                    or exists (select 1 from radar_comercial.images_urls i
                                where i.poi_id = p.id
                                  and i.url like '%gps-cs-s%'
                                  and (i.dados is not null
                                       or i.storage_path is not null)))""")
        com_imagem = {r[0] for r in cur.fetchall()}
        _log("   %d ligação(ões) já têm imagem de alguma fonte"
             % len(com_imagem))

        # E de quais ligacoes cada POI participa, para cruzar sem ir ao banco
        # de novo dentro do laco.
        cur.execute("""select poi_id, ligacao
                         from radar_comercial.ligacao_poi
                        where descartado_em is null""")
        ligs_do_poi = {}
        for (pid_, lig_) in cur:
            ligs_do_poi.setdefault(pid_, []).append(lig_)

        cur.execute(SQL_ALVO, {"sem_ligacao": 1 if sem else 0,
                               "sem_catalogo": 1 if sem_catalogo else 0})
    fora = 0
    fora_com_imagem = 0
    saida = []
    # `--fatia 0,1/3` — MAIS DE UMA FATIA POR PROCESSO.
    #
    # A divisao precisa acompanhar a CAPACIDADE, e nao o numero de maquinas: o
    # i9 roda 20 trabalhadores e o notebook 10, entao meio a meio deixaria o i9
    # ocioso na segunda metade do tempo. Com tercos, o i9 leva dois e o
    # notebook um.
    #
    # E precisa ser UM PROCESSO POR MAQUINA, e nao dois no i9: cada processo
    # segura ate `CONEXOES` sessoes do pooler, que tem 20 NO TOTAL para a
    # pilha inteira. Tres processos de captura sozinhos consumiam 18 e o
    # julgamento nao cabia mais.
    n_fatia = set()
    m_fatia = None
    if fatia:
        quais, m = str(fatia).split("/")
        m_fatia = int(m)
        n_fatia = {int(x) for x in quais.split(",") if x.strip() != ""}
    for pid, la, lo, nome, fonte, cat in cur.fetchall():
        if m_fatia and (pid % m_fatia) not in n_fatia:
            continue
        # ALGUMA LIGACAO DESTE POI JA TEM IMAGEM DE OUTRA FONTE? Entao ele nao
        # precisa ser fotografado: a instalacao ja tem o que a IA ver.
        if any(l in com_imagem for l in ligs_do_poi.get(pid, ())):
            fora_com_imagem += 1
            continue
        if poligono and not area_utils.ponto_no_poligono(la, lo, poligono):
            fora += 1
            continue
        saida.append({"id": pid, "lat": la, "lng": lo, "nome": nome,
                      "fonte": fonte, "categoria": cat})
        if limite and len(saida) >= limite:
            break
    return saida, fora


#: QUANTAS FALHAS SEGUIDAS ANTES DE PARAR. Sem este freio o laço varre a
#: fila inteira falhando em milissegundos por item e termina "sem erro", com a
#: fila zerada e nenhuma foto gravada — o pior desfecho possível, porque parece
#: sucesso.
FALHAS_SEGUIDAS_LIMITE = 25


class Poco:
    """Um punhado de conexões emprestadas por gravação, e não por trabalhador.

    O DEFEITO QUE ISTO CORRIGE, medido em 04/09/2026: cada trabalhador abria a
    SUA conexão e a segurava a rodada inteira. Com 30 trabalhadores o Supavisor
    respondeu `(EMAXCONNSESSION) max clients reached in session mode - pool_size:
    20` e a captura morreu antes da primeira foto. Não foi a máquina que
    sufocou — havia 108 GB livres e 32 núcleos ociosos; foram as 20 conexões do
    pooler, que o número de trabalhadores consumia um a um.

    A conta que estava errada era a de amarrar as duas coisas. Um trabalhador
    passa ~11 s por POI, e usa o banco por poucos milissegundos em cada uma das
    quatro gravações. Trinta trabalhadores geram algo como onze escritas por
    segundo — que meia dúzia de conexões atende com folga.

    Por isso o empréstimo é POR GRAVAÇÃO e não por trabalhador: quem está
    esperando o Google carregar um panorama não precisa segurar uma conexão de
    banco enquanto isso.
    """

    def __init__(self, n: int):
        import queue
        self.fila = queue.Queue()
        self.n = max(1, n)
        for _ in range(self.n):
            self.fila.put(bc.conectar())

    @contextlib.contextmanager
    def pegar(self):
        con = self.fila.get()
        # CONEXÃO MORTA SE TROCA, e não se entrega assim mesmo. Em 07/09/2026 o
        # banco derrubou as sessões dos dois processos de uma vez; o poço seguiu
        # entregando os mesmos objetos mortos e cada POI seguinte estourava
        # `connection already closed` em milissegundos.
        if getattr(con, "closed", 0):
            con = bc.conectar()
        try:
            yield con
        except Exception:
            # CONEXÃO QUE VIU EXCEÇÃO PODE ESTAR EM TRANSAÇÃO ABORTADA, e
            # devolvê-la assim contamina o próximo que a pegar: todo comando
            # seguinte falha com "current transaction is aborted".
            try:
                con.rollback()
            except Exception:                                  # noqa: BLE001
                # Não deu nem para desfazer: a conexão já se foi. Devolver este
                # objeto ao poço seria devolver o defeito para o próximo.
                try:
                    con.close()
                except Exception:                              # noqa: BLE001
                    pass
                con = None
            raise
        finally:
            self.fila.put(con if con is not None else bc.conectar())

    def fechar(self):
        while not self.fila.empty():
            try:
                self.fila.get_nowait().close()
            except Exception:                                  # noqa: BLE001
                pass


#: Quanto cada trabalhador espera a mais que o anterior antes de começar.
#:
#: Três trabalhadores abrindo o Maps no mesmo segundo disputam a mesma banda e
#: o mesmo início de sessão. Um segundo e meio entre eles não atrasa uma rodada
#: de centenas de POIs e tira a disputa do momento mais frágil.
ESCALONAR_S = 1.5


async def _aquecer(page) -> None:
    """Abre um panorama descartável antes do primeiro POI de verdade.

    O DEFEITO QUE ISTO CORRIGE, e é o mesmo de manhã visto por outro ângulo.
    Ficou parecendo resolvido porque a medida era boa: 19 de 20 POIs com as
    quatro visadas. Mas naquela rodada cada trabalhador fazia CINCO POIs — só o
    primeiro pegava a aba fria, e os outros quatro escondiam a falha na média.

    Com três POIs e três trabalhadores, cada um faz UM: todos os POIs são
    primeiro-POI, e o resultado desabou para 1 de 3, igual no i9 e no notebook,
    que têm IPs diferentes. Não era limite do Google — era a aba fria, que
    precisa baixar o JavaScript inteiro do Maps antes de mostrar o primeiro
    tile e estoura a espera de `_abrir`.

    Isto importa mais, e não menos, com a fila: várias rodadas curtas significam
    muitos arranques a frio, que é justamente onde o defeito mora.

    O panorama usado é fixo e conhecido; o que interessa dele não é a imagem, é
    o cache que ele deixa na aba. Falhar aqui não é motivo para desistir do
    POI — no pior caso a aba entra fria, que é como era antes.
    """
    try:
        await sv._abrir_por_id(page, PANO_AQUECIMENTO, 0, 90)
        await page.wait_for_timeout(600)
    except Exception:                                          # noqa: BLE001
        pass


#: Um panorama qualquer de Canoas, só para o Maps carregar o próprio código.
PANO_AQUECIMENTO = "msYLglDqsAq9mfemgrpY8g"


# QUANTAS CONEXÕES, independentemente de quantos trabalhadores. Seis atendem
# 30 trabalhadores com folga pela conta acima, e deixam as outras 14 do pooler
# para a API, o painel e o restante do fluxo — que rodam ao mesmo tempo.
#:
#: O TETO VEM DO AMBIENTE PORQUE O POOLER E COMPARTILHADO.
#:
#: A porta 7100 aceita 20 sessoes NO TOTAL — entre todas as maquinas, a API, o
#: painel, o realtime e as capturas. Com 6 fixos aqui, tres processos de
#: captura sozinhos ja consomem 18 e o quarto morre com
#: `(EMAXCONNSESSION) max clients reached`. Foi o que aconteceu em 10/09/2026
#: ao dividir a fila entre i9 e notebook.
#:
#: `RADAR_CONEXOES` deixa cada processo declarar quanto vai pegar, para que a
#: soma caiba. Nao ha coordenacao automatica: quem dispara e quem faz a conta.
CONEXOES = int(os.environ.get("RADAR_CONEXOES") or 6)


def gravar(poco, poi_id, tipo, dados, **extra):
    campos = ["poi_id", "tipo", "dados", "bytes_tam"]
    vals = [poi_id, tipo, psycopg2_bin(dados), len(dados) if dados else None]
    for k, v in extra.items():
        if v is not None:
            campos.append(k)
            vals.append(v)
    marc = ", ".join(["%s"] * len(campos))
    sets = ", ".join("%s = excluded.%s" % (c, c) for c in campos
                     if c not in ("poi_id", "tipo"))
    # RECAPTURA APAGA O CAMINHO, e sem isto ela e' INVISIVEL.
    #
    # As imagens migraram para o Storage em 07/09/2026 e `imagens._de_linha`
    # passou a PREFERIR `storage_path`, caindo no `bytea` so quando nao ha
    # caminho. Este upsert grava `dados` novos e nunca mexia no caminho — que
    # continuava apontando para o arquivo ANTIGO. Resultado: recapturar
    # gravava a imagem nova no banco e todo mundo continuava lendo a velha.
    #
    # Descoberto em 10/09/2026 ao tirar a mira: recapturei 23 POIs, o `bytea`
    # veio limpo, e o dossie continuou entregando a foto com a cruz verde
    # desenhada. Nao e' um defeito da mira — vale para QUALQUER correcao de
    # foto: panorama novo, enquadramento errado, imagem cortada.
    #
    # Apagar o caminho e a correcao certa, e nao "reenviar por cima": a
    # verdade passa a ser o `bytea` recem-gravado, e a migracao para o Storage
    # reenvia depois, quando for a vez dela.
    if dados:
        sets += ", storage_path = null"
    with poco.pegar() as con:
        with con.cursor() as k:
            k.execute(
                "insert into radar_comercial.poi_evidencia (%s) values (%s) "
                "on conflict (id_empresa, poi_id, tipo) do update set %s, "
                "capturado_em = now()" % (", ".join(campos), marc, sets), vals)
        con.commit()


def psycopg2_bin(dados):
    import psycopg2
    return psycopg2.Binary(dados) if dados else None


# ── a captura ──────────────────────────────────────────────────────────────
async def um_poi(page, poco, alvo, placar) -> None:
    """As quatro visadas de UM POI, numa aba que já vem aberta.

    A ABA É DO TRABALHADOR, E NÃO DO POI — e essa troca é a correção de
    04/09/2026. Abrir um contexto novo por POI significava cache vazio: cada
    ponto rebaixava o JavaScript inteiro do Maps antes de mostrar o primeiro
    pixel, e com três trabalhadores disputando a banda isso estourava os 25 s
    que `_abrir` espera pela marca `,3a,` na URL. O sintoma era enganoso — a
    mensagem gravada era "o Maps não entrou em modo panorama", que soa como
    "não existe foto aqui". Existia: os mesmos quatro POIs que falharam abriram
    em 8 s quando rodados sozinhos numa aba reaproveitada.
    """
    lat, lng = alvo["lat"], alvo["lng"]
    try:
        # As quatro visadas saem do MESMO panorama, girando a câmera.
        m = await asyncio.to_thread(sv.metadados_pano, lat, lng)
        if m is False:
            gravar(poco, alvo["id"], "sv_frente", None,
                   motivo_falha="o Google confirma que não há panorama aqui")
            placar["sem_pano"] += 1
        elif m is None:
            gravar(poco, alvo["id"], "sv_frente", None,
                   motivo_falha="metadados não responderam")
            placar["sem_metadados"] += 1
        else:
            d = sv._dist_m(m["lat"], m["lng"], lat, lng)
            frente = sv._bearing(m["lat"], m["lng"], lat, lng)
            fov = sv._fov_por_distancia(d)
            for tipo, giro in VISADAS:
                heading = (frente + giro) % 360
                # A FRENTE FECHA NO ALVO, AS OUTRAS TRÊS ABREM.
                #
                # `_fov_por_distancia` escolhe o zoom para enquadrar a
                # coordenada — o que serve à frente e atrapalha o resto: nas
                # laterais e no fundo não há alvo para enquadrar, há entorno
                # para varrer. Fechar o campo ali cortaria justamente a esquina
                # onde o comércio de bairro costuma estar, que é o que estas
                # três visadas existem para achar.
                fov_aqui = fov if giro == 0 else FOV_ENTORNO
                try:
                    # DUAS TENTATIVAS, e a segunda não é teimosia: os metadados
                    # JÁ garantiram que o panorama existe e disseram o id dele.
                    # Não abrir é transitório — tile lento, aba disputando banda
                    # com os outros trabalhadores. Medido em 04/09/2026: 3 de 6
                    # POIs falharam na primeira e o panorama existia nos três.
                    ok = await sv._abrir_por_id(page, m["pano_id"], heading, fov_aqui)
                    if not ok:
                        await page.wait_for_timeout(1500)
                        ok = await sv._abrir_por_id(page, m["pano_id"], heading, fov_aqui)
                    if not ok:
                        gravar(poco, alvo["id"], tipo, None,
                               motivo_falha="o Maps não entrou em modo panorama "
                                            "em duas tentativas")
                        placar["falha_pano"] += 1
                        continue
                    await page.wait_for_timeout(1200)
                    bruto = await page.screenshot()
                    limpo = _cortar_interface(bruto)
                    # A MIRA SAIU. Regra do dono do produto em 09/09/2026: "o
                    # prompt da IA passa a receber as 4 visadas nas 4 direcoes
                    # SEM O MARCADOR DE ONDE ESTA O LOCAL BUSCADO, para nao
                    # tendenciar".
                    #
                    # E ela tendenciava de um jeito que o texto do prompt nao
                    # alcancava: a cruz verde estava DESENHADA NOS PIXELS do
                    # `sv_frente`, apontando um imovel. Tirar a mencao dela do
                    # prompt — feito no mesmo dia — nao tirou a mira da imagem;
                    # o modelo continuava vendo para onde apontar, e o que se
                    # media como "a IA reconheceu a fachada" podia ser "a IA
                    # leu a seta".
                    #
                    # O ALVO CONTINUA NO CENTRO por construcao: o `heading`
                    # mira nele e o corte preserva o centro. Quem precisa saber
                    # onde olhar tem o enquadramento; quem precisa julgar, nao
                    # tem mais a resposta desenhada em cima.
                    #
                    # `_marcar_centro` fica no modulo, sem chamador. Nao e
                    # descuido: `identificar_divergente` faz o mesmo desenho
                    # por conta propria (via `anotar`), porque LA a mira e o
                    # produto — o print existe para um humano ver ONDE esta o
                    # achado. Se um dia esse caminho quiser reaproveitar este
                    # codigo, ele esta aqui e documentado.
                    img = limpo
                    # O WEBP E O ULTIMO PASSO, depois do corte e da mira: as
                    # duas etapas usam cv2 e falam PNG entre si.
                    img = _para_webp(img)
                    gravar(poco, alvo["id"], tipo, img,
                           pano_id=m["pano_id"], cam_lat=m["lat"], cam_lng=m["lng"],
                           heading=heading, pitch=5.0, fov=float(fov_aqui),
                           distancia_m=d, largura_px=LARG, altura_px=ALT,
                           data_imagem=_data_do_pano(m["pano_id"]))
                    placar[tipo] += 1
                except Exception as e:                         # noqa: BLE001
                    gravar(poco, alvo["id"], tipo, None,
                           motivo_falha=type(e).__name__)
                    placar["falha_pano"] += 1

        # O SATÉLITE SAIU DAQUI em 04/09/2026 — ver o cabeçalho. O código do
        # `SAT_HTML` continua no arquivo porque a vista de cima ainda serve à
        # etapa de telhados; o que deixou de existir é a captura por POI.
    finally:
        # A ABA CONTINUA VIVA para o próximo POI. O que precisa voltar ao
        # estado inicial é o TAMANHO DA JANELA: o satélite a encolheu para
        # 720×720, e o Street View do próximo ponto quer os 1280×900.
        try:
            await page.set_viewport_size({"width": LARG, "height": ALT})
        except Exception:                                      # noqa: BLE001
            pass


async def rodar(area, limite, aplicar, trabalhadores, pois=None,
                sem_catalogo=False, fatia=""):
    # ÁREA PEDIDA E INEXISTENTE É ERRO, e não 'sem filtro'. Com
    # `--poi` não há área a exigir: o id já é o recorte.
    poligono = None if pois else (area_utils.exigir_area(area)
                                  if area else None)
    con = bc.conectar()
    lista, fora = alvos(con, poligono, limite, pois, sem_catalogo,
                        fatia)
    _log("   %d POI(s) na fila da evidência" % len(lista))
    if fora:
        _log("   %d fora do desenho" % fora)
    if not lista:
        _log("   nada a capturar. Marque categorias em 'Categorias para a IA'")
        _log("   e confira que há vínculo com ligação residencial ativa.")
        con.close()
        return {"alvos": 0}
    for a in lista[:5]:
        _log("      %8d %-34s %s" % (a["id"], a["nome"][:34], a["fonte"]))
    if not aplicar:
        _log("   (ensaio: nada capturado. Use --aplicar)")
        con.close()
        return {"alvos": len(lista), "capturados": 0}

    from playwright.async_api import async_playwright
    placar = {k: 0 for k in [v[0] for v in VISADAS]
              + ["sem_pano", "sem_metadados", "falha_pano"]}
    t0 = time.time()
    poco = Poco(min(CONEXOES, max(1, trabalhadores)))
    async with async_playwright() as pw:
        nav = await pw.chromium.launch(headless=False, args=ARGS_NAV)
        try:
            fila = asyncio.Queue()
            for a in lista:
                fila.put_nowait(a)

            # O freio é compartilhado: quem zera e quem conta são
            # trabalhadores diferentes, e o que interessa é a série do
            # CONJUNTO, não a de cada um. Falha isolada é normal; falha em
            # série significa que o problema não está no POI.
            freio = {"seguidas": 0}

            async def obreiro(n):
                ctx = await nav.new_context(
                    viewport={"width": LARG, "height": ALT},
                    device_scale_factor=DENSIDADE)
                page = await ctx.new_page()
                # PARTIDA ESCALONADA E ABA AQUECIDA — ver `_aquecer`.
                await asyncio.sleep(ESCALONAR_S * n)
                await _aquecer(page)
                try:
                    while True:
                        try:
                            a = fila.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        try:
                            await um_poi(page, poco, a, placar)
                            freio["seguidas"] = 0
                        except Exception as e:                 # noqa: BLE001
                            freio["seguidas"] += 1
                            _log("      %d FALHOU: %s" % (a["id"], str(e)[:70]))
                            # O POI VOLTA PARA A FILA, uma vez só. Ele não tem
                            # culpa de o banco ter caído, e sem isto uma soluço
                            # de rede vira buraco silencioso na cobertura.
                            a["_tentativas"] = a.get("_tentativas", 0) + 1
                            if a["_tentativas"] < 2:
                                fila.put_nowait(a)
                            if freio["seguidas"] >= FALHAS_SEGUIDAS_LIMITE:
                                _log("      PARANDO: %d falhas seguidas. Não é "
                                     "o POI, é o banco ou o Google. A fila fica "
                                     "com %d itens intactos."
                                     % (freio["seguidas"], fila.qsize()))
                                return
                            # A ABA PODE TER MORRIDO JUNTO. Sem trocá-la, o
                            # trabalhador arrasta o mesmo erro por toda a fila
                            # restante e o placar culpa POIs que estão sãos.
                            if page.is_closed():
                                ctx = await nav.new_context(
                                    viewport={"width": LARG, "height": ALT},
                                    device_scale_factor=DENSIDADE)
                                page = await ctx.new_page()
                        feitos = placar["sv_frente"] + placar["sem_pano"]
                        if feitos and feitos % 5 == 0:
                            _log("      %d/%d · %.1f s/POI"
                                 % (feitos, len(lista),
                                    (time.time() - t0) / max(feitos, 1)))
                finally:
                    try:
                        await ctx.close()
                    except Exception:                          # noqa: BLE001
                        pass

            await asyncio.gather(*[obreiro(i) for i in range(trabalhadores)])
        finally:
            await nav.close()
            poco.fechar()

    dt = time.time() - t0
    _log("")
    for k in sorted(placar):
        if placar[k]:
            _log("   %-16s %5d" % (k, placar[k]))
    _log("   %d POI(s) em %.1f min · %.1f s por POI"
         % (len(lista), dt / 60, dt / max(len(lista), 1)))
    con.close()
    return {"alvos": len(lista), **placar, "segundos": dt}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--area", default=area_utils.AREA_PADRAO)
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--trabalhadores", type=int, default=3)
    p.add_argument("--poi", action="append", type=int,
                   help="repetível; recaptura estes POIs, ignorando a fila")
    # DUAS MAQUINAS NA MESMA FILA, SEM PISAR UMA NA OUTRA.
    #
    # A fila e uma consulta, nao uma tabela com reserva: duas instancias
    # leriam a MESMA lista e fotografariam os mesmos POIs, gastando o dobro
    # de proxy para metade do resultado. `--fatia 0/2` e `--fatia 1/2` cortam
    # por `id % 2`, que e estavel e nao precisa de coordenacao entre elas.
    p.add_argument("--fatia", default="",
                   help="N/M ou N,N/M — processa so os POIs com id %% M em N")
    p.add_argument("--sem-catalogo", dest="sem_catalogo",
                   action="store_true",
                   help="fotografa tambem o POI cuja categoria o catalogo "
                        "marca como nao avaliavel")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    _log("▶ evidência para a IA — 4 visadas de rua por POI")
    r = asyncio.run(rodar(a.area, a.limite, a.aplicar, a.trabalhadores,
                            a.poi, a.sem_catalogo, a.fatia))
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
