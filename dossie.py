# -*- coding: utf-8 -*-
"""dossie.py — o documento comprobatório de um POI.

É o entregável que sai da empresa: a concessionária leva este papel para
justificar a mudança de tarifa de um imóvel. Por isso ele reúne TUDO o que
sustenta a decisão — cadastro, dados empresariais, fotos, fachada, avaliação da
IA, nível de confiança, a decisão humana e quem a tomou.

Três decisões de desenho que valem explicação:

**Nasce da aprovação, não do achado.** Um POI achado é hipótese; um POI aprovado
é afirmação com dono. O dossiê carrega o nome de quem aprovou, e é isso que o
transforma de relatório em documento.

**As imagens vão embutidas em base64.** PDF que aponta para URL vira papel em
branco no dia em que o servidor sai do ar ou a sessão expira — e o cliente abre
o arquivo meses depois, offline, para contestar uma cobrança.

**A procedência aparece sempre aqui.** É o oposto da tela: no dossiê, dizer que
o endereço veio do CNEFE e a foto do Street View é o que sustenta a afirmação
diante de um questionamento. Documento sem origem não se defende.
"""
from __future__ import annotations

import base64
import html
import json
import os
from datetime import datetime

import imagens


def _b64(dados: bytes | None, tipo: str = "image/jpeg") -> str | None:
    if not dados:
        return None
    return f"data:{tipo};base64," + base64.b64encode(dados).decode("ascii")


def _rot(v) -> str | None:
    """`moradia_luxo_habitada` → "Moradia luxo habitada".

    O vocabulário do banco é em snake_case porque é chave de enum e de filtro;
    o dossiê é lido por gente da concessionária, e chave de enum num documento
    comprobatório parece erro de exportação.
    """
    if not v:
        return None
    return ", ".join(p.replace("_", " ").strip().capitalize()
                     for p in str(v).split(",") if p.strip())


def _e(v) -> str:
    return html.escape(str(v)) if v not in (None, "") else "—"


