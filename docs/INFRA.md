# Infraestrutura — ComercialRadar

> Perfil detectado: **`wsl-dev`** (host Windows com WSL). Achados informam, nada
> bloqueia. Subida em 12/08/2026.

O dado é real, mesmo o perfil sendo de desenvolvimento. Onde isso muda o
julgamento, está dito.

---

## Onde tudo mora

| | |
|---|---|
| Máquina | **i9** — `100.115.117.49` no tailnet, Windows com WSL Ubuntu 24.04 |
| Recursos | 16 núcleos · 94 GB de RAM no WSL (128 no host) · **781 GB livres** |
| Diretório | `/home/orbisgrid/comercialradar-infra/pilha` |
| Volume do banco | `.../pilha/volumes/db/data` — **dentro do sistema de arquivos do WSL** |

O volume **nunca** vai para `/mnt/c`. A travessia entre o FS do WSL e o do
Windows destrói o I/O — é a diferença entre um banco usável e um que trava.

A escolha do i9 não foi preferência: o notebook tem **um disco só**, com 66 GB
livres, e o banco tem 66 GB. Dump mais restauração pediriam ~132 GB.

---

## O que subiu

Onze serviços, todos `healthy`. Versões fixas — nada de `:latest`, porque
atualização é ato deliberado, com backup antes.

| Serviço | Imagem |
|---|---|
| db | `supabase/postgres:17.6.1.136` |
| api-gw | `envoyproxy/envoy:v1.39.0` |
| rest | `postgrest/postgrest:v14.12` |
| auth | `supabase/gotrue:v2.189.0` |
| realtime | `supabase/realtime:v2.102.3` |
| storage | `supabase/storage-api:v1.60.4` |
| supavisor | `supabase/supavisor:2.9.5` |
| studio | `supabase/studio:2026.08.03` |
| meta · imgproxy · functions | `postgres-meta:v0.96.6` · `imgproxy:v3.30.1` · `edge-runtime:v1.74.0` |

```bash
cd /home/orbisgrid/comercialradar-infra/pilha
sh run.sh start | stop | status | logs [servico] | secrets
```

---

## Portas — e por que estas

O i9 já rodava uma pilha geo inteira. As portas foram escolhidas contra ela:

```
ocupadas antes:  5000-5003 OSRM/OpenTopoData · 5433 e 8080 Nominatim
                 8888 SearXNG · 11434 Ollama · 2322 e 9201 Photon
                 5432 (Windows) PostgreSQL 14 nativo, alheio a esta pilha
```

| Do tailnet | → WSL | O quê |
|---|---|---|
| `100.115.117.49:8000` | `:8000` | gateway — REST, Auth, Storage, Studio |
| `100.115.117.49:5442` | `:5442` | Postgres pelo Supavisor |
| `100.115.117.49:6543` | `:6543` | pooler em modo transação |
| `100.115.117.49:5443` | `:5443` | banco de **referência** (CNEFE, Receita, OSM) |
| `100.115.117.49:5444` | `:5444` | Postgres **direto**, para o worker do pipeline |

**A 5444 não passa pelo pooler, de propósito.** Pooler existe para quem abre
conexão por requisição — o PostgREST. O pipeline faz o oposto: poucas conexões
com trabalho longo, `COPY` e `execute_values`, e o modo transação do Supavisor
perde tabela temporária, *prepared statement* e lock de sessão.

> O Postgres da pilha escuta na **5442 dentro do container**, não na 5432:
> `POSTGRES_PORT` define a porta interna também. Publicar `5444:5432` apontava
> para porta vazia — o `docker-proxy` aceitava e fechava, produzindo
> *"server closed the connection unexpectedly"*, erro com cara de banco e causa
> de mapeamento.

O encaminhamento escuta **só na interface do Tailscale**, e não em `0.0.0.0`
como as regras que já existiam ali: quem precisa alcançar a pilha está no
tailnet. A porta externa **5442** existe porque a 5432 do Windows já é do
PostgreSQL 14 nativo.

