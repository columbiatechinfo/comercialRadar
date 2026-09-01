# Esta pasta NÃO roda o sistema. Ela é o repositório.

O Radar Comercial roda **inteiro no servidor**, e este notebook é terminal: manda
comando por SSH e lê log. Nenhum processo de mineração roda aqui.

Para que isso deixe de ser uma promessa e passe a ser um fato, em 31/08/2026
foram **removidos desta pasta** o `.venv`, o `.venv-coletivo` e o
`node_modules` — os três ambientes de execução. Sem eles não há como um
`python minerar_tudo.py` disparado por engano começar a fotografar o Google
Maps a partir daqui, queimando o IP de casa.

Junto saíram os **dados gerados** (`dados_externos/`, `capturas/`, `crops/`,
`cache_ibge/`, `.perfis_ifood/` e afins). Nenhum deles é fonte: todos são
produto de execução, e o original está no servidor.

## Por que isso importa

O sistema abre navegador de verdade contra o Google. Rodando do servidor, ele
sai por um pool de 500 IPs da Webshare, com rotação automática quando um começa
a receber resultado vazio. Rodando daqui, sairia pelo IP residencial — e o
sintoma de queimá-lo não é um erro claro, é CAPTCHA em toda busca, inclusive nas
que a pessoa faz no navegador dela.

## O que ficou aqui

```
.git/                 a história — 276 arquivos .py, e é de onde o código volta
.claude/  .agents/    memória de agente
skills/               instruções de agente
docs/                 a documentação, inclusive o que foi feito e onde retomar
*.md                  README, CHANGELOG, DOCUMENTACAO, PIPELINE, este arquivo
.env                  credencial — não é código, e o servidor tem a dele
mapa_google.html      um mapa solto, para teste (ver abaixo)
```

**O código saiu do disco, não do repositório.** Os 276 arquivos `.py`, os 8 `.ts`
e as 13 migrações continuam no último commit, e no GitHub. Para trazê-los de
volta:

```bash
git checkout -- .
```

E aqui está a única coisa perigosa desta arrumação: enquanto as remoções
estiverem na árvore de trabalho, um `git add -A && git commit` **apagaria o
código do repositório também**. Todo commit feito nesta pasta precisa nomear o
caminho — `git commit --only <arquivo>`.

## Onde ler o que foi feito

- **[docs/EXECUCAO-31-08-2026.md](docs/EXECUCAO-31-08-2026.md)** — a fase 1
  rodada ponta a ponta: as nove etapas, o que cada uma produziu, os seis
  defeitos encontrados e onde retomar.
- **[docs/BASES_PUBLICAS.md](docs/BASES_PUBLICAS.md)** — de onde vem cada base e
  como se dispara a carga de novo.
- `git log` — cada commit traz o defeito e a medição no corpo da mensagem.

## Como mexer no sistema a partir daqui

```bash
# ler
ssh i9 "cd ~/Documentos/sistemas/radarComercial && docker logs --tail 40 <container>"

# enviar código alterado
scp arquivo.py i9:/home/a2l/Documentos/sistemas/radarComercial/

# rodar
ssh i9 "cd ~/Documentos/sistemas/radarComercial && docker run --rm --network host \
  -v \$PWD:/app -v /app/node_modules -w /app -e HOME=/tmp \
  radar-minerador:latest python <script> <args>"
```

Depois do `scp`, rodar `sed -i 's/\r$//'` no servidor: o Windows grava CRLF e o
shell do container não perdoa.

## mapa_google.html

Um mapa do Google solto, no mesmo padrão que a captura usa para gerar os tiles
do OCR — mesma biblioteca, mesma chave, mesmo modo de carregar. Serve para
testar comportamento do mapa sem subir a máquina de captura inteira.

Ele precisa da chave da Maps JavaScript API, e **a chave não está neste
arquivo**: cole-a no campo da própria página, ou passe por `?key=` na URL. Ela
fica só na memória da aba.
