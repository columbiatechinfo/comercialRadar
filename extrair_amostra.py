# -*- coding: utf-8 -*-
"""Junta, para cada ligacao da amostra, o que a IA VIU e o que ela decidiu."""
import base64
import io as _io
import json
import sys

import base_comum as bc
import dossie_ligacao as dl
import avaliar_ia as ia
import imagens as bi

try:
    from PIL import Image
    TEM_PIL = True
except Exception:
    TEM_PIL = False


def encolher(b, largura=640, q=78):
    """A galeria inteira precisa caber em 16 MB de pagina publicada."""
    if not TEM_PIL:
        return b
    try:
        im = Image.open(_io.BytesIO(b))
        im = im.convert("RGB")
        if im.width > largura:
            im = im.resize((largura, int(im.height * largura / im.width)),
                           Image.LANCZOS)
        s = _io.BytesIO()
        im.save(s, "JPEG", quality=q, optimize=True)
        return s.getvalue()
    except Exception:
        return b


def main():
    ligs = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    con = bc.conectar()
    cur = con.cursor()
    cur.execute("""
        select v.ligacao, v.veredito, v.justificativa, v.percepcao, v.pois,
               v.fontes, c.nom_logradouro, c.nro, c.cidade
          from radar_comercial.ligacao_veredito v
          left join resources_root.cadastro_corsan c
                 on c.num_ligacao::text = v.ligacao
         where v.ligacao = any(%s)""", (ligs,))
    ver = {r[0]: r for r in cur.fetchall()}

    saida, bytes_total = [], 0
    for n, lig in enumerate(ligs, 1):
        r = ver.get(lig)
        if not r:
            continue
        try:
            texto, imgs, tipos, resumo = dl.montar(con, lig, ia, bi)
        except Exception as e:                                 # noqa: BLE001
            texto, imgs, tipos, resumo = ("(dossiê falhou: %s)" % e, [], [], {})
        fotos = []
        for b, tp in zip(imgs, tipos):
            pequeno = encolher(b)
            bytes_total += len(pequeno)
            fotos.append({"tipo": tp,
                          "b64": base64.b64encode(pequeno).decode()})
        p = r[3] or {}
        resp = (p.get("resposta") or {}) if isinstance(p, dict) else {}
        saida.append({
            "ligacao": lig, "veredito": r[1], "justificativa": r[2],
            "pois": r[4], "fontes": r[5],
            "endereco": ("%s, %s" % (r[6] or "", r[7] or "")).strip(", "),
            "cidade": r[8] or "",
            "ano_visadas": (p.get("ano_das_visadas") if isinstance(p, dict)
                            else None),
            "presenca_na_foto": resp.get("presenca_na_foto"),
            "fotos_validam": resp.get("fotos_do_google_validam"),
            "pois_de_outro_endereco": resp.get("pois_de_outro_endereco"),
            "dossie": texto, "fotos": fotos})
        print("%2d/%d  %s  %d imagem(ns)  %.1f MB acumulados"
              % (n, len(ligs), lig, len(fotos), bytes_total / 1e6), flush=True)

    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False)
    print("PIL: %s · %d ligacoes · %.1f MB de imagem"
          % (TEM_PIL, len(saida), bytes_total / 1e6))
    con.close()


if __name__ == "__main__":
    main()
