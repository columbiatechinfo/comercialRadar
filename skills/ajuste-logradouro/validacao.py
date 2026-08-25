# -*- coding: utf-8 -*-
"""
validacao.py — fail-fast antes de processamento, sem fallback silencioso
=======================================================================
v3.3 mantém fail-fast e adiciona escopo territorial/autoridade hierárquica:
- configuração estrita (chave desconhecida = ERRO);
- estrutura YAML validada antes de qualquer `.get()` em objetos de fonte;
- cabeçalho CSV duplicado = ERRO (pandas não pode renomear silenciosamente);
- aprendizado exige ao menos uma coordenada válida;
- `latest.json`, `runs/`, `.staging/` e `failed/` são áreas reservadas e não podem ser symlinks;
- caminhos de estado/leitura são validados contra colisão por `realpath`;
- schema é estrito também dentro de `fontes[]` e `colunas`;
- validação geográfica e projeção continuam usando a mesma fonte de verdade;
- aprendizado exige pyproj para não alterar o léxico com um motor métrico diferente.
"""
from __future__ import annotations
import csv
import os
import re

import numpy as np
import pandas as pd

OBRIGATORIAS = ("record_id", "logradouro")
ENUM = {"modo_numero": {"estrito", "compativel"}}
FAIXA = {"raio_aprendizado_m": (0.5, 500.0), "min_support_equiv": (1, 1000),
         "teto_bloco": (2, 10_000_000), "max_linhas_xlsx": (100, 1_048_575),
         "ratio_dominancia": (1.5, 1000.0), "min_freq_dominancia": (1, 10_000),
         "raio_independencia_m": (10.0, 5000.0),
         "reconciliacao_stale_s": (60.0, 604800.0),
         "retencao_runs": (0, 100000)}
BOOLEANOS = {"apenas_entre_fontes", "abortar_em_alerta", "xlsx", "parquet",
            "marcacao_por_fonte", "reparar_mojibake"}
BOOLEANOS_TOPO = {"hardening", "aprendizado"}
TOPO = {"projeto", "scope_id", "srid_metrico", "hardening", "aprendizado", "lexico_path",
        "vocab_path", "parametros", "fontes", "_base_dir", "_config_path"}
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SCOPE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
FONTE_KEYS = {"source_id", "arquivo", "encoding", "autoridade", "autoridade_nivel", "colunas"}
COLUNA_KEYS = {"record_id", "logradouro", "numero", "complemento", "lat", "lon", "scope_id"}
LIMIARES = {"coord_invalida": 0.20, "sem_numero": 0.40, "logradouro_vazio": 0.02,
            "record_id_duplicado": 0.0}


def _pct(n, d):
    return 0.0 if not d else round(n / d, 4)


def _real(base_dir: str, p: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.join(base_dir, p)))


def _estrutura_cfg(cfg) -> tuple[list[str], list]:
    """Retorna (erros, fontes_seguras) sem lançar por estrutura YAML inválida.

    v3.2.5: schema estrito em todos os níveis. Chave desconhecida nunca é
    ignorada porque um typo em `autoridade`/`colunas` pode mudar a semântica.
    """
    erros = []
    if not isinstance(cfg, dict):
        return [f"raiz do YAML deve ser objeto/mapa, veio {type(cfg).__name__}"], []
    if "aprendizado" not in cfg:
        erros.append("chave de topo 'aprendizado' é obrigatória e deve ser explícita (true/false)")
    scope = cfg.get("scope_id")
    if not isinstance(scope, str) or not SCOPE_ID_RE.fullmatch(scope or ""):
        erros.append("chave de topo 'scope_id' é obrigatória e deve ter 1–64 caracteres em [A-Za-z0-9_.:-]")
    elif scope != scope.upper():
        erros.append("scope_id deve estar em forma canônica MAIÚSCULA (ou código numérico, preferencialmente IBGE) para evitar duplicidade territorial por caixa")
    fontes = cfg.get("fontes")
    if not isinstance(fontes, list):
        erros.append(f"'fontes' deve ser lista, veio {type(fontes).__name__}")
        return erros, []
    seguras = []
    for i, f in enumerate(fontes):
        if not isinstance(f, dict):
            erros.append(f"fontes[{i}] deve ser objeto/mapa, veio {type(f).__name__}")
            continue
        desconhecidas = sorted(set(f) - FONTE_KEYS)
        for k in desconhecidas:
            erros.append(f"fontes[{i}] chave desconhecida: '{k}' (válidas: {sorted(FONTE_KEYS)})")
        m = f.get("colunas")
        if m is not None and isinstance(m, dict):
            for k in sorted(set(m) - COLUNA_KEYS):
                erros.append(f"fontes[{i}].colunas chave desconhecida: '{k}' (válidas: {sorted(COLUNA_KEYS)})")
        seguras.append(f)
    return erros, seguras


