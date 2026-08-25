# -*- coding: utf-8 -*-
"""Procedencia temporal: SNAPSHOT, COLLECTION e WORK — tres identidades distintas.

Ate a v3.3.0 existia uma assinatura so, e ela derivava dos PARAMETROS DE COLETA
(regiao+predicado, grade, bbox+strips, uf+qualidade). Isso identifica **como** se
coletou, nao **qual versao do mundo** se coletou: a mesma UF com a mesma grade em
agosto e em dezembro produzia a mesma assinatura, com milhares de estabelecimentos
diferentes por baixo. Pior: como o diretorio nao mudava e o marcador `DONE` seguia
la, a fonte podia ficar CONGELADA — dado velho, perfeitamente cacheado e
perfeitamente auditado como se fosse o snapshot atual.

    SOURCE VERSION  ─► snapshot_id     versao/conteudo real da fonte
    REQUEST         ─► collection_id   snapshot + bbox/filtros (o que se pediu)
    EXECUTION PLAN  ─► work_id         tiles/strips (como se materializou)

`observation_id = sha256(fonte | id_fonte | snapshot_id)`: `fonte+id_fonte`
identifica o OBJETO da fonte; com o snapshot, identifica a OBSERVACAO historica.
Mudar `strips` de 7 para 9 nao pode mudar a identidade de nenhum dado — muda o
`work_id` e mais nada.

MODOS (`--source-mode`)
  cache   nao consulta a rede; usa o snapshot ja resolvido em disco.
  latest  pergunta a fonte qual e a versao atual e coleta se houver novidade.
  pinned  exige um snapshot ja resolvido e recusa rodar se ele nao existir ou se
          a versao da fonte nao puder ser determinada — reprodutibilidade dura.
"""
import hashlib
import json
import os
import time
import urllib.request

MODOS = ("cache", "latest", "pinned")


def _ler_versao():
    """Fonte unica da versao: o arquivo VERSION. Constante duplicada em modulo vira
    numero fossil — foi assim que o User-Agent ficou anunciando 2.0 e 3.4 numa
    skill 3.6.0."""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip() or "desconhecida"
    except OSError:
        return "desconhecida"


ADAPTER_VERSAO = _ler_versao()
# Versao do PROCESSADOR: entra no fingerprint das etapas. Mudanca de algoritmo tem de
# invalidar artefato mesmo com snapshot e config identicos — a terceira versao que
# faltava (DATA, REQUEST e agora PROCESSOR).
PROCESSOR_VERSAO = {
    "init": "init:v2", "fetch": "fetch:v3", "raw": "raw:v3", "territory": "territory:v2",
    "normalize": "normalize:v4", "dedup": "dedup:evidencia:v3", "export": "export:v3",
    "map": "map:v2", "validate": "validate:v3",
}
ADAPTER_VERSAO_FONTE = {"osm": "osm_adapter:v5", "overture": "overture_adapter:v4",
                        "fsq": "fsq_adapter:v3", "ibge": "ibge_adapter:v2",
                        "ifood": "ifood_adapter:v1"}
UA = "a2l-extracao-poi-estadual/%s" % ADAPTER_VERSAO


def _h(*partes):
    blob = json.dumps([str(x) for x in partes], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def sha256_arquivo(path, limite_bytes=0):
    """SHA-256 do arquivo. `limite_bytes>0` le so o inicio (amostra declarada)."""
    h = hashlib.sha256()
    lido = 0
    with open(path, "rb") as fh:
        for bloco in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloco)
            lido += len(bloco)
            if limite_bytes and lido >= limite_bytes:
                return h.hexdigest(), lido
    return h.hexdigest(), lido


