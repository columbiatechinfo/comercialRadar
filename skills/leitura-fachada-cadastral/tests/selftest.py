#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selftest da skill leitura-fachada-cadastral.

Gate nas duas pontas: roda o validador sobre fixtures de comportamento conhecido e confere que
cada defeito plantado é efetivamente pego. Um validador que aprova tudo é pior que nenhum — ele
dá a sensação de controle sem o controle. Depois roda consolidar, painel e conciliação de ponta a ponta.

    python3 tests/selftest.py       # 0 = verde, 1 = alguma checagem falhou
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(RAIZ, "tests", "fixtures")
SCRIPTS = os.path.join(RAIZ, "scripts")

# fixture -> (status esperado, [(camada, trecho da mensagem) que devem aparecer])
ESPERADO = {
    "ok_campo_casa_terrea.json": ("aprovado", []),
    "ok_predio_multiplas_unidades.json": ("aprovado", []),
    "erro_estrutura_e_mapeamento.json": ("reprovado", [
        ("estrutura_imovel", "C-21"), ("estrutura_imovel", "C-22"), ("estrutura_imovel", "C-23"),
        ("mapeamento", "C-25"), ("mapeamento", "C-26"), ("mapeamento", "C-27")]),
    "ok_triagem_nao_imovel.json": ("aprovado", []),
    "ok_atividade_domiciliar.json": ("aprovado", []),
    "erro_triagem_ausente.json": ("reprovado", [("triagem", "obrigatório a partir de schema_versao 1.4.0")]),
    "erro_atividade_vizinho_contamina.json": ("reprovado", [("triagem", "C-19")]),
    "ok_comercio_multiplas_ucs.json": ("aprovado", []),
    "ok_endereco_caixas_correio_economias.json": ("aprovado", []),
    "erro_schema_vazio.json": ("reprovado", [("schema", "non-empty")]),
    "erro_uc_sem_oportunidade.json": ("reprovado", [("oportunidades", "MULTIPLAS_UCS_MESMO_ENDERECO"), ("oportunidades", "DIVERGENCIA_UC_ECONOMIAS")]),
    "erro_comercio_sem_data.json": ("reprovado", [("temporalidade", "C-11")]),
    "erro_achado_fonte_unica.json": ("reprovado", [("convergencia", "C-15/R-CONV-02")]),
    "erro_numero_divergente_vinculo.json": ("reprovado", [
        ("vinculo", "C-14/R-END-06"),
        ("oportunidades", "DIVERGENCIA_NUMERO_ENDERECO"),
        ("vinculo", "abaixo de 0.80"),
    ]),
    "erro_streetview_teto_e_lgpd.json": ("reprovado", [
        ("confianca", "acima do teto efetivo"),          # hidrômetro 0.90 em Street View
        ("coerencia", "C-06"),                            # nao_observavel com valor preenchido
        ("coerencia", "C-08"),                            # inferido sem regra nomeada
        ("coerencia", "C-03"),                            # leitura de mostrador fora de foto de campo
        ("lgpd", "dado_pessoal_transcrito"),
        ("lgpd", "termo juridicamente carregado"),        # "gato" em campo de valor
        ("vocabulario", "fora do vocabul"),               # asfalto_novo
        ("economias", "R-ECO-08"),                        # unitario_default com confiança 0.80
    ]),
    "erro_divergencia_economias.json": ("reprovado", [
        ("economias", "R-ECO-09"),                        # 4 medidores x 1 porta, divergencia=false
    ]),
}


def falha(msg, acumulador):
    acumulador.append(msg)
    print("  FALHOU: %s" % msg)


