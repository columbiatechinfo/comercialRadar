-- 0012 — de ONDE a foto foi tirada, e não só de quem ela é.
--
-- O DEFEITO não é "a câmera não é gravada": é que DOIS escritores usam a mesma
-- coluna com sentidos opostos.
--
--   streetview_capture._gravar_imagem  → grava a CÂMERA (m["lat"], m["lng"])
--   descrever_imagens._arquivar_shot   → grava o POI
--
-- Medido: das 3.929 linhas COM `pano_id` (caminho novo), só 33 têm lat igual à
-- do POI — 99% são a câmera. Das 32.309 SEM `pano_id`, 20.060 são o POI. Ou
-- seja, a mesma coluna significa coisas diferentes conforme quem escreveu, e
-- ninguém consegue saber qual é qual sem esse cruzamento.
--
-- O custo apareceu ao tentar marcar os vizinhos do CNEFE na fachada: o rumo
-- calculado deu ZERO, porque a câmera estava — segundo o banco — exatamente
-- sobre o alvo. Os marcadores foram desenhados contra o norte.
--
-- Aqui as colunas passam a dizer o que são, e as 3.929 linhas boas são
-- preenchidas retroativamente: `pano_id IS NOT NULL` é o discriminador
-- confiável. As demais ficam NULL — e NULL é honesto, ao contrário de um
-- palpite que pareceria medição.
--
-- `lat/lng` fica onde está, relida como "o alvo que a foto pretende mostrar".

ALTER TABLE streetview_imgs
    ADD COLUMN IF NOT EXISTS cam_lat  double precision,
    ADD COLUMN IF NOT EXISTS cam_lng  double precision,
    ADD COLUMN IF NOT EXISTS heading  double precision,
    ADD COLUMN IF NOT EXISTS fov      integer;

COMMENT ON COLUMN streetview_imgs.cam_lat IS
    'Latitude do PANORAMA (posição da câmera). NULL nas capturas anteriores a 17/08/2026.';
COMMENT ON COLUMN streetview_imgs.cam_lng IS
    'Longitude do panorama.';
COMMENT ON COLUMN streetview_imgs.heading IS
    'Rumo em graus da câmera para o alvo, 0=norte. Com fov, permite projetar '
    'uma coordenada do mundo na imagem.';
COMMENT ON COLUMN streetview_imgs.fov IS
    'Abertura usada na captura. Varia com a distância (ver _fov_por_distancia): '
    'sem ela a projeção assume 80 e erra proporcionalmente ao zoom aplicado.';
COMMENT ON COLUMN streetview_imgs.lat IS
    'Latitude do ALVO que a foto pretende mostrar — NÃO é a posição da câmera. '
    'Para a câmera, ver cam_lat.';

-- ── Preenchimento retroativo das 3.929 linhas do caminho novo ──────────────
--
-- Nessas, `lat/lng` JÁ É a câmera; só falta nomeá-la e derivar rumo e abertura.
-- As fórmulas são as mesmas do Python (`_bearing`, `_dist_m`,
-- `_fov_por_distancia`), reescritas em SQL — divergir aqui faria a projeção
-- errar por motivo invisível.
WITH camera AS (
    SELECT s.id,
           s.lat  AS cla, s.lng  AS clo,
           p.maps_lat AS pla, p.maps_lng AS plo
      FROM streetview_imgs s
      JOIN pois p ON p.id = s.poi_id
     WHERE s.pano_id IS NOT NULL
       AND s.cam_lat IS NULL
       AND p.maps_lat IS NOT NULL
       -- câmera sobre o alvo não tem rumo definido: seriam as 33 linhas em que
       -- as coordenadas coincidem, e um rumo inventado ali é pior que NULL.
       AND (s.lat <> p.maps_lat OR s.lng <> p.maps_lng)
), calc AS (
    SELECT id, cla, clo,
           degrees(atan2(
               sin(radians(plo - clo)) * cos(radians(pla)),
               cos(radians(cla)) * sin(radians(pla))
             - sin(radians(cla)) * cos(radians(pla)) * cos(radians(plo - clo))
           )) AS rumo,
           sqrt(power((pla - cla) * 111320, 2)
              + power((plo - clo) * 111320 * cos(radians(cla)), 2)) AS dist
      FROM camera
)
UPDATE streetview_imgs s
   SET cam_lat = c.cla,
       cam_lng = c.clo,
       -- atan2 devolve -180..180; só o lado negativo precisa girar. Módulo em
       -- `double precision` não existe no Postgres, e converter para numeric
       -- só para isso perderia precisão sem ganhar nada.
       heading = CASE WHEN c.rumo < 0 THEN c.rumo + 360 ELSE c.rumo END,
       fov     = CASE WHEN c.dist <=  40 THEN 80
                      WHEN c.dist <=  90 THEN 60
                      WHEN c.dist <= 150 THEN 40
                      ELSE 25 END
  FROM calc c
 WHERE s.id = c.id;

-- Só faz sentido consultar quem TEM a geometria completa; índice parcial para
-- não pesar sobre as 32 mil linhas antigas que nunca terão esses valores.
CREATE INDEX IF NOT EXISTS ix_sv_com_camera
    ON streetview_imgs (poi_id)
    WHERE cam_lat IS NOT NULL AND heading IS NOT NULL;