def agora():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cabecalhos(url, timeout=60):
    """HEAD da fonte: Last-Modified, ETag e Content-Length. Falha vira dict vazio —
    a ausencia e registrada, nunca inventada."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            h = dict(r.headers)
    except Exception as e:                                    # noqa: BLE001
        return {"erro": str(e)[:200]}
    return {"last_modified": h.get("Last-Modified"), "etag": h.get("ETag"),
            "content_length": h.get("Content-Length")}


def baixar_texto(url, timeout=60, limite=4096):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limite).decode("utf-8", "replace").strip()
    except Exception:                                          # noqa: BLE001
        return None


# --------------------------------------------------------------------- modelo
STATUS = ("verified_latest", "cached", "pinned", "indeterminate", "network_failed_fallback")


def snapshot(fonte, source_version, digest=None, algoritmo=None, escopo=None,
             status="indeterminate", **extra):
    """Identidade da VERSAO DA FONTE.

    `content_digest` + `digest_algorithm` + `digest_scope` em vez de um
    `content_sha256` que as vezes guardava `md5:`, as vezes `sha256-64mb:` e as vezes
    `shards:` — o nome mentia sobre o algoritmo e sobre o escopo.

    `resolution_status` nasce no RESOLVEDOR e diz como a versao foi obtida. E dele que
    `is_latest_at_collection` deve descender: `source_mode=latest` com a consulta
    falhando e fallback para o snapshot anterior NAO e "latest verificado"."""
    determinado = bool(source_version or digest)
    sid = _h("snapshot", fonte, source_version or "", algoritmo or "", digest or "")
    d = {"snapshot_id": sid if determinado else "indeterminado",
         "fonte": fonte,
         "source_version": source_version,
         "content_digest": digest,
         "digest_algorithm": algoritmo,
         "digest_scope": escopo,
         "resolution_status": status if determinado else "indeterminate",
         "determinado": determinado,
         "retrieved_at": agora()}
    d.update({k: v for k, v in extra.items() if v is not None})
    return d


def collection_id(snap, *pedido):
    """O QUE se pediu daquele snapshot: bbox, UF, filtros. Nao inclui plano de
    execucao — mudar o tamanho do tile nao muda o que foi pedido."""
    return _h("collection", snap.get("snapshot_id"), *pedido)


def ids_da_fonte(fonte, snap, cfg, bbox):
    """`collection_id`/`work_id` ESPECIFICOS por adapter.

    Ate a v3.4.0 toda fonte recebia (uf, bbox, osm_predicado, osm_sem_nome) na
    collection e (ov_tile_graus, fsq_strips) no work. Mudar `osm_sem_nome` gerava
    outra collection do FSQ; mudar `fsq_strips` gerava outro work do Overture. Em
    volume nacional isso e cache duplicado a toa."""
    bb = [round(float(b), 6) for b in bbox]
    if fonte == "osm":
        from .vendor.osm_pbf import regiao_da_uf
        col = collection_id(snap, "osm", regiao_da_uf(cfg.uf), cfg.uf, bb,
                            cfg.osm_predicado, cfg.osm_sem_nome)
        return col, work_id(col, ADAPTER_VERSAO_FONTE["osm"])
    if fonte == "overture":
        col = collection_id(snap, "overture", cfg.uf, bb, "type=place")
        return col, work_id(col, cfg.ov_tile_graus, ADAPTER_VERSAO_FONTE["overture"])
    if fonte == "fsq":
        col = collection_id(snap, "fsq", cfg.uf, bb)
        return col, work_id(col, cfg.fsq_strips, ADAPTER_VERSAO_FONTE["fsq"])
    if fonte == "ifood":
        # O escopo do iFood NAO e o bbox da UF: e a lista de sementes. Duas
        # cidades diferentes na mesma UF tem o mesmo bbox e sao coletas
        # distintas — por isso a colecao carrega o digest das sementes, que ja
        # vem no `source_version` do snapshot.
        col = collection_id(snap, "ifood", cfg.uf)
        return col, work_id(col, ADAPTER_VERSAO_FONTE["ifood"])
    col = collection_id(snap, "ibge", cfg.uf, cfg.malha_qualidade)
    return col, work_id(col, ADAPTER_VERSAO_FONTE["ibge"])


def work_id(colecao, *plano):
    """COMO se materializou: tiles, strips, chunks. Nenhum ID de dado depende disso;
    o diretorio depende, para que parte de um plano nunca se misture com a de outro."""
    return _h("work", colecao, *plano)


def exigir_determinado(cfg, snaps):
    """`pinned` so passa com a versao de TODAS as fontes resolvida."""
    if getattr(cfg, "source_mode", "cache") != "pinned":
        return
    vagas = sorted(f for f, s in snaps.items()
                   if f in cfg.fontes and not s.get("determinado"))
    if vagas:
        raise RuntimeError(
            "--source-mode pinned exige versao resolvida de cada fonte; "
            "indeterminada(s): %s. Rode uma vez com --source-mode latest." % ", ".join(vagas))


def consulta_permitida(cfg, ja_resolvido):
    """So `latest` toca a rede.

    DEFEITO CORRIGIDO (v3.6.0): `pinned` sem snapshot em disco devolvia True e ia
    buscar a versao corrente — `pinned` se autopinava. Um pin criado sozinho nao e
    pin. Criar o pin e operacao EXPLICITA: rode `--source-mode latest` uma vez."""
    return getattr(cfg, "source_mode", "cache") == "latest"


def exigir_pin(cfg, fonte, antigo):
    """`pinned` e `cache` exigem snapshot ja resolvido — nenhum dos dois materializa
    dado externo sob `snapshot_id='indeterminado'`."""
    modo = getattr(cfg, "source_mode", "cache")
    if modo == "latest" or (antigo and antigo.get("determinado")):
        return
    if fonte not in tuple(cfg.fontes) + ("ibge",):
        return
    raise RuntimeError(
        "--source-mode %s exige snapshot ja resolvido para `%s`, e nao ha nenhum em "
        "disco. Consultar a fonte aqui seria materializar bytes do mundo atual sob uma "
        "identidade que nao foi verificada. Rode uma vez com --source-mode latest."
        % (modo, fonte))


def baixar_verificando(url, destino, digest=None, algoritmo=None, chunk=1 << 20,
                       baixador=None):
    """Baixa para `.part`, VERIFICA o digest e so entao promove.

    DEFEITO CORRIGIDO (v3.6.0), reproduzido em execucao: com `sul-latest.osm.pbf` de
    AGOSTO em cache e snapshot resolvido de SETEMBRO, o download era pulado
    (`if exists and not force: return`) e o arquivo velho era promovido para dentro de
    `snapshots/<snapshot_setembro>/` — bytes antigos carimbados com identidade nova,
    exatamente a promessa que o snapshot deveria garantir.

    Verificar tambem protege contra a fonte ser atualizada ENTRE a leitura do checksum
    e o download."""
    parcial = destino + ".part"
    if baixador is not None:
        baixador(url, parcial)
    else:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=300) as r, open(parcial, "wb") as fh:
            while True:
                b = r.read(chunk)
                if not b:
                    break
                fh.write(b)
    if digest and algoritmo:
        h = hashlib.new(algoritmo)
        with open(parcial, "rb") as fh:
            for b in iter(lambda: fh.read(chunk), b""):
                h.update(b)
        obtido = h.hexdigest()
        if obtido != digest:
            os.remove(parcial)
            raise RuntimeError(
                "digest do download nao confere: esperado %s=%s, obtido %s. O blob NAO "
                "foi promovido ao snapshot — bytes nao verificados nunca recebem "
                "identidade." % (algoritmo, digest, obtido))
    os.replace(parcial, destino)
    return destino


# ------------------------------------------------------------------ store
def carregar(cfg, fonte):
    p = os.path.join(cfg.dir_fonte(fonte, "snapshots"), "resolvido.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def salvar(cfg, fonte, snap):
    p = os.path.join(cfg.dir_fonte(fonte, "snapshots"), "resolvido.json")
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=2)
    os.replace(p + ".tmp", p)
    hist = os.path.join(cfg.dir_fonte(fonte, "snapshots"), "%s.json" % snap["snapshot_id"])
    if not os.path.exists(hist):
        with open(hist, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=2)
    return snap


# ------------------------------------------------------------- resolvedores
def resolver_ibge(cfg, man):
    """Malha local: SHA-256 do arquivo. API: SHA-256 do artefato persistido.

    DEFEITO CORRIGIDO (v3.4.0): `--malha-parquet` nao entrava em identidade nenhuma.
    Trocar `a.parquet` por `b.parquet` mantinha `sig_ibge` e o hash de `init`
    identicos — o pipeline entendia que continuava sendo a mesma fonte."""
    path = cfg.malha_parquet or os.environ.get("DIVISOES_PARQUET", "")
    if path and os.path.exists(path):
        sha, _ = sha256_arquivo(path)
        return snapshot("ibge", "parquet_local", digest=sha, algoritmo="sha256",
                        escopo="full", status="verified_latest",
                        source_url=os.path.abspath(path))
    persistido = os.path.join(cfg.dir_fonte("ibge", cfg.sig_ibge()), "malha.parquet")
    if os.path.exists(persistido):
        sha, _ = sha256_arquivo(persistido)
        return snapshot("ibge", "api_ibge/%s" % cfg.malha_qualidade, digest=sha,
                        algoritmo="sha256", escopo="full", status="cached",
                        source_url="servicodados.ibge.gov.br/api/v3/malhas")
    # ainda nao baixada: identidade so depois do download (init resolve de novo)
    return snapshot("ibge", None, source_url="servicodados.ibge.gov.br/api/v3/malhas")


def resolver_osm(cfg, man, consultar):
    """Geofabrik publica `<arquivo>.md5` ao lado do `.pbf`. Esse checksum e a versao
    oficial: `sul-latest.osm.pbf` de agosto e de setembro tem o mesmo NOME e md5
    diferente. Sem rede, cai no sha256 do arquivo local."""
    from .vendor.osm_pbf import regiao_da_uf, url_regiao
    url = url_regiao(cfg.uf)
    extra = {"source_url": url, "regiao": regiao_da_uf(cfg.uf)}
    if consultar:
        md5 = baixar_texto(url + ".md5")
        cab = cabecalhos(url)
        if md5:
            extra.update({k: v for k, v in cab.items() if k != "erro"})
            return snapshot("osm", cab.get("last_modified") or "md5",
                            digest=md5.split()[0], algoritmo="md5", escopo="full",
                            status="verified_latest", **extra)
        if cab.get("last_modified"):
            extra.update({k: v for k, v in cab.items() if k != "erro"})
            return snapshot("osm", cab["last_modified"], status="verified_latest", **extra)
    local = os.path.join(cfg.dir_fonte("osm", "pbf"),
                         "%s-latest.osm.pbf" % regiao_da_uf(cfg.uf))
    if os.path.exists(local):
        sha, n = sha256_arquivo(local, limite_bytes=64 << 20)
        return snapshot("osm", "local", digest=sha, algoritmo="sha256",
                        escopo="first_64mb", status="network_failed_fallback",
                        content_length=os.path.getsize(local), amostra_bytes=n, **extra)
    return snapshot("osm", None, **extra)


def release_de_caminhos(caminhos, padrao="release/"):
    """Extrai a release mais recente de uma lista de caminhos `.../release/<v>/...`.
    Isolada para ser testavel sem rede."""
    vs = set()
    for c in caminhos or ():
        c = str(c)
        i = c.find(padrao)
        if i < 0:
            continue
        resto = c[i + len(padrao):].strip("/").split("/")
        if resto and resto[0]:
            vs.add(resto[0])
    return sorted(vs)[-1] if vs else None


def resolver_overture(cfg, man, consultar):
    """A versao da CLI e SOFTWARE, nao dataset. A release real vem, em ordem:
    `OVERTURE_RELEASE` no ambiente; listagem do prefixo publico no S3. Sem nenhuma
    das duas, o snapshot fica `indeterminado` — declarado, nunca presumido."""
    extra = {"source_url": "s3://overturemaps-us-west-2/release/"}
    rel = os.environ.get("OVERTURE_RELEASE")
    if rel:
        # release informada pelo ambiente e `specified`, nao `verified_latest`: nada
        # comprovou que ela e a mais recente.
        return snapshot("overture", rel, status="pinned", origem="OVERTURE_RELEASE", **extra)
    if consultar:
        try:
            import duckdb
            con = duckdb.connect()
            con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
            linhas = con.execute(
                "SELECT file FROM glob('s3://overturemaps-us-west-2/release/*/*')").fetchall()
            rel = release_de_caminhos([r[0] for r in linhas])
            con.close()
        except Exception as e:                                 # noqa: BLE001
            extra["erro_consulta"] = str(e)[:200]

        # PATCH LOCAL — 25/08/2026, comercialRadar. NAO E DA SKILL DE ORIGEM.
        #
        # O `glob` acima devolve ZERO LINHAS, sem levantar exceção: o DuckDB
        # ASSINA a requisição, e o bucket do Overture é público e responde à
        # listagem anônima. Sem erro para registrar e sem release para declarar,
        # o snapshot ficava `indeterminate` — e a etapa `dedup` recusava a
        # entrega, corretamente:
        #
        #     ERRO na etapa 'dedup': observacao sob snapshot indeterminado nas
        #     fontes overture. Resolva a versao da fonte (--source-mode latest)
        #     antes de gerar a entrega.
        #
        # Custou 38 minutos de RS e 3 de PI, os dois DEPOIS de processar tudo:
        # 1.065.018 pontos em 497 municípios, barrados no último passo por falta
        # de um nome de versão.
        #
        # A listagem HTTP do MESMO bucket funciona sem credencial. Não é outra
        # fonte — é o mesmo prefixo por outra porta, então a identidade
        # declarada continua sendo a real.
        if not rel:
            # O CATÁLOGO STAC, e NÃO a listagem do bucket. A diferença custou
            # uma rodada.
            #
            # A primeira versão deste patch lia
            # `s3.amazonaws.com/?list-type=2&prefix=release/` e pegava o maior
            # prefixo. Ele devolvia `2026-08-19.0` — que EXISTE no bucket e o
            # CLI RECUSA:
            #
            #     Error: Release '2026-08-19.0' is no longer available.
            #     Overture keeps only the last two monthly releases (~60 days)
            #
            # Prefixo em disco não é release publicada: há pastas em publicação,
            # e provavelmente restos das que saíram da janela de retenção.
            # Declarar uma delas como identidade da fonte é pior que não
            # declarar: o download inteiro falha, 56 tiles de 56, e o erro fala
            # de retenção quando a causa é a escolha da versão.
            #
            # O STAC é o que o próprio CLI consulta (`--stac`, o padrão), então
            # é a única lista cuja resposta o download vai honrar.
            try:
                import json as _json
                import re as _re
                import urllib.request
                with urllib.request.urlopen(
                        "https://stac.overturemaps.org/catalog.json", timeout=40) as r_:
                    cat = _json.load(r_)
                vs = []
                for l in cat.get("links", []):
                    if l.get("rel") != "child":
                        continue
                    m = _re.search(r"(\d{4}-\d{2}-\d{2}\.\d+)",
                                   "%s %s" % (l.get("title") or "", l.get("href") or ""))
                    if m:
                        vs.append(m.group(1))
                rel = sorted(vs)[-1] if vs else None
                if rel:
                    extra["origem_listagem"] = "stac_catalog"
            except Exception as e:                             # noqa: BLE001
                extra["erro_listagem_http"] = str(e)[:200]
    return snapshot("overture", rel,
                    status="verified_latest" if (rel and consultar) else
                    ("pinned" if rel else "indeterminate"), **extra)


def resolver_fsq(cfg, man, consultar, listar_releases=None):
    """A release do FSQ e um diretorio `dt=YYYY-MM-DD`. Ate a v3.3.0, `files.json`
    em cache fazia o pipeline nunca mais perguntar qual era a ultima — podia rodar
    em outubro servindo a release de agosto."""
    extra = {"source_url": "hf://datasets/foursquare/fsq-os-places/release"}
    if not consultar or listar_releases is None:
        return snapshot("fsq", None, **extra)
    try:
        rel, arquivos = listar_releases()
    except Exception as e:                                     # noqa: BLE001
        extra["erro_consulta"] = str(e)[:200]
        return snapshot("fsq", None, **extra)
    manifesto_sha = hashlib.sha256(
        "\n".join(sorted(arquivos or ())).encode("utf-8")).hexdigest()
    return snapshot("fsq", rel, digest=manifesto_sha[:32], algoritmo="sha256",
                    escopo="shard_manifest", status="verified_latest",
                    shards=len(arquivos or ()), **extra)


def resolver_ifood(cfg, man, consultar):
    """A versao da fonte iFood e a LISTA DE SEMENTES, nao uma release.

    O iFood nao publica release nem dump: o que existe e o estado do endpoint
    naquele instante. Fingir uma versao de servidor seria inventar identidade.
    O que de fato determina a coleta e o conjunto de merchant ids pedidos —
    entao e ele que assina o snapshot, pelo sha256 da lista ordenada.

    Consequencia deliberada: acrescentar uma cidade a enumeracao muda o
    snapshot e cria outra colecao. E o comportamento certo — o dado anterior
    continua valido para o escopo anterior, e nada e sobrescrito em silencio.
    """
    caminho = str(getattr(cfg, "ifood_ids", "") or "")
    extra = {"source_url": "https://marketplace.ifood.com.br/v1/merchants/{id}/extra",
             "sementes_arquivo": caminho or None}
    if not caminho or not os.path.exists(caminho):
        return snapshot("ifood", None, **extra)
    try:
        from .ifood import sementes_de_arquivo
        ids = sorted(set(str(x).strip() for x in sementes_de_arquivo(caminho)() if str(x).strip()))
    except Exception as e:                                     # noqa: BLE001
        extra["erro_consulta"] = str(e)[:200]
        return snapshot("ifood", None, **extra)
    if not ids:
        extra["erro_consulta"] = "arquivo de sementes vazio"
        return snapshot("ifood", None, **extra)
    dig = hashlib.sha256(chr(10).join(ids).encode("utf-8")).hexdigest()
    # `source_version` legivel por humano: quantas lojas, e o comeco do digest.
    return snapshot("ifood", "sementes:%d:%s" % (len(ids), dig[:12]),
                    digest=dig[:32], algoritmo="sha256", escopo="lista_de_ids",
                    status="verified_latest", sementes=len(ids), **extra)


def resolver_todas(cfg, man, bbox, listar_fsq=None):
    """Resolve snapshot + collection + work de cada fonte e persiste no manifesto."""
    out = {}
    for fonte in ("ibge",) + tuple(cfg.fontes):
        antigo = carregar(cfg, fonte)
        forcar = fonte in getattr(cfg, "refresh_fontes", ())
        consultar = forcar or consulta_permitida(cfg, bool(antigo and antigo.get("determinado")))
        if fonte == "ibge":
            snap = resolver_ibge(cfg, man)
        elif fonte == "osm":
            snap = resolver_osm(cfg, man, consultar)
        elif fonte == "overture":
            snap = resolver_overture(cfg, man, consultar)
        elif fonte == "ifood":
            snap = resolver_ifood(cfg, man, consultar)
        else:
            snap = resolver_fsq(cfg, man, consultar, listar_fsq)
        # `cache`/`pinned` NAO recalculam identidade com outro algoritmo: o snapshot
        # ja resolvido MANDA. Hash local serve para verificar integridade, nao para
        # substituir identidade — senao o mesmo arquivo tem dois snapshot_id
        # dependendo de como foi resolvido.
        if antigo and antigo.get("determinado") and not consultar:
            snap = dict(antigo, resolution_status=("pinned" if cfg.source_mode == "pinned"
                                                   else "cached"))
            salvar(cfg, fonte, snap)
        elif not snap.get("determinado") and antigo and antigo.get("determinado"):
            snap = dict(antigo, resolution_status="network_failed_fallback")
            salvar(cfg, fonte, snap)
        else:
            salvar(cfg, fonte, snap)
        # a exigencia e sobre o RESULTADO: nenhum modo materializa dado externo sob
        # `snapshot_id='indeterminado'`. Fonte que se resolve localmente (IBGE, pelo
        # sha do artefato) passa sem rede; fonte que so a rede determina, nao.
        if not snap.get("determinado"):
            exigir_pin(cfg, fonte, antigo)
        col, wk = ids_da_fonte(fonte, snap, cfg, bbox)
        out[fonte] = {"snapshot": snap, "collection_id": col, "work_id": wk}
    man.procedencia(out)
    exigir_determinado(cfg, {f: v["snapshot"] for f, v in out.items()})
    return out
