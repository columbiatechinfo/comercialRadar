-- 0096b · o resultado da comparação entre o número lido e o cadastrado.
--
-- A PRIMEIRA VERSÃO DE `enriquecer_cruzado` IA GRAVAR ISTO EM
-- `ligacao_poi.suspeita_motivo`, e essa coluna tem dono: `telhados.py` guarda
-- ali a medida que sustentou a suspeita ("telhado de 1302 m2; comercial; mesma
-- via"), em 67 vínculos. Escrever por cima teria apagado o registro de outro
-- módulo — o mesmo erro que a migração 0052 cometeu com `fundido_para`.
--
-- O RESULTADO É DA LIGAÇÃO, e não do vínculo: o número está pregado no imóvel,
-- e o imóvel é um só, ainda que dez POIs apontem para ele. Por isso a coluna
-- fica em `leitura_visual`, ao lado do número que a originou.
alter table radar_comercial.leitura_visual
  add column if not exists confere_com_cadastro boolean,
  add column if not exists numero_no_cadastro text;

comment on column radar_comercial.leitura_visual.confere_com_cadastro is
  'true: a IA leu na fachada o mesmo numero da ligacao. false: leu outro — o '
  'veredito daquela ligacao se apoiou na fachada do vizinho. nulo: nao leu.';
