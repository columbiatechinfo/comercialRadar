#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Consolida anotações de fachada (JSON, uma por imagem) em tabela achatada.

Layout de saída — uma linha por imagem, e para cada atributo quatro colunas irmãs:

    agua_hidrometro_presente        valor
    agua_hidrometro_presente_conf   confianca 0-1
    agua_hidrometro_presente_juizo  observado | inferido | ausente_confirmado | nao_observavel
    agua_hidrometro_presente_regra  identificador da regra de derivação (quando inferido)

Esse layout existe para o filtro que todo consumidor a jusante precisa fazer sem abrir JSON:
`WHERE agua_hidrometro_presente_conf >= 0.7 AND agua_hidrometro_presente_juizo <> 'inferido'`.

A ordem das colunas vem do schema, não das chaves encontradas — assim o CSV de hoje tem o mesmo
cabeçalho do de amanhã mesmo que o lote de hoje não tenha nenhum imóvel com piscina.

Uso:
    python3 consolidar.py <diretorio_anotacoes> --out saida/fachadas [--formato csv,parquet]
Saídas: <out>.csv, <out>.parquet (se pandas disponível), <out>_dicionario.csv
Exit code: 0 sucesso, 2 erro.
"""

import argparse
import csv
import json
import os
import sys

BLOCOS = ["enderecamento", "edificacao", "uso", "ocupacao", "agua", "energia",
          "esgoto", "acessos", "entorno"]

COLS_IMAGEM = ["arquivo", "fonte", "data_captura", "ano_captura", "data_captura_origem", "idade_meses", "enquadramento",
               "apta_para_leitura", "motivo_inaptidao", "nitidez", "iluminacao",
               "obstrucao", "lat", "lon", "srid", "origem_coord"]
COLS_VINCULO = ["matricula", "imovel_id", "logradouro_base", "numero_base", "municipio_ibge",
                "uso_base", "categoria_tarifaria_base", "economias_agua_base", "ucs_energia_base",
                "esgoto_cobrado_base", "area_construida_base_m2",
                "metodo_vinculo", "confianca_vinculo", "distancia_imovel_m", "numero_confere",
                "logradouro_confere", "ambiguidade"]
COLS_ECON = ["economias_estimativa", "economias_faixa_min", "economias_faixa_max",
             "economias_metodo", "economias_confianca", "economias_divergencia",
             "economias_justificativa"]
COLS_OP = ["oportunidades_total", "oportunidades_alta_critica", "oportunidades_lista",
           "sinais_imagem_total", "achados_convergentes_total", "contradicoes_total",
           "oportunidade_multiplas_ucs", "oportunidade_divergencia_uc_economias",
           "oportunidade_uso_comercial", "oportunidade_uso_misto",
           "oportunidade_divergencia_numero", "oportunidade_economias_ocultas",
           "oportunidade_esgoto_sem_cobranca", "oportunidade_area_divergente"]
COLS_AUD = ["passes_executados", "rodadas_verificacao", "campos_preenchidos",
            "campos_nao_observaveis", "confianca_media", "anotador", "timestamp"]

COLS_ESTRUTURA = ["estrutura_" + c for c in ['tipologia', 'torres_qtd', 'pavimentos_qtd', 'pavimentos_residenciais', 'pavimentos_comerciais', 'prumadas_aparentes', 'unidades_por_pavimento_estimadas', 'unidades_residenciais_estimadas', 'unidades_comerciais_estimadas', 'unidades_fisicas_estimadas', 'faixa_min', 'faixa_max', 'metodo_estimativa', 'identificadores_unidades', 'nome_edificio', 'blocos_identificados', 'ucs_energia_visiveis', 'hidrometros_visiveis', 'composicao_uso_terreo', 'composicao_uso_superiores', 'acessos_residenciais', 'acessos_comerciais', 'entradas_garagem', 'score_multiplas_unidades', 'score_divergencia_cadastral', 'score_verticalizacao', 'mudanca_estrutural', 'confianca_estrutural']]

COLS_MAPEAMENTO = ["mapeamento_" + c for c in ['geom_imovel_wkt', 'srid', 'face_id', 'quadra_id', 'logradouro_id', 'bairro', 'numero_observado', 'numero_base', 'status_endereco', 'numero_estimado_contextual', 'fonte_numero_estimado', 'confianca_numero_estimado', 'uso_base', 'uso_observado', 'mudanca_uso', 'transicao_uso', 'economias_base', 'economias_estimadas', 'ucs_energia', 'gap_uc_economias', 'score_subdivisao', 'score_oportunidade', 'score_convergencia', 'probabilidade_confirmacao', 'impacto_anual_estimado', 'valor_esperado_anual', 'idade_evidencia_meses', 'vigencia_evidencia', 'cluster_oportunidade']]

COLS_TRIAGEM = ["triagem_conteudo_imagem", "triagem_e_imovel", "triagem_apto_para_cadastro",
                "triagem_uso_parcial_permitido", "triagem_motivo_descarte",
                "triagem_atividade_economica_aparente", "triagem_sinais_atividade_economica",
                "triagem_atividade_no_alvo", "triagem_natureza_atividade",
                "triagem_formalidade_aparente", "triagem_confianca", "triagem_evidencia"]

CODIGOS_ALERTA = ["IMAGEM_INAPTA", "ENQUADRAMENTO_PARCIAL", "DIVERGENCIA_ECONOMIAS",
                  "NUMERO_DIVERGE_CADASTRO", "SUSPEITA_IMOVEL_INEXISTENTE", "SUSPEITA_VACANCIA",
                  "IMPEDIMENTO_LEITURA", "MEDICAO_NAO_LOCALIZADA", "USO_DIVERGE_CADASTRO",
                  "SUSPEITA_IRREGULARIDADE", "IMAGEM_DEFASADA", "DADO_PESSOAL_PRESENTE",
                  "COLETIVA_DETECTADA", "REVISAO_HUMANA", "MULTIPLAS_UCS_MESMO_ENDERECO",
                  "DIVERGENCIA_UC_ECONOMIAS", "COMERCIO_TEMPORALMENTE_DEFASADO", "VINCULO_IMOVEL_AMBIGUO",
                  "DIVERGENCIA_NUMERO_ENDERECO", "IMAGEM_FORA_DE_ESCOPO",
                  "ATIVIDADE_ECONOMICA_APARENTE", "ATIVIDADE_DOMICILIAR_APARENTE",
                  "ATIVIDADE_DE_VIZINHO"]


def _carregar(p):
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def ordem_atributos(schema):
    """Ordem canônica bloco.campo, lida do schema — garante cabeçalho estável entre lotes."""
    props = schema["properties"]["atributos"]["properties"]
    ordem = []
    for bloco in BLOCOS:
        for campo in (props.get(bloco, {}).get("properties") or {}):
            ordem.append("%s.%s" % (bloco, campo))
    return ordem


def cabecalho(paths):
    cols = ["fonte_arquivo"] + COLS_IMAGEM + COLS_VINCULO + COLS_TRIAGEM + COLS_ESTRUTURA + COLS_MAPEAMENTO
    for p in paths:
        base = p.replace(".", "_")
        cols += [base, base + "_conf", base + "_juizo", base + "_regra"]
    cols += COLS_ECON
    cols += ["economias_evidencias"]
    cols += COLS_OP
    cols += ["alerta_" + c.lower() for c in CODIGOS_ALERTA]
    cols += ["alertas_criticos", "alertas_total", "alertas_lista"]
    cols += ["lgpd_pessoas_visiveis", "lgpd_placas_visiveis", "lgpd_acao"]
    cols += COLS_AUD
    return cols


def achatar(anot, arquivo, paths):
    img = anot.get("imagem") or {}
    qual = img.get("qualidade") or {}
    coord = img.get("coordenada") or {}
    vinc = anot.get("vinculo") or {}
    eco = anot.get("economias") or {}
    aud = anot.get("auditoria") or {}
    lgpd = anot.get("lgpd") or {}
    alertas = anot.get("alertas") or []

    linha = {"fonte_arquivo": os.path.basename(arquivo)}
    linha.update({
        "arquivo": img.get("arquivo"), "fonte": img.get("fonte"),
        "data_captura": img.get("data_captura"), "ano_captura": img.get("ano_captura"),
        "data_captura_origem": img.get("data_captura_origem"), "idade_meses": img.get("idade_meses"),
        "enquadramento": img.get("enquadramento"),
        "apta_para_leitura": img.get("apta_para_leitura"),
        "motivo_inaptidao": img.get("motivo_inaptidao"),
        "nitidez": qual.get("nitidez"), "iluminacao": qual.get("iluminacao"),
        "obstrucao": ";".join(qual.get("obstrucao") or []),
        "lat": coord.get("lat"), "lon": coord.get("lon"),
        "srid": coord.get("srid"), "origem_coord": coord.get("origem"),
    })
    linha.update({c: vinc.get(c) for c in COLS_VINCULO})

    tri = anot.get("triagem") or {}
    linha.update({
        "triagem_conteudo_imagem": tri.get("conteudo_imagem"),
        "triagem_e_imovel": tri.get("e_imovel"),
        "triagem_apto_para_cadastro": tri.get("apto_para_cadastro"),
        "triagem_uso_parcial_permitido": tri.get("uso_parcial_permitido"),
        "triagem_motivo_descarte": tri.get("motivo_descarte"),
        "triagem_atividade_economica_aparente": tri.get("atividade_economica_aparente"),
        "triagem_sinais_atividade_economica": tri.get("sinais_atividade_economica"),
        "triagem_atividade_no_alvo": tri.get("atividade_no_alvo"),
        "triagem_natureza_atividade": tri.get("natureza_atividade"),
        "triagem_formalidade_aparente": tri.get("formalidade_aparente"),
        "triagem_confianca": tri.get("confianca_triagem"),
        "triagem_evidencia": tri.get("evidencia_triagem"),
    })

    est = anot.get("estrutura_imovel") or {}
    faixa_u = est.get("faixa_unidades_fisicas") or [None, None]
    for c in COLS_ESTRUTURA:
        chave = c[len("estrutura_"):]
        if chave == "faixa_min":
            linha[c] = faixa_u[0]
        elif chave == "faixa_max":
            linha[c] = faixa_u[1]
        elif chave == "metodo_estimativa":
            linha[c] = ";".join(est.get("metodo_estimativa") or [])
        else:
            linha[c] = est.get(chave)

    mp = anot.get("mapeamento") or {}
    for c in COLS_MAPEAMENTO:
        linha[c] = mp.get(c[len("mapeamento_"):])

    for p in paths:
        bloco, _, nome = p.partition(".")
        campo = ((anot.get("atributos") or {}).get(bloco) or {}).get(nome) or {}
        base = p.replace(".", "_")
        valor = campo.get("valor")
        if isinstance(valor, list):
            valor = ";".join(str(v) for v in valor)
        linha[base] = valor
        linha[base + "_conf"] = campo.get("confianca")
        linha[base + "_juizo"] = campo.get("juizo")
        linha[base + "_regra"] = campo.get("regra")

    faixa = eco.get("faixa") or [None, None]
    linha.update({
        "economias_estimativa": eco.get("estimativa"),
        "economias_faixa_min": faixa[0], "economias_faixa_max": faixa[1],
        "economias_metodo": eco.get("metodo"), "economias_confianca": eco.get("confianca"),
        "economias_divergencia": eco.get("divergencia"),
        "economias_justificativa": eco.get("justificativa"),
        "economias_evidencias": ";".join(
            "%s=%s" % (k, v) for k, v in sorted((eco.get("evidencias_contadas") or {}).items())),
    })

    ops = anot.get("oportunidades") or []
    opcodes = [o.get("codigo") for o in ops if isinstance(o, dict)]
    linha["oportunidades_total"] = len(ops)
    linha["oportunidades_alta_critica"] = sum(1 for o in ops if isinstance(o, dict) and o.get("prioridade") in ("alta", "critica"))
    linha["oportunidades_lista"] = ";".join(sorted(set(c for c in opcodes if c)))
    linha["sinais_imagem_total"] = sum(1 for o in ops if isinstance(o, dict) and o.get("nivel_evidencia") == "sinal_imagem")
    linha["achados_convergentes_total"] = sum(1 for o in ops if isinstance(o, dict) and o.get("nivel_evidencia") == "achado_convergente")
    linha["contradicoes_total"] = sum(1 for o in ops if isinstance(o, dict) and o.get("nivel_evidencia") == "contradicao")
    linha["oportunidade_multiplas_ucs"] = 1 if "MULTIPLAS_UCS_MESMO_ENDERECO" in opcodes else 0
    linha["oportunidade_divergencia_uc_economias"] = 1 if "DIVERGENCIA_UC_ECONOMIAS" in opcodes else 0
    linha["oportunidade_uso_comercial"] = 1 if "USO_COMERCIAL_NAO_CADASTRADO" in opcodes else 0
    linha["oportunidade_uso_misto"] = 1 if "USO_MISTO_POTENCIAL" in opcodes else 0
    linha["oportunidade_divergencia_numero"] = 1 if "DIVERGENCIA_NUMERO_ENDERECO" in opcodes else 0
    linha["oportunidade_economias_ocultas"] = 1 if "ECONOMIAS_OCULTAS_POTENCIAL" in opcodes else 0
    linha["oportunidade_esgoto_sem_cobranca"] = 1 if "ESGOTO_SEM_COBRANCA_POTENCIAL" in opcodes else 0
    linha["oportunidade_area_divergente"] = 1 if "AREA_DIVERGENTE_POTENCIAL" in opcodes else 0

    codigos = [a.get("codigo") for a in alertas]
    for c in CODIGOS_ALERTA:
        linha["alerta_" + c.lower()] = 1 if c in codigos else 0
    linha["alertas_criticos"] = sum(1 for a in alertas if a.get("severidade") == "critico")
    linha["alertas_total"] = len(alertas)
    linha["alertas_lista"] = ";".join(sorted(set(c for c in codigos if c)))

    linha.update({
        "lgpd_pessoas_visiveis": lgpd.get("pessoas_visiveis"),
        "lgpd_placas_visiveis": lgpd.get("placas_veiculo_visiveis"),
        "lgpd_acao": lgpd.get("acao"),
    })
    linha.update({c: aud.get(c) for c in COLS_AUD})
    return linha


def dicionario(paths, vocab, obs, destino):
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["coluna", "bloco", "tipo", "vocabulario", "teto_campo",
                    "teto_streetview", "teto_drone"])
        for c in COLS_IMAGEM + COLS_VINCULO:
            w.writerow([c, "metadado", "texto", "", "", "", ""])
        for c in COLS_ESTRUTURA + COLS_MAPEAMENTO:
            bloco_dic = "estrutura_imovel" if c.startswith("estrutura_") else "mapeamento"
            chave = bloco_dic + "." + c.split("_", 1)[1]
            lista = (vocab["listas"].get(chave) or vocab["listas_multivaloradas"].get(chave) or [])
            t = obs["tetos"].get(chave, {})
            w.writerow([c, bloco_dic, "categorico" if lista else "misto", "|".join(lista),
                        t.get("campo", ""), t.get("streetview", ""), t.get("drone", "")])
        for c in COLS_TRIAGEM:
            chave = "triagem." + c[len("triagem_"):]
            lista = (vocab["listas"].get(chave) or vocab["listas_multivaloradas"].get(chave) or [])
            t = obs["tetos"].get(chave, {})
            w.writerow([c, "triagem", "categorico" if lista else "misto", "|".join(lista),
                        t.get("campo", ""), t.get("streetview", ""), t.get("drone", "")])
        for p in paths:
            bloco, _, _nome = p.partition(".")
            base = p.replace(".", "_")
            lista = (vocab["listas"].get(p) or vocab["listas_multivaloradas"].get(p) or [])
            tipo = ("multivalorado" if p in vocab["listas_multivaloradas"]
                    else "categorico" if lista else "livre_ou_numerico")
            t = obs["tetos"].get(p, {})
            w.writerow([base, bloco, tipo, "|".join(lista),
                        t.get("campo", ""), t.get("streetview", ""), t.get("drone", "")])
            w.writerow([base + "_conf", bloco, "numerico_0_1", "", "", "", ""])
            w.writerow([base + "_juizo", bloco, "categorico",
                        "observado|inferido|ausente_confirmado|nao_observavel", "", "", ""])
            w.writerow([base + "_regra", bloco, "texto", "", "", "", ""])
        for c in COLS_ECON + ["economias_evidencias"]:
            w.writerow([c, "economias", "misto", "", "", "", ""])
        for c in COLS_OP:
            w.writerow([c, "oportunidades", "misto", "", "", "", ""])
        for c in CODIGOS_ALERTA:
            w.writerow(["alerta_" + c.lower(), "alertas", "binario_0_1", "", "", "", ""])
        for c in COLS_AUD:
            w.writerow([c, "auditoria", "misto", "", "", "", ""])


def main():
    ap = argparse.ArgumentParser(description="Consolida anotações de fachada em tabela achatada.")
    ap.add_argument("diretorio")
    ap.add_argument("--out", required=True, help="prefixo de saída, sem extensão")
    ap.add_argument("--formato", default="csv", help="csv,parquet")
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    ap.add_argument("--schema", default=os.path.join(base, "schema_fachada.json"))
    ap.add_argument("--vocabulario", default=os.path.join(base, "vocabulario.json"))
    ap.add_argument("--observabilidade", default=os.path.join(base, "observabilidade.json"))
    args = ap.parse_args()

    try:
        schema = _carregar(args.schema)
        vocab = _carregar(args.vocabulario)
        obs = _carregar(args.observabilidade)
    except Exception as exc:
        print("ERRO ao carregar assets: %s" % exc, file=sys.stderr)
        return 2

    arquivos = []
    for raiz, _, nomes in os.walk(args.diretorio):
        arquivos += [os.path.join(raiz, n) for n in sorted(nomes) if n.lower().endswith(".json")]
    arquivos.sort()
    if not arquivos:
        print("Nenhuma anotação .json em %s" % args.diretorio, file=sys.stderr)
        return 2

    paths = ordem_atributos(schema)
    cols = cabecalho(paths)

    linhas, ilegiveis = [], []
    for a in arquivos:
        try:
            linhas.append(achatar(_carregar(a), a, paths))
        except Exception as exc:
            ilegiveis.append((a, str(exc)))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    formatos = {f.strip().lower() for f in args.formato.split(",")}

    if "csv" in formatos:
        with open(args.out + ".csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
            w.writeheader()
            for l in linhas:
                w.writerow(l)

    if "parquet" in formatos:
        try:
            import pandas as pd
            pd.DataFrame(linhas, columns=cols).to_parquet(args.out + ".parquet", index=False)
        except Exception as exc:
            print("aviso: parquet não gerado (%s)" % exc, file=sys.stderr)

    dicionario(paths, vocab, obs, args.out + "_dicionario.csv")

    # Funil: entrada = saída + ilegíveis. Sem isso não se sabe a cobertura real da campanha.
    print("Anotações lidas: %d | linhas geradas: %d | ilegíveis: %d | colunas: %d"
          % (len(arquivos), len(linhas), len(ilegiveis), len(cols)))
    for a, e in ilegiveis:
        print("  ilegivel: %s -> %s" % (os.path.basename(a), e), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
