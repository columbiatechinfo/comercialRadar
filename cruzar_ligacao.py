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

`confianca` é `criterios_ok / 5`, sem peso escondido — está escrito na própria
tabela. Guardar os cinco separados é o que permite rever a régua depois sem
refazer o cruzamento.

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

import base_comum as bc

# O RAIO DE BUSCA É MAIOR QUE O CRITÉRIO, de propósito. `ate_20m` é um dos cinco
# critérios; se a busca parasse em 20 m, um POI a 35 m que bate endereço e
# número nunca seria visto — e ele é exatamente o caso interessante, o ponto
# cuja coordenada está torta mas cujo endereço está certo.
RAIO_BUSCA_M = 60.0
PERTO_M = 20.0

# Quantos candidatos por ligação. Guardar todos encheria a tabela de vínculos de
# confiança 0,2 que ninguém vai revisar.
MAX_POR_LIGACAO = 5

SQL_CANDIDATOS = """
    select l.num_ligacao::text,
           l.{via}, l.{numero}, l.{tipo},
           p.id, coalesce(p.fonte,''), coalesce(p.nome,''),
           coalesce(p.endereco,''),
           coalesce(lr.logradouro,''), coalesce(lr.numero,''),
           st_distance(l.geom, p.pt::geography) as metros,
           st_y(l.geom::geometry), st_x(l.geom::geometry),
           st_y(p.pt), st_x(p.pt)
      from (select id, fonte, nome, endereco,
                   st_setsrid(st_makepoint(
                       coalesce(maps_lng, lng_origem),
                       coalesce(maps_lat, lat_origem)), 4326) as pt
              from radar_comercial.pois
             where fundido_em is null
               and upper(coalesce(cidade,'')) = upper(%s)
               and coalesce(maps_lat, lat_origem) is not null
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
               and st_dwithin(c.geom, p.pt::geography, %s)
           ) l on true
      left join radar_comercial.logradouro_resolvido lr on lr.poi_id = p.id
     order by l.num_ligacao, metros
"""


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
    """Só os dígitos do número da porta. `1509-A` e `1509` são a mesma porta."""
    d = re.sub(r"\D", "", str(s or ""))
    return d.lstrip("0") or ""


