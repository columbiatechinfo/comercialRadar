-- 0101 · a busca web tambem pelo GOOGLE MAPS (motor `google_maps`).
--
-- Medido em 12/09/2026: a pagina de busca do Google (/search) tomou /sorry por
-- qualquer IP — os 500 estaticos da Webshare e o do proprio i9. O Maps, pelos
-- mesmos IPs, abriu a ficha do lugar em 3 de 3. A consulta e a mesma (endereco
-- da instalacao + "empresa"); o texto guardado e o do painel do Maps. Quem
-- escreve aqui e so o `buscar_web.py`.
--
-- NOT VALID e depois VALIDATE: a troca da checagem pega o lock so por um
-- instante, e a validacao das linhas antigas nao para a avaliacao que le a
-- tabela o tempo todo (ver "DDL trava a fila inteira").
set lock_timeout = '10s';
alter table radar_comercial.busca_web drop constraint if exists busca_web_motor_check;
alter table radar_comercial.busca_web
    add constraint busca_web_motor_check check (motor in ('google', 'google_maps', 'bing')) not valid;
alter table radar_comercial.busca_web validate constraint busca_web_motor_check;
