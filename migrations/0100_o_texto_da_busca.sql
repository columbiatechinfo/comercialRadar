-- 0100 · a busca web guarda o TEXTO da pagina, e qual navegador a capturou.
--
-- O PROCESSO ENXUTO (dono do produto, 12/09/2026): a pagina da busca nao vai
-- mais como imagem para a IA, nem passa por uma chamada separada que a lia. O
-- texto dela vai junto do dossie curto na propria avaliacao. O navegador ja
-- entrega o texto exato da pagina; as paginas guardadas antes disto so tem o
-- print, e para elas o texto sai do OCR no i9.
--
-- `navegador`: `sessao_humana` (a do enriquecimento do Google Maps, com proxy e
-- cookies de consentimento) ou `repositorio` (a sessao furtiva do Scrapling, a
-- do iFood), que e a reserva.
alter table radar_comercial.busca_web
    add column if not exists texto text,
    add column if not exists navegador text;
comment on column radar_comercial.busca_web.texto is
    'O texto da pagina de resultados, como o navegador o entregou (ou do OCR do print, nas antigas).';
