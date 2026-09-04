-- A CATEGORIA SE PREENCHE SOZINHA, no momento em que o vinculo nasce.
--
-- A migracao 0068 criou a coluna e preencheu as 65.772 linhas que existiam. Ela
-- nao resolveu o dia seguinte: quem ESCREVE em `ligacao_poi` sao dois scripts —
-- `cruzar_ligacao.py` e `telhados.py` — e nenhum dos dois sabia da coluna. Os
-- vinculos das rodadas novas nasceriam sem categoria, e o marcador de
-- oportunidade (ligacao RESIDENCIAL com comercio no local) nunca apareceria
-- para o que fosse minerado a partir de agora.
--
-- POR QUE GATILHO, E NAO UM `INSERT` MAIOR EM CADA SCRIPT. Sao dois escritores
-- hoje, e a lista de escritores e o que sempre fica desatualizada — foi assim
-- que `vinculo_poi` acabou exigida pelo mapa e preenchida por ninguem. Uma
-- regra no banco cobre os dois e cobre o terceiro que vier.
--
-- O CUSTO E POR LINHA, E FOI MEDIDO: a busca pontual em
-- `resources_root.cadastro_corsan` usa a chave primaria `(id_empresa,
-- num_ligacao)` e leva 0,4 ms. Uma rodada grava alguns milhares de vinculos —
-- segundos, nao minutos.
--
-- E NAO E `security definer` de proposito: o gatilho roda com a identidade de
-- quem inseriu, que e a mesma que ja enxerga a base do cliente. Elevar
-- privilegio aqui daria a qualquer escritor uma janela para a base inteira.

create or replace function radar_comercial.preencher_categoria_ligacao()
returns trigger
language plpgsql
as $$
begin
    -- So busca quando ha o que buscar. `ligacao` e texto e pode vir com
    -- lixo: `::bigint` levantaria excecao e derrubaria a gravacao inteira
    -- por causa de uma linha — daí o teste do formato antes do cast.
    if new.ligacao is null or new.ligacao !~ '^[0-9]+$' then
        return new;
    end if;

    select cc.categoria into new.categoria_ligacao
      from resources_root.cadastro_corsan cc
     where cc.id_empresa = new.id_empresa
       and cc.num_ligacao = new.ligacao::bigint;

    return new;
end;
$$;

drop trigger if exists tg_categoria_ligacao on radar_comercial.ligacao_poi;

create trigger tg_categoria_ligacao
    before insert or update of ligacao on radar_comercial.ligacao_poi
    for each row
    execute function radar_comercial.preencher_categoria_ligacao();