### O IP da distro muda, e a regra guarda o antigo

`netsh portproxy` aponta para o IP da distro WSL, que **muda a cada reinício**.
O sintoma é cruel: os serviços sobem normalmente, os logs ficam limpos, e nada
responde de fora.

Resolvido por uma **tarefa agendada na inicialização** do Windows do i9 —
`WSL portproxy - comercialRadar`, rodando `wsl-portproxy.ps1` como SYSTEM. Ela
espera a distro responder, descobre o IP atual e reconstrói as doze regras,
registrando em `wsl-portproxy.log`. Conserta também as regras antigas do i9,
que tinham a mesma fragilidade e não eram cobertas por nada.

Para rodar à mão, se algo sumir da rede:

```powershell
powershell -ExecutionPolicy Bypass -File $env:USERPROFILE\wsl-portproxy.ps1
```

> **`networkingMode=mirrored` foi tentado em 12/08/2026 e revertido.** Dispensaria
> o portproxy inteiro, e a máquina atende os requisitos — Windows 11 build 26200,
> WSL 2.2.4. Mas em modo espelhado o tráfego de entrada passa pelo **firewall do
> Hyper-V**, que é outra camada de regras: das dez portas testadas do notebook,
> só Ollama e Nominatim responderam, e as outras oito — que funcionavam —
> pararam. Revertido em seguida, com o `.wslconfig` restaurado do backup e as
> regras reconstruídas com o IP corrente. Voltou 11/11.
>
> Registrado para não ser tentado de novo sem antes resolver o firewall do
> Hyper-V (`Get-NetFirewallHyperVRule`), que é o trabalho real por trás disso.

> **O script é ASCII de propósito.** Acento e travessão viram lixo quando o
> PowerShell lê o arquivo como ANSI, e o erro aparece como "cadeia sem
> terminador" numa linha que parece perfeita.

---

## Papéis de banco

Criados à mão: o Supabase não os cria, e são eles que tornam o RLS confiável.
Se a aplicação for **dona** da tabela ou tiver `BYPASSRLS`, toda política vira
decoração — e a `/seguranca` audita isso como crítico.

| Papel | Para | `superuser` | `bypassrls` |
|---|---|---|---|
| `comercialradar_migrator` | migrations e CI | não | não |
| `comercialradar_app` | a API, sujeita a RLS | não | não |
| `comercialradar_readonly` | relatório e BI | não | não |
| `comercialradar_worker` | **o pipeline Python** | não | **sim** |

O nome leva a ferramenta porque o banco é compartilhado: `app` genérico esconde
de quem é o papel quando houver três sistemas ali.

**O `worker` tem `BYPASSRLS`, e é consciente.** Ele roda no servidor, sem usuário
logado, e nunca é alcançável pelo navegador — é o "worker de confiança" que a
regra da casa prevê. Sem isso, com RLS ligada e sem policy, o pipeline leria zero
linha das próprias tabelas. Ele é **separado do `app`** justamente para que o
`app` continue sujeito à RLS: dar `BYPASSRLS` ao papel da API transformaria toda
policy futura em decoração.

Senhas no `.env` da pilha, com permissão `600`.

> Isolamento **provado**, não afirmado: `comercialradar_app` recebe
> `permission denied for schema radartelhados` ao tentar atravessar sem grant.

---

## Segredos

Gerados pelo `utils/generate-keys.sh` do próprio projeto, que conhece o formato
da versão instalada. Os valores de exemplo do repositório oficial são **públicos**
e varridos por scanner — subir com eles é deixar a porta aberta.

> Na primeira execução o `setup.sh` **imprimiu os segredos na saída**, e eles
> foram parar na transcrição da sessão. Como nada dependia deles ainda, todos os
> sete foram **regerados** antes de qualquer serviço nascer. Custo zero por ter
> sido pego na hora; caro se tivesse sido percebido depois.

---

## PostGIS — como ficou

