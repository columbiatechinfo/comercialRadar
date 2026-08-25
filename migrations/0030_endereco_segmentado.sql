-- 0030 — o endereço grudado, lido em campos pela IA da Spark.
--
-- POR QUE UMA TABELA, e não uma coluna em `pois`
--
-- A chave é o TEXTO do endereço, não o POI. O mesmo endereço aparece em vários
-- POIs (galeria, prédio comercial, rede com duas lojas na mesma rua) e em mais
-- de uma base — `pois`, `cadastro_cliente`, `ifood_merchant`. Guardar por POI
-- pagaria a mesma leitura muitas vezes e permitiria que dois registros do mesmo
-- texto discordassem entre si.
--
-- Numa base municipal os endereços distintos são ordens de grandeza menos que os
-- registros: é a mesma memoização por valor distinto que a `ajuste-logradouro`
-- documenta (69,0 s -> 11,2 s em 300k registros).
--
-- `metodo` É O QUE IMPEDE A BASE DE MENTIR
--
--   ia       todos os campos devolvidos existem no texto original
--   parcial  algum campo foi DESCARTADO por não estar no texto (o modelo
--            expandiu abreviação, deduziu cidade pelo CEP, completou "Brasil")
--   revisar  nem o logradouro sobreviveu à conferência
--   falhou   a Spark não respondeu, ou respondeu o que não dá para ler
--
-- `motivo` guarda o que foi descartado, com o valor. Sem isso, "parcial" seria
-- um rótulo sem recurso: ninguém saberia o que o modelo tentou inventar.
--
-- `modelo` viaja junto porque troca de modelo é troca de leitor. Quando o
-- `qwen3vl-moe` virar outro, dá para reprocessar só o que o antigo leu.

create table if not exists comercialradar.endereco_segmentado (
  endereco       text primary key,
  logradouro     text,
  numero         text,
  complemento    text,
  bairro         text,
  cep            text,
  cidade         text,
  uf             text,
  metodo         text not null,
  motivo         text,
  modelo         text,
  segmentado_em  timestamptz not null default now()
);

-- O recorte por município é como o adaptador lê: cidade + uf, nunca a tabela
-- inteira. Sem índice isso vira varredura da base de endereços a cada rodada.
create index if not exists ix_endereco_segmentado_cidade
  on comercialradar.endereco_segmentado (cidade, uf);

-- A fila de revisão humana é consulta de rotina — o que NÃO é `ia` é o que
-- alguém precisa olhar.
create index if not exists ix_endereco_segmentado_metodo
  on comercialradar.endereco_segmentado (metodo)
  where metodo <> 'ia';

-- SEM `tenant_id`, E ISSO É DELIBERADO.
--
-- A migração 0029 estabeleceu que toda tabela nova nasce com política de RLS, e
-- o teste `test_isolamento_tenant.py` cobra a REGRA, não uma lista — então esta
-- exceção precisa estar escrita onde o teste e quem o lê vão procurar.
--
-- O conteúdo aqui é a leitura de um TEXTO PÚBLICO: "R. Mal. Rondon, 1199" é o
-- mesmo endereço para qualquer cliente, e a segmentação dele não revela nada
-- sobre a carteira de ninguém. Carimbar tenant faria a Corsan pagar de novo a
-- leitura que outra concessionária já fez do mesmo logradouro, e faria a mesma
-- rua ter duas verdades no banco.
--
-- O vínculo com o cliente mora onde sempre morou: em `pois` e em
-- `cadastro_cliente`, que têm `tenant_id` e RLS. Esta tabela é dicionário, não
-- cadastro.
comment on table comercialradar.endereco_segmentado is
  'Dicionario compartilhado: texto de endereco -> campos. Sem tenant_id de '
  'proposito (ver comentario da migracao 0030): e leitura de texto publico, '
  'nao dado de cliente.';
