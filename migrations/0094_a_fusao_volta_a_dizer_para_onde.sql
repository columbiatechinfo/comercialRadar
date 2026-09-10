-- 0094 · a fusao volta a dizer PARA ONDE, e QUEM decidiu.
--
-- AS DUAS COLUNAS JA EXISTIRAM. As migracoes 0036 e 0038 as criaram, e a 0052
-- as removeu em 03/09/2026 com uma justificativa que era verdadeira naquele
-- dia: "zero linhas preenchidas nas 301.297, e as unicas citacoes estao em
-- scripts de uma vez so que nao fazem parte do pipeline".
--
-- O QUE MUDOU: agora ha quem escreva. `fundir_mesma_fonte.py` (10/09/2026)
-- funde a duplicata que a fusao entre fontes nao ve — duas linhas da MESMA
-- fonte para o mesmo estabelecimento —, e a decisao do dono do produto foi
-- aplicar so as 1.062 de nome identico justamente porque a operacao e
-- REVERSIVEL. Ela so e reversivel se `fundido_para` disser para onde o POI
-- foi: sem isso, desfazer exige rastrear pelo nome, e a propria 0036 mediu que
-- 11,7% dos casos dariam mais de um destino.
--
-- E `fundido_por` separa duas coisas que nao se desfazem juntas: a fusao que a
-- REGRA fez e que a regra nao faria mais esta obsoleta; a que a IA decidiu,
-- nao. Ver a 0038. O valor novo aqui e 'mesma_fonte'.
--
-- DE QUEBRA, ISTO CONSERTA `cruzar_fontes.py`. Ele grava as tres colunas desde
-- 27/08 e foi editado em 08/09, cinco dias DEPOIS de a 0052 remove-las — ou
-- seja, ele quebraria hoje na primeira fusao. Nao esta no pipeline
-- automatico (nem `minerar_tudo` nem `server` o chamam), e por isso o defeito
-- nunca apareceu.
alter table radar_comercial.pois
  add column if not exists fundido_para bigint,
  add column if not exists fundido_por text;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'pois_fundido_para_fkey') then
    alter table radar_comercial.pois
      add constraint pois_fundido_para_fkey
      foreign key (fundido_para) references radar_comercial.pois (id)
      on delete set null;
  end if;
end $$;

comment on column radar_comercial.pois.fundido_para is
  'Em qual POI este foi absorvido. E o que torna a fusao reversivel.';
comment on column radar_comercial.pois.fundido_por is
  'Quem decidiu: regra, ia ou mesma_fonte.';

create index if not exists pois_fundido_para_ix
  on radar_comercial.pois (fundido_para) where fundido_para is not null;