def coletar(poi_id: int, con) -> dict:
    """Junta tudo que existe sobre o POI. Só leitura, e sempre pela conexão de
    QUEM PEDIU: se o usuário não alcança o POI pela RLS, não há dossiê."""
    d: dict = {"poi_id": poi_id}
    with con.cursor() as cur:
        cur.execute("""
            select p.nome, p.categoria, p.endereco, p.telefone, p.website,
                   p.cnpj, p.razao_social, p.nome_fantasia, p.cnae,
                   p.situacao_cadastral, p.socios, p.avaliacao, p.total_avaliacoes,
                   p.resumo_avaliacoes, p.cidade, p.uf,
                   coalesce(p.maps_lat, p.lat_origem), coalesce(p.maps_lng, p.lng_origem),
                   p.fonte, p.endereco_fonte, t.nome
              from pois p left join tenants t on t.id = p.tenant_id
             where p.id = %s""", (poi_id,))
        r = cur.fetchone()
        if not r:
            return {}
        campos = ("nome categoria endereco telefone website cnpj razao_social "
                  "nome_fantasia cnae situacao_cadastral socios avaliacao "
                  "total_avaliacoes resumo_avaliacoes cidade uf lat lng fonte "
                  "endereco_fonte empresa").split()
        d.update(dict(zip(campos, r)))

        cur.execute("""select confere, equivalencia, porte, pessoas_estimadas,
                              atividade_real, veredito, veredito_motivo
                         from analise_ia where poi_id = %s limit 1""", (poi_id,))
        a = cur.fetchone()
        if a:
            d["ia"] = dict(zip("confere equivalencia porte pessoas atividade "
                               "veredito motivo".split(), a))

        # Os nomes aqui são os REAIS da tabela: `uso_declarado`, `oportunidade`
        # e `alerta` não existem — são `economias_base`, `oportunidades` e
        # `numero_confere`. Chutar nome de coluna em documento comprobatório
        # daria um dossiê que falha na hora de gerar, com o cliente esperando.
        cur.execute("""select numero_lido, numero_confere, uso_observado,
                              oportunidades, confianca, economias_base,
                              gap_uc_economias, tipologia, estado_conservacao,
                              tipo_cliente, habitacoes_distintas, metodo_habitacoes,
                              tipo_via, pessoas_na_imagem, numero_na_parede,
                              veredito_comercial, nota_comercial,
                              veredito_justificativa, veredito_fatores,
                              -- leitura 2.0.0, em quatro fases
                              descricao, acao_recomendada, alvo_encontrado,
                              posicao_na_imagem, marcador_google_visivel,
                              tipo_imovel, status_ocupacao, multiplas_unidades,
                              limite_ambiguo, indicio_comercial,
                              atividade_economica_aparente, atividade_no_alvo,
                              texto_do_letreiro, ressalva, elementos,
                              identificacao, imagens_usadas, schema_versao
                         from fachada_anotacao where poi_id = %s
                         order by criado_em desc limit 1""", (poi_id,))
        f = cur.fetchone()
        if f:
            d["fachada"] = dict(zip(
                "numero numero_confere uso_observado oportunidades confianca "
                "economias_base gap tipologia conservacao "
                "tipo_cliente habitacoes metodo_habitacoes tipo_via "
                "pessoas numero_parede veredito nota justificativa fatores "
                "descricao acao alvo_encontrado posicao marcador_google "
                "tipo_imovel status_ocupacao multiplas_unidades limite_ambiguo "
                "indicio_comercial atividade_aparente atividade_no_alvo "
                "letreiro ressalva elementos identificacao imagens_usadas "
                "schema_versao".split(), f))

        # A TRIAGEM DA FOTO — por que a fachada foi aceita ou descartada.
        #
        # Sem ela, o supervisor que abre um ponto julgado só pelas fotos do Maps
        # não tem como saber que a fachada existia e foi reprovada: a tela
        # pareceria "não capturaram a rua", quando o caso é "capturaram e a
        # árvore cobria tudo".
        cur.execute("""select nota_total, veredito, motivo, tipo_de_foto, notas
                         from fachada_triagem where poi_id = %s
                        order by id desc limit 1""", (poi_id,))
        t = cur.fetchone()
        if t:
            d["triagem"] = dict(zip("nota veredito motivo tipo_de_foto notas".split(), t))

        # O LINK PARA A CENA VIVA. O supervisor precisa poder girar a câmera:
        # a foto é um recorte de um panorama, e o que decide a dúvida costuma
        # estar dez graus fora do enquadramento.
        cur.execute("""select pano_id, heading, fov, data_captura, cam_lat, cam_lng
                         from streetview_imgs
                        where poi_id = %s and angulo = 'facade'
                        order by id desc limit 1""", (poi_id,))
        sv = cur.fetchone()
        if sv:
            d["streetview_data"] = sv[3]
            d["camera"] = {"heading": sv[1], "fov": sv[2],
                           "lat": sv[4], "lng": sv[5]}
            if sv[0]:
                d["streetview_url"] = (
                    f"https://www.google.com/maps/@?api=1&map_action=pano&pano={sv[0]}"
                    f"&heading={(sv[1] or 0):.0f}&pitch=5&fov={sv[2] or 60}")

        cur.execute("""select a.status, a.motivo_generico, a.motivo_escrito,
                              a.observacao, a.decidido_em, us.nome, us.email
                         from atribuicao a left join usuarios us on us.id = a.supervisor_id
                        where a.poi_id = %s and a.status <> 'pendente'
                        order by a.decidido_em desc limit 1""", (poi_id,))
        dec = cur.fetchone()
        if dec:
            d["decisao"] = dict(zip("status generico escrito observacao em "
                                    "por email".split(), dec))

        # A FACHADA COM A MIRA sobre o imóvel que foi julgado.
        #
        # A foto crua mostra três casas lado a lado e nada diz qual delas a IA
        # leu — foi assim que passou despercebido o caso em que ela avaliou a
        # casa VIZINHA e o veredito parecia coerente.
        #
        # A mira vem da GEOMETRIA, não da resposta do modelo: onde a câmera
        # estava, para onde apontava, qual a abertura, e onde fica a coordenada
        # do ponto. É verificável e independe de a leitura ter acertado; usar a
        # `posicao_na_imagem` que ela devolveu faria a marca concordar com o
        # erro sempre que houvesse erro.
        bruta = (imagens.streetview_do_poi(poi_id, con) or [None])[0]
        d["streetview"] = _b64(bruta)
        cam = d.get("camera") or {}
        if bruta and cam.get("lat") is not None and d.get("lat") is not None:
            try:
                import anotar
                fx = anotar.posicao_do_alvo(cam["lat"], cam["lng"], d["lat"],
                                            d["lng"], cam.get("heading") or 0,
                                            cam.get("fov") or 60)
                d["streetview_mirado"] = _b64(anotar.marcar_alvo(bruta, fx))
            except Exception:
                # Anotar é enfeite: se a Pillow falhar numa imagem torta, o
                # dossiê sai com a foto crua em vez de não sair.
                pass

        # AS FOTOS DO MAPS COM O VEREDITO DE CADA UMA.
        #
        # Vêm com id e data colados (`fotos_do_poi_com_id`) e casadas com o que
        # a fase 2 decidiu sobre cada foto. Sem isso o supervisor vê quatro
        # fotos soltas sem saber que duas são de OUTRO estabelecimento — e 16% a
        # 19% delas são, medido.
        cur.execute("""select distinct on (imagem_id) imagem_id, veredito, texto_legivel
                         from foto_maps_triagem where poi_id = %s
                        order by imagem_id, id desc""", (poi_id,))
        julg = {r[0]: {"veredito": r[1], "texto": r[2]} for r in cur.fetchall()}
        d["fotos"] = [{"src": _b64(f["b"]), "data": f["data"],
                       **julg.get(f["id"], {})}
                      for f in imagens.fotos_do_poi_com_id(poi_id, con, limite=4)]
    return d


