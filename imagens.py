# -*- coding: utf-8 -*-
"""
imagens.py — de onde vêm os bytes de uma imagem.

Desde 12/08/2026 os bytes moram no **Storage** da pilha, não no Postgres. Byte
de imagem dentro do banco faz todo backup, toda restauração e toda replicação
carregarem 6,3 GB a mais, para sempre — e imagem é justamente o dado que não
precisa de transação.

O banco guarda o caminho (`storage_path`); os bytes vêm por HTTP.

**Queda para o banco, de propósito.** Enquanto a coluna `dados` existir, uma
linha sem `storage_path` ainda é servida a partir dela. É isso que permite a
troca ser feita em fases: os 9 arquivos que leem imagem migram um a um, e
nenhum deles quebra no dia em que o outro ainda não migrou. Quando a coluna for
apagada (fase 3), a queda simplesmente deixa de encontrar linha e o caminho
único passa a ser o Storage.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
import endpoints

BUCKET = os.environ.get("SUPABASE_BUCKET", "comercialradar")


def _gateway() -> str:
    return endpoints.SUPABASE


def _chave() -> str:
    # `service_role` ignora a RLS do Storage. É a chave de PROCESSO DE SERVIDOR:
    # o pipeline e a API a usam, o navegador nunca.
    return (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def baixar(caminho: str, timeout: int = 60) -> bytes | None:
    """Bytes de um objeto do Storage. None se não existir ou a chave faltar."""
    chave = _chave()
    if not caminho or not chave:
        return None
    req = urllib.request.Request(
        f"{_gateway()}/storage/v1/object/{BUCKET}/{caminho}",
        headers={"apikey": chave, "Authorization": f"Bearer {chave}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def _de_linha(caminho: str | None, dados) -> bytes | None:
    if caminho:
        b = baixar(caminho)
        if b:
            return b
    return bytes(dados) if dados else None


def _tem_coluna(con, tabela: str, coluna: str) -> bool:
    """A coluna `dados` some na fase 3; consultá-la depois disso seria erro."""
    with con.cursor() as cur:
        cur.execute("""select 1 from information_schema.columns
                        where table_name=%s and column_name=%s limit 1""", (tabela, coluna))
        return cur.fetchone() is not None


def _buscar(con, tabela: str, onde: str, args: tuple, limite: int | None = None) -> list:
    """Lista de bytes das linhas que casarem, na ordem da consulta."""
    campos = "storage_path" + (", dados" if _tem_coluna(con, tabela, "dados") else "")
    sql = f"select {campos} from {tabela} where {onde}"
    if limite:
        sql += f" limit {int(limite)}"
    with con.cursor() as cur:
        cur.execute(sql, args)
        linhas = cur.fetchall()
    out = []
    for r in linhas:
        b = _de_linha(r[0], r[1] if len(r) > 1 else None)
        if b:
            out.append(b)
    return out


#: Maior largura guardada. E a largura que o CONSUMIDOR pede.
#:
#: `descrever_imagens.IMG_LARGURA` e 1024: a IA reduz para isso antes de olhar,
#: e o painel exibe menor ainda. Guardar 1280 era guardar 256 px que ninguem le
#: e que atravessam disco, backup e rede toda vez.
LARGURA_MAX = 1024

#: Qualidade do WebP. Medido em fachadas reais de Canoas (06/09/2026):
#:
#:      1280px WebP q80    101 KB   92% do original
#:      1024px WebP q80     70 KB   63%
#:       896px WebP q80     56 KB   51%
#:
#: 1024 em WebP economiza ~40% SEM PERDA PARA QUEM USA, porque e exatamente o
#: que a IA pede. Descer para 896 economizaria mais e ja seria abaixo do que
#: o consumidor pede — economia que o proximo a olhar paga.
WEBP_QUALIDADE = 80


def padronizar(dados: bytes, largura_max: int = LARGURA_MAX) -> tuple:
    """(bytes, tipo) prontos para o Storage: WebP, no maximo `largura_max` px.

    UM SO LUGAR CONVERTE. Cada fonte entrega o que entrega — o Street View sai
    em JPEG do navegador, o print do Airbnb em PNG, o tile em WebP — e sem um
    ponto comum cada uma escolheria formato e tamanho por conta, que e como se
    chega a 21 GB de imagem que ninguem abre no tamanho em que foi guardada.

    NAO E CRITICA: se o Pillow faltar ou o byte nao for imagem, devolve o
    original intacto. Guardar maior e pior que nao guardar.
    """
    try:
        from PIL import Image
    except Exception:                                          # noqa: BLE001
        return dados, "image/jpeg"
    try:
        import io as _io
        im = Image.open(_io.BytesIO(dados))
        if im.width > largura_max:
            alt = int(im.height * largura_max / im.width)
            im = im.resize((largura_max, alt), Image.LANCZOS)
        buf = _io.BytesIO()
        im.convert("RGB").save(buf, format="WEBP", quality=WEBP_QUALIDADE,
                               method=4)
        novo = buf.getvalue()
        # SO TROCA SE ENCOLHEU. Recomprimir um JPEG ja pequeno pode INCHAR —
        # medido: JPEG q85 sobre JPEG deu 116% do original.
        if novo and len(novo) < len(dados):
            return novo, "image/webp"
    except Exception:                                          # noqa: BLE001
        pass
    return dados, "image/jpeg"


def enviar(caminho: str, dados: bytes, tipo: str = "image/jpeg") -> bool:
    """Sobe um objeto para o Storage. `x-upsert` para reenvio não duplicar."""
    chave = _chave()
    if not chave or not dados:
        return False
    req = urllib.request.Request(
        f"{_gateway()}/storage/v1/object/{BUCKET}/{caminho}", data=dados, method="POST",
        headers={"apikey": chave, "Authorization": f"Bearer {chave}",
                 "Content-Type": tipo or "image/jpeg", "x-upsert": "true"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status in (200, 201)
    except urllib.error.HTTPError as e:
        return e.code == 409          # já existe = o byte está lá
    except Exception:
        return False


def gravar_streetview(poi_id: int, dados: bytes, lat, lng, con,
                      angulo: str = "facade", **extra) -> int | None:
    """Grava uma fachada: bytes no Storage, caminho no banco.

    Escrita e leitura precisam mudar JUNTAS. Na migração de 12/08/2026 eu movi
    só os leitores e deixei os escritores gravando na coluna `dados`, que já não
    existia — e o sintoma foi silencioso: `Capturados 0/4` num resumo sem uma
    linha de erro, porque a falha acontecia dentro do worker e virava "não
    capturou". Só apareceu quando a rodada ponta a ponta tentou capturar.

    Substitui a linha anterior do mesmo ângulo: recapturar existe para TROCAR a
    foto, e acumular versões só incharia a tabela e o bucket."""
    with con.cursor() as cur:
        cur.execute("DELETE FROM streetview_imgs WHERE poi_id=%s AND angulo=%s",
                    (poi_id, angulo))
        # `lat/lng` e o ALVO; `cam_*`, `heading` e `fov` sao a CAMERA (0012).
        # Sem a camera nao ha rumo, e sem rumo nao se projeta coordenada nenhuma
        # na imagem — foi o que impediu marcar os vizinhos do CNEFE na fachada.
        cur.execute("""INSERT INTO streetview_imgs (poi_id, bytes_tam, lat, lng, angulo,
                                                    data_captura, pano_id,
                                                    cam_lat, cam_lng, heading, fov)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (poi_id, len(dados), lat, lng, angulo,
                     extra.get("data_captura"), extra.get("pano_id"),
                     extra.get("cam_lat"), extra.get("cam_lng"),
                     extra.get("heading"), extra.get("fov")))
        sv_id = cur.fetchone()[0]
        # PADRONIZA ANTES DE SUBIR, e o tamanho gravado e o do byte que subiu.
        corpo, tipo = padronizar(dados)
        ext = "webp" if tipo == "image/webp" else "jpg"
        caminho = f"fachada/{sv_id % 100:02d}/{sv_id}.{ext}"
        if len(corpo) != len(dados):
            cur.execute("UPDATE streetview_imgs SET bytes_tam=%s WHERE id=%s",
                        (len(corpo), sv_id))
        if not enviar(caminho, corpo, tipo):
            # Sem o byte no Storage a linha seria uma promessa vazia: melhor
            # desfazer do que registrar imagem que não existe.
            cur.execute("DELETE FROM streetview_imgs WHERE id=%s", (sv_id,))
            return None
        cur.execute("UPDATE streetview_imgs SET storage_path=%s WHERE id=%s", (caminho, sv_id))
    con.commit()
    return sv_id


