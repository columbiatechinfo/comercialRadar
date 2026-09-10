# -*- coding: utf-8 -*-
"""cruzar_ligacao.py — o vínculo ancorado na ligação do cliente.

O QUE MUDA EM RELAÇÃO AO CRUZAMENTO ANTIGO

`cadastro_cliente.cruzar()` pergunta, para cada POI, qual ligação parece ser a
dele. Este pergunta o contrário: para cada LIGAÇÃO do cliente, quais POIs podem
ser aquele ponto. A diferença não é de estilo — é de quem manda.

A ligação é o que o cliente já tem cadastrado e cobra todo mês; ela existe com
ou sem radar. O POI é uma hipótese que o sistema levantou. Ancorar na ligação
faz o resultado ser lido como "esta instalação tem estes candidatos, com esta
confiança", que é a pergunta que o operador realmente faz.

OS CINCO CRITÉRIOS, CADA UM VISÍVEL POR SI

    mesmo_endereco      a via da ligação e a do POI, normalizadas, batem
    mesmo_numero        o número da porta bate
    ate_20m             a distância medida é de 20 m ou menos
    mesmo_telhado       os dois pontos caem no mesmo telhado
    telhado_comercial   a cor do telhado é de cobertura comercial

`confianca` sai de `confianca_de()`: rua E número juntos são a base forte
(0,70), rua só é a quadra (0,35), e telhado, telhado comercial e distância
entram como reforço. Era `criterios_ok / 5` até 06/09/2026 — ver a função.
Guardar os cinco separados é o que permite rever a régua depois sem refazer
o cruzamento.

OS DOIS ÚLTIMOS AINDA NÃO TÊM DADO, e isso é dito aqui em vez de escondido: o
telhado sai dos tiles capturados, e em 02/09/2026 existe UM tile no disco, de
uma quadra. O código dos dois critérios está escrito e roda; enquanto não houver
tile cobrindo o ponto, eles ficam falsos. O efeito é que a confiança máxima
observável hoje é 0,6 — e como isso pesa igual em todo mundo, a ordenação do
painel continua válida entre os vínculos.

    python cruzar_ligacao.py --base 1 --cidade Canoas --aplicar
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict

import area_utils
import base_comum as bc

# O RAIO DE BUSCA É MAIOR QUE O CRITÉRIO, de propósito. `ate_20m` é um dos cinco
# critérios; se a busca parasse em 20 m, um POI a 35 m que bate endereço e
# número nunca seria visto — e ele é exatamente o caso interessante, o ponto
# cuja coordenada está torta mas cujo endereço está certo.
#: Teto de confianca quando o endereco do POI e INDICIO, e nao PROVA.
#:
#: `logradouro_resolvido.forca` ja separava os dois e o cruzamento ignorava a
#: distincao. `prova` e endereco que a fonte publicou ou que o CEP confirmou;
#: `indicio` e endereco DEDUZIDO DA COORDENADA — o CNEFE mais proximo, a 6,5 m
#: em media, ou o OSRM a 8,9 m.
#:
#: A diferenca importa porque, no indicio, a rua e o numero SAO A COORDENADA
#: escrita de outro jeito. Dizer "rua e numero batem, logo 0,95" quando a rua
#: e o numero vieram do proprio ponto e raciocinio circular: mede-se
#: proximidade duas vezes e chama-se a segunda de porta identificada.
#:
#: Medido em Canoas, 06/09/2026: 3.428 vinculos de confianca >= 0,70 apoiam-se
#: num endereco `indicio` — 5,6% de todos os de alta confianca. O caso extremo
#: e o Airbnb, que embaralha o pino de proposito e mesmo assim produzia
#: vinculos 0,95.
#:
#: 0,60 e escolha: fica ABAIXO da faixa da porta identificada (0,70) e ACIMA
#: da quadra (0,40). O indicio continua valendo — so nao vale como prova.
TETO_ENDERECO_INDICIO = 0.60


def confianca_de(mesmo_end, mesmo_num, perto, mesmo_tel, tel_com,
                 end_prova: bool = True) -> float:
    """A regua de confianca de um par ligacao x POI.

    ERA `acertos / 5`, com os cinco criterios pesando igual — e isso fazia
    "rua + numero" (a PORTA identificada) empatar em 0,40 com "rua + ate 20 m"
    (a QUADRA identificada). Pior: "numero sem rua + 20 m" tambem dava 0,40, e
    numero sem rua e coincidencia — o 350 existe em toda rua da cidade.

    Decisao do dono do produto, 06/09/2026: rua E numero juntos valem mais que
    a soma das partes. A regua separa BASE de REFORCO:

        base     rua E numero ........ 0,70   a porta
                 rua so .............. 0,35   a quadra
                 nem rua ............. 0,10   so proximidade
        reforco  mesmo telhado ....... +0,20  desempata vizinho
                 telhado comercial ... +0,05
                 ate 20 m ............ +0,05
        teto 1,00

    Medido sobre os 72.285 vinculos existentes antes de aplicar: os 23.469 com
    rua+numero sobem para 0,70-0,75 e se separam dos 27.220 que so tem a
    quadra (0,40); os 2.498 de "numero sem rua + 20 m" caem de 0,40 para 0,15.

    Os cinco criterios continuam gravados separados: mudar esta regua nunca
    exige refazer o cruzamento — basta recalcular a coluna.
    """
    if mesmo_end and mesmo_num:
        v = 0.70
    elif mesmo_end:
        v = 0.35
    else:
        v = 0.10
    if mesmo_tel:
        v += 0.20
    if tel_com:
        v += 0.05
    if perto:
        v += 0.05
    if not end_prova and (mesmo_end or mesmo_num):
        # O TETO SO MORDE QUEM USOU O ENDERECO. Um par que casou por
        # proximidade e telhado nao fica pior por o POI ter endereco fraco —
        # ele nao usou endereco nenhum.
        v = min(v, TETO_ENDERECO_INDICIO)
    return min(1.0, round(v, 2))


# O RAIO DE BUSCA E O DO CRITERIO — 20 m, decisao do dono do produto em
# 06/09/2026, com o numero na mesa.
#
# Era 60 m, e a justificativa era boa: um POI a 35 m que bate rua e numero e o
# caso interessante, o ponto de coordenada torta com endereco certo. Medido:
# esse resgate vale 2.290 vinculos, 3% do total.
#
# O QUE PESOU CONTRA foi o ruido. A 60 m cada ligacao junta 21,5 POIs
# candidatos — o quarteirao inteiro — para guardar 5. A 20 m sao ~2,4, e a
# lista que chega ao operador para de vir cheia de vizinho. Os 2.290 se
# recuperam depois, num passe proprio sobre quem tem endereco batendo.
#: Ate onde a busca olha. Alem de `PERTO_M` a regra aperta — ver
#: `LONGE_SO_COM_ENDERECO`.
RAIO_BUSCA_M = 60.0
PERTO_M = 20.0

#: Alem de PERTO_M, so entra quem bate rua E numero.
#:
#: A 60 m soltos cada ligacao juntava 21,5 POIs candidatos — o quarteirao
#: inteiro — para guardar 5, e a lista chegava ao operador cheia de vizinho. A
#: 20 m secos sao 3,8 candidatos, mas perdem-se 2.290 vinculos com rua E numero
#: batendo entre 20 e 60 m: o POI de coordenada torta e endereco certo, que e
#: justamente o caso que o cadastro resolve e o mapa nao.
#:
#: A regra hibrida fica com os dois: perto, qualquer criterio serve; longe, so
#: a porta identificada passa. Decidido em 06/09/2026 com os numeros na mesa.
LONGE_SO_COM_ENDERECO = True

#: Teto de candidatos FRACOS por ligacao — os que nao identificam a porta.
#:
#: NAO VALE PARA QUEM BATE RUA E NUMERO, e a distincao e o modelo do produto.
#: Uma ligacao e um ENDERECO FISICO, nao um ponto: um predio comercial com
#: vinte lojas tem uma instalacao no cadastro e vinte POIs legitimos. Cortar em
#: cinco jogava fora quinze estabelecimentos reais.
#:
#: Pior: o corte contaminava a eleicao do passo seguinte. `pois.id_ligacao_base`
#: sai dos candidatos GRAVADOS; se a ligacao certa de um POI nao coubesse entre
#: os cinco daquela ligacao, esse POI nunca a recebia — e ficava orfao ou
#: colado numa ligacao pior.
#:
#: O teto continua existindo para o candidato fraco (so proximidade, sem rua e
#: numero): esses sim sao o quarteirao ao redor, e guardar todos encheria a
#: tabela de vinculo que ninguem vai revisar.
MAX_FRACOS_POR_LIGACAO = 5

SQL_CANDIDATOS = """
    select l.num_ligacao::text,
           l.{via}, l.{numero}, l.{tipo},
           p.id, coalesce(p.fonte,''), coalesce(p.nome,''),
           coalesce(p.endereco,''),
           coalesce(lr.logradouro,''), coalesce(lr.numero,''),
           st_distance(l.geom, p.pt) as metros,
           st_y(l.geom::geometry), st_x(l.geom::geometry),
           st_y(p.pt::geometry), st_x(p.pt::geometry)
      from (select id, fonte, nome, endereco, pt_geo as pt
              from radar_comercial.pois
             where fundido_em is null
               and upper(coalesce(cidade,'')) = upper(%s)
               and pt_geo is not null
             {corte}) p
      join lateral (
            select c.num_ligacao, c.{via}, c.{numero}, c.{tipo}, c.geom
              from {tabela} c
             where c.geom is not null
               and upper(coalesce(c.{cidade},'')) = upper(%s)
               and upper(coalesce(c.{tipo},'')) = any(%s)
               -- `geom` DE `cadastro_corsan` E GEOGRAPHY, e `ix_corsan_geom`
               -- e GiST sobre ela. Entao `st_dwithin` em metros usa o indice
               -- direto — nao ha caixa grosseira a montar, nem grau a
               -- converter. (A primeira versao supunha geometry e caiu em
               -- `st_y(geography) does not exist`, que e o banco dizendo qual
               -- dos dois tipos ele guarda.)
               and st_dwithin(c.geom, p.pt, %s)
           ) l on true
      left join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id
