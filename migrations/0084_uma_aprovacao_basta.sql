-- 0084 · a ligacao com uma aprovacao ja esta decidida.
--
-- REGRA DO DONO DO PRODUTO, 08/09/2026: "as ligacoes que tiverem ao menos 1
-- POI vinculado e aprovado ja saem dessa lista; ela e seus vinculos sao
-- deixados como aprovados, mesmo que algumas das fontes individualmente tenha
-- sido reprovada ou mandada pra revisao".
--
-- POR QUE ISSO E COERENTE, e nao um atalho. Cada POI e uma testemunha
-- independente sobre o mesmo hidrometro. Uma testemunha que diz "vi a loja" e
-- prova; outra que diz "nao vi nada" e ausencia de prova, nao prova de
-- ausencia — e nas bases publicas ela costuma significar apenas que aquela
-- fonte nao tinha foto, nota nem comentario. Somar as duas como se pesassem
-- igual seria deixar o silencio anular o testemunho.
--
-- MEDIDO nas 10.798 residenciais ativas que a regra decide:
--
--     6.989 tinham TAMBEM uma revisao humana   -> a contradicao se resolve
--     1.284 tinham TAMBEM um reprovado         -> idem
--     9.814 (91 por cento) tem TRES OU MAIS POIs sustentando
--       304 (3 por cento) dependem de um POI so
--     media de 5,3 POIs e 2,8 fontes por ligacao aprovada
--
-- Ou seja: as aprovadas nao sao os casos fracos. Sao justamente onde ha mais
-- evidencia acumulada. E 8.273 contradicoes se resolvem sem gastar um segundo
-- de modelo.
--
-- O VEREDITO MAIS FORTE VENCE entre os aprovados. `aprovado_exato` significa
-- que o letreiro traz o nome do cadastro; `aprovado_comercial` significa que
-- ha comercio mas nao se sabe qual. Quando as duas leituras existem na mesma
-- ligacao, a primeira e mais informativa e e ela que fica.
insert into radar_comercial.ligacao_veredito
    (id_empresa, ligacao, veredito, justificativa, confianca,
     pois, fontes, imagens, percepcao, modelo, segundos)
select
    -- A EMPRESA VAI FIXA, e nao por `core.empresa_atual()`. A migracao roda
    -- pelo psql como `supabase_admin`, que nao carrega claim de JWT: a funcao
    -- devolve NULL e o insert morre no not-null. Mesmo caso da migracao 0080.
    'd4939b46-bc67-4fdb-8a04-ce3f7ae3a8c2'::uuid,
    d.ligacao,
    d.veredito,
    format('APROVADA PELA REGRA: %s de %s registro(s) vinculado(s) a este '
           'hidrometro receberam aprovacao da IA, vindos de %s fonte(s) '
           'independente(s). Basta uma testemunha para o comercio estar '
           'provado; as demais leituras seguem no dossie.',
           d.aprovados, d.pois, d.fontes),
    case when d.veredito = 'aprovado_exato' then 0.90 else 0.70 end,
    d.pois, d.fontes, 0,
    jsonb_build_object(
        'regra', 'uma_aprovacao_basta',
        'decidido_em', now(),
        'pois_aprovados', d.ids_aprovados,
        'vereditos_dos_pois', d.vereditos),
    'regra',
    0
  from (
    select lp.ligacao,
           count(distinct lp.poi_id)                                as pois,
           count(distinct p.fonte)                                  as fontes,
           count(distinct v.poi_id) filter
                (where v.veredito like 'aprovado%')                 as aprovados,
           -- O MAIS FORTE VENCE: `min` sobre os dois textos poe
           -- 'aprovado_comercial' antes de 'aprovado_exato' na ordem
           -- alfabetica, entao a escolha e explicita e nao alfabetica.
           case when bool_or(v.veredito = 'aprovado_exato')
                then 'aprovado_exato' else 'aprovado_comercial' end as veredito,
           jsonb_agg(distinct v.poi_id) filter
                (where v.veredito like 'aprovado%')                 as ids_aprovados,
           jsonb_agg(distinct v.veredito) filter
                (where v.veredito is not null)                      as vereditos
      from radar_comercial.ligacao_poi lp
      join resources_root.cadastro_corsan l
           on l.num_ligacao::text = lp.ligacao
      join radar_comercial.pois p
           on p.id = lp.poi_id and p.fundido_em is null
      left join radar_comercial.poi_veredito v on v.poi_id = lp.poi_id
     where lp.descartado_em is null
       and upper(coalesce(l.categoria, '')) = 'RESIDENCIAL'
       and upper(coalesce(l.sit_ligacao, '')) = 'ATIVA'
     group by lp.ligacao
    having coalesce(bool_or(v.veredito like 'aprovado%'), false)
  ) d
-- QUEM JA TEM VEREDITO DE LIGACAO NAO E TOCADO. A regra preenche o vazio; ela
-- nao sobrepoe um julgamento que a IA tenha feito com o dossie completo, que e
-- mais informado que ela.
on conflict (id_empresa, ligacao) do nothing;
