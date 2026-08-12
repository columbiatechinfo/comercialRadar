#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Concilia N anotações de fachada do MESMO imóvel em um registro por imóvel.

Uma campanha real produz mais de uma imagem por matrícula — a foto do coletor, o Street View de dois anos
atrás, o voo de drone do quarteirão. Cada uma enxerga bem uma coisa diferente: a foto de campo resolve
hidrômetro e numeração, o drone resolve pavimentos, cobertura e footprint, o Street View resolve testada e
contexto urbano. Conciliar não é escolher "a melhor foto"; é escolher **a melhor fonte por campo**.

Como decide (determinístico, mesma entrada -> mesma saída):

  score(campo, imagem) = min(confianca_declarada, teto_efetivo(atributo, fonte, qualidade, idade))

O teto vem de `validar_anotacao.teto_efetivo` — a mesma função do gate, importada, não reimplementada, para
que conciliação e validação nunca divirjam de fórmula. Empate resolve por data de captura mais recente e,
persistindo, por nome de arquivo.

O que o script NÃO faz, deliberadamente:

  - não promove `sinal_imagem` a `achado_convergente`. Duas fotos de fachada continuam sendo UMA fonte
    independente (`imagem_fachada`); fachada + aérea são duas, e nesse caso a elegibilidade é marcada e
    reportada — mas a promoção é decisão do motor cadastral, com fonte externa na mesa, não deste script.
  - não apaga a divergência. Valor diferente entre imagens de datas diferentes num campo volátil é série
    temporal (o comércio fechou, a obra terminou, o medidor foi retirado), não erro; num campo estável, é
    contradição e vai para revisão.

Uso:
    python3 conciliar_multifoto.py <dir_anotacoes> [--chave matricula|imovel_id|arquivo]
        [--out saida/conciliado] [--relatorio saida/conciliacao.json] [--elevar-convergencia]