def main():
    problemas = []
    tmp = tempfile.mkdtemp(prefix="selftest_fachada_")
    try:
        # ---------------------------------------------------------------- validador
        print("[1/5] Validador sobre fixtures")
        rel = os.path.join(tmp, "validacao.json")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "validar_anotacao.py"), FIX,
             "--relatorio", rel, "--quiet"],
            capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            falha("validador terminou com código %d: %s" % (proc.returncode, proc.stderr), problemas)
            return 1

        with open(rel, encoding="utf-8") as fh:
            relatorio = json.load(fh)
        por_arquivo = {os.path.basename(r["arquivo"]): r for r in relatorio["resultados"]}

        for nome, (status_esp, checagens) in ESPERADO.items():
            r = por_arquivo.get(nome)
            if not r:
                falha("fixture não avaliada: %s" % nome, problemas)
                continue
            if r["status"] != status_esp:
                falha("%s: status %s, esperado %s (erros: %s)"
                      % (nome, r["status"], status_esp,
                         "; ".join(e["mensagem"] for e in r["erros"]) or "nenhum"), problemas)
            for camada, trecho in checagens:
                achou = any(e["camada"] == camada and trecho.lower() in e["mensagem"].lower()
                            for e in r["erros"])
                if not achou:
                    falha("%s: defeito plantado não detectado -> [%s] %s"
                          % (nome, camada, trecho), problemas)
            print("  %-40s %s (%d erros, %d avisos)"
                  % (nome, r["status"], len(r["erros"]), len(r["avisos"])))

        # ------------------------------------------------------- determinismo do validador
        print("[2/5] Determinismo (duas execuções idênticas)")
        rel2 = os.path.join(tmp, "validacao2.json")
        subprocess.run([sys.executable, os.path.join(SCRIPTS, "validar_anotacao.py"), FIX,
                        "--relatorio", rel2, "--quiet"], capture_output=True, text=True)
        with open(rel, encoding="utf-8") as a, open(rel2, encoding="utf-8") as b:
            if a.read() != b.read():
                falha("relatório não determinístico entre execuções", problemas)
            else:
                print("  relatórios idênticos")

        # ---------------------------------------------------------------- consolidação
        print("[3/5] Consolidação")
        out = os.path.join(tmp, "fachadas")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "consolidar.py"), FIX, "--out", out,
             "--formato", "csv"], capture_output=True, text=True)
        if proc.returncode != 0:
            falha("consolidar.py falhou: %s" % proc.stderr, problemas)
        else:
            import csv as _csv
            with open(out + ".csv", encoding="utf-8-sig") as fh:
                linhas = list(_csv.DictReader(fh, delimiter=";"))
            if len(linhas) != len(ESPERADO):
                falha("consolidado com %d linhas, esperado %d" % (len(linhas), len(ESPERADO)), problemas)
            obrig = ["agua_hidrometro_presente", "agua_hidrometro_presente_conf",
                     "agua_hidrometro_presente_juizo", "agua_hidrometro_presente_regra",
                     "economias_estimativa", "alerta_impedimento_leitura",
                     "esgoto_caixa_inspecao_aparente", "energia_medidores_energia_qtd",
                     "enderecamento_numero_caixa_correio", "enderecamento_numero_endereco_consolidado",
                     "achados_convergentes_total", "oportunidade_economias_ocultas",
                     "triagem_conteudo_imagem", "triagem_e_imovel",
                     "triagem_atividade_economica_aparente", "triagem_atividade_no_alvo",
                     "triagem_formalidade_aparente", "alerta_imagem_fora_de_escopo",
                     "estrutura_tipologia", "estrutura_unidades_fisicas_estimadas",
                     "mapeamento_status_endereco", "mapeamento_gap_uc_economias",
                     "mapeamento_valor_esperado_anual"]
            ausentes = [c for c in obrig if c not in (linhas[0].keys() if linhas else [])]
            if ausentes:
                falha("colunas ausentes no consolidado: %s" % ", ".join(ausentes), problemas)
            if not os.path.isfile(out + "_dicionario.csv"):
                falha("dicionário de dados não gerado", problemas)
            # cabeçalho estável: consolidar 1 fixture deve dar as mesmas colunas de 3
            uma = os.path.join(tmp, "uma")
            os.makedirs(uma, exist_ok=True)
            shutil.copy(os.path.join(FIX, "ok_campo_casa_terrea.json"), uma)
            subprocess.run([sys.executable, os.path.join(SCRIPTS, "consolidar.py"), uma,
                            "--out", os.path.join(tmp, "uma_out"), "--formato", "csv"],
                           capture_output=True, text=True)
            with open(os.path.join(tmp, "uma_out.csv"), encoding="utf-8-sig") as fh:
                cab_uma = fh.readline()
            with open(out + ".csv", encoding="utf-8-sig") as fh:
                cab_tres = fh.readline()
            if cab_uma != cab_tres:
                falha("cabeçalho varia com o conteúdo do lote (deveria vir do schema)", problemas)
            else:
                print("  %d linhas, %d colunas, cabeçalho estável"
                      % (len(linhas), len(linhas[0]) if linhas else 0))

        # ---------------------------------------------------------------------- painel
        print("[4/5] Painel HTML")
        painel = os.path.join(tmp, "painel.html")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "painel_fachadas.py"), out + ".csv",
             "--out", painel], capture_output=True, text=True)
        if proc.returncode != 0:
            falha("painel_fachadas.py falhou: %s" % proc.stderr, problemas)
        elif not os.path.isfile(painel) or os.path.getsize(painel) < 2000:
            falha("painel gerado vazio ou truncado", problemas)
        else:
            print("  painel gerado (%d KB)" % (os.path.getsize(painel) // 1024 or 1))

        # --------------------------------------------------- conciliação multi-imagem
        print("[5/5] Conciliação multi-imagem")
        fix_conc = os.path.join(RAIZ, "tests", "fixtures_conciliacao")
        conc = os.path.join(tmp, "conc")
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "conciliar_multifoto.py"), fix_conc,
             "--chave", "matricula", "--out", conc,
             "--relatorio", os.path.join(tmp, "conc_rel.json")],
            capture_output=True, text=True)
        if proc.returncode not in (0, 1):
            falha("conciliar_multifoto.py falhou: %s" % proc.stderr, problemas)
        else:
            with open(conc + ".json", encoding="utf-8") as fh:
                reg = json.load(fh)
            if len(reg) != 1:
                falha("conciliação produziu %d imóveis, esperado 1" % len(reg), problemas)
            else:
                r = reg["1234567"]
                campos = r["campos"]
                # 3 imagens, 2 classes de fonte independentes (fachada + aérea)
                if r["n_imagens"] != 3 or r["fontes_independentes_de_imagem"] != 2:
                    falha("agrupamento errado: %d imagens / %d classes"
                          % (r["n_imagens"], r["fontes_independentes_de_imagem"]), problemas)
                # campo volátil divergente entre datas => série temporal, não contradição
                uso = campos.get("uso.uso_predominante", {})
                if uso.get("status") != "temporal" or uso.get("valor") != "residencial":
                    falha("uso divergente entre datas deveria ser temporal/residencial, veio %s/%s"
                          % (uso.get("status"), uso.get("valor")), problemas)
                # campo estável divergente => contradição sinalizada, nunca silenciada
                pav = campos.get("edificacao.pavimentos_visiveis", {})
                if pav.get("status") != "contradicao":
                    falha("pavimentos divergentes deveriam gerar contradicao, veio %s"
                          % pav.get("status"), problemas)
                # complementaridade: campo exclusivo do drone e campo exclusivo da foto de campo
                if campos.get("edificacao.area_permeavel", {}).get("fonte_vencedora") != "drone":
                    falha("area_permeavel deveria vir do drone", problemas)
                if campos.get("agua.hidrometro_presente", {}).get("fonte_vencedora") != "campo":
                    falha("hidrometro deveria vir da foto de campo", problemas)
                # sem --elevar-convergencia nada é promovido
                if any(o.get("nivel_evidencia") == "achado_convergente"
                       for o in r["oportunidades"]):
                    falha("promoção a achado_convergente sem flag explícita", problemas)
                print("  1 imóvel, %d imagens, %d contradição(ões), %d divergência(s) temporal(is)"
                      % (r["n_imagens"], len(r["contradicoes"]), len(r["divergencias_temporais"])))

            conc2 = os.path.join(tmp, "conc2")
            subprocess.run([sys.executable, os.path.join(SCRIPTS, "conciliar_multifoto.py"), fix_conc,
                            "--chave", "matricula", "--out", conc2], capture_output=True, text=True)
            with open(conc + ".json", encoding="utf-8") as a, open(conc2 + ".json", encoding="utf-8") as b:
                if a.read() != b.read():
                    falha("conciliação não determinística entre execuções", problemas)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%s" % ("SELFTEST VERDE" if not problemas
                    else "SELFTEST VERMELHO — %d problema(s)" % len(problemas)))
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main())