class Telhado:
    """Os dois critérios que dependem de imagem — e o que fazer sem ela.

    Um tile é uma foto de satélite com a coordenada no nome do arquivo:
    `tile_r000_c000_-29.91955_-51.18093.png`. Para dizer se dois pontos caem no
    mesmo telhado, é preciso ter o tile que cobre os dois.

    Quando não há tile, os dois critérios ficam FALSOS e a contagem de "sem
    cobertura" sobe. Não é o mesmo que dizer que os telhados são diferentes, e a
    diferença aparece no relatório: um número de vínculos sem imagem é um pedido
    de captura, não um defeito do cruzamento.
    """

    # O tile cobre ~um quarteirão. Sem metadado de zoom no nome, mede-se pelo
    # que a captura produz hoje: z20, 640 px, ~0,11 m/px no equador.
    LADO_GRAUS = 0.0009

    def __init__(self, pasta: str = "capturas"):
        self.tiles = []
        for raiz, _, arquivos in os.walk(pasta):
            for a in arquivos:
                m = re.match(r"tile_r\d+_c\d+_(-?\d+\.\d+)_(-?\d+\.\d+)\.png$", a)
                if m:
                    self.tiles.append((float(m.group(1)), float(m.group(2)),
                                       os.path.join(raiz, a)))
        self.sem_cobertura = 0
        self._cache = {}

    def _tile_de(self, lat, lon):
        for tlat, tlon, caminho in self.tiles:
            if (abs(lat - tlat) <= self.LADO_GRAUS
                    and abs(lon - tlon) <= self.LADO_GRAUS):
                return (tlat, tlon, caminho)
        return None

    def _cor(self, tile, lat, lon):
        """A cor média num quadradinho ao redor do ponto."""
        chave = (tile[2], round(lat, 6), round(lon, 6))
        if chave in self._cache:
            return self._cache[chave]
        try:
            from PIL import Image
        except Exception:                                  # noqa: BLE001
            return None
        try:
            img = Image.open(tile[2]).convert("RGB")
            larg, alt = img.size
            # do canto superior esquerdo do tile para o pixel
            fx = (lon - (tile[1] - self.LADO_GRAUS)) / (2 * self.LADO_GRAUS)
            fy = ((tile[0] + self.LADO_GRAUS) - lat) / (2 * self.LADO_GRAUS)
            x, y = int(fx * larg), int(fy * alt)
            if not (0 <= x < larg and 0 <= y < alt):
                return None
            j = 6
            caixa = img.crop((max(0, x - j), max(0, y - j),
                              min(larg, x + j), min(alt, y + j)))
            px = list(caixa.getdata())
            if not px:
                return None
            cor = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
        except Exception:                                  # noqa: BLE001
            cor = None
        self._cache[chave] = cor
        return cor

    def julgar(self, lat1, lon1, lat2, lon2):
        """Devolve (mesmo_telhado, telhado_comercial)."""
        if not self.tiles or lat1 is None or lat2 is None:
            self.sem_cobertura += 1
            return False, False
        t = self._tile_de(lat1, lon1)
        if not t or t != self._tile_de(lat2, lon2):
            self.sem_cobertura += 1
            return False, False
        c1, c2 = self._cor(t, lat1, lon1), self._cor(t, lat2, lon2)
        if not c1 or not c2:
            self.sem_cobertura += 1
            return False, False
        # MESMO TELHADO: as duas amostras têm a mesma cor, dentro de uma folga.
        # Telhado é superfície contínua; asfalto ao lado de telha muda muito.
        mesmo = all(abs(a - b) <= 28 for a, b in zip(c1, c2))
        # COMERCIAL: cobertura clara e sem cor — fibrocimento e metálica, que é
        # o que cobre galpão e loja de rua. Telha cerâmica é vermelha e puxa
        # residencial. Este limiar ainda NÃO foi calibrado contra fachada
        # conferida; é hipótese explícita, não medição.
        r, g, b = c1
        claro = (r + g + b) / 3 >= 120
        pouca_cor = max(r, g, b) - min(r, g, b) <= 26
        return mesmo, bool(mesmo and claro and pouca_cor)


