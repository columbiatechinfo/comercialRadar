-- 0096d · onde o letreiro sem número pode estar.
--
-- Três caminhos, do mais barato para o mais caro, e o que resolveu fica dito
-- em `resolvido_por` — sem isso, um candidato certo e um chute pareceriam a
-- mesma coisa na tela.
--
--   nome_na_base ...... o nome do letreiro já existe numa base nossa, na mesma
--                       via, e aquele registro traz o número. Custo zero.
--   faixa_de_numeros .. a IA leu outros números NA MESMA CENA. O letreiro está
--                       entre o menor e o maior deles, e os candidatos deixam
--                       de ser a rua inteira.
--   triangulacao ...... o mesmo letreiro foi visto de câmeras diferentes, em
--                       julgamentos diferentes da mesma via. A interseção dos
--                       candidatos de cada leitura é menor que qualquer uma.
alter table radar_comercial.fachada_vista
  add column if not exists candidatos text[],
  add column if not exists resolvido_por text
    check (resolvido_por is null or resolvido_por in
           ('nome_na_base', 'faixa_de_numeros', 'triangulacao'));

comment on column radar_comercial.fachada_vista.candidatos is
  'Ligacoes que podem atender este letreiro. Uma so = candidato unico; '
  'varias = fila humana curta, com a foto ao lado.';
