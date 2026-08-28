-- 0038 — a fusão registra QUEM a decidiu, e o desfazer para de girar em falso.
--
-- O PROBLEMA
--
-- `--desfundir` desfaz o que a regra de HOJE não refaria. O critério parece
-- certo e não é: boa parte desses pares não é recusada — é MANDADA À IA, e a IA
-- os refunde. Na passada seguinte eles voltam a ser elegíveis pela mesma conta,
-- são desfeitos de novo, perguntados de novo, refundidos de novo.
--
-- MEDIDO em 28/08/2026: a execução real desfez 2.483 fusões e o cruzamento
-- refez 1.913. Rodar outra vez encontraria mais 1.824 elegíveis. Não é resto —
-- é um laço, e cada volta custa uma passada inteira da Spark.
--
-- O QUE FALTAVA
--
-- Saber quem decidiu. Uma fusão que a REGRA fez e que a regra não faria mais
-- está obsoleta: desfazê-la é a correção que o `--desfundir` existe para
-- aplicar. Uma fusão que a IA decidiu olhando nome, endereço e telefone não
-- está obsoleta só porque a regra sozinha não a faria — a regra nunca a faria,
-- é por isso que a IA foi consultada.
--
--   `fundido_por`   `regra` ou `ia`. Gravado no momento da fusão.
--
-- Com ela, `--desfundir` deixa as decisões da IA em paz por padrão e a operação
-- converge. `--desfundir-ia` reabre também as dela, para quando o que mudou foi
-- o modelo ou o prompt — aí a pergunta é outra e vale refazê-la.
--
-- O PASSADO É RECUPERÁVEL, e sem chute: a fusão grava `regra` ou `ia` em
-- `vinculo_poi.confianca_origem` do vínculo que ela move, e hoje são 12.594
-- `regra` e 2.562 `ia`. `backfill_fundido_para.py` preenche os dois campos pelo
-- mesmo rastreio de nome.

begin;

alter table pois add column if not exists fundido_por text;

do $$
begin
  alter table pois add constraint pois_fundido_por_valido
    check (fundido_por is null or fundido_por in ('regra', 'ia', 'importacao'));
exception when duplicate_object then null;
end $$;

commit;
