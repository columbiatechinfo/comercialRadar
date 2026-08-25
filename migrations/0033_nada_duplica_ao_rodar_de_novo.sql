-- 0033 — rodar de novo sobre a mesma área só pode ACRESCENTAR.
--
-- Regra do dono do produto, 25/08/2026: uma segunda passada numa área já
-- trabalhada não repete o que já está registrado — nem POI, nem imagem, nem
-- qualquer outra fonte.
--
-- ====================== O ESTADO DE HOJE, MEDIDO ===========================
--
--   place_id repetido em `pois` ............................. 0
--   Street View repetido (poi + angulo) ..................... 5 grupos, 6 imagens
--   mesmo nome na mesma coordenada (fontes diferentes) ...... 191 grupos
--
-- A base está limpa. O problema é que **nada garante que continue**: não havia
-- índice único em lugar nenhum, e as zero duplicatas eram consequência de os
-- ingestores serem cuidadosos — o que vale até alguém escrever um caminho novo.
--
-- E já houve o contrário. Em julho de 2026 os `recuperado_gemini` acumularam
-- 455 duplicatas porque o Gemini não devolve `place_id` e o dedup era por ele:
-- "Livraria Harmonia" entrou seis vezes. Convenção não impede reinserção;
-- índice impede.
--
-- ==================== O QUE ESTA MIGRAÇÃO NÃO FAZ ==========================
--
-- Não apaga nada. As 6 imagens em excesso e os 191 grupos de nome+coordenada
-- FICAM. Os índices abaixo são parciais justamente para poderem nascer sobre
-- uma base que já tem essas sobras: eles travam o futuro sem exigir uma
-- limpeza do passado que ninguém pediu.
--
-- Os 191 grupos são trabalho da camada de vínculo (migração 0032): a mesma loja
-- vista por fontes diferentes deve virar UM POI com várias abas — não sumir uma.

-- ─────────────────────────── POI não duplica ────────────────────────────────
--
-- `place_id` é a identidade do ponto na fonte que o produziu:
-- `g/11ft3cxch8` no Google, `estadual:<cluster>` na extração, `osm:node/123`.
-- Dois POIs com o mesmo `place_id` são a mesma coisa contada duas vezes.
--
-- Parcial em `<> ''` porque POI sem place_id existe e é legítimo — o Gemini não
-- devolve um, e o ingestor deduplica esses pela origem (`fonte` + `sessao` +
-- `nome_original`). Índice sobre o vazio impediria o segundo POI sem place_id
-- de existir, que é o oposto do que se quer.
create unique index if not exists ux_pois_place_id
  on comercialradar.pois (place_id)
  where place_id is not null and place_id <> '';

-- ──────────────────── imagem de Street View não duplica ─────────────────────
--
-- A chave é (POI, ÂNGULO), e não (POI, arquivo): o giro 360° captura sete
-- imagens do mesmo ponto — `facade`, `g0`, `g60`, `g120`, `g180`, `g240`,
-- `g300` — e todas são legítimas. O que não pode é a MESMA vista entrar duas
-- vezes numa recaptura.
--
-- Restrito a `pano_id is not null`: é o caminho atual, e ele já está com zero
-- duplicatas. As linhas de 2022 (sem `pano_id`, ângulos `g90`/`p1`/`p2`) ficam
-- de fora — são as que carregam as 6 sobras, e apagá-las para poder criar um
-- índice seria destruir captura paga para satisfazer uma restrição.
create unique index if not exists ux_streetview_poi_angulo
  on comercialradar.streetview_imgs (poi_id, angulo)
  where pano_id is not null and angulo is not null;

-- ───────────────────── o vínculo de fonte não duplica ───────────────────────
--
-- Já garantido pela 0032 (`ix_vinculo_poi_ativo`): o mesmo registro de fonte
-- não pode compor dois POIs ao mesmo tempo. Fica registrado aqui porque é a
-- terceira perna da mesma regra, e quem vier procurar "o que impede duplicar"
-- precisa achar as três juntas.

comment on index comercialradar.ux_pois_place_id is
  'Rodada nova na mesma area ACRESCENTA, nao repete. Parcial: POI sem place_id '
  'e legitimo e deduplica por origem (fonte+sessao+nome_original).';

comment on index comercialradar.ux_streetview_poi_angulo is
  'Uma vista por angulo por POI. O giro 360 sao sete angulos distintos, todos '
  'legitimos; a mesma vista duas vezes, nao. Restrito ao caminho com pano_id '
  'para nao exigir apagar as capturas de 2022.';