```
origem (notebook)     PostgreSQL 16.11 · PostGIS 3.6.1
produto (pilha)       PostgreSQL 17.6  · PostGIS 3.3.7   única na imagem Supabase
referência (próprio)  PostgreSQL 17.5  · PostGIS 3.5.2   imagem postgis/postgis
```

Postgres 16 → 17 é o sentido que funciona; PostGIS para trás não é caminho
suportado. Por isso as tabelas de referência foram para um container com PostGIS
mais novo, e não para a pilha.

> **Correção do que eu havia escrito antes:** a `ibge_cnefe` **não tem coluna de
> geometria nenhuma** — os 23 GB dela são texto e número. Ao medir, o banco
> inteiro tem 5 colunas de geometria, todas `GEOMETRY` 2D em SRID 4326, sem
> coluna gerada e sem constraint usando `ST_`. O argumento do PostGIS para
> separar os bancos era **bem mais fraco** do que pintei. O que sustenta a
> separação é o outro motivo: analítico pesado não divide instância com quem
> atende usuário.

Ver [ADR 0003](adr/0003-onde-mora-cada-banco.md).

---

## Storage

`STORAGE_BACKEND` em arquivo local, dentro do volume da pilha, hoje com **6,7 GB**.
Em `wsl-dev` isso basta. Em produção vai para o **R2** — egress zero, e o disco
fica só para o Postgres, porque disco cheio derruba o banco junto.

Os bytes de imagem **já saíram do banco** — ver a seção "Imagens" adiante.

---

## Backup — feito, e restaurado

Diário às 03:10 pelo cron do i9, com verificação de restauração aos domingos.

| O que | Tamanho | Entra? | Por quê |
|---|---|---|---|
| Banco do produto | 220 MB (31 MB comprimido) | **sim** | POIs, cadastro, anotações e os usuários do Auth. Nada disso se refaz |
| Storage | 6,7 GB | **sim** | cada fachada custou chamada de API e captura |
| Banco de referência | 60 GB | não | base pública do IBGE e da Receita: rebaixa da fonte |

O Storage vai por `rsync --link-dest`: cada dia fica navegável como cópia
inteira, mas custa em disco só o que mudou. Tar diário de 6,7 GB encheria o disco
para guardar quatorze vezes o mesmo arquivo.

```bash
bash ~/comercialradar-infra/backup/backup.sh              # rodar à mão
bash ~/comercialradar-infra/backup/testar_restauracao.sh  # provar que restaura
```

> **A restauração é testada, não presumida.** O script restaura o dump mais
> recente num banco descartável e confere as nove tabelas linha a linha, mais a
> existência do schema `auth` — banco com dado e sem quem possa lê-lo não é
> restauração. Validado em 12/08/2026. Backup nunca restaurado é hipótese: ele é
> gerado, ocupa disco, parece certo, e só no dia do desastre se descobre que
> faltava um schema.

---

## Imagens — fora do banco

Os 6,3 GB de `bytea` viraram 71.965 objetos no bucket `comercialradar`. O banco
guarda o caminho; os bytes vêm por HTTP.

```
fachada/<id % 100>/<id>.jpg    32.380 objetos
foto/<id % 100>/<id>.jpg       39.585 objetos
```

O prefixo de dois dígitos existe para não deixar dezenas de milhares de objetos
num diretório só — listar vira operação cara e algumas ferramentas engasgam.

`imagens.py` é o único ponto que sabe disso. Ele busca no Storage e **cai para a
coluna `dados`** quando a linha não tem caminho — foi essa queda que permitiu
migrar os três leitores (a IA, o painel e o descritor) um a um, sem que nenhum
quebrasse enquanto o outro não tinha migrado.

> Uma falha em 71.965 envios: **um GIF**. O bucket tinha sido criado aceitando só
> JPEG, PNG e WebP, e a base tinha exatamente um GIF entre 39.737 fotos.
> Restringir tipo protege contra upload indevido, mas a lista tem de refletir o
> que EXISTE — senão o portão barra dado legítimo e a falha só aparece no meio de
> uma carga de 40 mil.