def validar_parametros(cfg: dict):
    erros, alertas = [], []
    if not isinstance(cfg, dict):
        return [f"raiz do YAML deve ser objeto/mapa, veio {type(cfg).__name__}"], alertas
    p_raw = cfg.get("parametros")
    p = {} if p_raw is None else p_raw
    conhecidos = set(ENUM) | set(FAIXA) | BOOLEANOS
    if not isinstance(p, dict):
        return [f"'parametros' deve ser objeto/mapa, veio {type(p).__name__}"], alertas
    for k, v in p.items():
        if k not in conhecidos:
            erros.append(f"parâmetro desconhecido: '{k}' (válidos: {sorted(conhecidos)})")
        elif k in ENUM and v not in ENUM[k]:
            erros.append(f"parâmetro '{k}' inválido: '{v}' — esperado {sorted(ENUM[k])}")
        elif k in FAIXA:
            lo, hi = FAIXA[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi:
                erros.append(f"parâmetro '{k}'={v!r} fora da faixa [{lo}, {hi}]")
        elif k in BOOLEANOS and not isinstance(v, bool):
            erros.append(f"parâmetro '{k}' deve ser booleano, veio {type(v).__name__}")
    for k in BOOLEANOS_TOPO:
        if k in cfg and not isinstance(cfg[k], bool):
            erros.append(f"chave de topo '{k}' deve ser booleano, veio {type(cfg[k]).__name__}")
    if "lexico_path" in cfg and (not isinstance(cfg["lexico_path"], str) or not cfg["lexico_path"].strip()):
        erros.append("lexico_path deve ser string não vazia")
    if "vocab_path" in cfg and cfg.get("vocab_path") is not None and (not isinstance(cfg["vocab_path"], str) or not cfg["vocab_path"].strip()):
        erros.append("vocab_path deve ser string não vazia ou null/omitido")
    for k in cfg:
        if k not in TOPO:
            erros.append(f"chave de topo desconhecida: '{k}'")
    if isinstance(p, dict) and p.get("reparar_mojibake") is True:
        try:
            import ftfy  # noqa: F401
        except Exception:
            erros.append("parametro reparar_mojibake=true exige ftfy instalado; fixe a versão no ambiente")
    return erros, alertas


def _cabecalho_csv(path: str, encoding: str) -> tuple[list[str], list[str]]:
    """Lê a primeira linha com csv.reader e detecta nomes repetidos ANTES do pandas."""
    with open(path, "r", encoding=encoding, newline="") as fh:
        row = next(csv.reader(fh), None)
    if row is None:
        return [], []
    # BOM pode sobreviver em utf-8 quando o chamador não usa utf-8-sig.
    row = [c.lstrip("\ufeff") if i == 0 else c for i, c in enumerate(row)]
    seen, dup = set(), []
    for c in row:
        if c in seen and c not in dup:
            dup.append(c)
        seen.add(c)
    return row, dup


def validar_destino(cfg: dict, out_dir: str) -> list[str]:
    """Guardas de filesystem e colisão de papéis.

    A raiz de saída controla `latest.json`, `runs/`, `.staging/` e `failed/`.
    Diretórios controlados nunca podem ser symlinks. O léxico (estado mutável)
    deve ser um caminho exclusivo: não pode compartilhar bytes com vocabulário,
    configuração nem qualquer entrada.
    """
    erros = []
    if not isinstance(cfg, dict):
        return ["configuração inválida: raiz não é objeto/mapa"]
    base = cfg.get("_base_dir", ".")
    out_abs = os.path.abspath(out_dir)
    out = os.path.realpath(out_abs)
    if os.path.lexists(out_abs) and os.path.islink(out_abs):
        # A raiz pode ser um alias intencional, mas o usuário precisa apontar
        # explicitamente para o destino real para que o confinamento seja auditável.
        erros.append(f"--out não pode ser symlink: {out_abs} -> {os.path.realpath(out_abs)}")
        return erros
    if os.path.exists(out) and not os.path.isdir(out):
        erros.append(f"--out aponta para arquivo, não diretório: {out}")
        return erros

    controladas_dir = [os.path.join(out, x) for x in ("runs", ".staging", "failed")]
    controladas_file = [os.path.join(out, "latest.json")]
    for ctrl in controladas_dir + controladas_file:
        if os.path.lexists(ctrl) and os.path.islink(ctrl):
            erros.append(f"área CONTROLADA da saída não pode ser symlink: {ctrl} -> {os.path.realpath(ctrl)}")
    for ctrl in controladas_dir:
        if os.path.exists(ctrl) and not os.path.isdir(ctrl):
            erros.append(f"área CONTROLADA deveria ser diretório: {ctrl}")
        if os.path.lexists(ctrl):
            try:
                if os.path.commonpath([os.path.realpath(ctrl), out]) != out:
                    erros.append(f"área CONTROLADA escapa de --out: {ctrl} -> {os.path.realpath(ctrl)}")
            except ValueError:
                erros.append(f"área CONTROLADA em filesystem incompatível com --out: {ctrl}")
    latest = controladas_file[0]
    if os.path.exists(latest) and not os.path.isfile(latest):
        erros.append(f"latest.json deve ser arquivo regular: {latest}")

    refs = []
    fontes = cfg.get("fontes") if isinstance(cfg.get("fontes"), list) else []
    for i, f in enumerate(fontes):
        if isinstance(f, dict) and f.get("arquivo"):
            refs.append((f"entrada[{f.get('source_id', i)}]", _real(base, f["arquivo"]), "entrada"))
    lex_val = cfg.get("lexico_path", "lexico.json")
    if isinstance(lex_val, str) and lex_val:
        refs.append(("léxico", _real(base, lex_val), "estado"))
    vocab_val = cfg.get("vocab_path")
    if isinstance(vocab_val, str) and vocab_val:
        refs.append(("vocabulário", _real(base, vocab_val), "leitura"))
    if cfg.get("_config_path"):
        refs.append(("configuração", os.path.realpath(os.path.abspath(cfg["_config_path"])), "leitura"))

    for nome, rp, _papel in refs:
        if rp == out:
            erros.append(f"{nome} colide com o diretório de saída: {rp}")
        if rp in controladas_file:
            erros.append(f"{nome} colide com arquivo CONTROLADO da saída: {rp}")
        for ctrl in controladas_dir:
            try:
                dentro = os.path.commonpath([rp, ctrl]) == ctrl
            except ValueError:
                dentro = False
            if dentro:
                erros.append(f"{nome} está dentro de área controlada da saída ({ctrl}): {rp}")

    # Estado mutável precisa de caminho canônico único. Symlink no próprio
    # lexico quebra o domínio do lock/atomic replace e portanto é proibido.
    lex_cfg = cfg.get("lexico_path", "lexico.json")
    if isinstance(lex_cfg, str) and lex_cfg:
        lex_abs = os.path.abspath(os.path.join(base, lex_cfg))
        if os.path.lexists(lex_abs) and os.path.islink(lex_abs):
            erros.append(f"lexico_path não pode ser symlink: {lex_abs} -> {os.path.realpath(lex_abs)}")
        # Estado mutável não pode ter múltiplos nomes físicos (hardlink), pois
        # cada nome teria sidecar de lock diferente e poderia criar split-brain.
        if os.path.isfile(lex_abs) and not os.path.islink(lex_abs):
            try:
                if os.stat(lex_abs, follow_symlinks=False).st_nlink > 1:
                    erros.append(f"lexico_path não pode ser hardlink (nlink>1): {lex_abs}")
            except OSError:
                pass

    estados = [(n, p) for n, p, papel in refs if papel == "estado"]
    leituras = [(n, p) for n, p, papel in refs if papel != "estado"]
    for ne, pe in estados:
        for nr, pr in leituras:
            colisao = pe == pr
            if not colisao and os.path.exists(pe) and os.path.exists(pr):
                try:
                    colisao = os.path.samefile(pe, pr)  # captura hardlink/inode alias
                except OSError:
                    pass
            if colisao:
                erros.append(f"COLISÃO DE PAPÉIS: {ne} e {nr} apontam para o mesmo arquivo/inode: {pe} ~ {pr}")
    # Dois papéis de leitura diferentes no mesmo arquivo são suspeitos, mas só
    # bloqueamos vocabulário/configuração entre si; a mesma entrada pode ser
    # declarada por duas fontes de-para por decisão operacional.
    especiais = [(n, p) for n, p in leituras if n in ("vocabulário", "configuração")]
    for i, (na, pa) in enumerate(especiais):
        for nb, pb in especiais[i+1:]:
            if pa == pb:
                erros.append(f"COLISÃO DE PAPÉIS: {na} e {nb} apontam para o mesmo arquivo: {pa}")
    return erros



def _validar_vocab(cfg: dict) -> list[str]:
    """Vocabulário é opcional; quando explicitamente informado, deve existir e ter schema mínimo válido."""
    if "vocab_path" not in cfg or cfg.get("vocab_path") is None:
        return []
    vp = cfg.get("vocab_path")
    if not isinstance(vp, str) or not vp.strip():
        return ["vocab_path informado deve ser string não vazia; omita a chave para desabilitar"]
    path = _real(cfg.get("_base_dir", "."), vp)
    if not os.path.isfile(path):
        return [f"vocab_path informado mas arquivo inexistente: {vp}"]
    try:
        import json
        obj = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        return [f"vocab_path inválido/ilegível: {e.__class__.__name__}: {e}"]
    if not isinstance(obj, dict):
        return ["vocab_path: raiz JSON deve ser objeto/mapa"]
    tv = obj.get("tipo_via", {})
    if not isinstance(tv, dict):
        return ["vocab_path: 'tipo_via' deve ser objeto/mapa"]
    erros = []
    for tok, ent in tv.items():
        if not isinstance(tok, str) or not tok or not isinstance(ent, dict) or not isinstance(ent.get("expansao"), str) or not ent.get("expansao"):
            erros.append(f"vocab_path: entrada tipo_via inválida para token {tok!r}")
            if len(erros) >= 5:
                break
    return erros

def validar(cfg: dict) -> dict:
    import geo as G
    import numero as NU

    erros, fontes_seguras = _estrutura_cfg(cfg)
    if not isinstance(cfg, dict):
        return {"erros": erros, "alertas": [], "metricas": {}, "geografia": {}, "ok": False}

    base_dir = cfg.get("_base_dir", ".")
    alertas, metricas = [], {}
    vistos = set()
    vistos_casefold = set()
    all_lon, all_lat = [], []
    aprende_raw = cfg.get("aprendizado")
    exige_coord = aprende_raw is True

    # v3.2.4: alteração de estado persistente exige o mesmo motor métrico em
    # todas as máquinas. O fallback ENU continua disponível apenas no modo
    # somente-aplicação; aprendizado sem pyproj seria semanticamente diferente
    # na borda do raio e quebraria a reprodutibilidade do léxico.
    if exige_coord and not getattr(G, "_PYPROJ", False):
        erros.append("aprendizado=true exige pyproj instalado — fallback ENU não pode "
                     "alterar estado persistente; instale requirements-production.txt")

    e_par, a_par = validar_parametros(cfg)
    erros += e_par
    alertas += a_par
    erros += _validar_vocab(cfg)

    if cfg.get("srid_metrico") is not None:
        try:
            ok_srid, motivo, info = G.validar_srid(cfg["srid_metrico"])
        except Exception as e:
            ok_srid, motivo, info = False, str(e), {}
        if not ok_srid:
            erros.append(f"srid_metrico: {motivo}")
        elif not info.get("preferencial_br"):
            alertas.append(f"srid_metrico EPSG:{cfg['srid_metrico']} é métrico "
                           f"({info.get('familia')}) mas fora do SIRGAS 2000/UTM 31965–31985")
    if not fontes_seguras:
        erros.append("nenhuma fonte válida declarada")
    def _nivel_fonte(f):
        if isinstance(f.get("autoridade_nivel"), int):
            return int(f.get("autoridade_nivel"))
        return 100 if f.get("autoridade") is True else 0
    if exige_coord and not any(_nivel_fonte(f) > 0 for f in fontes_seguras):
        alertas.append("nenhuma fonte com autoridade_nivel>0 — a direção do léxico vai depender só de dominância contextual")

    for idx, f in enumerate(fontes_seguras):
        inicio_erros = len(erros)
        sid = f.get("source_id")
        if not sid:
            erros.append(f"fontes[{idx}] sem source_id"); continue
        if not isinstance(sid, str) or not SOURCE_ID_RE.fullmatch(sid):
            erros.append(f"[{sid}] source_id inválido — use 1–64 caracteres em "
                         "[A-Za-z0-9_.-], iniciando por alfanumérico")
        if sid in vistos:
            erros.append(f"[{sid}] source_id duplicado")
        if isinstance(sid, str) and sid.casefold() in vistos_casefold:
            erros.append(f"[{sid}] source_id colide por casefold (filesystem case-insensitive)")
        vistos.add(sid)
        if isinstance(sid, str):
            vistos_casefold.add(sid.casefold())

        if "autoridade" in f and not isinstance(f.get("autoridade"), bool):
            erros.append(f"[{sid}] autoridade deve ser booleano")
        if "autoridade_nivel" in f:
            nv = f.get("autoridade_nivel")
            if not isinstance(nv, int) or isinstance(nv, bool) or not 0 <= nv <= 1000:
                erros.append(f"[{sid}] autoridade_nivel deve ser inteiro entre 0 e 1000")
            elif "autoridade" in f:
                # autoridade_nivel é o campo canônico v3.3; quando o alias legado
                # também aparece, ambos precisam expressar a mesma intenção.
                if f.get("autoridade") is True and nv <= 0:
                    erros.append(f"[{sid}] conflito: autoridade=true mas autoridade_nivel={nv}")
                if f.get("autoridade") is False and nv > 0:
                    erros.append(f"[{sid}] conflito: autoridade=false mas autoridade_nivel={nv}")

        if not isinstance(f.get("arquivo"), str) or not f.get("arquivo"):
            erros.append(f"[{sid}] 'arquivo' deve ser string não vazia")
            continue
        path = os.path.join(base_dir, f["arquivo"])
        if not os.path.exists(path):
            erros.append(f"[{sid}] arquivo inexistente: {f.get('arquivo')}")
            continue
        enc = f.get("encoding", "utf-8")
        if not isinstance(enc, str) or not enc:
            erros.append(f"[{sid}] 'encoding' deve ser string não vazia")
            continue
        try:
            cab_raw, duplicadas = _cabecalho_csv(path, enc)
        except Exception as e:
            erros.append(f"[{sid}] não foi possível ler cabeçalho bruto: {e.__class__.__name__}: {e}")
            continue
        if duplicadas:
            erros.append(f"[{sid}] cabeçalho CSV contém coluna(s) duplicada(s): {duplicadas} — "
                         "pandas renomearia silenciosamente e quebraria o contrato de preservação")
            continue
        try:
            cab = pd.Index(cab_raw)
        except Exception as e:
            erros.append(f"[{sid}] cabeçalho inválido: {e}")
            continue
        m = f.get("colunas")
        if not isinstance(m, dict):
            erros.append(f"[{sid}] 'colunas' deve ser objeto/mapa")
            continue

        for canon in OBRIGATORIAS:
            if not m.get(canon):
                erros.append(f"[{sid}] coluna obrigatória não configurada: {canon}")
        if exige_coord and not (m.get("lat") and m.get("lon")):
            erros.append(f"[{sid}] aprendizado=true exige lat/lon configurados")
        if exige_coord and not m.get("numero"):
            erros.append(f"[{sid}] aprendizado=true exige a coluna 'numero' — "
                         "sem número não existe gate de prova, o léxico não aprende")
        scope_cfg = str(cfg.get("scope_id") or "")
        # v3.3.2 — para município IBGE (7 dígitos), aprendizado nacional só é
        # aceito quando CADA fonte comprova o município por uma coluna de linha.
        # Isso impede gravar dados de Uruguaiana dentro do scope de Canoas por
        # simples erro de configuração.
        if exige_coord and re.fullmatch(r"\d{7}", scope_cfg) and not m.get("scope_id"):
            erros.append(f"[{sid}] scope_id municipal IBGE exige colunas.scope_id no aprendizado — "
                         "cada registro deve comprovar o município/território declarado")
        if "autoridade" in f and not isinstance(f["autoridade"], bool):
            erros.append(f"[{sid}] 'autoridade' deve ser booleano")

        reservadas = [c for c in cab if c.startswith("aj_") or c.startswith("__a2l_")]
        if reservadas:
            erros.append(f"[{sid}] a entrada usa namespace RESERVADO da skill: "
                         f"{reservadas[:5]} — renomeie na origem; a skill nunca sobrescreve")
        for canon, src in m.items():
            if src and src not in cab:
                erros.append(f"[{sid}] coluna configurada inexistente: "
                             f"{canon} -> '{src}' (arquivo tem {len(cab)} colunas)")
        if len(erros) > inicio_erros:
            continue

        usar = list(dict.fromkeys(c for c in m.values() if c in cab))
        try:
            df = pd.read_csv(path, dtype=str, encoding=enc,
                             keep_default_na=False, usecols=usar)
        except Exception as e:
            erros.append(f"[{sid}] falha ao ler dados: {e.__class__.__name__}: {e}")
            continue
        n = len(df)
        if n == 0:
            erros.append(f"[{sid}] arquivo sem registros"); continue

        met = {"registros": n}
        rid = df[m["record_id"]]
        dup = int(rid.duplicated().sum())
        met["record_id_duplicado"] = _pct(dup, n)
        if dup:
            alertas.append(f"[{sid}] {dup} record_id duplicados ({met['record_id_duplicado']:.1%})")

        if m.get("scope_id"):
            sv = df[m["scope_id"]].astype(str).str.strip()
            vazios_scope = int((sv == "").sum())
            divergentes = int((sv != str(cfg.get("scope_id"))).sum())
            met_scope_vals = sorted(set(sv[sv != ""].tolist()))
            if vazios_scope:
                erros.append(f"[{sid}] scope_id de linha vazio em {vazios_scope}/{n} registros")
            if divergentes:
                amostra = met_scope_vals[:5]
                erros.append(f"[{sid}] {divergentes}/{n} registros divergem do scope_id "
                             f"declarado '{cfg.get('scope_id')}' (valores encontrados: {amostra})")

        vazio = int((df[m["logradouro"]].str.strip() == "").sum())
        met["logradouro_vazio"] = _pct(vazio, n)
        if met["logradouro_vazio"] > LIMIARES["logradouro_vazio"]:
            alertas.append(f"[{sid}] logradouro vazio em {met['logradouro_vazio']:.1%}")

        if m.get("numero") in cab:
            sn = sum(1 for v in df[m["numero"]] if NU.parse(v)["chave"] is None)
            met["sem_numero"] = _pct(sn, n)
            comm = sum(1 for v in df[m["numero"]] if NU.parse(v)["modificador"])
            met["numero_com_modificador"] = _pct(comm, n)
            if met["sem_numero"] > LIMIARES["sem_numero"]:
                alertas.append(f"[{sid}] {met['sem_numero']:.1%} sem número — "
                               "esses registros não votam no léxico")

        if m.get("lat") in cab and m.get("lon") in cab:
            lat = pd.to_numeric(df[m["lat"]], errors="coerce").to_numpy(dtype="float64")
            lon = pd.to_numeric(df[m["lon"]], errors="coerce").to_numpy(dtype="float64")
            gdiag, lon_s, lat_s = G.diagnosticar(lon, lat)
            met["coord_invalida"] = _pct(gdiag["coord_invalidas"], n)
            met["coord_validas"] = int(gdiag.get("coord_validas", 0))
            met["particoes_utm"] = gdiag["particoes_utm"]
            if gdiag.get("coord_validas", 0):
                all_lon.append(lon_s)
                all_lat.append(lat_s)
            if met["coord_invalida"] > LIMIARES["coord_invalida"]:
                alertas.append(f"[{sid}] coordenada inválida em {met['coord_invalida']:.1%}")
            if exige_coord and gdiag.get("coord_validas", 0) == 0:
                erros.append(f"[{sid}] aprendizado=true mas nenhuma coordenada válida existe")
        metricas[sid] = met

    # Uma única visão geográfica para TODAS as fontes, ponderada por registro.
    gglobal = None
    if all_lon:
        lon_all = np.concatenate(all_lon)
        lat_all = np.concatenate(all_lat)
        gglobal, _lon, _lat = G.diagnosticar(lon_all, lat_all)
        if exige_coord and gglobal.get("multizona"):
            partes = ", ".join(f"{k}={v}" for k, v in gglobal["particoes_utm"].items())
            erros.append("aprendizado cruza múltiplas partições UTM "
                         f"({partes}) — particione por zona/hemisfério antes de executar")
        if cfg.get("srid_metrico") is not None and gglobal.get("coord_validas"):
            informado = int(cfg["srid_metrico"])
            z_inf, h_inf = G.particao_do_srid(informado)
            if z_inf is not None and len(gglobal["particoes_utm"]) == 1:
                part_dados = next(iter(gglobal["particoes_utm"]))
                esperado_part = f"{z_inf}{h_inf}"
                if part_dados != esperado_part:
                    esperado, _ = G.autodetect_srid(gglobal["lon_mediana"], gglobal["lat_mediana"])
                    erros.append(f"srid_metrico EPSG:{informado} representa zona/hemisfério {esperado_part}, "
                                 f"mas os dados estão em {part_dados} (esperado EPSG:{esperado})")
            elif z_inf is None:
                esperado, _ = G.autodetect_srid(gglobal["lon_mediana"], gglobal["lat_mediana"])
                alertas.append(f"srid_metrico EPSG:{informado} não é UTM — em produção use "
                               f"SIRGAS 2000/UTM da região (ex.: EPSG:{esperado}) e "
                               "abortar_em_alerta: true")
    elif exige_coord:
        erros.append("aprendizado=true mas nenhuma coordenada válida foi encontrada no conjunto das fontes")

    return {"erros": erros, "alertas": alertas, "metricas": metricas,
            "geografia": gglobal or {}, "ok": not erros}


def exigir(rel: dict, abortar_em_alerta: bool = False) -> None:
    for e in rel.get("erros", []):
        print(f"  ERRO    {e}")
    for a in rel.get("alertas", []):
        print(f"  ALERTA  {a}")
    for sid, m in rel.get("metricas", {}).items():
        print(f"  METRICA [{sid}] " + " ".join(f"{k}={v}" for k, v in m.items()))
    if rel.get("erros"):
        raise SystemExit(2)
    if abortar_em_alerta and rel.get("alertas"):
        raise SystemExit(3)
