-- 0104 · a busca web sai do Google: DuckDuckGo e Yahoo (13/09/2026)
--
-- Decisao do dono do produto. A busca complementar pesquisou dentro do Google
-- Maps de 12 a 13/09/2026 — erro meu: o Maps devolve os lugares da REGIAO do
-- mapa, e 71% das 22.275 buscas eram lista de lugares de outras ruas e numeros.
-- Medido em 13/09/2026 nos mesmos 20 enderecos: o Google comum bloqueou 8 mesmo
-- com 3 tentativas; DuckDuckGo e Yahoo nao bloquearam nenhuma, e 11 e 10 paginas
-- traziam empresa na MESMA rua e numero.
--
--   resultados   todos os resultados da pagina: [{titulo, url, trecho, no_endereco}]
--   no_endereco  quantos deles citam a rua e o numero da instalacao (e a cidade,
--                o bairro ou o CEP). SO ESSES vao para a IA; e o "achou" do SEEK.
alter table radar_comercial.busca_web drop constraint if exists busca_web_motor_check;
alter table radar_comercial.busca_web add constraint busca_web_motor_check
    check (motor = any (array['google', 'google_maps', 'bing', 'duckduckgo', 'yahoo']));
alter table radar_comercial.busca_web
    add column if not exists resultados jsonb,
    add column if not exists no_endereco smallint;
