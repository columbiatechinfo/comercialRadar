# Produção — `https://a2lsolucoes.com/seek`

> O molde é o do Hippo: repositório a2lGcp, `docs/adr/0004-producao-em-a2lsolucoes-hippo.md`
> e `docs/PRODUCAO.md`, lidos e seguidos em 14/09/2026. Este arquivo é o procedimento do
> Comercial Radar (SEEK na raiz, extração em `/seek/extrair`).

## O desenho

```
navegador ──https──> nginx (VPS 193.203.174.5)
                       ├── /seek/ws   → 127.0.0.1:7740/ws  ┐ ponta do ssh -R que o i9
                       └── /seek/     → 127.0.0.1:7740/    ┘ mantém (seek-tunel.service)
                                              │
                                              └── i9 127.0.0.1:7740 → radar-comercial-api (7720 no contêiner)
```

| Decisão | Por quê |
|---|---|
| Caminho `/seek`, não subdomínio | pedido do dono do produto. Custo: mesma origem do site institucional e do Hippo (o `sessionStorage` é por origem; as chaves do radar são `cr_*`) |
| A **ferramenta inteira** sob `/seek` | decisão do dono do produto, com a alternativa "só as rotas da SEEK" apresentada. Toda `/api/*` exige login, menos `/api/login`, `/api/renovar` e `/api/saude` |
| Túnel **próprio**, não porta a mais no do Hippo | chave e usuário separados; um túnel cai sem derrubar o outro, e a chave de um não abre a porta do outro |
| O prefixo é resolvido **na API** | o front tem ~150 caminhos absolutos (`/api/...`, `/static/...`) e a API devolve URLs de imagem em JSON. O nginx manda `X-Forwarded-Prefix: /seek`, e `PrefixoPublico` (server.py) acrescenta `/seek` nas respostas de texto — só quando o cabeçalho vale exatamente isso. Pelo Tailscale nada muda |
| Limite no login | 10 tentativas/min por IP, rajada de 10 (`seek-limites.conf`): o login passou a estar na internet |

## Arquivos

| Fonte (este repositório) | Onde vive |
|---|---|
| `deploy/nginx/seek.conf` | VPS `/etc/nginx/seek.conf`, incluído por UMA linha no server block de `a2lsolucoes.com` |
| `deploy/nginx/seek-seguranca.conf` | VPS `/etc/nginx/seek-seguranca.conf`, incluído em cada location |
| `deploy/nginx/seek-limites.conf` | VPS `/etc/nginx/conf.d/seek-limites.conf` (contexto http) |
| `deploy/seek-tunel.service` | i9 `/etc/systemd/system/seek-tunel.service` |

## O que se faz UMA vez

### 1 · Usuário e chave do túnel

**Na VPS**, usuário sem shell:

```bash
ssh prod 'adduser --system --group --shell /usr/sbin/nologin --home /home/seek-tunel seek-tunel && mkdir -p /home/seek-tunel/.ssh && chmod 700 /home/seek-tunel/.ssh && chown -R seek-tunel:seek-tunel /home/seek-tunel'
```

**No i9**, chave só para isto, e o host key da VPS no known_hosts ANTES do serviço
(`ProtectHome=read-only` impede o ssh de gravá-lo depois):

```bash
ssh-keygen -t ed25519 -N '' -C 'seek-tunel i9->vps' -f ~/.ssh/id_ed25519_seek_tunel
```

```bash
ssh-keygen -F 193.203.174.5 | grep -v '^#' | ssh-keygen -lf -
```

Tem de imprimir `SHA256:nCfcIE90huoFlvpogpp972I7zqjPx5ErxUeOq+U/Q/8` (ED25519, conferido pelo Hippo em 06/09/2026).

**Na VPS**, `/home/seek-tunel/.ssh/authorized_keys` (dono `seek-tunel`, modo 600), enviado por
arquivo e `scp`, nunca por heredoc dentro de `ssh '...'`:

```
restrict,port-forwarding,permitopen="127.0.0.1:1",permitlisten="127.0.0.1:7740" ssh-ed25519 AAAA... seek-tunel i9->vps
```

### 2 · Provar o túnel à mão, depois entregar ao systemd

```bash
ssh -i ~/.ssh/id_ed25519_seek_tunel -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -NT -R 127.0.0.1:7740:127.0.0.1:7740 seek-tunel@193.203.174.5
```

Fica pendurado. Da VPS, `ss -ltn | grep 7740` e `curl -s 127.0.0.1:7740/api/saude`. Então `Ctrl+C` e:

```bash
sudo cp deploy/seek-tunel.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now seek-tunel
```

### 3 · nginx

Backup, a linha no server block, os três arquivos, `nginx -t` e **reload** (nunca restart:
as conexões em curso são do site da empresa e do Hippo):

```bash
ssh prod 'cp -a /etc/nginx/sites-available/a2lsolucoes.com /root/backups/nginx-a2lsolucoes-$(date +%Y%m%d-%H%M%S).conf'
```

A linha, logo abaixo do `include /etc/nginx/auth-a2l.conf;`:

```
include /etc/nginx/seek.conf;
```

```bash
ssh prod 'nginx -t && systemctl reload nginx'
```

### 4 · A API com a camada do prefixo

`PrefixoPublico` está no `server.py`, que vai dentro da imagem:

```bash
cd deploy && docker compose -f compose.radar-comercial-api.yml --env-file ../.env up -d --build
```

## Conferir

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://a2lsolucoes.com/seek/api/saude
```

```bash
curl -s https://a2lsolucoes.com/seek/ | grep -o 'src="[^"]*sessao.js[^"]*"'
```

O primeiro dá 200 (banco respondendo pelo túnel); o segundo mostra `/seek/static/sessao.js`.
Na tela: login, fila, uma ficha com fotos e o visor, e `/seek/extrair` com o mapa.

## Endereços e portas

| O quê | Onde |
|---|---|
| URL pública | `https://a2lsolucoes.com/seek/` (SEEK) · `/seek/extrair` (extração) |
| Ponta do túnel | VPS `127.0.0.1:7740` → i9 `127.0.0.1:7740` |
| API no i9 | `radar-comercial-api`, `127.0.0.1:7740` → 7720 no contêiner |
| Chave do túnel | i9 `~/.ssh/id_ed25519_seek_tunel`, usuário `seek-tunel@VPS` |
| Endereço interno | `https://a2l-server-main-i914hx-pc-1.tail7e301b.ts.net:8445/` continua valendo |

## Pendências conhecidas

- **Content-Security-Policy**: fica de fora até inventariar os domínios externos da extração (mapa, fontes, Street View).
- **O túnel é ponto único de falha**, com um desktop de escritório atrás — o mesmo Q-008 do Hippo.
- **Monitor**: o `a2l-monitor` da VPS olha só o Hippo; acrescentar `127.0.0.1:7740/api/saude` e `https://a2lsolucoes.com/seek/`.
- **`/seek/docs` e `/seek/openapi.json`** ficam públicos junto com a ferramenta inteira.
