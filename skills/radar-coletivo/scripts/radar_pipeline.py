#!/usr/bin/env python3
"""
radar_pipeline — NÚCLEO ÚNICO de preparação da base (v4.5.2, 3ª revisão).

Antes havia três pipelines operacionais com contratos diferentes: o runner
canônico tinha dedup→anomalia→F7→gate na ordem certa, mas cnefe_coletivas.py
e cnefe_mapa.py orquestravam por conta própria — um bug corrigido no runner
podia reaparecer nos auxiliares. Agora dedup + sanidade + anomalia + F7 +
gate existem EM UM ÚNICO LUGAR e todos os entrypoints consomem:

    df, stats = radar_pipeline.preparar_base(input, ...)
    m = radar_pipeline.gate_universal(df)      # NUM>0 & sanidade & sem anomalia
    mc = radar_pipeline.gate_conf(df)          # + RADAR_CONF_FAIXA ALTA+

Writers (Excel, mapa, legado) NUNCA recalculam regra de negócio.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

import radar_utils as xu


def preparar_base(input_path: str,
                  municipio: int = None,
                  sanidade_pct: float = 0.001,
                  anomalia_seq_min: int = 15,
                  anomalia_cobertura: float = 0.95,
                  geo_raio_grosseiro: float = 300.0,
                  log=lambda m: None):
    """Ordem canônica ÚNICA (a mesma para todo consumidor):

    load tipado → validar_schema → NUM→int → DEDUP EXATO (linha idêntica não
    é evidência; remove ANTES de qualquer estatística) → harmonização de
    grafia → sanidade geo 3 camadas → complementos 7 camadas → promoção Q/L
    → classificação lexical de atividade → enrich/chaves → RADAR_ID →
    confronto declarado×contado → anomalia de coleta S1-S5 → F7
    confiabilidade → classificação de coletivas → FLAG_COL zerado fora do
    gate universal.

    Retorna (df, stats) — stats alimenta o funil do manifest.
    """
    import cnefe_coletivas as cc   # import tardio: evita ciclo de módulos

    xu.SANIDADE_RAIO_GROSSEIRO_KM = float(geo_raio_grosseiro)

    # R6: o dedupe da LINHA BRUTA acontece dentro de load_cnefe, antes de
    # qualquer coerção — repeti-lo aqui é rede de segurança, não a política.
    df = cc.load_cnefe(input_path, municipio)
    xu.validar_schema(df)
    n0 = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    n_dup = n0 - len(df)
    log(f"base: {n0:,} raw | dup exatos removidos: {n_dup:,} -> {len(df):,}")

    df = xu.harmonizar_logradouro(df)
    df = xu.sanidade_geo(df, pct=sanidade_pct, particao='COD_MUNICIPIO')
    df = cc.normalizar_complemento(df)

    # promoção Quadra/Lote (Padrão B) — insumo da calibração Q/L do S2
    df['NRO_OFICIAL'] = df['NUM_ENDERECO']
    df['CHAVE_QUADRA_LOTE'] = None
    df['PADRAO_ENDERECO'] = 'LOGRADOURO'
    _m0 = df['NUM_ENDERECO'] == 0
    if _m0.any():
        _ql = df[_m0].apply(xu.promover_quadra_lote, axis=1, result_type='expand')
        df.loc[_m0, 'NRO_OFICIAL'] = _ql[0].values
        df.loc[_m0, 'CHAVE_QUADRA_LOTE'] = _ql[1].values
        df.loc[_m0, 'PADRAO_ENDERECO'] = _ql[2].values

    df = cc.classificar_uso(df)
    df = cc.enriquecer(df)
    df = xu.gerar_id_rastreio(df)
    df = xu.confrontar_declaracao_estab(df)

    df = xu.detectar_anomalia_coleta(df, seq_min=anomalia_seq_min,
                                     cobertura_min=anomalia_cobertura)
    df = xu.detectar_anomalia_pos_t1(df)

    # F7 ANTES de qualquer gate — a faixa participa da trava
    _conf = df.apply(lambda r: xu.calcular_confiabilidade(r, is_inferido=False),
                     axis=1)
    df = pd.concat([df, pd.DataFrame(_conf.tolist(), index=df.index)], axis=1)

    df = cc.classificar_coletiva(df)
    df.loc[~gate_universal(df), 'FLAG_COL'] = 0

    stats = {
        'entrada_raw': int(n0),
        'duplicados_exatos_removidos': int(n_dup),
        'apos_dedup': int(len(df)),
        'erro_geometria': int((df['SANIDADE_GEO'] != 'OK').sum()),
        'anomalia_coleta': int((df['FLAG_ANOMALIA_COLETA'] == 1).sum()),
        'gate_universal_aprovado': int(gate_universal(df).sum()),
        'gate_f7_alta_mais': int(gate_conf(df).sum()),
        # o funil passa a DECLARAR por que cada registro ficou fora
        'inelegibilidade': {k: int(v) for k, v in
                            motivo_inelegibilidade(df).value_counts().items()},
    }
    df['CNF_ELEGIBILIDADE'] = motivo_inelegibilidade(df)
    return df, stats


# Escada de anomalia. O comentário de `_decompor_sinais_anomalia` já dizia,
# desde a v4.5.1, que a graduação certa é 1 observação / 2 quarentena / 3
# exclusão sobre N_SINAIS_PRIMARIOS — e a política executada continuava
# binária: QUALQUER sinal derrubava o registro. Uma sequência perfeita de
# numeração (S2) pode ser perfeitamente verdadeira num prédio bem enumerado.
# Um sinal isolado é observação, não veredito.
ANOMALIA_EXCLUI_A_PARTIR_DE = 3


def motivo_inelegibilidade(df: pd.DataFrame) -> pd.Series:
    """POR QUE cada registro ficou fora — nunca em silêncio.

    A exclusão existia e não aparecia em lugar nenhum: quem recebe a base não
    tinha como saber que um endereço não virou candidato por causa da FAIXA DE
    CONFIANÇA, que é heurística não calibrada (pesos 40/30/30, M6 pendente).
    Decisão não calibrada pode ficar — invisível, não.
    """
    n_sin = (df['N_SINAIS_PRIMARIOS'] if 'N_SINAIS_PRIMARIOS' in df.columns
             else pd.Series(0, index=df.index)).fillna(0).astype(int)
    faixa = (df['RADAR_CONF_FAIXA'] if 'RADAR_CONF_FAIXA' in df.columns
             else pd.Series('', index=df.index)).fillna('').astype(str)
    return pd.Series(np.select(
        [df['NUMERO'] <= 0,
         df['SANIDADE_GEO'] != 'OK',
         n_sin >= ANOMALIA_EXCLUI_A_PARTIR_DE,
         ~faixa.isin(['ALTA', 'MUITO_ALTA']),
         n_sin == 2,
         n_sin == 1],
        ['RETIDO_SEM_NUMERO', 'RETIDO_GEO', 'RETIDO_ANOMALIA',
         'RETIDO_CONFIANCA', 'ELEGIVEL_COM_QUARENTENA',
         'ELEGIVEL_COM_OBSERVACAO'],
        default='ELEGIVEL'), index=df.index)


def gate_universal(df: pd.DataFrame) -> pd.Series:
    """R1+R2+geo — máscara canônica única de aprovação.

    A anomalia deixou de ser binária: só EXCLUI a partir de
    `ANOMALIA_EXCLUI_A_PARTIR_DE` sinais PRIMÁRIOS independentes. Um ou dois
    sinais marcam o registro (ver `motivo_inelegibilidade`) e o deixam seguir.
    """
    n_sin = (df['N_SINAIS_PRIMARIOS'] if 'N_SINAIS_PRIMARIOS' in df.columns
             else pd.Series(0, index=df.index)).fillna(0).astype(int)
    return ((df['NUMERO'] > 0)
            & (df['SANIDADE_GEO'] == 'OK')
            & (n_sin < ANOMALIA_EXCLUI_A_PARTIR_DE))


def gate_conf(df: pd.DataFrame) -> pd.Series:
    """Gate universal + F7 (fail-closed: coluna ausente ABORTA)."""
    xu._exigir('RADAR_CONF_FAIXA' in df.columns,
               "R3: RADAR_CONF_FAIXA ausente — preparar_base não rodou F7")
    return gate_universal(df) & df['RADAR_CONF_FAIXA'].isin(['ALTA', 'MUITO_ALTA'])