def cruzar(base_id: int, cidade: str, aplicar: bool, raio: float,
           limite: int) -> dict:
    con = bc.conectar()
    cur = con.cursor()

    cur.execute("""
        select nome, tabela_dados, mapa_colunas, tipos_comerciais, estado
          from radar_comercial.base_cliente where id = %s
    """, (base_id,))
    r = cur.fetchone()
    if not r:
        _log("   base %s não existe" % base_id)
        con.close()
        return {}
    nome, tabela, mapa, tipos, estado = r
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

    # O LIMITE CORTA NA ENTRADA, E NAO NO FIM.
    #
    # Posto depois do `order by`, ele nao economiza nada: o Postgres precisa
    # calcular o join inteiro e ordenar tudo para saber quais sao as primeiras
    # N linhas. O primeiro ensaio ficou mais de dez minutos preso assim. Cortando
    # os POIs na entrada, um ensaio de 3.000 POIs faz o trabalho de 3.000 POIs.
    sql = SQL_CANDIDATOS.format(
        tabela=tabela, via=mapa["endereco"], numero=mapa["numero"],
        tipo=mapa["tipo_cliente"], cidade=mapa.get("cidade", "cidade"),
        corte=("limit %d" % int(limite)) if limite else "")

    t0 = time.time()
    cur.execute(sql, [cidade, cidade, tipos, raio])
    linhas = cur.fetchall()
    _log("   %d pares ligação×POI a até %.0f m · %.1f s"
         % (len(linhas), raio, time.time() - t0))

    telhado = Telhado()
    _log("   %d tile(s) de satélite no disco" % len(telhado.tiles))

    # AS FONTES QUE EXISTEM AGORA — o denominador da adesão. Ele vai gravado
    # junto para o número não mentir quando uma fonte nova entrar depois.
    cur.execute("""select count(distinct coalesce(fonte,'')) from radar_comercial.pois
                    where fundido_em is null and upper(coalesce(cidade,''))=upper(%s)""",
                (cidade,))
    fontes_no_momento = int(cur.fetchone()[0] or 0)

    por_ligacao = defaultdict(list)
    placar = Counter()
    for (lig, via_l, num_l, _tipo, poi_id, fonte, _nome_poi, end_poi,
         via_poi, num_poi, metros, llat, llon, plat, plon) in linhas:
        # A VIA DO POI VEM DA PENEIRA DE ENDEREÇO quando ela resolveu; o campo
        # `endereco` do POI é texto solto, do jeito que a fonte escreveu.
        via_p = _via(via_poi or end_poi)
        num_p = _num(num_poi) or _num(re.sub(r"^\D+", "", end_poi))

        mesmo_end = bool(via_l and via_p and _via(via_l) == via_p)
        mesmo_num = bool(num_l and num_p and _num(num_l) == num_p)
        perto = bool(metros is not None and metros <= PERTO_M)
        mesmo_tel, tel_com = telhado.julgar(llat, llon, plat, plon)

        ok = sum((mesmo_end, mesmo_num, perto, mesmo_tel, tel_com))
        if ok == 0:
            placar["descartado_sem_criterio"] += 1
            continue
        por_ligacao[lig].append(
            (ok, -(metros or 9e9), poi_id, fonte, mesmo_end, mesmo_num, perto,
             mesmo_tel, tel_com, metros))

    registros = []
    for lig, cands in por_ligacao.items():
        cands.sort(reverse=True)
        aderentes = len({c[3] for c in cands[:MAX_POR_LIGACAO] if c[3]})
        for (ok, _neg, poi_id, fonte, me, mn, pe, mt, tc, metros) in \
                cands[:MAX_POR_LIGACAO]:
            registros.append((base_id, str(lig), poi_id, me, mn, pe, mt, tc,
                              metros, ok, round(ok / 5.0, 4),
                              aderentes, fontes_no_momento, fonte or None))
            placar["criterios_%d" % ok] += 1
            for chave, valor in (("mesmo_endereco", me), ("mesmo_numero", mn),
                                 ("ate_20m", pe), ("mesmo_telhado", mt),
                                 ("telhado_comercial", tc)):
                if valor:
                    placar[chave] += 1

    _log("   %d ligações com candidato · %d vínculos a gravar"
         % (len(por_ligacao), len(registros)))
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
    execute_values(cur, """
        insert into radar_comercial.ligacao_poi
            (id_base, ligacao, poi_id, mesmo_endereco, mesmo_numero, ate_20m,
             mesmo_telhado, telhado_comercial, metros, criterios_ok, confianca,
             fontes_aderentes, fontes_no_momento, fonte_poi)
        values %s
        on conflict (id_base, ligacao, poi_id) do update set
            mesmo_endereco = excluded.mesmo_endereco,
            mesmo_numero = excluded.mesmo_numero,
            ate_20m = excluded.ate_20m,
            mesmo_telhado = excluded.mesmo_telhado,
            telhado_comercial = excluded.telhado_comercial,
            metros = excluded.metros,
            criterios_ok = excluded.criterios_ok,
            confianca = excluded.confianca,
            fontes_aderentes = excluded.fontes_aderentes,
            fontes_no_momento = excluded.fontes_no_momento,
            fonte_poi = excluded.fonte_poi,
            gerado_em = now()
    """, registros, page_size=1000)
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
    a = p.parse_args(argv)
    _log("▶ vínculo ancorado na ligação · %s" % a.cidade)
    saida = cruzar(a.base, a.cidade, a.aplicar, a.raio, a.limite)
    for k, v in sorted(saida.items()):
        if isinstance(v, int):
            _log("      %-26s %7d" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
