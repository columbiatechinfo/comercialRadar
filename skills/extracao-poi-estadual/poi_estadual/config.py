# -*- coding: utf-8 -*-
"""Configuracao da execucao: parametros, diretorios e hash de escopo POR ETAPA.

Por que hash por etapa e nao global: um hash unico invalidaria a coleta inteira
(centenas de milhares de POIs, dezenas de minutos) quando o usuario so mudou
`--min-conf` ou `--excluir`. Cada etapa declara de quais campos ela depende; o
resume so reaproveita artefato cujo hash de escopo bate com a config atual.

Consequencia pratica no principio fetch-once: `--excluir` NAO invalida `fetch`
(a coleta e no bbox da UF, o recorte e posterior), mas invalida `territory`.
"""
import dataclasses
import hashlib
import json
import os

UFS = ("AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT",
       "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO")
FONTES_VALIDAS = ("overture", "osm", "fsq", "ifood")
FORMATOS_VALIDOS = ("csv", "geoparquet")
DEDUP_VALIDOS = ("evidencia", "legado", "exato", "none")
OSM_PREDICADOS = ("ampliado", "classico")
SOURCE_MODES = ("cache", "latest", "pinned")

ETAPAS = ("init", "fetch", "raw", "territory", "normalize", "dedup", "export", "map", "validate")

# Campos da config que cada etapa consome. Mudou o campo -> artefato da etapa fica obsoleto.
# `ov_tile_graus`/`fsq_strips` sao PLANO DE EXECUCAO: nao mudam a identidade do
# dado (snapshot/collection), mas mudam o diretorio materializado. Entram no hash
# de coleta para o logico e o fisico nao divergirem — a v3.3.0 dizia
# "FETCH: reaproveitado" apontando para um diretorio de fonte novo e vazio.
_COLETA = ("uf", "fontes", "osm_predicado", "osm_sem_nome",
           "ov_tile_graus", "fsq_strips", "ifood_ids")
_TERR = _COLETA + ("excluir", "malha_qualidade", "simplificar_graus")
_NORM = _TERR + ("min_conf",)
_DEDUP = _NORM + ("dedup_modo", "dedup_raio_m", "dedup_sim_min", "dedup_sim_cross",
                  "dedup_jaccard_min", "dedup_diam_max_m", "dedup_ctx_raio_m",
                  "dedup_ctx_min", "dedup_semnome_modo", "dedup_celula_m", "dedup_halo_m")
ESCOPO_ETAPA = {
    "init":      ("uf", "excluir", "malha_qualidade"),
    "fetch":     _COLETA,
    "raw":       _COLETA,
    "territory": _TERR,
    "normalize": _NORM,
    "dedup":     _DEDUP,
    "export":    _DEDUP + ("formatos",),
    "map":       _DEDUP,
    "validate":  _DEDUP + ("formatos", "max_fusao_suspeita"),
}

# Precedencia de etapa: nao roda a etapa N sem a N-1 concluida.
PREDECESSORA = {
    "fetch": "init", "raw": "fetch", "territory": "raw", "normalize": "territory",
    "dedup": "normalize", "export": "dedup", "map": "export", "validate": "export",
}