"""
    # SEM `order by` NO SQL — E DE PROPOSITO.
    #
    # Havia `order by l.num_ligacao, metros` aqui. Ele ordenava TODOS os pares
    # POI x ligacao da cidade — milhoes deles — so para o Postgres, e era o que
    # deixava o cruzamento de Canoas em CPU por mais de 14 minutos: nao era a
    # busca espacial (essa usa o GiST dos dois lados), era a ordenacao do
    # resultado inteiro.
    #
    # E o pior: o Python nao usava essa ordem. Logo abaixo, `por_ligacao`
    # agrupa por ligacao e faz `cands.sort(reverse=True)` — reordena cada grupo
    # por conta propria, por (criterios, -metros), e corta os 5. A ordenacao do
    # SQL era jogada fora linha a linha. Tirar ela nao muda um resultado, e
    # devolve o cruzamento a segundos: o trabalho pesado ja estava no Python.


import regra_vinculo as rv

def _log(m: str) -> None:
    print(m, flush=True)


def _sem_acento(s: str) -> str:
    t = unicodedata.normalize("NFD", str(s or "").upper())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# A NORMALIZAÇÃO É A DA SKILL, e não uma minha. `ajuste-logradouro` já resolve
# abreviatura, tipo de via e acento do jeito que o resto do sistema espera; uma
# segunda regra aqui faria "AV BRASIL" bater com "AVENIDA BRASIL" numa etapa e
# não na outra.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "skills", "ajuste-logradouro", "scripts"))
    from normalizacao_base import norm_logradouro          # type: ignore

    def _via(s: str) -> str:
        return norm_logradouro(str(s or ""), hard=True) or ""
except Exception:                                          # noqa: BLE001
    def _via(s: str) -> str:
        t = _sem_acento(s)
        t = re.sub(r"^(RUA|AV|AVENIDA|TV|TRAVESSA|R|EST|ESTRADA|ROD|RODOVIA)\b\.?\s*",
                   "", t)
        return re.sub(r"[^A-Z0-9 ]", " ", t).strip()


def _num(s) -> str:
    r"""Só os dígitos do número da porta. `1509-A` e `1509` são a mesma porta.

    DELEGA PARA `regra_vinculo`, e a diferenca nao e cosmetica. Esta funcao
    fazia `re.sub(r"\D", "", s)`, que CONCATENA todos os grupos de digitos:
    "350 sala 2" virava "3502" e nunca casava com a porta 350, e o lixo
    "136092310200" era aceito como numero de porta.

    Descoberto em 08/09/2026 pela diferenca: `casar_por_endereco` casou 391
    pares que este cruzamento tinha descartado por "numero diferente".
    """
    return rv.numero_limpo(s) or ""


class Telhado:
    """Os dois criterios que dependem de imagem. Casca fina sobre `telhados.py`.

    A IMPLEMENTACAO ANTIGA MORAVA AQUI E ESTAVA ERRADA DE DUAS FORMAS.

    Primeira: `LADO_GRAUS = 0.0009` supunha um tile de ~200 m de lado. O tile que
    a captura produz e 3840x2160 a zoom 19, que a 0,2588 m/px da 994 x 559 m —
    cinco vezes maior, e retangular, nao quadrado. Procurar "o tile que cobre
    este ponto" com essa constante erra o alvo quase sempre.

    Segunda: ela decidia "mesmo telhado" comparando a COR MEDIA num quadradinho
    ao redor de cada ponto. Duas casas geminadas com a mesma telha davam "mesmo
    telhado"; um telhado com claraboia dava "telhados diferentes". Cor nao e
    geometria.

    `telhados.py` faz as duas coisas direito: a caixa do tile sai do zoom e da
    latitude (ou da caixa MEDIDA que o proprio mapa reportou), e a pertinencia
    sai de uma extracao de construcao de verdade. A classe fica com o nome e a
    interface para nao reescrever a chamada, e delega.
    """

    #: Raios sondados ao redor da ligacao quando o pixel exato nao e telhado.
    #: A coordenada da ligacao e a do hidrometro — calcada, muro, frente do
    #: lote —, e o predio dela e o mais proximo. 12 m cobre o recuo de um lote
    #: urbano; alem disso ja e o vizinho.
    ANEIS_DE_SONDAGEM_M = (4.0, 8.0, 12.0)

    #: Quantas segmentacoes (33 MB cada) ficam vivas ao mesmo tempo.
    #: Ligacoes vizinhas caem no mesmo tile, entao 64 ja acerta quase sempre; o
    #: resto vem do .npz em disco. Sem este teto, o cruzamento residencial de
    #: Canoas estourou 24 GB (OOMKilled, 06/09/2026).
    SEGMENTACOES_VIVAS = 64

    #: Tamanho da celula do indice de tiles, em graus (~1,1 km). O maior tile
    #: tem 994 m de lado, entao a vizinhanca 3x3 de uma celula sempre contem
    #: todo tile que cobre um ponto dela.
    CELULA_GRAUS = 0.01

    def __init__(self, pasta: str = "capturas"):
        import collections
        import telhados as _t
        self._t = _t
        self.tiles = _t.achar_tiles_no_disco(pasta)
        # O menor primeiro: varios tiles cobrem o mesmo ponto, e o de 166 m a
        # zoom 20 distingue construcoes vizinhas que o de 994 m mistura.
        self.tiles.sort(key=lambda t: t.meia_lat * t.meia_lng)
        self.sem_cobertura = 0
        self.sondados = 0                 # ligacoes achadas pelo anel, nao pelo pixel
        # INDICE POR CELULA. Um tile entra em toda celula que a caixa dele toca.
        self._celulas = collections.defaultdict(list)
        g = self.CELULA_GRAUS
        for tl in self.tiles:
            a, b, c, d = tl.caixa
            for i in range(int(a // g), int(b // g) + 1):
                for j in range(int(c // g), int(d // g) + 1):
                    self._celulas[(i, j)].append(tl)
        # CACHE DE SEGMENTACAO, mais recente por ultimo.
        self._vivas = collections.OrderedDict()

    def _candidatos(self, lat, lon):
        """Os tiles que podem cobrir o ponto — poucos, pelo indice."""
        g = self.CELULA_GRAUS
        return self._celulas.get((int(lat // g), int(lon // g)), ())

    def _lembrar(self, tl):
        """Registra o tile como recem-usado; solta o mais antigo se passou do teto."""
        chave = id(tl)
        if chave in self._vivas:
            self._vivas.move_to_end(chave)
            return
        self._vivas[chave] = tl
        if len(self._vivas) > self.SEGMENTACOES_VIVAS:
            _, velho = self._vivas.popitem(last=False)
            velho._seg = None             # a memoria volta; o .npz fica no disco

    def _telhado_em(self, tl, lat, lon):
        """(segmento, props) se o ponto cai em telhado neste tile; senao (None, None)."""
        s, p = self._t.segmento_de(tl, lat, lon)
        self._lembrar(tl)
        if s is not None and p and p.get("telhado"):
            return s, p
        return None, None

    def _onde(self, lat, lon):
        """O tile e o telhado da ligacao — o de baixo dela, ou o MAIS PROXIMO.

        Medido em 06/09/2026, 300 pares com rua e numero batendo: 96% morriam
        aqui porque a ligacao (o hidrometro) cai na calcada, nao no telhado.
        O predio da ligacao e o mais proximo dela: se o pixel exato nao e
        telhado, sondam-se aneis crescentes em oito direcoes.
        """
        import math as _m
        for tl in self._candidatos(lat, lon):
            if not tl.cobre(lat, lon):
                continue
            s, p = self._telhado_em(tl, lat, lon)
            if s is not None:
                return tl, s, p
            # Nao esta em cima de telhado: o predio mais proximo, em aneis.
            ky = 110540.0
            kx = 111320.0 * _m.cos(_m.radians(lat))
            for raio in self.ANEIS_DE_SONDAGEM_M:
                for k in range(8):
                    ang = k * _m.pi / 4.0
                    la = lat + (raio * _m.sin(ang)) / ky
                    lo = lon + (raio * _m.cos(ang)) / kx
                    if not tl.cobre(la, lo):
                        continue
                    s, p = self._telhado_em(tl, la, lo)
                    if s is not None:
                        self.sondados += 1
                        return tl, s, p
        return None, None, None

    def julgar(self, lat1, lon1, lat2, lon2):
        """(mesmo_telhado, telhado_comercial).

        Ponto 1 e a LIGACAO (achada pelo pixel ou pelo anel); ponto 2 e o POI,
        que e um pino do Maps e quase sempre ja esta sobre o telhado — mas
        tambem ganha o anel, porque um pino na porta e comum.
        """
        if not self.tiles or lat1 is None or lat2 is None:
            self.sem_cobertura += 1
            return False, False
        tl, s1, p1 = self._onde(lat1, lon1)
        if tl is None:
            self.sem_cobertura += 1
            return False, False
        s2, _ = self._telhado_em(tl, lat2, lon2)
        if s2 is None:
            import math as _m
            ky = 110540.0
            kx = 111320.0 * _m.cos(_m.radians(lat2))
            for raio in self.ANEIS_DE_SONDAGEM_M:
                for k in range(8):
                    ang = k * _m.pi / 4.0
                    la = lat2 + (raio * _m.sin(ang)) / ky
                    lo = lon2 + (raio * _m.cos(ang)) / kx
                    if tl.cobre(la, lo):
                        s2, _ = self._telhado_em(tl, la, lo)
                        if s2 is not None:
                            break
                if s2 is not None:
                    break
        if s2 is None:
            self.sem_cobertura += 1
            return False, False
        mesmo = (s1 == s2)
        return mesmo, bool(mesmo and p1.get("comercial"))


def cruzar(base_id: int, cidade: str, aplicar: bool, raio: float,
           limite: int, area: str = "", tipos_over: str = "",
           situacao: str = "") -> dict:
    con = bc.conectar()
    cur = con.cursor()
    # RECORTE PELA AREA. Mesma regra da etapa 9: com poligono, so ligacoes e
    # POIs dentro dele; em modo municipio o poligono e a cidade inteira.
    poligono = area_utils.carregar_area(area) if area else None

    # O CRUZAMENTO E INTRA-EMPRESA DESDE 03/09/2026.
    #
    # Ate a migracao 0050 este era o unico passo cross-tenant do radar. Os POIs
    # eram da A2L e as ligacoes da Corsan, e a RLS isolava cada uma na sua; para
    # o join enxergar os dois lados, esta funcao assumia aqui um usuario nivel 9
    # e passava a sessao inteira sob `core.eh_suporte()`.
    #
    # FUNCIONAVA, E ERA O MODO ERRADO DE FUNCIONAR. Nao era um passo elevado: a
    # elevacao valia a conexao toda, entao todo o cruzamento — milhares de
    # transacoes — rodava com a trava de isolamento desligada. Um engano em
    # qualquer consulta daqui para baixo enxergaria as tres empresas do banco em
    # vez de uma, e nada no resultado denunciaria isso.
    #
    # A 0050 consertou onde estava o defeito, que era a modelagem e nao a
    # permissao: o POI mineirado PARA um cliente E do cliente. POI e ligacao
    # passaram a ser da mesma empresa, e o pipeline ganhou identidade dentro
    # dela (`pipeline@corsan.servico.invalido`, nivel 4 — Administrador, porque
    # alterar POI exige isso). Sem fronteira, a travessia perdeu o motivo.
    #
    # `RADAR_USUARIO_SUPORTE` ficou aposentada no .env. Se um dia voltar a
    # existir POI de uma empresa com ligacao de outra, o conserto e a modelagem
    # de novo — nao a elevacao.

    cur.execute("""
        select nome, tabela_dados, mapa_colunas, tipos_comerciais, estado,
               tipos_a_cruzar
          from radar_comercial.base_cliente where id = %s
    """, (base_id,))
    r = cur.fetchone()
    if not r:
        _log("   base %s não existe" % base_id)
        con.close()
        return {}
    nome, tabela, mapa, tipos, estado, a_cruzar = r
    if estado != "pronta":
        # O PORTÃO BARRA A GRAVAÇÃO, NÃO O ENSAIO.
        #
        # Gravar vínculos a partir de colunas que ninguém conferiu põe no painel
        # ligação apontando para o lugar errado, sem nada dizendo de onde veio —
        # por isso `--aplicar` exige a base confirmada.
        #
        # Mas o ensaio não escreve nada, e é justamente ele que ajuda a decidir:
        # ver quantos vínculos o mapeamento sugerido produziria, e com que
        # confiança, é informação para a confirmação — não algo que dependa
        # dela. Barrar os dois deixava a pessoa confirmar às cegas.
        if aplicar:
            _log("   a base «%s» está em %s. Confirme o mapeamento no painel"
                 % (nome, estado))
            _log("   antes de gravar — é o que garante que as colunas são as")
            _log("   certas. O ensaio, sem --aplicar, roda mesmo assim.")
            con.close()
            return {"estado": estado}
        _log("   ⚠️  base em %s: este é um ENSAIO sobre o mapeamento sugerido," % estado)
        _log("      ainda não confirmado por ninguém.")

    tipos = [str(t).upper() for t in (tipos or [])]

    # A LISTA DO PAINEL MANDA, E `--tipos` MANDA MAIS.
    #
    # `tipos_a_cruzar` (migracao 0070) e o que o operador marcou na tela da
    # base: onde procurar comercio escondido. Vazio significa "use
    # tipos_comerciais" — o comportamento anterior a 06/09/2026 —, para
    # nenhuma base existente mudar de alvo por acidente.
    a_cruzar = [str(x).upper() for x in (a_cruzar or [])]
    if a_cruzar:
        tipos = a_cruzar
        _log("   tipos a cruzar (painel): %s" % ", ".join(tipos))

    if tipos_over:
        # CATEGORIA POR FORA DA BASE — para auditar o que a base nao declara como
        # comercial. Ex.: `--tipos RESIDENCIAL` acha comercio numa ligacao
        # residencial (subfaturacao). Nao muda o pipeline; e escolha de quem roda.
        tipos = [t.strip().upper() for t in tipos_over.split(",") if t.strip()]
        _log("   tipos por --tipos: %s" % ", ".join(tipos))
    if not tipos:
        # ENSAIO SEM TIPOS DECLARADOS. `tipos_comerciais` só é obrigatório para
        # marcar a base como pronta; num rascunho ele costuma estar vazio. Para
        # o ensaio poder mostrar alguma coisa, vale tudo que não é residencial —
        # e isso é dito, para ninguém tomar por declaração.
        cur.execute('select distinct upper("%s")::text from %s where "%s" is not null'
                    % (mapa["tipo_cliente"], tabela, mapa["tipo_cliente"]))
        tipos = [r[0] for r in cur.fetchall() if r[0] != "RESIDENCIAL"]
        _log("      tipos não declarados — o ensaio usa o que não é residencial")
    faltando = [c for c in ("ligacao", "endereco", "numero", "latitude",
                            "longitude", "tipo_cliente") if not mapa.get(c)]
    if faltando:
        _log("   o mapeamento não declara: %s" % ", ".join(faltando))
        con.close()
        return {}

    _log("   base «%s» · %s" % (nome, tabela))
    _log("   tipos comerciais: %s" % ", ".join(tipos))

    # O CASAMENTO ESPACIAL E EM MEMORIA, NAO NO SQL — E O MOTIVO E MEDIDO.
    #
    # Antes, um unico SELECT pedia ao Postgres TODOS os pares ligacao x POI a
    # ate 60 m e ainda ordenava o resultado. Para Canoas inteira (104 mil POIs x
    # 13 mil ligacoes comerciais) isso ficava mais de 14 minutos em CPU, mesmo
    # com indice GiST dos dois lados: o custo nao e achar o vizinho, e materializar
    # e mexer no resultado gigante. E o Python logo abaixo REORDENA cada ligacao
    # por conta propria — a ordem do SQL era jogada fora.
    #
    # Entao o banco faz so o que faz barato: duas leituras filtradas e indexadas
    # (as ligacoes comerciais da cidade, e os POIs da cidade). O casamento por
    # distancia — que e consulta de vizinhanca, nao de tabela — roda num
    # `cKDTree` do scipy: construir a arvore com 104 mil pontos e consultar 13
    # mil ligacoes leva segundos, e escala para a base estadual (2,5 milhoes)
    # sem o join explodir.
    #
    # A projecao e equiretangular local (metros), ancorada na latitude media da
    # cidade. Sobre o vao de um municipio o erro fica muito abaixo de 1 m — e o
    # limiar aqui e 60 m —, e nao depende de acertar a zona UTM, o que importa
    # quando a base cobre um estado que cruza dois fusos.
    import math as _math

    import numpy as _np
    from scipy.spatial import cKDTree as _cKDTree

    # `_cvia`/`_cnum` e nao `_via`/`_num`: estes ultimos sao FUNCOES do modulo
    # (normalizam via e numero do POI logo abaixo), e um local de mesmo nome as
    # sombreava — `via_p = _via(...)` estourava com "str object is not callable".
    _cvia, _cnum = mapa["endereco"], mapa["numero"]
    _tip, _cid = mapa["tipo_cliente"], mapa.get("cidade", "cidade")
    _lig = mapa.get("ligacao", "num_ligacao")
    _corte = (" limit %d" % int(limite)) if limite else ""

    # CIDADE COMPARADA SEM ACENTO, DOS DOIS LADOS.
    #
    # A corsan grava a cidade SEM acento ("GRAVATAI"); o `municipio_da_area`
    # devolve COM acento ("Gravatai"), da malha do IBGE. Um `upper(cidade) =
    # upper(%s)` falhava para toda cidade acentuada — deu 0 candidatos em
    # Gravatai no teste ponta-a-ponta de 03/09/2026, e so nao aparecera em
    # Canoas porque "CANOAS" nao tem acento. `translate` tira o acento nas duas
    # pontas; e o mesmo remedio do seletor de municipio.
    _AC_DE = "'áàâãäéèêëíìîïóòôõöúùûüçñ'"
    _AC_PARA = "'aaaaaeeeeiiiiooooouuuucn'"

    def _sa(expr):
        return "translate(lower(" + expr + "), " + _AC_DE + ", " + _AC_PARA + ")"

    t0 = time.time()
    # Ligacoes comerciais da cidade. Sem coalesce no tipo: lower(NULL) da NULL,
    # que nao casa — tipo/cidade nulo nao entra, que e o certo.
    # A COLUNA DE SITUACAO VEM DO MAPA DA BASE, e ate 03/09/2026 vinha chumbada
    # como `sit_ligacao` — o nome que a Corsan usa. O proximo cliente chama de
    # `status`, `sit_lig` ou `situacao_hidrometro`, e a consulta morreria com
    # "column does not exist" no meio do cruzamento, longe da causa.
    #
    # `mapa_colunas` existe exatamente para isso: e a declaracao de qual coluna
    # e o que, feita uma vez por base. Chumbar um nome ali dentro desmonta a
    # unica coisa que torna o sistema multi-cliente.
    _csit = mapa.get("situacao") or ""
    if situacao and not _csit:
        _log("   ⚠️  --situacao pedido, mas a base nao declarou a coluna de")
        _log("      situacao no mapa. O filtro fica de fora desta passada.")
    _sit = ""
    if situacao and _csit:
        _sit = ' and upper(coalesce("' + _csit + '", \'\')) = upper(%s)'
    _sql_lig = (
        'select "' + _lig + '"::text, "' + _cvia + '", "' + _cnum + '", "' + _tip + '", '
        'st_y(geom::geometry), st_x(geom::geometry) '
        'from ' + tabela + ' where geom is not null '
        'and ' + _sa('"' + _cid + '"') + ' = ' + _sa('%s') + ' '
        'and upper("' + _tip + '") = any(%s)' + _sit
    )
    _par_lig = [cidade, tipos] + ([situacao] if _sit else [])
    cur.execute(_sql_lig, _par_lig)
    ligs = cur.fetchall()

    _sql_poi = (
        "select p.id, coalesce(p.fonte,''), coalesce(p.nome,''), "
        "       coalesce(p.endereco,''), coalesce(lr.logradouro,''), "
        "       coalesce(lr.numero,''), st_y(p.pt_geo::geometry), "
        "       st_x(p.pt_geo::geometry), "
        # A FORCA DO ENDERECO VIAJA COM O POI. Sem ela a regua nao consegue
        # distinguir a porta que a fonte publicou da porta que foi deduzida
        # da propria coordenada — ver `TETO_ENDERECO_INDICIO`.
        "       coalesce(lr.forca,'') = 'prova' as end_prova "
        "  from radar_comercial.pois p "
        "  left join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id "
        " where p.fundido_em is null and p.pt_geo is not null "
        "   and " + _sa("p.cidade") + " = " + _sa("%s") + _corte
    )
    cur.execute(_sql_poi, [cidade])
    pois = cur.fetchall()

    # OS DOIS LADOS, EM VOZ ALTA. O log imprimia so o numero de PARES — e
    # 1.911.705 pares, sem saber que vieram de 88.767 ligacoes e 115.518 POIs,
    # parece que o cruzamento carregou o estado inteiro. Numero que ninguem
    # consegue conferir e numero que nao serve.
    _log("   %d ligações %s e %d POIs entraram no cruzamento (cidade %s)"
         % (len(ligs), "/".join(tipos), len(pois), cidade))

    if poligono is not None:
        _n_lig, _n_poi = len(ligs), len(pois)
        # ligs: (lig, via, num, tipo, LAT, LON)
        # pois: (id, fonte, nome, end, via, num, LAT, LON, end_prova)
        # `_n_poi` e nao `_np`: `_np` e o numpy importado nesta funcao — um local
        # com esse nome o sombreava e estourava em `_np.empty` logo abaixo.
        ligs = [r for r in ligs
                if area_utils.ponto_no_poligono(r[4], r[5], poligono)]
        pois = [r for r in pois
                if area_utils.ponto_no_poligono(r[6], r[7], poligono)]
        _log("   recorte pela area %r: %d→%d ligacoes · %d→%d POIs"
             % (area, _n_lig, len(ligs), _n_poi, len(pois)))

    linhas = []
    if ligs and pois:
        lat0 = _math.radians(sum(r[6] for r in pois) / len(pois))
        kx = 111320.0 * _math.cos(lat0)          # metros por grau de longitude
        ky = 110540.0                            # metros por grau de latitude
        pxy = _np.empty((len(pois), 2))
        pxy[:, 0] = [r[7] * kx for r in pois]    # x = lon
        pxy[:, 1] = [r[6] * ky for r in pois]    # y = lat
        arvore = _cKDTree(pxy)
        for (lg, via_l, num_l, tipo_l, llat, llon) in ligs:
            lx, ly = llon * kx, llat * ky
            for i in arvore.query_ball_point((lx, ly), raio):
                (pid, fonte, nome_p, end_p, via_p, num_p, plat, plon,
                 end_prova) = pois[i]
                metros = _math.hypot(pxy[i, 0] - lx, pxy[i, 1] - ly)
                linhas.append((lg, via_l, num_l, tipo_l, pid, fonte, nome_p,
                               end_p, via_p, num_p, metros, llat, llon,
                               plat, plon, end_prova))
    _log("   %d pares ligação×POI a até %.0f m (cKDTree em memória) · %.1f s"
         % (len(linhas), raio, time.time() - t0))

    telhado = Telhado()
    _log("   %d tile(s) georreferenciado(s) no disco" % len(telhado.tiles))

    # A RARIDADE DOS NOMES DA CIDADE, uma vez. `regra_vinculo.parecidos` se
    # recusa a trabalhar sem ela — sem os pesos, "Pizzaria" casaria com
    # "Pizzaria" e o criterio do nome viraria o vazamento novo.
    _log("   %d tokens de nome pesados" % rv.carregar_pesos(con, cidade))

    # AS FONTES QUE EXISTEM AGORA — o denominador da adesão. Ele vai gravado
    # junto para o número não mentir quando uma fonte nova entrar depois.
    cur.execute("""select count(distinct coalesce(fonte,'')) from radar_comercial.pois
                    where fundido_em is null and upper(coalesce(cidade,''))=upper(%s)""",
                (cidade,))
    fontes_no_momento = int(cur.fetchone()[0] or 0)

    por_ligacao = defaultdict(list)
    placar = Counter()
    for (lig, via_l, num_l, _tipo, poi_id, fonte, _nome_poi, end_poi,
         via_poi, num_poi, metros, llat, llon, plat, plon,
         end_prova) in linhas:
        # O NUMERO QUE A FONTE PUBLICOU, cru — `_num` normaliza para comparar
        # e `rv.numero_limpo` tem regra propria (zero a esquerda, lixo de sete
        # digitos), entao o veto recebe o original.
        num_p = num_poi or end_poi
        # A VIA DO POI VEM DA PENEIRA DE ENDEREÇO quando ela resolveu; o campo
        # `endereco` do POI é texto solto, do jeito que a fonte escreveu.
        via_p = _via(via_poi or end_poi)
        num_p = _num(num_poi) or _num(re.sub(r"^\D+", "", end_poi))

        mesmo_end = bool(via_l and via_p and _via(via_l) == via_p)
        mesmo_num = bool(num_l and num_p and _num(num_l) == num_p)
        perto = bool(metros is not None and metros <= PERTO_M)

        # A RUA E A UNICA EXIGENCIA BARATA. O resto — numero, 10 m, telhado,
        # nome — e decidido por `regra_vinculo.aceitar`, depois, com a ligacao
        # inteira na mao: o criterio do nome precisa saber quem JA entrou.
        #
        # O QUE ISTO SUBSTITUI, e por que: a condicao anterior era
        # `mesmo_end or mesmo_num or perto`, um OU com `perto` valendo ate
        # 20 m. Dentro de 20 m, portanto, o endereco nunca era testado —
        # qualquer POI do quarteirao entrava. Medido em Canoas: dos 350.494
        # vinculos gravados, so 21,8% batiam rua E numero, e 256.548 (73%)
        # juntavam um POI que PUBLICA outro numero de porta. Em 81,6% das
        # ligacoes julgadas, as fotos que a IA olhou eram de outro imovel.
        if not mesmo_end:
            placar["descartado_rua_diferente"] += 1
            continue
        por_ligacao[lig].append({
            "poi": poi_id, "fonte": fonte, "nome": _nome_poi,
            "mesma_rua": mesmo_end, "mesmo_numero": mesmo_num,
            "perto20": perto, "metros": metros,
            "llat": llat, "llon": llon, "plat": plat, "plon": plon,
            "end_prova": end_prova, "mesmo_telhado": False,
            "telhado_comercial": False,
            "contradiz": rv.contradiz_numero(num_p, num_l, end_prova)})

    # OS FINALISTAS DE CADA LIGACAO, E SO ELES, VAO AO TELHADO.
    #
    # A lista ja esta fechada pelos criterios baratos; o telhado entra para
    # desempatar vizinho e reforcar a confianca dos que serao gravados de
    # qualquer forma. Rodar antes, em todos os pares, era o custo que fazia o
    # cruzamento residencial de Canoas levar horas.
    registros = []
    finalistas = 0

    # A ORDEM DAS LIGACOES E GEOGRAFICA, E NAO A DO DICIONARIO.
    #
    # O laco abaixo le a segmentacao do tile que cobre cada ponto, e so
    # `SEGMENTACOES_VIVAS` (64) ficam na memoria — o resto volta do .npz em
    # disco. Em ordem de dicionario as ligacoes consecutivas caem em bairros
    # diferentes: medido em 06/09/2026, amostras seguidas do arquivo aberto
    # pulavam de -29,89 a -29,94 de latitude. O cache nunca acertava e o
    # processo releu 4 GB de .npz.
    #
    # Ordenar pela MESMA celula do indice de tiles (`CELULA_GRAUS`, ~1,1 km)
    # poe as ligacoes vizinhas em sequencia: o tile que a primeira carregou
    # serve para as centenas seguintes. Nao muda um vinculo sequer — a eleicao
    # do vinculo do POI e feita depois, em SQL, por `confianca desc`.
    _g = telhado.CELULA_GRAUS

    def _celula_da_ligacao(item):
        # Todos os candidatos de uma ligacao trazem a coordenada dela, entao o
        # primeiro basta.
        c = item[1][0]
        return (int(c["llat"] // _g), int(c["llon"] // _g))

    for lig, cands in sorted(por_ligacao.items(), key=_celula_da_ligacao):
        # O TELHADO SO ONDE ELE DECIDE. E o teste caro — contorna predio em
        # foto aerea —, e a regra so precisa dele para o candidato que ja
        # falhou no numero e nos 10 m e nao esta vetado. Quem entrou pelo
        # numero recebe o telhado depois, so para a confianca.
        for c in cands:
            precisa = (not c["mesmo_numero"]
                       and not (c["metros"] is not None
                                and c["metros"] < rv.PERTO_M)
                       and not c["contradiz"])
            if precisa:
                mt, tc = telhado.julgar(c["llat"], c["llon"],
                                        c["plat"], c["plon"])
                c["mesmo_telhado"], c["telhado_comercial"] = mt, tc
                finalistas += 1

        aceitos = rv.aceitar(cands)
        if not aceitos:
            placar["ligacao_sem_candidato_valido"] += 1
            continue

        # O TELHADO DOS ACEITOS QUE AINDA NAO O TEM. Vai gravado na coluna e
        # entra na regua de confianca — mas nao decidiu nada acima.
        escolhidos = [c for c in cands if c["poi"] in aceitos]
        for c in escolhidos:
            if not c["mesmo_telhado"] and not c["telhado_comercial"]:
                mt, tc = telhado.julgar(c["llat"], c["llon"],
                                        c["plat"], c["plon"])
                c["mesmo_telhado"], c["telhado_comercial"] = mt, tc
                finalistas += 1

        aderentes = len({c["fonte"] for c in escolhidos if c["fonte"]})
        for c in escolhidos:
            me, mn = c["mesma_rua"], c["mesmo_numero"]
            pe, mt, tc = c["perto20"], c["mesmo_telhado"], c["telhado_comercial"]
            ok = sum((me, mn, pe, mt, tc))
            if not c["end_prova"] and (me or mn):
                placar["endereco_so_indicio"] += 1
            registros.append((base_id, str(lig), c["poi"], me, mn, pe, mt, tc,
                              c["metros"], ok,
                              confianca_de(me, mn, pe, mt, tc, c["end_prova"]),
                              aderentes, fontes_no_momento, c["fonte"] or None))
            placar["criterios_%d" % ok] += 1
            placar["aceito_por_" + aceitos[c["poi"]]] += 1
            for chave, valor in (("mesmo_endereco", me), ("mesmo_numero", mn),
                                 ("ate_20m", pe), ("mesmo_telhado", mt),
                                 ("telhado_comercial", tc)):
                if valor:
                    placar[chave] += 1
    _log("   telhado julgado em %d finalistas (e nao nos %d pares)"
         % (finalistas, len(linhas)))
    if placar.get("ligacao_com_muitos_no_mesmo_endereco"):
        _log("   %d ligação(ões) com mais de %d POIs no mesmo endereço — "
             "prédio com várias lojas, todos vinculados"
             % (placar["ligacao_com_muitos_no_mesmo_endereco"],
                MAX_FRACOS_POR_LIGACAO))

    _log("   %d ligações com candidato · %d vínculos a gravar"
         % (len(por_ligacao), len(registros)))
    if telhado.sondados:
        _log("   %d ligações acharam o telhado pelo anel (hidrômetro na calçada)"
             % telhado.sondados)
    if telhado.sem_cobertura:
        _log("   %d pares sem tile cobrindo os dois pontos — os dois critérios"
             % telhado.sem_cobertura)
        _log("   de telhado ficam falsos neles. É pedido de captura, não erro.")

    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar)")
        con.close()
        return {"pares": len(linhas), "vinculos": len(registros), **dict(placar)}

    from psycopg2.extras import execute_values
    cur.execute("select count(*) from radar_comercial.ligacao_poi where id_base = %s",
                (base_id,))
    antes = int(cur.fetchone()[0] or 0)

    # SEM TILE, O TELHADO NAO E ATUALIZADO — ele e PRESERVADO.
    #
    # O DEFEITO QUE ISTO EVITA, visto em 08/09/2026: as 38 pastas de captura
    # de Canoas estavam com `tiles_z20` vazio, e `Telhado` carregou zero tiles.
    # Nesse estado `julgar()` devolve (False, False) para todo par — nao porque
    # os telhados sejam diferentes, mas porque nao ha imagem para olhar.
    #
    # O upsert grava por cima. Uma corrida do cruzamento com o disco sem tiles
    # zeraria os 46.395 `mesmo_telhado` verdadeiros que ja estavam na tabela,
    # calculados quando as imagens existiam — e ninguem veria: a coluna nao
    # ficaria nula, ficaria FALSA, que e uma afirmacao, e nao uma lacuna.
    #
    # Entao: com tile, grava o que mediu; sem tile, mantem o que estava la.
    _tel = ("excluded.mesmo_telhado" if telhado.tiles
            else "radar_comercial.ligacao_poi.mesmo_telhado")
    _telc = ("excluded.telhado_comercial" if telhado.tiles
             else "radar_comercial.ligacao_poi.telhado_comercial")
    if not telhado.tiles:
        _log("   ⚠️  nenhum tile no disco: mesmo_telhado/telhado_comercial")
        _log("      ficam COMO ESTAVAM nos vinculos que ja existiam.")
    execute_values(cur, ("""
        insert into radar_comercial.ligacao_poi
            (id_base, ligacao, poi_id, mesmo_endereco, mesmo_numero, ate_20m,
             mesmo_telhado, telhado_comercial, metros, criterios_ok, confianca,
             fontes_aderentes, fontes_no_momento, fonte_poi)
        values %%s
        on conflict (id_base, ligacao, poi_id) do update set
            mesmo_endereco = excluded.mesmo_endereco,
            mesmo_numero = excluded.mesmo_numero,
            ate_20m = excluded.ate_20m,
            mesmo_telhado = %(tel)s,
            telhado_comercial = %(telc)s,
            metros = excluded.metros,
            criterios_ok = excluded.criterios_ok,
            confianca = excluded.confianca,
            fontes_aderentes = excluded.fontes_aderentes,
            fontes_no_momento = excluded.fontes_no_momento,
            fonte_poi = excluded.fonte_poi,
            gerado_em = now()
    """ % {"tel": _tel, "telc": _telc}), registros, page_size=1000)
    con.commit()

    # O VÍNCULO DE MAIOR CONFIANÇA VOLTA PARA O POI. É o que faz o mapa mostrar
    # "este ponto é a ligação tal" sem uma segunda consulta por POI.
    cur.execute("""
        update radar_comercial.pois p
           set id_base = v.id_base, id_ligacao_base = v.ligacao
          from (select distinct on (poi_id) poi_id, id_base, ligacao
                  from radar_comercial.ligacao_poi
                 where id_base = %s
                 order by poi_id, confianca desc, metros nulls last) v
         where p.id = v.poi_id
    """, (base_id,))
    marcados = cur.rowcount
    con.commit()

    cur.execute("select count(*) from radar_comercial.ligacao_poi where id_base = %s",
                (base_id,))
    depois = int(cur.fetchone()[0] or 0)
    _log("   ligacao_poi: %d → %d · %d POIs receberam a ligação de maior confiança"
         % (antes, depois, marcados))
    con.close()
    return {"pares": len(linhas), "vinculos": len(registros), **dict(placar)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Cruza a base do cliente com os POIs, ancorado na ligação")
    p.add_argument("--base", type=int, required=True)
    p.add_argument("--cidade", required=True)
    p.add_argument("--raio", type=float, default=RAIO_BUSCA_M,
                   help="raio de busca em metros (o critério de perto é 20 m)")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--area", default="",
                   help="nome da area; recorta pelo desenho. Sem ela, a cidade toda.")
    p.add_argument("--tipos", default="",
                   help="categorias a cruzar, separadas por virgula (ex.: RESIDENCIAL). "
                        "Sem ela, os tipos_comerciais declarados da base.")
    p.add_argument("--situacao", default="",
                   help="filtra sit_ligacao da base (ex.: Ativa).")
    a = p.parse_args(argv)
    _log("▶ vínculo ancorado na ligação · %s" % a.cidade)
    saida = cruzar(a.base, a.cidade, a.aplicar, a.raio, a.limite, a.area,
                   a.tipos, a.situacao)
    for k, v in sorted(saida.items()):
        if isinstance(v, int):
            _log("      %-26s %7d" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