def gravar_foto(img_id: int, dados: bytes, con, tipo: str = "image/jpeg", **extra) -> bool:
    """Grava os bytes de uma foto já cadastrada em `images_urls`."""
    caminho = f"foto/{img_id % 100:02d}/{img_id}.jpg"
    if not enviar(caminho, dados, tipo):
        return False
    with con.cursor() as cur:
        cur.execute("""UPDATE images_urls
                          SET storage_path=%s, bytes_tam=%s, content_type=%s,
                              data_imagem=COALESCE(%s, data_imagem)
                        WHERE id=%s""",
                    (caminho, len(dados), tipo, extra.get("data_imagem"), img_id))
    con.commit()
    return True


def streetview_por_id(sv_id: int, con) -> bytes | None:
    r = _buscar(con, "streetview_imgs", "id = %s", (sv_id,))
    return r[0] if r else None


def streetview_do_poi(poi_id: int, con, angulo: str = "facade", limite: int = 1) -> list:
    return _buscar(con, "streetview_imgs",
                   "poi_id = %s and angulo = %s order by id", (poi_id, angulo), limite)


def fotos_do_poi(poi_id: int, con, limite: int = 4) -> list:
    return _buscar(con, "images_urls", "poi_id = %s order by id", (poi_id,), limite)


def fotos_do_poi_com_id(poi_id: int, con, limite: int = 4) -> list:
    """As fotos do Maps com o `id` e a DATA colados nos bytes.

    Existe porque `fotos_do_poi` devolve só a lista de bytes e DESCARTA em
    silêncio a linha cuja imagem não abre (ver `_buscar`). Quem casava essa
    lista com as datas por posição — `zip(bytes, datas)` — recebia a data da
    foto 1 grudada nos bytes da foto 2 assim que uma falhasse, sem erro nenhum.

    Isso não é detalhe cosmético: a leitura de fachada pesa cada foto pela
    IDADE dela em relação à data do Street View. Data trocada é peso trocado, e
    o veredito muda sem que nada acuse.
    """
    with con.cursor() as cur:
        cur.execute(f"""select id, data_imagem, storage_path
                              {", dados" if _tem_coluna(con, "images_urls", "dados") else ""}
                          from images_urls where poi_id = %s
                         order by id limit {int(limite)}""", (poi_id,))
        linhas = cur.fetchall()
    saida = []
    for r in linhas:
        b = _de_linha(r[2], r[3] if len(r) > 3 else None)
        if b:
            saida.append({"id": r[0], "data": r[1] or "sem data", "b": b})
    return saida