class ConfigInvalida(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Config:
    """Parametros da execucao. Campos de dados entram no hash; campos de
    performance (budget, batch, threads) NAO — mudar batch nao invalida dado."""
    uf: str
    excluir: tuple = ()
    fontes: tuple = FONTES_VALIDAS
    # v3.0.0 — DEFAULT 0.0. O corte de 0,50 descartava 22,1% de Canoas e mais de um
    # terco de Santa Maria, em silencio. `confianca` e `confianca_classe` viajam no
    # dado: filtrar e decisao de quem consome, nao da coleta.
    min_conf: float = 0.0
    formatos: tuple = ("csv", "geoparquet")
    gerar_mapa: bool = False
    base_dir: str = "./execucao"

    # geometria — `simplificar_graus=0.0` preserva a fronteira municipal.
    malha_qualidade: str = "maxima"      # maxima | intermediaria
    simplificar_graus: float = 0.0

    # coleta OSM — `ampliado` cobre healthcare/craft/transporte/industria; `classico`
    # reproduz as 5 chaves da v2. `osm_sem_nome` admite POI sem `name`.
    osm_predicado: str = "ampliado"
    osm_sem_nome: bool = True
    # politica de frescor da fonte (nao entra no hash: e politica, nao dado)
    source_mode: str = "cache"           # cache | latest | pinned
    refresh_fontes: tuple = ()           # fontes a reconsultar nesta execucao

    # dedup — `evidencia` e o motor v3 (contexto + telefone + diametro)
    dedup_modo: str = "evidencia"
    dedup_raio_m: int = 30
    dedup_sim_min: int = 85
    dedup_sim_cross: int = 92
    dedup_jaccard_min: float = 0.60
    dedup_diam_max_m: float = 90.0
    dedup_ctx_raio_m: float = 200.0
    dedup_ctx_min: int = 3
    dedup_semnome_modo: str = "absorver"  # absorver | marcar
    # blocking espacial: a divisa municipal deixa de ser parede no matching.
    # `dedup_celula_m = 0` volta a particionar por municipio (comportamento v3.1).
    dedup_celula_m: float = 2000.0
    dedup_halo_m: float = 0.0            # 0 = auto (max(raio_forte_m, 250))

    # plano de execucao da coleta (muda o diretorio materializado, nao o dado)
    ov_tile_graus: float = 1.0
    fsq_strips: int = 7
    # iFood: o caminho do arquivo de sementes (merchant ids) e o dos
    # proxies. Entram no hash de coleta porque outro conjunto de ids e
    # outra coleta — reaproveitar o parquet anterior serviria o escopo
    # errado com cara de cache valido.
    ifood_ids: str = ""
    ifood_proxies: str = ""

    # gate semantico: fracao maxima de clusters com fusao suspeita antes de reprovar
    max_fusao_suspeita: float = 0.02

    # performance (fora do hash)
    budget_s: float = 0.0                # 0 = sem time-box; >0 = para e retoma
    ov_cap: int = 20000
    treat_batch: int = 40000
    clip_chunk: int = 120000
    duckdb_memory: str = "2.6GB"
    threads: int = 8
    malha_parquet: str = ""              # parquet IBGE local; vazio = baixa da API

    # ---------------------------------------------------------------- validacao
    def __post_init__(self):
        if self.uf.upper() not in UFS:
            raise ConfigInvalida("UF invalida: %s" % self.uf)
        object.__setattr__(self, "uf", self.uf.upper())
        object.__setattr__(self, "excluir", tuple(sorted(str(c).strip() for c in self.excluir)))
        for c in self.excluir:
            if not (c.isdigit() and len(c) == 7):
                raise ConfigInvalida("COD IBGE deve ter 7 digitos: %r" % c)
        f = tuple(sorted(set(x.strip().lower() for x in self.fontes)))
        if not f or any(x not in FONTES_VALIDAS for x in f):
            raise ConfigInvalida("fontes invalidas: %r (validas: %s)" % (self.fontes, FONTES_VALIDAS))
        object.__setattr__(self, "fontes", f)
        fm = tuple(sorted(set(x.strip().lower() for x in self.formatos)))
        if not fm or any(x not in FORMATOS_VALIDOS for x in fm):
            raise ConfigInvalida("formatos invalidos: %r" % (self.formatos,))
        object.__setattr__(self, "formatos", fm)
        if not 0.0 <= float(self.min_conf) <= 1.0:
            raise ConfigInvalida("min_conf fora de [0,1]: %s" % self.min_conf)
        if self.malha_qualidade not in ("maxima", "intermediaria"):
            raise ConfigInvalida("malha_qualidade: maxima|intermediaria")
        object.__setattr__(self, "refresh_fontes",
                           tuple(sorted(set(x.strip().lower() for x in self.refresh_fontes if x))))
        if any(x not in FONTES_VALIDAS for x in self.refresh_fontes):
            raise ConfigInvalida("refresh_fontes invalidas: %r" % (self.refresh_fontes,))
        if self.source_mode not in SOURCE_MODES:
            raise ConfigInvalida("source_mode: %s" % "|".join(SOURCE_MODES))
        if self.osm_predicado not in OSM_PREDICADOS:
            raise ConfigInvalida("osm_predicado: %s" % "|".join(OSM_PREDICADOS))
        if self.dedup_modo not in DEDUP_VALIDOS:
            raise ConfigInvalida("dedup_modo: %s" % "|".join(DEDUP_VALIDOS))
        if self.dedup_semnome_modo not in ("absorver", "marcar"):
            raise ConfigInvalida("dedup_semnome_modo: absorver|marcar")
        if not 0.0 <= float(self.dedup_jaccard_min) <= 1.0:
            raise ConfigInvalida("dedup_jaccard_min fora de [0,1]: %s" % self.dedup_jaccard_min)
        if not 0.0 <= float(self.max_fusao_suspeita) <= 1.0:
            raise ConfigInvalida("max_fusao_suspeita fora de [0,1]: %s" % self.max_fusao_suspeita)
        if float(self.dedup_celula_m) < 0 or float(self.dedup_halo_m) < 0:
            raise ConfigInvalida("dedup_celula_m/dedup_halo_m nao podem ser negativos")
        if 0 < float(self.dedup_celula_m) < 500:
            raise ConfigInvalida("dedup_celula_m muito pequena (%s): a celula deve ser bem "
                                 "maior que o halo" % self.dedup_celula_m)
        if float(self.dedup_diam_max_m) < float(self.dedup_raio_m):
            raise ConfigInvalida("dedup_diam_max_m (%s) < dedup_raio_m (%s)"
                                 % (self.dedup_diam_max_m, self.dedup_raio_m))
        if "fsq" in self.fontes and not os.environ.get("HF_TOKEN"):
            raise ConfigInvalida("fonte fsq exige HF_TOKEN no ambiente")
        # Falhar AQUI, e nao depois dos ~421 MB do OSM: a fonte ifood sem
        # sementes nao tem o que buscar, e descobrir isso na etapa `fetch`
        # custa a coleta inteira das outras fontes.
        if "ifood" in self.fontes and not str(self.ifood_ids).strip():
            raise ConfigInvalida(
                "fonte ifood exige --ifood-ids: os merchant ids vem da "
                "enumeracao (navegador), nao da skill")
        object.__setattr__(self, "base_dir", os.path.abspath(self.base_dir))

    # ------------------------------------------------------------------- hashes
    def campos_hash(self):
        d = dataclasses.asdict(self)
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}

    def hash_etapa(self, etapa):
        if etapa not in ESCOPO_ETAPA:
            raise ConfigInvalida("etapa desconhecida: %s" % etapa)
        campos = ESCOPO_ETAPA[etapa]
        d = self.campos_hash()
        alvo = {k: d[k] for k in campos}
        blob = json.dumps(alvo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def hashes(self):
        return {e: self.hash_etapa(e) for e in ETAPAS}

    # -------------------------------------------------------------- diretorios
    @property
    def cache(self):
        return os.path.join(self.base_dir, "cache")

    @property
    def saida(self):
        return os.path.join(self.base_dir, "saida")

    def dir(self, *partes):
        p = os.path.join(self.cache, *partes)
        os.makedirs(p, exist_ok=True)
        return p

    # ---- cache: identidade FISICA = identidade LOGICA (v3.3.0) -------------
    # Ate a v3.2.0 o manifesto sabia que uma etapa estava OBSOLETA e o worker
    # reaproveitava o arquivo assim mesmo (`if os.path.exists(outp): continue`).
    # Mudar `--min-conf` marcava `normalize` como obsoleta e os lotes `n_*.parquet`
    # da execucao anterior continuavam la. Agora o hash de escopo E parte do
    # CAMINHO: hash diferente => diretorio diferente => reuso acidental deixa de
    # ser possivel, sem `if hash mudou: apaga` espalhado por modulo.
    def dir_proc(self, etapa, *partes):
        """Artefato de PROCESSAMENTO, sob o hash de escopo da etapa."""
        return self.dir("proc", etapa, self.hash_etapa(etapa), *[str(x) for x in partes])

    def dir_fonte(self, fonte, *partes):
        """Artefato de FONTE. Separado do processamento: a mesma coleta alimenta
        qualquer recorte, `min_conf` ou parametro de dedup, sem novo download."""
        return self.dir("fontes", fonte, *[str(x) for x in partes])

    def sig(self, *partes):
        """Assinatura curta e deterministica de um conjunto de parametros."""
        blob = json.dumps([str(x) for x in partes], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def dir_colecao(self, fonte, colecao, trabalho=None, *partes):
        """`fontes/<fonte>/collections/<collection_id>/<work_id>/...`"""
        base = ["collections", colecao] + ([trabalho] if trabalho else [])
        return self.dir_fonte(fonte, *(base + [str(x) for x in partes]))

    def sig_ibge(self):
        return self.sig("ibge", self.uf, self.malha_qualidade)

    def sig_osm(self):
        from .vendor.osm_pbf import regiao_da_uf
        return self.sig("osm", regiao_da_uf(self.uf), self.osm_predicado, self.osm_sem_nome)

    def arq_saida(self, nome):
        os.makedirs(self.saida, exist_ok=True)
        return os.path.join(self.saida, nome)

    def preparar(self):
        for d in (self.base_dir, self.cache, self.saida):
            os.makedirs(d, exist_ok=True)
        return self

    @property
    def rotulo(self):
        """Sufixo dos arquivos de saida — generico, sem nome de UF fixo no codigo."""
        base = "POI_%s" % self.uf
        return base + ("_exceto_%dmun" % len(self.excluir) if self.excluir else "")


def salvar_atomico(df, path):
    """Escrita atomica: .tmp + os.replace. Retomada nunca ve arquivo parcial."""
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path
