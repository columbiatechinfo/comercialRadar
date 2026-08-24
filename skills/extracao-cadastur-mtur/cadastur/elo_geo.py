"""Elo CNPJ -> coordenada externa, com gate de ambiguidade e qualidade."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

SRID = 4674


def _ler(caminho: Path) -> pd.DataFrame:
    if caminho.suffix.lower() in (".parquet", ".pq"):
        return pd.read_parquet(caminho)
    return pd.read_csv(caminho, sep=None, engine="python", dtype=str)


def _cnpj14(s: pd.Series) -> pd.Series:
    dig = s.astype(str).str.replace(r"\D", "", regex=True)
    return dig.where(dig.str.len() == 14, "")


def elo(df: pd.DataFrame, saida: Path, cnpj_geocod: Path | None) -> dict:
    if cnpj_geocod is None:
        for nome in ["cadastur_geo.parquet", "ddl_elo_geo.sql", "geo_conflitos.csv", "geo_rejeitadas.csv"]:
            (saida / nome).unlink(missing_ok=True)
    if "cnpj" not in df.columns:
        pd.DataFrame(columns=["cnpj14", "_dataset", "_ref_periodo", "uf", "municipio"]).to_csv(
            saida / "chaves_cnpj.csv", index=False, sep=";")
        return {"gate_geo": "SEM_CNPJ_NO_RECORTE", "cnpj_distintos": 0,
                "contrato": str(saida / "chaves_cnpj.csv")}

    base = df.copy()
    base["cnpj14"] = _cnpj14(base["cnpj"])
    for c in ("uf", "municipio"):
        if c not in base.columns:
            base[c] = ""
    chaves = (base.loc[base.cnpj14 != "", ["cnpj14", "_dataset", "_ref_periodo", "uf", "municipio"]]
                  .drop_duplicates().sort_values(["_dataset", "cnpj14", "_ref_periodo"]))
    chaves.to_csv(saida / "chaves_cnpj.csv", index=False, sep=";")

    if cnpj_geocod is None:
        return {"gate_geo": "PENDENTE", "cnpj_distintos": int(chaves.cnpj14.nunique()),
                "contrato": str(saida / "chaves_cnpj.csv")}

    geo = _ler(Path(cnpj_geocod))
    candidatos_cnpj = [c for c in geo.columns if c.lower().startswith("cnpj")]
    if not candidatos_cnpj:
        raise ValueError("arquivo geocodificado sem coluna CNPJ")
    col_cnpj = candidatos_cnpj[0]
    geo["cnpj14"] = _cnpj14(geo[col_cnpj])
    lon_candidates = [c for c in geo.columns if c.lower() in ("lon", "longitude", "x")]
    lat_candidates = [c for c in geo.columns if c.lower() in ("lat", "latitude", "y")]
    if not lon_candidates or not lat_candidates:
        raise ValueError("arquivo geocodificado sem lon/lat reconheciveis")
    lon, lat = lon_candidates[0], lat_candidates[0]
    geo = geo.loc[:, ["cnpj14", lon, lat]].rename(columns={lon: "lon", lat: "lat"})
    geo["lon"] = pd.to_numeric(geo["lon"], errors="coerce")
    geo["lat"] = pd.to_numeric(geo["lat"], errors="coerce")

    ok = (geo.cnpj14.ne("") & geo.lon.between(-74.1, -28.8) & geo.lat.between(-33.9, 5.4)
          & ~((geo.lon == 0) & (geo.lat == 0)))
    rejeitadas = geo[~ok].copy()
    geo_ok = geo[ok].drop_duplicates(["cnpj14", "lon", "lat"])

    # CNPJ com mais de uma coordenada valida e' ambiguo: nao escolhe arbitrariamente.
    cont = geo_ok.groupby("cnpj14").size()
    ambiguos = set(cont[cont > 1].index)
    conflitos = geo_ok[geo_ok.cnpj14.isin(ambiguos)].sort_values(["cnpj14", "lon", "lat"])
    conflitos.to_csv(saida / "geo_conflitos.csv", index=False, sep=";")
    rejeitadas.to_csv(saida / "geo_rejeitadas.csv", index=False, sep=";")
    geo_unica = geo_ok[~geo_ok.cnpj14.isin(ambiguos)].drop_duplicates("cnpj14")

    casado = chaves.merge(geo_unica, on="cnpj14", how="left")
    casado.to_parquet(saida / "cadastur_geo.parquet", index=False)
    cob = float(casado.lon.notna().mean()) if len(casado) else 0.0
    (saida / "ddl_elo_geo.sql").write_text(f"""-- elo geocodificado (EPSG:{SRID})
CREATE TABLE cadastur.elo_geo (
  cnpj14 char(14), _dataset text, _ref_periodo text, uf char(2), municipio text,
  lon double precision, lat double precision,
  geom geometry(Point,{SRID}) GENERATED ALWAYS AS
       (ST_SetSRID(ST_MakePoint(lon,lat),{SRID})) STORED
);
CREATE INDEX ix_elo_geom ON cadastur.elo_geo USING gist (geom);
CREATE INDEX ix_elo_cnpj ON cadastur.elo_geo (cnpj14);
""", encoding="utf-8")
    return {"gate_geo": "OK" if cob > 0 else "SEM CASAMENTO",
            "cnpj_distintos": int(chaves.cnpj14.nunique()),
            "coordenadas_rejeitadas_no_gate": int(len(rejeitadas)),
            "cnpj_geocod_ambiguos": int(len(ambiguos)),
            "cobertura_geocodificada": round(cob, 4)}


def elo_parquet(parquet_base: Path, saida: Path, cnpj_geocod: Path | None) -> dict:
    """Executa o elo lendo somente as colunas necessarias dos Parquets consolidados."""
    partes: list[pd.DataFrame] = []
    for p in sorted(parquet_base.rglob("*.parquet")):
        try:
            import pyarrow.parquet as pq
            nomes = set(pq.read_schema(p).names)
        except Exception:
            nomes = set(pd.read_parquet(p).columns)
        if "cnpj" not in nomes:
            continue
        cols = [c for c in ["cnpj", "_dataset", "_ref_periodo", "uf", "municipio"] if c in nomes]
        d = pd.read_parquet(p, columns=cols)
        for c in ["uf", "municipio"]:
            if c not in d.columns:
                d[c] = ""
        partes.append(d[["cnpj", "_dataset", "_ref_periodo", "uf", "municipio"]])
    if not partes:
        vazio = pd.DataFrame(columns=["cnpj", "_dataset", "_ref_periodo", "uf", "municipio"])
        return elo(vazio, saida, cnpj_geocod)
    # Aqui a RAM cresce com as colunas de chave, nao com o bronze completo.
    base = pd.concat(partes, ignore_index=True)
    return elo(base, saida, cnpj_geocod)
