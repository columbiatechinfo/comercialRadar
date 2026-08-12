#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Valida anotações de fachada (skill leitura-fachada-cadastral).

Gate de entrega: uma anotação só entra na base se passar aqui. Verifica, em ordem:
  1. estrutura      — campos obrigatórios, tipos, formato do campo anotado
  2. vocabulario    — valores dentro da lista fechada (assets/vocabulario.json)
  3. confianca      — nenhum campo acima do teto efetivo da fonte
                      (assets/observabilidade.json: teto x decaimento temporal x penalidades)
  4. coerencia      — regras C-01..C-10, estruturais e declarativas
  5. lgpd           — nenhum dado pessoal transcrito; termos juridicamente carregados barrados
  6. economias      — teste de concordancia aplicado quando ha divergencia

Usa jsonschema (Draft 2020-12) + regras semânticas. Determinístico: mesma entrada -> mesmo relatório, mesma ordem.

Uso:
    python3 validar_anotacao.py <arquivo.json|diretorio> \
        [--schema assets/schema_fachada.json] \
        [--observabilidade assets/observabilidade.json] \
        [--vocabulario assets/vocabulario.json] \
        [--relatorio saida/validacao.json] [--quiet]

Exit code: 0 = tudo aprovado, 1 = ao menos uma anotação reprovada, 2 = erro de execução.
"""

import argparse
import json
import os
import re
import sys
import unicodedata

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

TOL = 0.005  # tolerância de float na comparação com o teto
IDADE_PRESUMIDA_SV = 36  # meses, quando streetview/mapillary não traz data

BLOCOS = ["enderecamento", "edificacao", "uso", "ocupacao", "agua", "energia",
          "esgoto", "acessos", "entorno"]

JUIZOS = {"observado", "inferido", "ausente_confirmado", "nao_observavel"}


# --------------------------------------------------------------------------- util

def _norm(s):
    """minúsculas sem acento, para busca de termo proibido."""
    if not isinstance(s, str):
        return ""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def _carregar(caminho):
    with open(caminho, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _listar_json(alvo):
    if os.path.isfile(alvo):
        return [alvo]
    achados = []
    for raiz, _, arquivos in os.walk(alvo):
        for nome in sorted(arquivos):
            if nome.lower().endswith(".json"):
                achados.append(os.path.join(raiz, nome))
    return sorted(achados)


def _iter_campos(anot):
    """(path, dict_do_campo) para todo campo anotado dos blocos."""
    for bloco in BLOCOS:
        conteudo = (anot.get("atributos") or {}).get(bloco) or {}
        if not isinstance(conteudo, dict):
            continue
        for nome in sorted(conteudo.keys()):
            campo = conteudo[nome]
            if isinstance(campo, dict):
                yield "%s.%s" % (bloco, nome), campo


def _valor(anot, path):
    bloco, _, nome = path.partition(".")
    campo = ((anot.get("atributos") or {}).get(bloco) or {}).get(nome)
    return campo.get("valor") if isinstance(campo, dict) else None


def _como_lista(valor):
    if valor is None:
        return []
    if isinstance(valor, list):
        return [str(v).strip() for v in valor if str(v).strip()]
    return [p.strip() for p in str(valor).split(";") if p.strip()]


def _canon_numero(valor):
    """Normalização só para comparação, nunca substitui a transcrição literal."""
    if valor is None:
        return None
    x = _norm(str(valor)).upper()
    x = x.replace("NUMERO", "").replace("NRO", "").replace("NO", "")
    x = re.sub(r"[^0-9A-Z]", "", x)
    if x in ("", "SN", "SEMNUMERO"):
        return None
    x = re.sub(r"^0+(?=\d)", "", x)
    return x


# ------------------------------------------------------------------- teto de conf.

def teto_efetivo(path, imagem, obs):
    """Confiança máxima que a imagem sustenta para este atributo.

    teto_fonte x decaimento_temporal(voláteis) x penalidade_enquadramento x penalidade_qualidade
    """
    fonte = imagem.get("fonte", "desconhecida")
    base = obs["tetos"].get(path, {}).get(
        fonte, obs["teto_default"].get(fonte, obs["teto_default"]["desconhecida"]))

    if path in obs.get("volateis", []):
        idade = imagem.get("idade_meses")
        if idade is None and fonte in ("streetview", "mapillary"):
            idade = IDADE_PRESUMIDA_SV
        if idade:
            dec = obs["decaimento_temporal"]
            fator = 0.5 ** (float(idade) / float(dec["meia_vida_meses"]))
            base = max(dec["piso"] * base, base * fator)

    base *= obs["penalidade_enquadramento"].get(imagem.get("enquadramento"), 1.0)
    qual = imagem.get("qualidade") or {}
    base *= obs["penalidade_qualidade"]["nitidez"].get(qual.get("nitidez", "boa"), 1.0)
    base *= obs["penalidade_qualidade"]["iluminacao"].get(qual.get("iluminacao", "boa"), 1.0)
    return round(base, 4)


# ------------------------------------------------------------------------ camadas

def checar_estrutura(anot, erros):
    for obrig in ("schema_versao", "imagem", "atributos", "economias", "auditoria"):
        if obrig not in anot:
            erros.append(("estrutura", "raiz", "campo obrigatório ausente: %s" % obrig))

    img = anot.get("imagem") or {}
    for obrig in ("arquivo", "fonte", "enquadramento", "apta_para_leitura"):
        if obrig not in img:
            erros.append(("estrutura", "imagem", "campo obrigatório ausente: imagem.%s" % obrig))

    if img.get("apta_para_leitura") is False and not img.get("motivo_inaptidao"):
        erros.append(("estrutura", "imagem",
                      "apta_para_leitura=false exige motivo_inaptidao preenchido"))

    for path, campo in _iter_campos(anot):
        if "juizo" not in campo or "confianca" not in campo or "valor" not in campo:
            erros.append(("estrutura", path, "campo anotado exige valor, juizo e confianca"))
            continue
        if campo["juizo"] not in JUIZOS:
            erros.append(("estrutura", path, "juizo inválido: %r" % campo["juizo"]))
        conf = campo.get("confianca")
        if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
            erros.append(("estrutura", path, "confianca fora de [0,1]: %r" % conf))

    eco = anot.get("economias") or {}
    for obrig in ("estimativa", "metodo", "confianca", "divergencia"):
        if obrig not in eco:
            erros.append(("estrutura", "economias", "campo obrigatório ausente: %s" % obrig))


def checar_vocabulario(anot, vocab, erros, avisos, contadores):
    listas = vocab["listas"]
    multi = vocab["listas_multivaloradas"]

    for path, campo in _iter_campos(anot):
        valor = campo.get("valor")
        if valor is None:
            continue

        if path in listas:
            vals = [str(valor)]
        elif path in multi:
            vals = _como_lista(valor)
        else:
            continue

        permitidos = listas.get(path) or multi.get(path)
        for v in vals:
            if v in permitidos:
                continue
            if v.startswith("outro:"):
                contadores.setdefault("outro", []).append("%s=%s" % (path, v))
                avisos.append(("vocabulario", path,
                               "valor fora da lista registrado como outro: %s" % v))
                continue
            erros.append(("vocabulario", path,
                          "valor %r fora do vocabulário fechado (use um da lista ou 'outro:<desc>')" % v))


def checar_confianca(anot, obs, erros):
    img = anot.get("imagem") or {}
    for path, campo in _iter_campos(anot):
        teto = teto_efetivo(path, img, obs)
        conf = float(campo.get("confianca") or 0.0)

        if teto <= 0.0:
            if campo.get("juizo") != "nao_observavel" or campo.get("valor") is not None:
                erros.append(("confianca", path,
                              "atributo não observável na fonte %r: exige juizo=nao_observavel e valor=null"
                              % img.get("fonte")))
            continue

        if conf > teto + TOL:
            erros.append(("confianca", path,
                          "confianca %.2f acima do teto efetivo %.2f (fonte=%s, enquadramento=%s, idade=%s)"
                          % (conf, teto, img.get("fonte"), img.get("enquadramento"),
                             img.get("idade_meses"))))


def checar_coerencia(anot, vocab, erros, avisos):
    img = anot.get("imagem") or {}
    varredura_txt = _norm(" | ".join(
        " ".join(c.get("itens") or []) for c in (anot.get("varredura") or [])))

    for path, campo in _iter_campos(anot):
        juizo = campo.get("juizo")
        valor = campo.get("valor")
        evid = (campo.get("evidencia") or "").strip()

        # C-06
        if juizo == "nao_observavel" and valor is not None:
            erros.append(("coerencia", path, "C-06: juizo=nao_observavel exige valor=null"))
        # C-07
        if juizo in ("observado", "inferido", "ausente_confirmado") and not evid:
            erros.append(("coerencia", path, "C-07: juizo=%s exige evidencia descritiva" % juizo))
        # C-08
        if juizo == "inferido" and not (campo.get("regra") or "").strip():
            erros.append(("coerencia", path,
                          "C-08: juizo=inferido exige o identificador da regra (ver references/derivacao.md)"))
        # C-09 — âncora fraca: só avisa, pois a varredura é resumida por natureza
        if juizo == "observado" and varredura_txt and campo.get("celula"):
            if _norm(str(campo.get("celula"))) not in _norm(
                    " ".join(c.get("celula", "") for c in (anot.get("varredura") or []))):
                avisos.append(("coerencia", path,
                               "C-09: célula %s não consta na varredura" % campo.get("celula")))

    for regra in vocab["coerencia"]:
        if "se" not in regra:
            continue
        cond = regra["se"]
        rid, desc = regra["id"], regra["descricao"]

        ativa = False
        if "campo" in cond:
            v = _valor(anot, cond["campo"])
            if "em" in cond:
                ativa = v in cond["em"]
            elif cond.get("preenchido"):
                ativa = v is not None and str(v).strip() != ""
        elif "economias_estimativa_maior_que" in cond:
            est = (anot.get("economias") or {}).get("estimativa")
            ativa = isinstance(est, int) and est > cond["economias_estimativa_maior_que"]
        if not ativa:
            continue

        neg = regra.get("entao_nao") or {}
        if neg:
            v2 = _valor(anot, neg["campo"]) if "campo" in neg else None
            if "em" in neg and v2 in neg["em"]:
                erros.append(("coerencia", neg["campo"], "%s: %s (valor=%s)" % (rid, desc, v2)))
            if "maior_que" in neg and isinstance(v2, int) and v2 > neg["maior_que"]:
                erros.append(("coerencia", neg["campo"], "%s: %s (valor=%s)" % (rid, desc, v2)))
            if "contem_algum" in neg:
                achados = set(_como_lista(v2)) & set(neg["contem_algum"])
                if achados:
                    erros.append(("coerencia", neg["campo"],
                                  "%s: %s (indícios conflitantes: %s)" % (rid, desc, ", ".join(sorted(achados)))))

        exig = regra.get("entao_exige") or {}
        if exig:
            if "imagem_fonte_em" in exig and img.get("fonte") not in exig["imagem_fonte_em"]:
                erros.append(("coerencia", cond.get("campo", rid),
                              "%s: %s (fonte=%s)" % (rid, desc, img.get("fonte"))))
            if exig.get("evidencias_contadas_nao_vazia"):
                ev = (anot.get("economias") or {}).get("evidencias_contadas") or {}
                if not any(isinstance(x, int) and x > 0 for x in ev.values()):
                    erros.append(("coerencia", "economias", "%s: %s" % (rid, desc)))


def checar_lgpd(anot, vocab, erros, avisos):
    lgpd = anot.get("lgpd") or {}
    if lgpd.get("dado_pessoal_transcrito") is True:
        erros.append(("lgpd", "lgpd", "dado_pessoal_transcrito=true: remova a transcrição antes de entregar"))

    if (lgpd.get("pessoas_visiveis") or lgpd.get("placas_veiculo_visiveis")):
        codigos = {a.get("codigo") for a in (anot.get("alertas") or [])}
        if "DADO_PESSOAL_PRESENTE" not in codigos:
            avisos.append(("lgpd", "alertas",
                           "pessoa/placa visível sem alerta DADO_PESSOAL_PRESENTE"))

    proibidos = vocab["termos_proibidos_em_valor"]
    for path, campo in _iter_campos(anot):
        alvo = _norm(str(campo.get("valor") or ""))
        for termo in proibidos:
            if termo in alvo:
                erros.append(("lgpd", path,
                              "termo juridicamente carregado em campo de valor: %r — registre a evidência e "
                              "classifique como suspeita" % termo))


def checar_economias(anot, erros, avisos):
    eco = anot.get("economias") or {}
    ev = {k: v for k, v in (eco.get("evidencias_contadas") or {}).items()
          if isinstance(v, int) and v > 0}
    est, conf = eco.get("estimativa"), float(eco.get("confianca") or 0.0)

    fortes = {k: v for k, v in ev.items()
              if k in ("medidor_energia", "hidrometro_bateria", "portas_independentes",
                       "campainhas", "caixas_correio")}
    if len(set(fortes.values())) > 1 and (max(fortes.values()) - min(fortes.values())) > 1:
        if not eco.get("divergencia"):
            erros.append(("economias", "economias",
                          "R-ECO-09: evidências discordam em >1 (%s) e divergencia=false"
                          % ", ".join("%s=%d" % kv for kv in sorted(fortes.items()))))
        if conf > 0.55 + TOL:
            erros.append(("economias", "economias",
                          "R-ECO-09: divergência exige confianca <= 0.55 (atual %.2f)" % conf))
        if not eco.get("faixa"):
            avisos.append(("economias", "economias", "R-ECO-09: divergência sem faixa [min,max] preenchida"))

    if eco.get("metodo") == "unitario_default" and conf > 0.55 + TOL:
        erros.append(("economias", "economias",
                      "R-ECO-08: metodo=unitario_default exige confianca <= 0.55 (atual %.2f)" % conf))

    if isinstance(est, int) and est > 1:
        codigos = {a.get("codigo") for a in (anot.get("alertas") or [])}
        if "COLETIVA_DETECTADA" not in codigos:
            avisos.append(("economias", "alertas", "estimativa>1 sem alerta COLETIVA_DETECTADA"))



USOS_ECONOMICOS = {"comercial", "servicos", "industrial", "misto_res_com", "publico",
                   "religioso", "educacional", "saude", "rural_agropecuario"}
CONTEUDO_COM_IMOVEL = {"fachada_imovel", "imovel_parcial", "terreno_sem_edificacao",
                       "edificacao_em_obra"}
OPORT_CATEGORIA = {"USO_COMERCIAL_NAO_CADASTRADO", "USO_MISTO_POTENCIAL",
                   "ATIVIDADE_COMERCIAL_MUDOU", "ATIVIDADE_DOMICILIAR_POTENCIAL",
                   "COMERCIO_APARENTA_DESATIVADO"}


def checar_triagem(anot, obs, vocab, erros, avisos):
    """Triagem: o que a imagem retrata e se há atividade econômica — e de quem ela é.

    Duas perguntas baratas que evitam dois erros caros: anotar uma foto que não é de imóvel
    (custo puro, lixo na base) e importar o comércio do vizinho para o cadastro do alvo
    (achado falso, com consequência tarifária).
    """
    tri = anot.get("triagem")
    versao = str(anot.get("schema_versao") or "0")

    if not tri:
        if versao >= "1.4.0":
            erros.append(("triagem", "triagem",
                          "bloco triagem obrigatório a partir de schema_versao 1.4.0"))
        else:
            avisos.append(("triagem", "triagem",
                           "anotação sem bloco triagem (schema_versao %s); migre para 1.4.0" % versao))
        return

    img = anot.get("imagem") or {}
    conteudo = tri.get("conteudo_imagem")
    e_imovel = tri.get("e_imovel")
    atividade = tri.get("atividade_economica_aparente")
    alvo = tri.get("atividade_no_alvo")
    sinais = [s for s in _como_lista(tri.get("sinais_atividade_economica")) if s != "nenhum"]
    conf = float(tri.get("confianca_triagem") or 0.0)
    codigos = {a.get("codigo") for a in (anot.get("alertas") or [])}
    cod_oport = {o.get("codigo") for o in (anot.get("oportunidades") or [])}

    # vocabulário fechado do bloco (checar_vocabulario só percorre atributos)
    for campo, chave in (("conteudo_imagem", "triagem.conteudo_imagem"),
                         ("atividade_economica_aparente", "triagem.atividade_economica_aparente"),
                         ("atividade_no_alvo", "triagem.atividade_no_alvo"),
                         ("natureza_atividade", "triagem.natureza_atividade"),
                         ("formalidade_aparente", "triagem.formalidade_aparente"),
                         ("uso_parcial_permitido", "triagem.uso_parcial_permitido")):
        val = tri.get(campo)
        permitidos = vocab["listas"].get(chave) or []
        if val is not None and permitidos and val not in permitidos and not str(val).startswith("outro:"):
            erros.append(("triagem", "triagem.%s" % campo,
                          "valor %r fora do vocabulário fechado" % val))
    permitidos_sinais = vocab["listas_multivaloradas"].get("triagem.sinais_atividade_economica") or []
    for s in _como_lista(tri.get("sinais_atividade_economica")):
        if s not in permitidos_sinais and not s.startswith("outro:"):
            erros.append(("triagem", "triagem.sinais_atividade_economica",
                          "sinal %r fora do vocabulário fechado" % s))

    # teto de fonte: a triagem é barata, mas atividade econômica em imagem aérea é quase cega
    teto_conteudo = teto_efetivo("triagem.conteudo_imagem", img, obs)
    if conf > teto_conteudo + TOL:
        erros.append(("triagem", "triagem.confianca_triagem",
                      "confianca_triagem %.2f acima do teto efetivo %.2f (fonte=%s)"
                      % (conf, teto_conteudo, img.get("fonte"))))
    if atividade in ("sinal_fraco", "sinal_forte"):
        teto_ativ = teto_efetivo("triagem.atividade_economica_aparente", img, obs)
        if teto_ativ <= 0.05:
            erros.append(("triagem", "triagem.atividade_economica_aparente",
                          "fonte %r não sustenta leitura de atividade econômica" % img.get("fonte")))
        elif conf > teto_ativ + TOL:
            erros.append(("triagem", "triagem.confianca_triagem",
                          "confianca_triagem %.2f acima do teto de atividade econômica %.2f na fonte %s"
                          % (conf, teto_ativ, img.get("fonte"))))

    # coerência interna do bloco
    if conteudo in CONTEUDO_COM_IMOVEL and e_imovel is False:
        erros.append(("triagem", "triagem.e_imovel",
                      "conteudo_imagem=%s é incompatível com e_imovel=false" % conteudo))
    if conteudo not in CONTEUDO_COM_IMOVEL and e_imovel is True:
        erros.append(("triagem", "triagem.e_imovel",
                      "conteudo_imagem=%s não retrata imóvel; e_imovel deveria ser false" % conteudo))

    # C-20
    if conteudo == "ininteligivel" and img.get("apta_para_leitura") is not False:
        erros.append(("triagem", "C-20",
                      "C-20: conteudo_imagem=ininteligivel exige imagem.apta_para_leitura=false"))

    # C-16 — imagem sem imóvel não gera atributo nem oportunidade; ela gera recoleta
    if e_imovel is False:
        preenchidos = [p for p, c in _iter_campos(anot) if c.get("valor") is not None]
        if preenchidos:
            erros.append(("triagem", "C-16",
                          "C-16: imagem sem imóvel não produz atributo cadastral (%d preenchidos: %s...)"
                          % (len(preenchidos), ", ".join(preenchidos[:3]))))
        if anot.get("oportunidades"):
            erros.append(("triagem", "C-16", "C-16: imagem sem imóvel não produz oportunidade cadastral"))
        if "IMAGEM_FORA_DE_ESCOPO" not in codigos:
            erros.append(("triagem", "C-16",
                          "C-16: e_imovel=false exige alerta IMAGEM_FORA_DE_ESCOPO"))

    if tri.get("apto_para_cadastro") is False:
        if not tri.get("motivo_descarte"):
            erros.append(("triagem", "triagem.motivo_descarte",
                          "apto_para_cadastro=false exige motivo_descarte"))
        if not tri.get("uso_parcial_permitido"):
            avisos.append(("triagem", "triagem.uso_parcial_permitido",
                           "declare para que a imagem ainda serve (medicao/interior/entorno/nenhum)"))

    # C-18 — atividade declarada sem sinal listado é opinião
    if atividade in ("sinal_fraco", "sinal_forte"):
        if not sinais:
            erros.append(("triagem", "C-18",
                          "C-18: atividade_economica_aparente=%s exige sinais_atividade_economica" % atividade))
        if not alvo:
            erros.append(("triagem", "C-18",
                          "C-18: atividade econômica detectada exige atividade_no_alvo (de quem é a atividade)"))
        if "ATIVIDADE_ECONOMICA_APARENTE" not in codigos:
            avisos.append(("triagem", "alertas",
                           "atividade econômica detectada sem alerta ATIVIDADE_ECONOMICA_APARENTE"))
    if atividade == "nenhuma_aparente" and sinais:
        erros.append(("triagem", "C-18",
                      "C-18: nenhuma_aparente com sinais listados (%s)" % ";".join(sinais)))

    uso = _valor(anot, "uso.uso_predominante")

    # C-17 — sinal forte no alvo tem que aparecer no uso ou virar oportunidade de categoria
    if atividade == "sinal_forte" and alvo == "no_imovel_alvo":
        if uso not in USOS_ECONOMICOS and not (cod_oport & OPORT_CATEGORIA):
            erros.append(("triagem", "C-17",
                          "C-17: sinal forte de atividade no imóvel-alvo com uso=%r e sem oportunidade de "
                          "categoria: registre o uso econômico ou abra a oportunidade" % uso))

    # C-19 — gate anti-contaminação: o comércio do vizinho não é do alvo
    if alvo in ("em_vizinho", "ambulante_via_publica") and uso in USOS_ECONOMICOS:
        erros.append(("triagem", "C-19",
                      "C-19: atividade atribuída a %s não sustenta uso %r no imóvel-alvo; use sinal próprio "
                      "do alvo ou marque atividade_no_alvo=no_imovel_alvo" % (alvo, uso)))
    if alvo == "em_vizinho" and "ATIVIDADE_DE_VIZINHO" not in codigos:
        avisos.append(("triagem", "alertas",
                       "atividade de vizinho sem alerta ATIVIDADE_DE_VIZINHO"))

    # atividade dentro de residência é o caso clássico de categoria divergente
    if tri.get("formalidade_aparente") == "atividade_domiciliar":
        if "ATIVIDADE_DOMICILIAR_APARENTE" not in codigos and \
                "ATIVIDADE_DOMICILIAR_POTENCIAL" not in cod_oport:
            avisos.append(("triagem", "formalidade_aparente",
                           "atividade domiciliar aparente sem alerta/oportunidade correspondente — "
                           "é o caso típico de categoria tarifária divergente"))


TIPOLOGIAS_MULTIUNIDADE = {"casas_geminadas", "sobrado_subdividido", "vila_corredor",
                           "condominio_horizontal", "edificio_vertical",
                           "condominio_vertical_multitorre", "predio_misto",
                           "galeria_comercial", "centro_comercial", "lote_multiplas_edificacoes"}
TIPOLOGIAS_VERTICAIS = {"edificio_vertical", "condominio_vertical_multitorre", "predio_misto",
                        "galeria_comercial", "centro_comercial"}
OPORT_SUBDIVISAO = {"PREDIO_MULTIPLAS_UNIDADES_CADASTRO_UNITARIO", "ECONOMIAS_OCULTAS_POTENCIAL",
                    "MULTIPLAS_UNIDADES_FISICAS", "CONDOMINIO_HORIZONTAL_CADASTRO_UNITARIO",
                    "GALERIA_COMERCIAL_SUBDIVIDIDA", "DIVERGENCIA_UC_ECONOMIAS"}
CAMPOS_TERRITORIAIS = ("face_id", "quadra_id", "logradouro_id", "cluster_oportunidade")


def checar_estrutura_imovel(anot, obs, vocab, erros, avisos):
    """Cadastro vertical: reconstruir a estrutura de unidades sem fundir as quatro medidas.

    unidades físicas != UCs elétricas != economias de água != hidrômetros. Fundir qualquer par
    delas produz um número que parece resposta e não é: a divergência entre elas É o produto.
    """
    est = anot.get("estrutura_imovel")
    versao = str(anot.get("schema_versao") or "0")
    if not est:
        if versao >= "1.5.0":
            erros.append(("estrutura_imovel", "estrutura_imovel",
                          "bloco estrutura_imovel obrigatório a partir de schema_versao 1.5.0"))
        return

    img = anot.get("imagem") or {}
    tip = est.get("tipologia")
    unidades = est.get("unidades_fisicas_estimadas")
    metodos = [m for m in (est.get("metodo_estimativa") or []) if m and m != "indeterminado"]
    conf = float(est.get("confianca_estrutural") or 0.0)

    permitidos = vocab["listas"].get("estrutura_imovel.tipologia") or []
    if tip and tip not in permitidos and not str(tip).startswith("outro:"):
        erros.append(("estrutura_imovel", "estrutura_imovel.tipologia",
                      "valor %r fora do vocabulário fechado" % tip))
    for campo in ("composicao_uso_terreo", "composicao_uso_superiores", "mudanca_estrutural"):
        val = est.get(campo)
        lista = vocab["listas"].get("estrutura_imovel." + campo) or []
        if val is not None and lista and val not in lista and not str(val).startswith("outro:"):
            erros.append(("estrutura_imovel", "estrutura_imovel." + campo,
                          "valor %r fora do vocabulário fechado" % val))
    lista_m = vocab["listas_multivaloradas"].get("estrutura_imovel.metodo_estimativa") or []
    for m in (est.get("metodo_estimativa") or []):
        if m not in lista_m:
            erros.append(("estrutura_imovel", "estrutura_imovel.metodo_estimativa",
                          "método %r fora do vocabulário fechado" % m))

    teto = teto_efetivo("estrutura_imovel.unidades_fisicas_estimadas", img, obs)
    if conf > teto + TOL:
        erros.append(("estrutura_imovel", "estrutura_imovel.confianca_estrutural",
                      "confianca_estrutural %.2f acima do teto efetivo %.2f (fonte=%s)"
                      % (conf, teto, img.get("fonte"))))

    # C-21 — contagem sem método é palpite com aparência de número
    if isinstance(unidades, int) and unidades > 1 and not metodos:
        erros.append(("estrutura_imovel", "C-21",
                      "C-21: unidades_fisicas_estimadas=%d exige metodo_estimativa declarado "
                      "(contagem_prumo, interfone, caixas_correio, medidores_energia...)" % unidades))

    # C-22 — vertical sem dimensionamento não reconstrói estrutura nenhuma
    if tip in TIPOLOGIAS_VERTICAIS:
        tem_dim = (est.get("pavimentos_qtd") and est.get("unidades_por_pavimento_estimadas"))
        tem_ident = bool((est.get("identificadores_unidades") or "").strip())
        if not tem_dim and not tem_ident:
            erros.append(("estrutura_imovel", "C-22",
                          "C-22: tipologia %r exige pavimentos_qtd + unidades_por_pavimento_estimadas "
                          "ou identificadores_unidades" % tip))
        if "ESTRUTURA_VERTICAL_DETECTADA" not in {a.get("codigo") for a in (anot.get("alertas") or [])}:
            avisos.append(("estrutura_imovel", "alertas",
                           "tipologia vertical sem alerta ESTRUTURA_VERTICAL_DETECTADA"))

    # coerência aritmética do prumo: torres x pavimentos x unidades/pavimento
    torres = est.get("torres_qtd") or 1
    pav_res = est.get("pavimentos_residenciais")
    upp = est.get("unidades_por_pavimento_estimadas")
    if all(isinstance(x, int) for x in (pav_res, upp)) and isinstance(unidades, int) and unidades:
        esperado = torres * pav_res * upp + (est.get("unidades_comerciais_estimadas") or 0)
        if esperado and abs(esperado - unidades) > max(1, round(0.25 * esperado)):
            avisos.append(("estrutura_imovel", "estrutura_imovel.unidades_fisicas_estimadas",
                           "R-EST-02: %d torre(s) x %d pavimento(s) x %d unidade(s) + %d comercial(is) = %d, "
                           "mas o registro declara %d — explique a diferença na evidência"
                           % (torres, pav_res, upp, est.get("unidades_comerciais_estimadas") or 0,
                              esperado, unidades)))

    # C-23 — divergência com o cadastro tem que virar fila, não ficar no bloco
    base = ((anot.get("vinculo") or {}).get("economias_agua_base"))
    cod_oport = {o.get("codigo") for o in (anot.get("oportunidades") or [])}
    if isinstance(unidades, int) and isinstance(base, int) and unidades > base:
        if not (cod_oport & OPORT_SUBDIVISAO):
            erros.append(("estrutura_imovel", "C-23",
                          "C-23: unidades físicas estimadas (%d) acima das economias cadastradas (%d) sem "
                          "oportunidade correspondente" % (unidades, base)))

    ucs = est.get("ucs_energia_visiveis")
    if isinstance(ucs, int) and isinstance(unidades, int) and ucs and unidades == ucs and metodos == ["medidores_energia"]:
        avisos.append(("estrutura_imovel", "R-EST-03",
                       "unidades físicas iguais às UCs com medidor como método único: UC elétrica não é "
                       "unidade física nem economia de água — busque uma segunda evidência (interfone, "
                       "caixas de correio, portas) ou registre a faixa"))

    if tip in TIPOLOGIAS_VERTICAIS:
        ev = (anot.get("economias") or {}).get("evidencias_contadas") or {}
        if isinstance(ev.get("portas_independentes"), int) and isinstance(unidades, int) and \
                unidades > 1 and ev["portas_independentes"] < unidades:
            avisos.append(("estrutura_imovel", "R-EST-04",
                           "em tipologia vertical a porta da testada conta acessos ao edifício, não "
                           "unidades: remova portas_independentes das evidências de economia e use "
                           "interfone, caixas de correio ou medidores"))

    if est.get("mudanca_estrutural") in ("casa_para_edificio", "lote_para_edificacao", "unidade_para_multiplas"):
        if "MUDANCA_ESTRUTURAL_FORTE" not in {a.get("codigo") for a in (anot.get("alertas") or [])}:
            avisos.append(("estrutura_imovel", "alertas",
                           "mudança estrutural declarada sem alerta MUDANCA_ESTRUTURAL_FORTE"))


def checar_mapeamento(anot, vocab, erros, avisos):
    """Atributos cartografáveis: a skill produz o insumo do mapa, não o mapa."""
    m = anot.get("mapeamento")
    if not m:
        return

    for campo in ("status_endereco", "transicao_uso", "vigencia_evidencia"):
        val = m.get(campo)
        lista = vocab["listas"].get("mapeamento." + campo) or []
        if val is not None and lista and val not in lista:
            erros.append(("mapeamento", "mapeamento." + campo,
                          "valor %r fora do vocabulário fechado" % val))

    # C-27 — quem preenche face/quadra/logradouro/cluster é o módulo territorial
    preenchidos = [c for c in CAMPOS_TERRITORIAIS if m.get(c)]
    if preenchidos:
        erros.append(("mapeamento", "C-27",
                      "C-27: campos de origem territorial preenchidos pela skill de fachada (%s): eles são "
                      "atribuídos pelo módulo pgv-mapeamento-cadastral ao agregar o território"
                      % ", ".join(preenchidos)))

    st = m.get("status_endereco")
    if st in ("confere", "diverge"):
        if not m.get("numero_observado") or not m.get("numero_base"):
            erros.append(("mapeamento", "C-24",
                          "C-24: status_endereco=%s exige numero_observado e numero_base" % st))
        elif st == "confere" and _canon_numero(m["numero_observado"]) != _canon_numero(m["numero_base"]):
            erros.append(("mapeamento", "C-24",
                          "C-24: status_endereco=confere com números distintos (%r vs %r)"
                          % (m["numero_observado"], m["numero_base"])))
        elif st == "diverge" and _canon_numero(m["numero_observado"]) == _canon_numero(m["numero_base"]):
            erros.append(("mapeamento", "C-24",
                          "C-24: status_endereco=diverge com números equivalentes (%r vs %r)"
                          % (m["numero_observado"], m["numero_base"])))
    if st == "hipotese_sequencia":
        if not m.get("numero_estimado_contextual") or not m.get("fonte_numero_estimado"):
            erros.append(("mapeamento", "C-24",
                          "C-24: hipótese de número exige numero_estimado_contextual e fonte_numero_estimado"))
        if float(m.get("confianca_numero_estimado") or 1.0) > 0.60 + TOL:
            erros.append(("mapeamento", "C-24",
                          "R-MAP-03: número por interpolação da face é hipótese, confiança máxima 0.60 "
                          "(declarado %.2f)" % float(m.get("confianca_numero_estimado") or 0)))

    # C-25
    cod_oport = {o.get("codigo") for o in (anot.get("oportunidades") or [])}
    if m.get("mudanca_uso") is True:
        if not m.get("uso_base") or not m.get("uso_observado"):
            erros.append(("mapeamento", "C-25", "C-25: mudanca_uso=true exige uso_base e uso_observado"))
        elif m["uso_base"] == m["uso_observado"]:
            erros.append(("mapeamento", "C-25",
                          "C-25: mudanca_uso=true com uso_base igual a uso_observado (%r)" % m["uso_base"]))
        elif not (cod_oport & OPORT_CATEGORIA):
            erros.append(("mapeamento", "C-25",
                          "C-25: mudança de uso declarada sem oportunidade de categoria correspondente"))

    # C-26 — valor esperado é probabilidade x impacto, não um número solto
    p, imp, ve = (m.get("probabilidade_confirmacao"), m.get("impacto_anual_estimado"),
                  m.get("valor_esperado_anual"))
    if ve is not None:
        if p is None or imp is None:
            erros.append(("mapeamento", "C-26",
                          "C-26: valor_esperado_anual exige probabilidade_confirmacao e impacto_anual_estimado"))
        elif abs(float(ve) - float(p) * float(imp)) > max(0.01, 0.02 * float(imp)):
            erros.append(("mapeamento", "C-26",
                          "C-26: valor_esperado_anual %.2f != probabilidade %.2f x impacto %.2f (= %.2f)"
                          % (float(ve), float(p), float(imp), float(p) * float(imp))))

    gap, eco, ucs = m.get("gap_uc_economias"), m.get("economias_base"), m.get("ucs_energia")
    if gap is not None and isinstance(eco, int) and isinstance(ucs, int) and gap != ucs - eco:
        erros.append(("mapeamento", "mapeamento.gap_uc_economias",
                      "gap declarado %s != ucs_energia (%d) - economias_base (%d)" % (gap, ucs, eco)))

    idade = (anot.get("imagem") or {}).get("idade_meses")
    if idade is not None and m.get("vigencia_evidencia"):
        faixa = ("0_12m" if idade <= 12 else "13_24m" if idade <= 24 else
                 "25_36m" if idade <= 36 else "37_60m" if idade <= 60 else "acima_60m")
        if m["vigencia_evidencia"] != faixa:
            erros.append(("mapeamento", "mapeamento.vigencia_evidencia",
                          "vigência declarada %s incompatível com idade_meses=%s (esperado %s)"
                          % (m["vigencia_evidencia"], idade, faixa)))


def checar_schema(anot, schema, erros):
    """Executa o contrato JSON Schema real. O --schema não é decorativo."""
    if Draft202012Validator is None:
        erros.append(("schema", "raiz", "dependência jsonschema ausente; instale `pip install jsonschema`"))
        return
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        erros.append(("schema", "asset", "schema inválido: %s" % exc))
        return
    val = Draft202012Validator(schema)
    encontrados = sorted(val.iter_errors(anot), key=lambda e: (list(e.absolute_path), e.message))
    for e in encontrados:
        path = ".".join(str(x) for x in e.absolute_path) or "raiz"
        erros.append(("schema", path, e.message))


def checar_temporalidade_oportunidades(anot, erros, avisos):
    img = anot.get("imagem") or {}
    attrs = anot.get("atributos") or {}
    uso = attrs.get("uso") or {}
    energia = attrs.get("energia") or {}
    vinc = anot.get("vinculo") or {}
    ops = anot.get("oportunidades") or []
    opcodes = {o.get("codigo") for o in ops if isinstance(o, dict)}

    data = img.get("data_captura")
    ano = img.get("ano_captura")
    if data and ano is not None:
        try:
            if int(str(data)[:4]) != int(ano):
                erros.append(("temporalidade", "imagem.ano_captura",
                              "C-13: ano_captura diverge de data_captura"))
        except Exception:
            pass

    # Comércio/atividade é uma afirmação temporal: sem data não pode circular como estado atual.
    campos_comercio = ["nome_estabelecimento_visivel", "atividade_letreiro",
                       "descricao_atividade_funcional", "situacao_estabelecimento_na_data_imagem",
                       "sinais_atividade_comercial"]
    comercio_preenchido = any(isinstance(uso.get(c), dict) and uso[c].get("valor") not in (None, "")
                              for c in campos_comercio)
    if comercio_preenchido and not data and img.get("fonte") in ("streetview", "mapillary"):
        erros.append(("temporalidade", "imagem.data_captura",
                      "C-11: leitura comercial em Street View/Mapillary exige data_captura conhecida"))

    med = energia.get("medidores_energia_qtd") or {}
    med_qtd = med.get("valor") if isinstance(med, dict) else None
    if isinstance(med_qtd, int) and med_qtd > 1:
        if "MULTIPLAS_UCS_MESMO_ENDERECO" not in opcodes:
            erros.append(("oportunidades", "energia.medidores_energia_qtd",
                          "C-12/R-UC-01: mais de um medidor exige oportunidade MULTIPLAS_UCS_MESMO_ENDERECO"))
        eco_base = vinc.get("economias_agua_base")
        if isinstance(eco_base, int) and eco_base < med_qtd and "DIVERGENCIA_UC_ECONOMIAS" not in opcodes:
            erros.append(("oportunidades", "vinculo.economias_agua_base",
                          "R-UC-02: UCs visíveis (%d) > economias_agua_base (%d); exige DIVERGENCIA_UC_ECONOMIAS"
                          % (med_qtd, eco_base)))

    uso_val = (uso.get("uso_predominante") or {}).get("valor") if isinstance(uso.get("uso_predominante"), dict) else None
    uso_base = str(vinc.get("uso_base") or "").lower()
    if uso_val in ("comercial", "misto_res_com", "servicos") and uso_base in ("residencial", "res"):
        if "USO_COMERCIAL_NAO_CADASTRADO" not in opcodes and "USO_MISTO_POTENCIAL" not in opcodes:
            avisos.append(("oportunidades", "uso.uso_predominante",
                           "uso visual comercial/misto diverge de uso_base residencial sem oportunidade explícita"))

    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        if op.get("automatizavel") is not False:
            erros.append(("oportunidades", "oportunidades.%d" % i,
                          "oportunidade cadastral deve ter automatizavel=false"))
        if op.get("codigo") in {"USO_COMERCIAL_NAO_CADASTRADO", "USO_MISTO_POTENCIAL",
                                "ATIVIDADE_COMERCIAL_MUDOU", "COMERCIO_APARENTA_DESATIVADO"}:
            if not op.get("data_referencia"):
                erros.append(("temporalidade", "oportunidades.%d.data_referencia" % i,
                              "oportunidade comercial exige data_referencia"))

def checar_endereco_e_convergencia(anot, erros, avisos):
    """Gates PGV v1.2: identidade por número e fonte única != achado."""
    vinc = anot.get("vinculo") or {}
    ops = anot.get("oportunidades") or []
    alertas = anot.get("alertas") or []
    opcodes = {o.get("codigo") for o in ops if isinstance(o, dict)}
    alertcodes = {a.get("codigo") for a in alertas if isinstance(a, dict)}

    num_visual = _valor(anot, "enderecamento.numero_endereco_consolidado")
    if num_visual in (None, ""):
        num_visual = _valor(anot, "enderecamento.numero_fachada")
    num_base = vinc.get("numero_base")
    cv, cb = _canon_numero(num_visual), _canon_numero(num_base)

    if cv and cb:
        diverge = cv != cb
        if diverge:
            if vinc.get("numero_confere") is not False:
                erros.append(("vinculo", "vinculo.numero_confere",
                              "C-14/R-END-06: número visual %r diverge de numero_base %r; exige numero_confere=false"
                              % (num_visual, num_base)))
            if "DIVERGENCIA_NUMERO_ENDERECO" not in opcodes:
                erros.append(("oportunidades", "enderecamento.numero_endereco_consolidado",
                              "C-14/R-END-06: divergência de número exige oportunidade DIVERGENCIA_NUMERO_ENDERECO"))
            if not ({"NUMERO_DIVERGE_CADASTRO", "DIVERGENCIA_NUMERO_ENDERECO"} & alertcodes):
                erros.append(("vinculo", "alertas",
                              "C-14/R-END-06: divergência de número exige alerta de identidade/endereço"))
            metodo = vinc.get("metodo_vinculo")
            if metodo in ("gps_numero", "endereco_textual"):
                if float(vinc.get("confianca_vinculo") or 0) >= 0.80:
                    erros.append(("vinculo", "vinculo.confianca_vinculo",
                                  "R-END-06: vínculo baseado em endereço com número divergente deve ficar abaixo de 0.80 até resolução"))
                if vinc.get("ambiguidade") is not True:
                    erros.append(("vinculo", "vinculo.ambiguidade",
                                  "R-END-06: vínculo por endereço com número divergente exige ambiguidade=true"))
        elif vinc.get("numero_confere") is False:
            avisos.append(("vinculo", "vinculo.numero_confere",
                           "número visual e numero_base são equivalentes após normalização, mas numero_confere=false"))

    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            continue
        nivel = op.get("nivel_evidencia")
        fontes = [f for f in (op.get("fontes_independentes") or []) if f]
        unicas = set(fontes)
        if nivel == "achado_convergente" and len(unicas) < 2:
            erros.append(("convergencia", "oportunidades.%d.fontes_independentes" % i,
                          "C-15/R-CONV-02: achado_convergente exige pelo menos duas fontes independentes"))
        if nivel == "contradicao" and len(unicas) < 2:
            erros.append(("convergencia", "oportunidades.%d.fontes_independentes" % i,
                          "R-CONV-04: contradicao exige pelo menos duas fontes em conflito"))
        if op.get("codigo") in {"DIVERGENCIA_UC_ECONOMIAS", "USO_COMERCIAL_NAO_CADASTRADO",
                                "ECONOMIAS_OCULTAS_POTENCIAL"}:
            if nivel == "achado_convergente" and "cadastro_companhia" not in unicas:
                erros.append(("convergencia", "oportunidades.%d.fontes_independentes" % i,
                              "achado de divergência com base deve declarar cadastro_companhia entre as fontes"))
        if op.get("codigo") == "DIVERGENCIA_NUMERO_ENDERECO" and nivel not in ("contradicao", "achado_convergente"):
            avisos.append(("convergencia", "oportunidades.%d.nivel_evidencia" % i,
                           "divergência explícita de número normalmente deve ser nivel_evidencia=contradicao"))


# ------------------------------------------------------------------------- runner

def validar(caminho, schema, obs, vocab):
    erros, avisos, contadores = [], [], {}
    try:
        anot = _carregar(caminho)
    except Exception as exc:  # JSON quebrado é reprovação, não crash do lote
        return {"arquivo": caminho, "status": "reprovado", "erros": [
            {"camada": "estrutura", "campo": "arquivo", "mensagem": "JSON ilegível: %s" % exc}],
            "avisos": [], "metricas": {}}

    img = anot.get("imagem") or {}
    checar_schema(anot, schema, erros)
    checar_estrutura(anot, erros)
    if not erros or img:
        checar_vocabulario(anot, vocab, erros, avisos, contadores)
        checar_confianca(anot, obs, erros)
        checar_coerencia(anot, vocab, erros, avisos)
        checar_lgpd(anot, vocab, erros, avisos)
        checar_economias(anot, erros, avisos)
        checar_temporalidade_oportunidades(anot, erros, avisos)
        checar_endereco_e_convergencia(anot, erros, avisos)
        checar_triagem(anot, obs, vocab, erros, avisos)
        checar_estrutura_imovel(anot, obs, vocab, erros, avisos)
        checar_mapeamento(anot, vocab, erros, avisos)

    campos = list(_iter_campos(anot))
    preenchidos = [c for _, c in campos if c.get("valor") is not None]
    confs = [float(c.get("confianca") or 0) for c in preenchidos]
    metricas = {
        "campos_totais": len(campos),
        "campos_preenchidos": len(preenchidos),
        "campos_nao_observaveis": sum(1 for _, c in campos if c.get("juizo") == "nao_observavel"),
        "confianca_media": round(sum(confs) / len(confs), 3) if confs else 0.0,
        "confianca_minima": round(min(confs), 3) if confs else 0.0,
        "alertas": len(anot.get("alertas") or []),
        "valores_outro": len(contadores.get("outro", [])),
    }
    # Auditoria declarada não é aceita por fé: deve bater com o que o pipeline recalcula.
    aud = anot.get("auditoria") or {}
    if aud.get("campos_preenchidos") is not None and aud.get("campos_preenchidos") != metricas["campos_preenchidos"]:
        erros.append(("auditoria", "auditoria.campos_preenchidos",
                      "valor declarado %s diverge do recalculado %s" %
                      (aud.get("campos_preenchidos"), metricas["campos_preenchidos"])))
    if aud.get("campos_nao_observaveis") is not None and aud.get("campos_nao_observaveis") != metricas["campos_nao_observaveis"]:
        erros.append(("auditoria", "auditoria.campos_nao_observaveis",
                      "valor declarado %s diverge do recalculado %s" %
                      (aud.get("campos_nao_observaveis"), metricas["campos_nao_observaveis"])))
    if aud.get("confianca_media") is not None:
        try:
            if abs(float(aud.get("confianca_media")) - metricas["confianca_media"]) > 0.02:
                erros.append(("auditoria", "auditoria.confianca_media",
                              "valor declarado %.3f diverge do recalculado %.3f" %
                              (float(aud.get("confianca_media")), metricas["confianca_media"])))
        except Exception:
            pass
    # Triagem que barra a imagem (fora de escopo ou inapta) encerra o protocolo cedo — e isso é
    # o comportamento desejado, não um passe esquecido. Só cobra os 7 passes de quem seguiu adiante.
    fora_de_escopo = ((anot.get("triagem") or {}).get("e_imovel") is False
                      or (anot.get("triagem") or {}).get("apto_para_cadastro") is False)
    if (img.get("apta_para_leitura") is True and not fora_de_escopo
            and int(aud.get("passes_executados") or 0) < 6):
        avisos.append(("auditoria", "auditoria.passes_executados",
                       "protocolo possui 7 passes (0-triagem a 6-reconstrução); registro declara %s"
                       % aud.get("passes_executados")))

    if metricas["confianca_media"] and metricas["confianca_media"] < 0.5:
        codigos = {a.get("codigo") for a in (anot.get("alertas") or [])}
        if "REVISAO_HUMANA" not in codigos:
            avisos.append(("auditoria", "alertas",
                           "confianca_media %.2f < 0.50 sem alerta REVISAO_HUMANA" % metricas["confianca_media"]))

    return {
        "arquivo": caminho,
        "status": "reprovado" if erros else "aprovado",
        "erros": [{"camada": c, "campo": p, "mensagem": m} for c, p, m in erros],
        "avisos": [{"camada": c, "campo": p, "mensagem": m} for c, p, m in avisos],
        "metricas": metricas,
    }


def main():
    ap = argparse.ArgumentParser(description="Valida anotações de fachada (padrão A2L).")
    ap.add_argument("alvo", help="arquivo .json ou diretório de anotações")
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    ap.add_argument("--schema", default=os.path.join(base, "schema_fachada.json"))
    ap.add_argument("--observabilidade", default=os.path.join(base, "observabilidade.json"))
    ap.add_argument("--vocabulario", default=os.path.join(base, "vocabulario.json"))
    ap.add_argument("--relatorio", default=None, help="caminho do relatório JSON de saída")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        schema = _carregar(args.schema)
        obs = _carregar(args.observabilidade)
        vocab = _carregar(args.vocabulario)
    except Exception as exc:
        print("ERRO ao carregar assets: %s" % exc, file=sys.stderr)
        return 2

    arquivos = _listar_json(args.alvo)
    if not arquivos:
        print("Nenhum .json encontrado em %s" % args.alvo, file=sys.stderr)
        return 2

    resultados = [validar(a, schema, obs, vocab) for a in arquivos]
    reprovados = [r for r in resultados if r["status"] == "reprovado"]

    relatorio = {
        "total": len(resultados),
        "aprovados": len(resultados) - len(reprovados),
        "reprovados": len(reprovados),
        "total_erros": sum(len(r["erros"]) for r in resultados),
        "total_avisos": sum(len(r["avisos"]) for r in resultados),
        "resultados": resultados,
    }

    if args.relatorio:
        os.makedirs(os.path.dirname(os.path.abspath(args.relatorio)) or ".", exist_ok=True)
        with open(args.relatorio, "w", encoding="utf-8") as fh:
            json.dump(relatorio, fh, ensure_ascii=False, indent=2)

    if not args.quiet:
        print("Anotações: %d | aprovadas: %d | reprovadas: %d | erros: %d | avisos: %d"
              % (relatorio["total"], relatorio["aprovados"], relatorio["reprovados"],
                 relatorio["total_erros"], relatorio["total_avisos"]))
        for r in resultados:
            if r["erros"] or r["avisos"]:
                print("\n%s  [%s]" % (os.path.basename(r["arquivo"]), r["status"].upper()))
                for e in r["erros"]:
                    print("  ERRO   %-12s %-42s %s" % (e["camada"], e["campo"], e["mensagem"]))
                for a in r["avisos"]:
                    print("  aviso  %-12s %-42s %s" % (a["camada"], a["campo"], a["mensagem"]))

    return 1 if reprovados else 0


if __name__ == "__main__":
    sys.exit(main())
