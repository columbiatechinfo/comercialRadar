# -*- coding: utf-8 -*-
"""O que a IA VIU, o PROMPT que ela recebeu, e o que decidiu."""
import base64
import io as _io
import json
import sys

import avaliar_ia as ia
import avaliar_ligacao as al
import base_comum as bc
import dossie_ligacao as dl
import imagens as bi

from PIL import Image


def encolher(b, largura=880, q=76):
    """Grande o bastante para o zoom em tela cheia valer alguma coisa."""
    try:
        im = Image.open(_io.BytesIO(b)).convert("RGB")
        if im.width > largura:
            im = im.resize((largura, int(im.height * largura / im.width)),
                           Image.LANCZOS)
        s = _io.BytesIO()
        im.save(s, "JPEG", quality=q, optimize=True)
        return s.getvalue()
    except Exception:                                          # noqa: BLE001
        return b


def main():
    ligs = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    con = bc.conectar()
    cur = con.cursor()
    secoes = ia._secoes_texto(con)

    cur.execute("""
        select v.ligacao, v.veredito, v.justificativa, v.percepcao, v.pois,
               v.fontes, v.modelo, c.nom_logradouro, c.nro, c.cidade
          from radar_comercial.ligacao_veredito v
          left join resources_root.cadastro_corsan c
                 on c.num_ligacao::text = v.ligacao
         where v.ligacao = any(%s)""", (ligs,))
    ver = {r[0]: r for r in cur.fetchall()}

    saida, tot = [], 0
    for n, lig in enumerate(ligs, 1):
        r = ver.get(lig)
        if not r:
            continue
        texto, imgs, tipos, resumo = dl.montar(con, lig, ia, bi)
        # O PROMPT EXATO, montado do mesmo jeito que `avaliar_ligacao.uma`.
        lista = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(tipos))
        prompt = al.PROMPT % {"dossie": texto, "lista": lista,
                              "julgar": al._regras_da_ligacao(secoes)}
        fotos = []
        for b, tp in zip(imgs, tipos):
            p = encolher(b)
            tot += len(p)
            fotos.append({"tipo": tp, "b64": base64.b64encode(p).decode()})
        pc = r[3] or {}
        resp = (pc.get("resposta") or {}) if isinstance(pc, dict) else {}
        saida.append({
            "ligacao": lig, "veredito": r[1], "justificativa": r[2],
            "pois": r[4], "fontes": r[5], "modelo": r[6],
            "endereco": ("%s, %s" % (r[7] or "", r[8] or "")).strip(", "),
            "cidade": r[9] or "",
            "ano_visadas": (pc.get("ano_das_visadas")
                            if isinstance(pc, dict) else None),
            "presenca_na_foto": resp.get("presenca_na_foto"),
            "fotos_validam": resp.get("fotos_do_google_validam"),
            "pois_coerentes": resp.get("pois_coerentes"),
            "pois_de_outro_endereco": resp.get("pois_de_outro_endereco"),
            "prompt": prompt, "fotos": fotos})
        print("%2d/%d  %s  %d img  %.1f MB"
              % (n, len(ligs), lig, len(fotos), tot / 1e6), flush=True)

    json.dump(saida, open(sys.argv[2], "w", encoding="utf-8"),
              ensure_ascii=False)
    print("%d ligacoes · %.1f MB de imagem" % (len(saida), tot / 1e6))
    con.close()


if __name__ == "__main__":
    main()
