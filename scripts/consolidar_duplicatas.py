# -*- coding: utf-8 -*-
"""Junta os POIs duplicados que nasceram antes da dedup por nome+coordenada.

Não é "apagar as cópias extras". Cada cópia costuma carregar coisa diferente —
medido: uma tinha 6 fotos e nenhuma fachada, as outras duas tinham fachada e
nenhuma foto. Apagar por antiguidade jogaria fora capturas; apagar por
novidade jogaria fora fotos.

Então: elege quem FICA, muda os filhos das outras para ela, completa os campos
vazios com o que as outras tinham, e só então remove as sobras.

Eleição, nesta ordem:
  1. quem tem anotação de fachada — é o dado mais caro e já validado
  2. senão, quem tem mais fotos
  3. empate: o id menor, que é o registro mais antigo

Fachadas repetidas do mesmo ponto: fica a mais recente por ângulo. São capturas
do mesmo panorama; as antigas só ocupam Storage e confundem a auditoria. O
objeto sai do bucket junto com a linha, senão vira lixo inalcançável.
"""
import os, sys, urllib.request, urllib.error

sys.path.insert(0, r"C:\Users\ceo\Documents\Sistemas\comercialRadar")
os.chdir(r"C:\Users\ceo\Documents\Sistemas\comercialRadar")
import config  # noqa: F401
import base_comum as bc

APLICAR = "--aplicar" in sys.argv

def apagar_do_storage(caminho: str) -> bool:
    chave = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    gw = f"http://{os.environ.get('I9_POSTGRES_HOST','100.115.117.49')}:8000"
    req = urllib.request.Request(f"{gw}/storage/v1/object/comercialradar/{caminho}",
                                 method="DELETE",
                                 headers={"apikey": chave, "Authorization": f"Bearer {chave}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status in (200, 204)
    except Exception:
        return False

con = bc.conectar(); con.autocommit = False
cur = con.cursor()

cur.execute("""
 select array_agg(id order by id)
   from pois where place_id is null and nome is not null
    and coalesce(maps_lat, lat_origem) is not null
  group by nome, round(coalesce(maps_lat,lat_origem)::numeric,5),
                round(coalesce(maps_lng,lng_origem)::numeric,5)
 having count(*) > 1""")
grupos = [r[0] for r in cur.fetchall()]
print(f"  {len(grupos)} grupos, {sum(len(g) for g in grupos)} POIs")

# Campos que valem herdar da cópia que sai, quando a que fica está vazia.
HERDA = ("cnpj", "cnpj_conf", "razao_social", "nome_fantasia", "cnae", "situacao_cadastral",
         "natureza_juridica", "socios", "telefone", "website", "instagram", "email",
         "facebook", "endereco", "categoria", "avaliacao", "total_avaliacoes",
         "resumo_avaliacoes", "fontes_web", "cidade", "uf")

removidos = herdados = sv_removidas = 0
for ids in grupos:
    cur.execute("""select p.id,
                    (select count(*) from fachada_anotacao f where f.poi_id=p.id),
                    (select count(*) from images_urls i where i.poi_id=p.id)
                   from pois p where p.id = any(%s)""", (ids,))
    info = cur.fetchall()
    fica = sorted(info, key=lambda x: (-(x[1] > 0), -x[2], x[0]))[0][0]
    saem = [i for i in ids if i != fica]

    # 1. completar o que falta na que fica
    for campo in HERDA:
        cur.execute(f"""update pois d set {campo} = o.{campo}
                          from pois o
                         where d.id = %s and o.id = any(%s)
                           and (d.{campo} is null or d.{campo}::text = '')
                           and o.{campo} is not null and o.{campo}::text <> ''""", (fica, saem))
        herdados += cur.rowcount

    # 2. mudar os filhos de dono ANTES de apagar — a FK é em cascata
    for tabela in ("images_urls", "comentarios", "horario_funcionamento", "streetview_imgs"):
        cur.execute(f"update {tabela} set poi_id = %s where poi_id = any(%s)", (fica, saem))
    cur.execute("""update fachada_anotacao set poi_id = %s
                    where poi_id = any(%s)
                      and not exists (select 1 from fachada_anotacao z where z.poi_id = %s)""",
                (fica, saem, fica))

    # 3. fachada repetida do mesmo ângulo: fica a mais recente
    cur.execute("""select id, storage_path from streetview_imgs
                    where poi_id = %s and id not in (
                      select distinct on (angulo) id from streetview_imgs
                       where poi_id = %s order by angulo, criado_em desc nulls last, id desc)""",
                (fica, fica))
    for sv_id, caminho in cur.fetchall():
        cur.execute("delete from streetview_imgs where id = %s", (sv_id,))
        if APLICAR and caminho:
            apagar_do_storage(caminho)
        sv_removidas += 1

    cur.execute("delete from pois where id = any(%s)", (saem,))
    removidos += len(saem)

if APLICAR:
    con.commit()
    print(f"  APLICADO: {removidos} POIs removidos, {herdados} campos herdados, "
          f"{sv_removidas} fachadas repetidas descartadas")
else:
    con.rollback()
    print(f"  SIMULACAO (nada gravado): removeria {removidos} POIs, herdaria {herdados} campos, "
          f"descartaria {sv_removidas} fachadas repetidas")
con.close()
