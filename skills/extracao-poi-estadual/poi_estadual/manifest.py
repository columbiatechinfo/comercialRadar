# -*- coding: utf-8 -*-
"""Manifesto da execucao: estado por etapa, hash de escopo, versao das fontes e funil.

Corrige a retomada da v1.0.0, que considerava uma etapa pronta so porque o arquivo
existia — permitindo (a) reaproveitar dado gerado com outros parametros e (b) rodar
`finish` antes de todas as partes terminarem, entregando resultado parcial como
completo.

Duas travas:
  - `exigir(etapa)`  : a etapa anterior precisa estar `completed`.
  - `reutilizavel()` : artefato so vale se o hash de escopo da etapa bate com a config.
"""
import datetime
import hashlib
import json
import os
import platform
import sys

from .config import ETAPAS, PREDECESSORA

ARQ = "manifesto.json"


def _agora():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class EtapaBloqueada(RuntimeError):
    pass


class Manifesto:
    def __init__(self, cfg):
        self.cfg = cfg
        self.path = os.path.join(cfg.base_dir, ARQ)
        self.d = self._carregar()

    # ----------------------------------------------------------------- io
    def _novo(self):
        rid = self._novo_run_id()
        return {
            "workspace_id": "%s_%s" % (self.cfg.uf, _agora()[:10]),
            "run_id": rid,
            "started_at": _agora(),
            "finished_at": None,
            "skill": "extracao-poi-estadual",
            "skill_versao": _versao(),
            "criado_em": _agora(),
            "atualizado_em": _agora(),
            "python": sys.version.split()[0],
            "plataforma": platform.platform(),
            "config": self.cfg.campos_hash(),
            "hashes": self.cfg.hashes(),
            "fontes_versao": {},
            "assinaturas": {},
            "procedencia": {},
            "runs": [],
            "etapas": {e: {"status": "pending", "hash": None, "em": None, "contagens": {}}
                       for e in ETAPAS},
            "funil": [],
            "status": "running",
        }

    def _novo_run_id(self):
        return "%s_%s" % (self.cfg.uf, datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))

    def _carregar(self):
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
            d["config"] = self.cfg.campos_hash()
            d["hashes"] = self.cfg.hashes()
            # v3.4.0 — WORKSPACE e estavel; RUN e desta invocacao. Antes o `run_id` e o
            # `criado_em` do primeiro uso do diretorio viravam `observed_at` de coletas
            # feitas meses depois.
            d.setdefault("workspace_id", d.get("run_id"))
            d.setdefault("runs", [])
            if d.get("run_id"):
                d["runs"] = (d.get("runs") or [])[-49:] + [
                    {"run_id": d["run_id"], "started_at": d.get("started_at"),
                     "finished_at": d.get("finished_at"),
                     "skill_versao": d.get("skill_versao")}]
            d["run_id"] = self._novo_run_id()
            d["started_at"] = _agora()
            d["finished_at"] = None
            # a versao do CODIGO e desta execucao; a do workspace fica onde nasceu
            d.setdefault("workspace_criado_com", d.get("skill_versao"))
            d["skill_versao"] = _versao()
            for e in ETAPAS:
                d.setdefault("etapas", {}).setdefault(
                    e, {"status": "pending", "hash": None, "em": None, "contagens": {}})
            return d
        os.makedirs(self.cfg.base_dir, exist_ok=True)
        return self._novo()

    def salvar(self):
        self.d["atualizado_em"] = _agora()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.d, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        return self.path

    # -------------------------------------------------------------- estado
    def status(self, etapa):
        return self.d["etapas"][etapa]["status"]

    def reutilizavel(self, etapa):
        """True se a etapa concluiu com o MESMO escopo de config E a MESMA linhagem
        de dado (snapshot/collection/work de cada fonte)."""
        e = self.d["etapas"][etapa]
        if e["status"] != "completed" or e["hash"] != self.cfg.hash_etapa(etapa):
            return False
        fp = e.get("fingerprint")
        # Artefato anterior a v3.6.0 nao tem fingerprint. Trata-lo como confiavel
        # seria abrir mao da garantia recem-criada: legado = STALE, reconstroi uma vez.
        return fp is not None and fp == self.fingerprint(etapa)

    def obsoleta(self, etapa):
        e = self.d["etapas"][etapa]
        if e["hash"] is not None and e["hash"] != self.cfg.hash_etapa(etapa):
            return True
        fp = e.get("fingerprint")
        if e["status"] == "completed" and fp is None:
            return True                       # legacy_without_fingerprint
        return bool(fp) and fp != self.fingerprint(etapa)

    def exigir(self, etapa):
        """Gate de precedencia. Sem isso, `export` roda sobre dedup incompleto."""
        pred = PREDECESSORA.get(etapa)
        if pred and not self.reutilizavel(pred):
            motivo = ("obsoleta (parametros mudaram)" if self.obsoleta(pred)
                      else "status=%s" % self.status(pred))
            raise EtapaBloqueada(
                "etapa '%s' exige '%s' concluida com os mesmos parametros — %s. "
                "Rode: poi_estadual.py run --etapa %s (ou --ate %s)."
                % (etapa, pred, motivo, pred, etapa))

    def iniciar(self, etapa):
        self.exigir(etapa)
        if self.obsoleta(etapa):
            self.d["etapas"][etapa].update(
                {"status": "stale", "contagens": {}, "em": _agora()})
        self.d["etapas"][etapa]["status"] = "running"
        self.salvar()

    def concluir(self, etapa, **contagens):
        self.d["etapas"][etapa] = {
            "status": "completed",
            "hash": self.cfg.hash_etapa(etapa),
            "fingerprint": self.fingerprint(etapa),
            "em": _agora(),
            "contagens": {k: _num(v) for k, v in contagens.items()},
        }
        self.salvar()

    def parcial(self, etapa, **contagens):
        """Etapa time-boxed que parou no meio: fica `partial`, nunca `completed`."""
        self.d["etapas"][etapa].update(
            {"status": "partial", "em": _agora(),
             "contagens": {k: _num(v) for k, v in contagens.items()}})
        self.salvar()

    def falhar(self, etapa, erro):
        self.d["etapas"][etapa].update(
            {"status": "failed", "em": _agora(), "erro": str(erro)[:500]})
        self.d["status"] = "failed"
        self.salvar()

    def finalizar(self):
        ok = all(self.d["etapas"][e]["status"] == "completed"
                 for e in ETAPAS if self.d["etapas"][e]["status"] != "pending")
        self.d["status"] = "completed" if ok else "partial"
        self.d["finished_at"] = _agora()
        self.salvar()

    # -------------------------------------------------------------- versoes
    def versao_fonte(self, fonte, **kv):
        """Release/snapshot da fonte externa — sem isso a execucao nao e reproduzivel."""
        self.d["fontes_versao"].setdefault(fonte, {}).update(
            {k: _num(v) for k, v in kv.items()})
        self.salvar()

    # ---------------------------------------------------------------- funil
    def procedencia(self, nova=None):
        """snapshot/collection/work por fonte. `assinatura()` continua respondendo,
        agora com o `work_id` — que e o que nomeia o diretorio materializado."""
        self.d.setdefault("procedencia", {})
        if nova:
            self.d["procedencia"].update(nova)
            self.d["assinaturas"] = {f: v["work_id"] for f, v in self.d["procedencia"].items()}
            self.salvar()
        return self.d["procedencia"]

    def snapshot(self, fonte):
        return (self.procedencia().get(fonte) or {}).get("snapshot") or {}

    def colecao(self, fonte):
        p = self.procedencia().get(fonte) or {}
        return p.get("collection_id"), p.get("work_id")

    def assinaturas(self, novas=None):
        """Assinatura do cache de cada fonte. Entra no manifesto para o relatorio
        poder dizer QUAL coleta produziu a entrega."""
        self.d.setdefault("assinaturas", {})
        if novas:
            self.d["assinaturas"].update(novas)
            self.salvar()
        return self.d["assinaturas"]

    def assinatura(self, fonte):
        return self.assinaturas().get(fonte) or "sem_assinatura"

    def fingerprint(self, etapa):
        """Identidade da ENTRADA da etapa: config + linhagem do dado.

        DEFEITO CORRIGIDO (v3.5.0): `reutilizavel()` so comparava o hash da CONFIG.
        O sistema sabia que o snapshot da fonte tinha mudado (collection nova,
        diretorio novo) e, ao mesmo tempo, considerava o `fetch` antigo reutilizavel —
        a promessa "fonte nova => coleta nova" morria no orquestrador."""
        from .procedencia import PROCESSOR_VERSAO
        proc = self.procedencia() or {}
        # so as fontes que a etapa realmente consome: `init` e IBGE, nao OSM.
        deps = ("ibge",) if etapa == "init" else (("ibge",) + tuple(self.cfg.fontes))
        linhagem = sorted((f, (v or {}).get("collection_id"), (v or {}).get("work_id"))
                          for f, v in proc.items() if f in deps)
        blob = json.dumps([self.cfg.hash_etapa(etapa), linhagem,
                           PROCESSOR_VERSAO.get(etapa, "?")],
                          sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def retrieved_at(self, fonte):
        """Quando ESTA fonte foi resolvida — nao quando o workspace nasceu."""
        return self.snapshot(fonte).get("retrieved_at") or self.d.get("started_at")

    def funil(self, etapa, entrada, saida, motivo):
        """Invariante A2L: entrada = saida + exclusoes contabilizadas por motivo."""
        self.d["funil"].append({
            "etapa": etapa, "entrada": _num(entrada), "saida": _num(saida),
            "descartados": _num(entrada) - _num(saida), "motivo": motivo, "em": _agora()})
        self.salvar()

    def funil_fecha(self):
        """Cada linha do funil precisa fechar; retorna lista de divergencias."""
        ruins = []
        for f in self.d["funil"]:
            if f["entrada"] - f["saida"] != f["descartados"]:
                ruins.append(f)
        return ruins


def _num(v):
    try:
        if isinstance(v, bool):
            return v
        return int(v)
    except (TypeError, ValueError):
        return v


def _versao():
    from .procedencia import _ler_versao
    return _ler_versao()
