# -*- coding: utf-8 -*-
"""fichas_cnpj.py — a ficha do Serasa dos CNPJs que a base nao data (dono do produto, 15/09/2026).

Regra: so se consulta a ficha do CNPJ que NAO veio com situacao e data de abertura da
nossa base da Receita — na pratica, o CNPJ que o iFood e o Cadastur trazem sem que haja
o registro da Receita do mesmo CNPJ na ligacao. O print vai para o Storage (a SEEK
exibe); a IA recebe o texto estruturado (`texto_ia`) — tabela `ficha_cnpj_web` (0113).

Roda na imagem do Camoufox (`radar-busca-camoufox`), com o rodizio de proxies da busca
web e navegador quente: bloqueio e registrado e o IP vai para o castigo; nada e
contornado.

    python fichas_cnpj.py --ligacoes-arquivo ligs.txt --navegadores 3 --aplicar
    python fichas_cnpj.py --ligacoes-arquivo ligs.txt --listar      # so os CNPJs, sem consultar
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import tempfile
import threading
import time

sys.path.insert(0, "/app")
import base_comum as bc  # noqa: E402

#: nao reconsulta ficha lida ha menos disto (e sem bloqueio)
VALIDADE_DIAS = 30

ROTULOS_SERASA = ["Razão Social", "Nome Fantasia", "Data de fundação", "Situação Cadastral",
                  "Código e descrição da natureza jurídica", "Matriz/Filial",
                  "Código e descrição da atividade econômica principal",
                  "Código e descrição da atividade econômica secundária",
                  "Logradouro", "Bairro", "CEP", "Município", "UF", "Telefone"]
CORTES = ("Localização e contato da empresa", "Dados da receita federal", "Consultar score", "Informamos que",
          "Consultar", "Perguntas frequentes", "Empresas relacionadas", "Veja também")
NOMES = {"Razão Social": "razão social", "Nome Fantasia": "nome fantasia", "Data de fundação": "fundação",
         "Situação Cadastral": "situação cadastral", "Código e descrição da natureza jurídica": "natureza jurídica",
         "Matriz/Filial": "matriz/filial", "Código e descrição da atividade econômica principal": "atividade principal",
         "Código e descrição da atividade econômica secundária": "atividades secundárias"}


def _log(m):
    print(m, flush=True)


def campos_serasa(texto):
    """Os campos da ficha pelo texto da pagina (testado nas 88 fichas da amostra de 14/09/2026)."""
    linhas = [l.strip() for l in (texto or "").split("\n") if l.strip()]
    pos = {}
    for i, l in enumerate(linhas):
        if l in ROTULOS_SERASA and l not in pos:
            pos[l] = i
    ordem = sorted(pos.items(), key=lambda x: x[1])
    val = {}
    for j, (rot, i) in enumerate(ordem):
        fim = ordem[j + 1][1] if j + 1 < len(ordem) else len(linhas)
        vs = []
        for l in linhas[i + 1:fim]:
            if any(l.startswith(c) for c in CORTES):
                break
            vs.append("oculto" if set(l) <= {"*"} else l)
        val[rot] = "; ".join(vs) if vs else "—"
    return val


def texto_ia(cnpj, v, quando):
    c = "%s.%s.%s/%s-%s" % (cnpj[:2], cnpj[2:5], cnpj[5:8], cnpj[8:12], cnpj[12:])
    partes = ["%s: %s" % (NOMES[k], v[k]) for k in NOMES if k in v]
    end = ", ".join(x for x in (v.get("Logradouro"), v.get("Bairro"), ("CEP " + v["CEP"]) if v.get("CEP") else None,
                                "%s/%s" % (v.get("Município") or "?", v.get("UF") or "?")) if x)
    partes.append("endereço: %s" % end)
    if "Telefone" in v:
        partes.append("telefone: %s" % v["Telefone"])
    return "FICHA DO CNPJ %s NO SERASA (consultada em %s):\n%s" % (c, quando.strftime("%d/%m/%Y"), "\n".join(partes))


def _data_br(s):
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    except ValueError:
        return None


def alvos(con, ligacoes):
    """[(id_empresa, cnpj, ligacao)]: CNPJ de POI das ligacoes sem situacao/abertura na base e sem o registro da
    Receita do mesmo CNPJ na ligacao, e sem ficha valida consultada ha menos de VALIDADE_DIAS."""
    cur = con.cursor()
    cur.execute("""select lp.ligacao, p.id_empresa::text, regexp_replace(coalesce(p.cnpj, ''), '\\D', '', 'g'),
                          lower(coalesce(p.fonte, '')), rd.situacao_cadastral, rd.bruto->>'data_inicio'
                     from radar_comercial.ligacao_poi lp
                     join radar_comercial.pois p on p.id = lp.poi_id and p.fundido_em is null
                     left join radar_comercial.receita_data rd on rd.poi_id = p.id
                    where lp.ligacao = any(%s) and lp.descartado_em is null""", (list(ligacoes),))
    linhas = cur.fetchall()
    com_base = {(l, c) for l, _e, c, f, s, d in linhas if len(c) == 14 and s and d}
    cur.execute("""select id_empresa::text, cnpj from radar_comercial.ficha_cnpj_web
                    where fonte = 'serasa' and not bloqueado and texto_ia is not null
                      and consultado_em > now() - make_interval(days => %s)""", (VALIDADE_DIAS,))
    ja = set(cur.fetchall())
    con.rollback()
    saida, vistos = [], set()
    for l, e, c, _f, s, d in linhas:
        if len(c) != 14 or (s and d) or (l, c) in com_base or (e, c) in ja or (e, c) in vistos:
            continue
        vistos.add((e, c))
        saida.append((e, c, l))
    return saida


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ligacoes-arquivo", dest="ligacoes_arquivo", required=True)
    p.add_argument("--navegadores", type=int, default=3)
    p.add_argument("--listar", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)
    ligs = [x.strip() for x in open(a.ligacoes_arquivo) if x.strip()]
    con = bc.conectar()
    lista = alvos(con, ligs)
    _log("▶ fichas do CNPJ: %d ligacoes · %d CNPJs sem situacao/abertura na base a consultar" % (len(ligs), len(lista)))
    if a.listar or not lista:
        for e, c, l in lista:
            _log("   %s %s" % (l, c))
        return 0

    import busca_navegador as bn
    import coleta_provas as cp
    import imagens

    proximo = bn.rodizio(quantos=30, pais="", embaralhar=True)
    trava_ip, trava_db = threading.Lock(), threading.Lock()

    def ip():
        with trava_ip:
            return proximo()

    placar = {"ok": 0, "bloqueado": 0, "falha": 0}
    fila = list(lista)
    trava_fila = threading.Lock()
    pasta = tempfile.mkdtemp(prefix="fichas_")
    t0 = time.time()

    def trabalhador(n):
        nav = cp.Navegador(ip)
        try:
            while True:
                with trava_fila:
                    if not fila:
                        return
                    emp, cnpj, lig = fila.pop(0)
                r = cp.coletar_serasa(nav, cnpj, pasta, ligacao=lig)
                agora = datetime.datetime.now(datetime.timezone.utc)
                v = campos_serasa(r.get("texto")) if r.get("texto") else {}
                ok = bool(v.get("Situação Cadastral") or v.get("Razão Social"))
                sp = None
                png = os.path.join(pasta, "serasa_%s.png" % cnpj)
                if ok and os.path.exists(png):
                    sp = "ficha_cnpj/%s/serasa_%s.png" % (cnpj[:2], cnpj)
                    if not imagens.enviar(sp, open(png, "rb").read(), "image/png"):
                        sp = None
                linha = {"id_empresa": emp, "cnpj": cnpj, "fonte": "serasa", "url": r.get("url"),
                         "bloqueado": bool(r.get("bloqueado")), "erro": None if ok else (r.get("erro") or "ficha sem campos"),
                         "situacao": (v.get("Situação Cadastral") or "").strip() or None,
                         "data_abertura": _data_br(v.get("Data de fundação")),
                         "campos": json.dumps(v or r.get("campos") or {}, ensure_ascii=False),
                         "texto_ia": texto_ia(cnpj, v, agora.astimezone()) if ok else None, "storage_path": sp}
                with trava_db:
                    placar["ok" if ok else ("bloqueado" if r.get("bloqueado") else "falha")] += 1
                    if a.aplicar:
                        with con.cursor() as k:
                            k.execute("""insert into radar_comercial.ficha_cnpj_web
                                           (id_empresa, cnpj, fonte, url, consultado_em, bloqueado, erro, situacao,
                                            data_abertura, campos, texto_ia, storage_path)
                                         values (%(id_empresa)s, %(cnpj)s, %(fonte)s, %(url)s, now(), %(bloqueado)s, %(erro)s,
                                                 %(situacao)s, %(data_abertura)s, %(campos)s::jsonb, %(texto_ia)s, %(storage_path)s)
                                         on conflict (id_empresa, cnpj, fonte) do update set url = excluded.url,
                                           consultado_em = now(), bloqueado = excluded.bloqueado, erro = excluded.erro,
                                           situacao = excluded.situacao, data_abertura = excluded.data_abertura,
                                           campos = excluded.campos, texto_ia = excluded.texto_ia,
                                           storage_path = coalesce(excluded.storage_path, radar_comercial.ficha_cnpj_web.storage_path)""",
                                      linha)
                        con.commit()
                    feitos = sum(placar.values())
                    _log("   [%d/%d] %s %s · %s · %.0f s" % (feitos, len(lista), lig, cnpj,
                                                          "ok " + (linha["situacao"] or "") if ok else (linha["erro"] or "")[:60],
                                                          r.get("segundos") or 0))
        finally:
            try:
                nav.fechar()
            except Exception:                                  # noqa: BLE001
                pass

    with cf.ThreadPoolExecutor(max(1, a.navegadores)) as ex:
        list(ex.map(trabalhador, range(a.navegadores)))
    con.close()
    _log("■ fichas do CNPJ prontas em %.1f min · %s" % ((time.time() - t0) / 60, placar))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
