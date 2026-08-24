"""Descoberta do catalogo Cadastur via API CKAN do MTur."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict

HOST = "https://dados.turismo.gov.br"
API_BASE = HOST + "/api/3/action/package_search"
UA = {"User-Agent": "A2L-extracao-cadastur/3.0.0"}

_TRI = {"PRIMEIRO": 1, "SEGUNDO": 2, "TERCEIRO": 3, "QUARTO": 4,
        "1": 1, "2": 2, "3": 3, "4": 4}
_FIM = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


@dataclass(frozen=True)
class Recurso:
    dataset: str
    atividade: str
    recurso_id: str
    recurso_nome: str
    url: str
    formato_ckan: str
    ref_rotulo: str
    ref_ano: int
    ref_trimestre: int
    ref_periodo: str
    recurso_modificado_em: str = ""
    recurso_criado_em: str = ""

    def chave(self) -> tuple:
        return (self.dataset, self.ref_ano, self.ref_trimestre, self.recurso_id)


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def _pacotes() -> list[dict]:
    """Pagina o catalogo; nunca assume que rows=100 cobre tudo."""
    out: list[dict] = []
    start, rows = 0, 100
    while True:
        qs = urllib.parse.urlencode({"fq": "tags:Cadastur", "rows": rows, "start": start})
        result = _get(API_BASE + "?" + qs)["result"]
        lote = result.get("results", [])
        out.extend(lote)
        start += len(lote)
        if not lote or start >= int(result.get("count", len(out))):
            break
    return out


def parse_periodo(rotulo: str) -> tuple[int, int]:
    txt = rotulo.upper()
    m_ano = re.search(r"(19|20)\d{2}", txt)
    if not m_ano:
        raise ValueError(f"recurso sem ano no rotulo: {rotulo!r}")
    ano = int(m_ano.group(0))
    m_tri = re.search(r"(PRIMEIRO|SEGUNDO|TERCEIRO|QUARTO|[1-4])\s*[ºO°]?\s*TRIMESTRE", txt)
    if not m_tri:
        m_tri = re.search(r"TRIMESTRE\s*(?:DE\s*)?([1-4])\b", txt)
    tri = _TRI[m_tri.group(1)] if m_tri else 0
    return ano, tri


def descobrir(datasets: list[str] | None = None,
              desde: int | None = None,
              ate: int | None = None) -> list[Recurso]:
    out: list[Recurso] = []
    for p in _pacotes():
        if datasets and p["name"] not in datasets:
            continue
        for r in p.get("resources", []):
            rotulo = (r.get("name") or r.get("description") or "").strip()
            ano, tri = parse_periodo(rotulo)
            if desde and ano < desde:
                continue
            if ate and ano > ate:
                continue
            out.append(Recurso(
                dataset=p["name"], atividade=p.get("title", p["name"]).strip(),
                recurso_id=r["id"], recurso_nome=rotulo, url=r["url"],
                formato_ckan=(r.get("format") or "").upper(), ref_rotulo=rotulo,
                ref_ano=ano, ref_trimestre=tri,
                ref_periodo=f"{ano}-{_FIM[tri] if tri else '12-31'}",
                recurso_modificado_em=str(r.get("last_modified") or ""),
                recurso_criado_em=str(r.get("created") or ""),
            ))
    return sorted(out, key=Recurso.chave)


def como_dict(r: Recurso) -> dict:
    return asdict(r)