Saídas: <out>.json (registro por imóvel), <out>.csv (achatado), relatório de divergências.
Exit code: 0 sucesso, 1 há contradições a revisar, 2 erro de execução.
"""

import argparse
import csv
import importlib.util
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(AQUI, "..", "assets")

CLASSE_FONTE = {"campo": "imagem_fachada", "streetview": "imagem_fachada",
                "mapillary": "imagem_fachada", "drone": "imagem_aerea",
                "desconhecida": "imagem_fachada"}

LIMIAR_CONFLITO = 0.50   # abaixo disso o valor é fraco demais para sustentar contradição


def _mod_validador():
    """Importa o validador irmão para reusar teto_efetivo/_iter_campos — fórmula única."""
    caminho = os.path.join(AQUI, "validar_anotacao.py")
    spec = importlib.util.spec_from_file_location("validar_anotacao", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _carregar(p):
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _chave(anot, arquivo, modo):
    vinc = anot.get("vinculo") or {}
    if modo == "arquivo":
        return os.path.splitext(os.path.basename(arquivo))[0]
    valor = vinc.get(modo)
    return str(valor).strip() if valor not in (None, "") else None


def _data(anot):
    return (anot.get("imagem") or {}).get("data_captura") or ""


def conciliar_campos(grupo, V, obs):
    """grupo: [(arquivo, anot)] -> {path: registro conciliado}"""
    voláteis = set(obs.get("volateis", []))
    saida, conflitos = {}, []

    paths = []
    for _, anot in grupo:
        for path, _campo in V._iter_campos(anot):
            if path not in paths:
                paths.append(path)
    paths.sort()

    for path in paths:
        candidatos = []
        for arquivo, anot in grupo:
            bloco, _, nome = path.partition(".")
            campo = ((anot.get("atributos") or {}).get(bloco) or {}).get(nome)
            if not isinstance(campo, dict) or campo.get("valor") is None:
                continue
            img = anot.get("imagem") or {}
            teto = V.teto_efetivo(path, img, obs)
            score = min(float(campo.get("confianca") or 0.0), teto)
            candidatos.append({
                "arquivo": os.path.basename(arquivo), "fonte": img.get("fonte"),
                "classe_fonte": CLASSE_FONTE.get(img.get("fonte"), "imagem_fachada"),
                "data": _data(anot), "valor": campo.get("valor"),
                "juizo": campo.get("juizo"), "regra": campo.get("regra"),
                "evidencia": campo.get("evidencia"), "score": round(score, 4),
            })
        if not candidatos:
            continue

        # ordenação estável e total: score desc, data desc (vazia por último), arquivo asc
        candidatos = sorted(candidatos, key=lambda c: (-c["score"], _inv(c["data"]), c["arquivo"]))
        vencedor = candidatos[0]

        fortes = [c for c in candidatos if c["score"] >= LIMIAR_CONFLITO]
        valores = {json.dumps(c["valor"], ensure_ascii=False, sort_keys=True) for c in fortes}
        divergente = len(valores) > 1
        datas = {c["data"] for c in fortes if c["data"]}
        temporal = divergente and len(datas) > 1 and path in voláteis

        registro = {
            "valor": vencedor["valor"], "confianca": vencedor["score"], "juizo": vencedor["juizo"],
            "regra": vencedor["regra"], "evidencia": vencedor["evidencia"],
            "fonte_vencedora": vencedor["fonte"], "arquivo_vencedor": vencedor["arquivo"],
            "data_vencedora": vencedor["data"] or None,
            "n_imagens_com_valor": len(candidatos),
            "classes_fonte": sorted({c["classe_fonte"] for c in candidatos}),
            "status": "temporal" if temporal else ("contradicao" if divergente else "convergente"),
        }
        saida[path] = registro
        if divergente:
            conflitos.append({
                "campo": path, "status": registro["status"],
                "candidatos": [{k: c[k] for k in ("arquivo", "fonte", "data", "valor", "score")}
                               for c in fortes],
            })
    return saida, conflitos


def _inv(data):
    """chave de ordenação decrescente por data com strings (vazio vai por último)."""
    return (data == "", tuple(-ord(ch) for ch in data))


def conciliar_oportunidades(grupo, elevar):
    por_codigo = {}
    for arquivo, anot in grupo:
        classe = CLASSE_FONTE.get((anot.get("imagem") or {}).get("fonte"), "imagem_fachada")
        for op in (anot.get("oportunidades") or []):
            cod = op.get("codigo")
            if not cod:
                continue
            alvo = por_codigo.setdefault(cod, {
                "codigo": cod, "classe": op.get("classe"), "prioridade": op.get("prioridade"),
                "confianca": 0.0, "nivel_evidencia": "sinal_imagem",
                "fontes_independentes": set(), "classes_imagem": set(),
                "acao_sugerida": op.get("acao_sugerida"), "evidencias": [], "automatizavel": False,
            })
            alvo["confianca"] = max(alvo["confianca"], float(op.get("confianca") or 0.0))
            alvo["fontes_independentes"] |= set(op.get("fontes_independentes") or [])
            alvo["classes_imagem"].add(classe)
            if op.get("evidencia"):
                alvo["evidencias"].append("[%s] %s" % (os.path.basename(arquivo), op["evidencia"]))
            ordem = {"sinal_imagem": 0, "achado_convergente": 1, "contradicao": 2}
            if ordem.get(op.get("nivel_evidencia"), 0) > ordem.get(alvo["nivel_evidencia"], 0):
                alvo["nivel_evidencia"] = op.get("nivel_evidencia")

    saida = []
    for cod in sorted(por_codigo):
        o = por_codigo[cod]
        fontes = o["fontes_independentes"] | o["classes_imagem"]
        elegivel = len(o["classes_imagem"]) > 1 or len(fontes) > 1
        nivel = o["nivel_evidencia"]
        if elevar and elegivel and nivel == "sinal_imagem":
            nivel = "achado_convergente"
        saida.append({
            "codigo": cod, "classe": o["classe"], "prioridade": o["prioridade"],
            "confianca": round(o["confianca"], 3), "nivel_evidencia": nivel,
            "elegivel_convergencia": elegivel,
            "fontes_independentes": sorted(fontes),
            "acao_sugerida": o["acao_sugerida"], "automatizavel": False,
            "evidencia": " | ".join(o["evidencias"])[:2000] or None,
        })
    return saida


def main():
    ap = argparse.ArgumentParser(description="Concilia várias anotações de fachada do mesmo imóvel.")
    ap.add_argument("diretorio")
    ap.add_argument("--chave", default="matricula", choices=["matricula", "imovel_id", "arquivo"])
    ap.add_argument("--out", default="conciliado")
    ap.add_argument("--relatorio", default=None)
    ap.add_argument("--observabilidade", default=os.path.join(ASSETS, "observabilidade.json"))
    ap.add_argument("--elevar-convergencia", action="store_true",
                    help="promove sinal_imagem a achado_convergente quando há classes de fonte distintas "
                         "(fachada + aérea). Default off: promoção é decisão do motor cadastral.")
    args = ap.parse_args()

    try:
        V = _mod_validador()
        obs = _carregar(args.observabilidade)
    except Exception as exc:
        print("ERRO ao preparar execução: %s" % exc, file=sys.stderr)
        return 2

    arquivos = []
    for raiz, _, nomes in os.walk(args.diretorio):
        arquivos += [os.path.join(raiz, n) for n in sorted(nomes) if n.lower().endswith(".json")]
    arquivos.sort()
    if not arquivos:
        print("Nenhuma anotação .json em %s" % args.diretorio, file=sys.stderr)
        return 2

    grupos, sem_chave, ilegiveis = {}, [], []
    for a in arquivos:
        try:
            anot = _carregar(a)
        except Exception as exc:
            ilegiveis.append({"arquivo": os.path.basename(a), "erro": str(exc)})
            continue
        if (anot.get("imagem") or {}).get("apta_para_leitura") is False:
            continue
        k = _chave(anot, a, args.chave)
        if not k:
            sem_chave.append(os.path.basename(a))
            continue
        grupos.setdefault(k, []).append((a, anot))

    registros, relatorio_conflitos = {}, []
    for k in sorted(grupos):
        grupo = sorted(grupos[k], key=lambda t: t[0])
        campos, conflitos = conciliar_campos(grupo, V, obs)
        oportunidades = conciliar_oportunidades(grupo, args.elevar_convergencia)
        classes = sorted({CLASSE_FONTE.get((a.get("imagem") or {}).get("fonte"), "imagem_fachada")
                          for _, a in grupo})
        registros[k] = {
            "chave": k, "tipo_chave": args.chave,
            "imagens": [{"arquivo": os.path.basename(f),
                         "fonte": (a.get("imagem") or {}).get("fonte"),
                         "data_captura": _data(a)} for f, a in grupo],
            "n_imagens": len(grupo),
            "classes_fonte": classes,
            "fontes_independentes_de_imagem": len(classes),
            "campos": campos,
            "oportunidades": oportunidades,
            "contradicoes": [c for c in conflitos if c["status"] == "contradicao"],
            "divergencias_temporais": [c for c in conflitos if c["status"] == "temporal"],
        }
        for c in conflitos:
            relatorio_conflitos.append(dict(c, chave=k))

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out + ".json", "w", encoding="utf-8") as fh:
        json.dump(registros, fh, ensure_ascii=False, indent=2, sort_keys=True)

    paths = sorted({p for r in registros.values() for p in r["campos"]})
    cols = ["chave", "n_imagens", "classes_fonte", "contradicoes", "divergencias_temporais",
            "oportunidades", "oportunidades_elegiveis_convergencia"]
    for p in paths:
        b = p.replace(".", "_")
        cols += [b, b + "_conf", b + "_juizo", b + "_fonte", b + "_status"]
    with open(args.out + ".csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for k in sorted(registros):
            r = registros[k]
            linha = {"chave": k, "n_imagens": r["n_imagens"],
                     "classes_fonte": ";".join(r["classes_fonte"]),
                     "contradicoes": len(r["contradicoes"]),
                     "divergencias_temporais": len(r["divergencias_temporais"]),
                     "oportunidades": ";".join(o["codigo"] for o in r["oportunidades"]),
                     "oportunidades_elegiveis_convergencia":
                         sum(1 for o in r["oportunidades"] if o["elegivel_convergencia"])}
            for p, reg in r["campos"].items():
                b = p.replace(".", "_")
                v = reg["valor"]
                linha[b] = ";".join(str(x) for x in v) if isinstance(v, list) else v
                linha[b + "_conf"] = reg["confianca"]
                linha[b + "_juizo"] = reg["juizo"]
                linha[b + "_fonte"] = reg["fonte_vencedora"]
                linha[b + "_status"] = reg["status"]
            w.writerow(linha)

    n_contr = sum(len(r["contradicoes"]) for r in registros.values())
    n_temp = sum(len(r["divergencias_temporais"]) for r in registros.values())
    n_eleg = sum(1 for r in registros.values() for o in r["oportunidades"] if o["elegivel_convergencia"])

    relatorio = {
        "anotacoes_lidas": len(arquivos), "imoveis": len(registros),
        "sem_chave": sem_chave, "ilegiveis": ilegiveis,
        "contradicoes": n_contr, "divergencias_temporais": n_temp,
        "oportunidades_elegiveis_convergencia": n_eleg,
        "elevacao_aplicada": bool(args.elevar_convergencia),
        "conflitos": relatorio_conflitos,
    }
    if args.relatorio:
        os.makedirs(os.path.dirname(os.path.abspath(args.relatorio)) or ".", exist_ok=True)
        with open(args.relatorio, "w", encoding="utf-8") as fh:
            json.dump(relatorio, fh, ensure_ascii=False, indent=2)

    # funil explícito: anotação que não virou imóvel tem que aparecer aqui, nunca sumir em silêncio
    print("Anotações: %d | imóveis conciliados: %d | sem chave: %d | ilegíveis: %d"
          % (len(arquivos), len(registros), len(sem_chave), len(ilegiveis)))
    print("Contradições entre imagens: %d | divergências temporais: %d | oportunidades elegíveis a "
          "convergência: %d%s" % (n_contr, n_temp, n_eleg,
                                  " (elevadas)" if args.elevar_convergencia else " (não elevadas)"))
    for c in relatorio_conflitos:
        if c["status"] == "contradicao":
            print("  contradicao %-14s %-38s %s" % (
                c["chave"], c["campo"],
                " vs ".join("%s=%r(%s)" % (x["fonte"], x["valor"], x["score"]) for x in c["candidatos"])))
    return 1 if n_contr else 0


if __name__ == "__main__":
    sys.exit(main())