def _mapa(lat, lng) -> str | None:
    """Print da posição. Sem chave, o dossiê sai sem o mapa em vez de sem sair."""
    chave = (os.environ.get("MAPS_SERVER_KEY") or "").strip()
    if not (chave and lat and lng):
        return None
    import urllib.request
    url = (f"https://maps.googleapis.com/maps/api/staticmap?center={lat},{lng}"
           f"&zoom=18&size=640x320&scale=2&maptype=roadmap"
           f"&markers=color:red%7C{lat},{lng}&key={chave}")
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return _b64(r.read(), "image/png")
    except Exception:
        return None


def montar_html(d: dict) -> str:
    if not d:
        return "<h1>POI não encontrado</h1>"

    def bloco(titulo, linhas):
        itens = "".join(
            f"<tr><th>{html.escape(k)}</th><td>{_e(v)}</td></tr>"
            for k, v in linhas if v not in (None, ""))
        return f"<section><h2>{titulo}</h2><table>{itens}</table></section>" if itens else ""

    ia = d.get("ia") or {}
    fa = d.get("fachada") or {}
    dec = d.get("decisao") or {}
    mapa = _mapa(d.get("lat"), d.get("lng"))

    imgs = ""
    # A mirada tem precedência sobre a crua: no documento comprobatório importa
    # mostrar QUAL imóvel foi lido, não só que havia uma foto da rua.
    sv = d.get("streetview_mirado") or d.get("streetview")
    if sv:
        leg = "Fachada — Google Street View"
        if d.get("streetview_data"):
            leg += f' ({_e(d["streetview_data"])})'
        if d.get("streetview_mirado"):
            leg += " · a mira indica o imóvel avaliado"
        imgs += f'<figure><img src="{sv}"><figcaption>{leg}</figcaption></figure>'
    # A legenda de cada foto diz DE QUANDO ela é e o que a IA concluiu sobre
    # ela. Foto sem essas duas coisas é ilustração; com elas é prova.
    VER_FOTO = {"mostra_o_alvo": "mostra o estabelecimento",
                "mostra_atividade_compativel": "mostra atividade do mesmo ramo",
                "mostra_outro": "é de OUTRO estabelecimento",
                "nao_e_estabelecimento": "não mostra estabelecimento",
                "indefinido": "não deu para dizer"}
    for i, f in enumerate([x for x in d.get("fotos", []) if x and x.get("src")], 1):
        leg = f"Foto {i} — Google Maps"
        if f.get("data"):
            leg += f' ({_e(f["data"])})'
        if f.get("veredito"):
            leg += f' · {VER_FOTO.get(f["veredito"], _e(f["veredito"]))}'
        imgs += f'<figure><img src="{f["src"]}"><figcaption>{leg}</figcaption></figure>'
    if mapa:
        imgs += f'<figure><img src="{mapa}"><figcaption>Posição — Google Maps</figcaption></figure>'

    # O VEREDITO COMERCIAL DA IA, com a nota à vista.
    #
    # Bloco próprio e não mais uma linha numa tabela: é a resposta que o cliente
    # está comprando — "isto é comércio?" — e ela precisa vir com a régua ao
    # lado. Nota sem a escala é número solto: 7 parece alto até alguém supor que
    # a escala ia a 100.
    veredito_ia = ""
    if fa.get("veredito"):
        cor = {"aprova_comercial": "#166534", "reprova": "#b91c1c",
               "recomenda_visita": "#b45309"}.get(fa["veredito"], "#475569")
        nota = fa.get("nota")
        fatores = fa.get("fatores") or []
        if isinstance(fatores, str):
            try:
                fatores = json.loads(fatores)
            except (ValueError, TypeError):
                fatores = [fatores]
        lista = "".join(f"<li>{_e(x)}</li>" for x in fatores)
        veredito_ia = f"""<section><h2>Veredito comercial da IA</h2>
  <div class="veredito" style="--c:{cor}">
    <strong>{_e(_rot(fa['veredito']))}</strong>
    {f'<span class="nota">{nota}<em>/10</em></span>' if nota is not None else ''}
  </div>
  <p class="just">{_e(fa.get('justificativa'))}</p>
  {f'<ul class="fatores">{lista}</ul>' if lista else ''}
  <p class="escala">Escala: <b>0</b> = com certeza não é comercial ·
    <b>10</b> = com certeza é. A leitura da imagem e este juízo são chamadas
    separadas: quem descreveu a foto não decidiu, e quem decidiu não viu a foto —
    recebeu a descrição, o cadastro e a Receita.</p>
</section>"""

    selo = ""
    if dec:
        cor = {"aprovado": "#166534", "reprovado": "#b91c1c",
               "devolvido": "#3730a3"}.get(dec.get("status"), "#475569")
        quando = dec.get("em").strftime("%d/%m/%Y às %H:%M") if dec.get("em") else "—"
        selo = (f'<div class="selo" style="--c:{cor}">'
                f'<strong>{_e(dec.get("status")).upper()}</strong>'
                f'<span>por {_e(dec.get("por"))} · {quando}</span></div>')

    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Dossiê — {_e(d.get('nome'))}</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  *{{box-sizing:border-box}}
  body{{font:13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#0f172a;margin:0}}
  header{{border-bottom:3px solid #2563eb;padding-bottom:10px;margin-bottom:16px;
    display:flex;justify-content:space-between;align-items:flex-end;gap:16px}}
  h1{{font-size:21px;margin:0}}
  .sub{{color:#64748b;font-size:12px;margin-top:3px}}
  .selo{{border:2px solid var(--c);color:var(--c);border-radius:9px;padding:7px 13px;
    text-align:center;white-space:nowrap}}
  .selo strong{{display:block;font-size:15px;letter-spacing:.5px}}
  .selo span{{font-size:10.5px;color:#475569}}
  .veredito{{display:flex;align-items:center;gap:12px;border-left:5px solid var(--c);
    background:#f8fafc;padding:9px 13px;border-radius:0 8px 8px 0}}
  .veredito strong{{color:var(--c);font-size:15px;letter-spacing:.3px}}
  .veredito .nota{{margin-left:auto;font-size:26px;font-weight:800;color:var(--c);
    line-height:1}}
  .veredito .nota em{{font-style:normal;font-size:13px;color:#64748b;font-weight:600}}
  .just{{margin:7px 0 4px;font-size:12.5px}}
  .fatores{{margin:0 0 4px;padding-left:18px;color:#334155;font-size:12px}}
  .escala{{color:#64748b;font-size:10.5px;margin:4px 0 0;line-height:1.45}}
  section{{margin-bottom:15px;break-inside:avoid}}
  h2{{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:#2563eb;
    margin:0 0 6px;border-bottom:1px solid #e2e8f0;padding-bottom:3px}}
  table{{width:100%;border-collapse:collapse}}
  th{{text-align:left;font-weight:600;color:#64748b;width:180px;padding:3px 8px 3px 0;
    vertical-align:top;font-size:12px}}
  td{{padding:3px 0;vertical-align:top}}
  .imgs{{display:flex;flex-wrap:wrap;gap:9px}}
  figure{{margin:0;width:calc(50% - 5px);break-inside:avoid}}
  figure img{{width:100%;border-radius:7px;border:1px solid #e2e8f0}}
  figcaption{{font-size:10px;color:#94a3b8;margin-top:3px}}
  footer{{margin-top:20px;padding-top:9px;border-top:1px solid #e2e8f0;
    font-size:10px;color:#94a3b8}}
</style></head><body>
<header>
  <div>
    <h1>{_e(d.get('nome'))}</h1>
    <div class="sub">{_e(d.get('categoria'))} · {_e(d.get('cidade'))}/{_e(d.get('uf'))}
      · POI #{d['poi_id']} · {_e(d.get('empresa'))}</div>
  </div>
  {selo}
</header>

{bloco("Identificação", [
    ("Endereço", d.get("endereco")), ("Telefone", d.get("telefone")),
    ("Site", d.get("website")), ("Coordenada",
     f"{d.get('lat')}, {d.get('lng')}" if d.get("lat") else None)])}

{bloco("Dados empresariais", [
    ("CNPJ", d.get("cnpj")), ("Razão social", d.get("razao_social")),
    ("Nome fantasia", d.get("nome_fantasia")), ("CNAE", d.get("cnae")),
    ("Situação cadastral", d.get("situacao_cadastral")), ("Sócios", d.get("socios"))])}

{bloco("Leitura de fachada", [
    ("Número lido", fa.get("numero")),
    ("Número na parede", fa.get("numero_parede")),
    ("Número confere com o cadastro", fa.get("numero_confere")),
    ("Uso observado", fa.get("uso_observado")), ("Tipologia", fa.get("tipologia")),
    ("Estado de conservação", fa.get("conservacao")),
    ("Economias no cadastro", fa.get("economias_base")),
    ("Diferença UC × economias", fa.get("gap")),
    ("Oportunidades", fa.get("oportunidades")), ("Confiança", fa.get("confianca"))])}

{bloco("Classificação do imóvel", [
    ("Tipo de cliente", _rot(fa.get("tipo_cliente"))),
    ("Habitações distintas", fa.get("habitacoes")),
    ("Como foram contadas", _rot(fa.get("metodo_habitacoes"))),
    ("Tipo de via", _rot(fa.get("tipo_via"))),
    ("Pessoas na imagem", _rot(fa.get("pessoas")))])}

{veredito_ia}

{bloco("Análise por IA", [
    ("Atividade observada", ia.get("atividade")), ("Porte", ia.get("porte")),
    ("Pessoas estimadas", ia.get("pessoas")), ("Confere com o cadastro", ia.get("confere")),
    ("Veredito", ia.get("veredito")), ("Motivo", ia.get("motivo"))])}

{bloco("O que dizem", [
    ("Avaliação", d.get("avaliacao")), ("Total de avaliações", d.get("total_avaliacoes")),
    ("Resumo", d.get("resumo_avaliacoes"))])}

{bloco("Decisão humana", [
    ("Situação", dec.get("status")), ("Motivo (categoria)", dec.get("generico")),
    ("Motivo", dec.get("escrito")), ("Observação", dec.get("observacao")),
    ("Responsável", dec.get("por"))])}

{f'<section><h2>Evidência visual</h2><div class="imgs">{imgs}</div></section>' if imgs else ''}

{bloco("Procedência do dado", [
    ("Origem do registro", d.get("fonte")),
    ("Origem do endereço", d.get("endereco_fonte"))])}

<footer>
  Documento gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')} ·
  ComercialRadar · {_e(d.get('empresa'))}.
  As imagens e os dados aqui reunidos são os que sustentaram a decisão registrada.
</footer>
</body></html>"""
