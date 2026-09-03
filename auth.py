# -*- coding: utf-8 -*-
"""Quem é o usuário da requisição, e com que conexão ele fala com o banco.

Duas responsabilidades que andam juntas e por isso moram no mesmo lugar:

1. **Identificar.** O token vem do GoTrue; daqui sai `id`, `id_empresa` e
   `nivel`.
2. **Conectar com esse crachá.** A conexão declara `request.jwt.claim.sub` por
   TRANSAÇÃO — o uuid do usuário, e nada mais. É de lá que `core.empresa_atual()`,
   `core.nivel_atual()` e `core.eh_suporte()` tiram empresa e nível, lendo
   `core.tb_users`. Uma variável, três respostas, uma fonte só.

O ponto que não pode ser afrouxado: **empresa e nível vêm do banco, a partir do
id do token — nunca do corpo da requisição e nunca do `user_metadata`.** Metadado do GoTrue é editável pelo próprio usuário em vários
fluxos; aceitar `nivel` de lá seria deixar o cliente escolher o próprio nível.

Sobre a validação do token: hoje ela é feita perguntando ao GoTrue, com cache de
60 s. Sem o `JWT_SECRET` no `.env` não dá para verificar a assinatura localmente,
e uma chamada HTTP por requisição não sobrevive a carga — o cache é o que torna
isso viável enquanto o segredo não estiver disponível. Com ele, trocar por
verificação local é mudar `_validar`; o resto deste arquivo não muda.
"""
from __future__ import annotations

import contextvars
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import psycopg2
from fastapi import Depends, Header, HTTPException

import config  # noqa: F401  (carrega o .env)
import endpoints

_GW = endpoints.SUPABASE
# A CHAVE DO LOGIN É A `anon`, E O NOME DA VARIÁVEL ESTAVA ERRADO.
#
# Este valor entra só como cabeçalho `apikey` na chamada ao GoTrue — e o
# docstring do `/api/login` já dizia "a chave `anon`". Mesmo assim a variável
# lida era `SUPABASE_SERVICE_ROLE_KEY`, a de superusuário: ela ignora RLS e vale
# para o banco inteiro. Guardar isso no `.env` de uma API web é pôr o Supabase
# todo atrás de um arquivo que só precisava conter a chave pública.
#
# A queda para o nome antigo fica por compatibilidade: onde só existir a
# variável velha, nada quebra. Storage e scripts continuam com o service_role,
# porque lá o privilégio é usado de fato.
#
# O SINTOMA DE NÃO TER NENHUMA DAS DUAS, medido em 02/09/2026: o envoy responde
# 401 a tudo, o `/api/login` traduz qualquer HTTPError em "e-mail ou senha
# inválidos", e a tela acusa credencial errada com a senha certa.
_ANON = ((os.environ.get("SUPABASE_ANON_KEY")
          or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip())

CACHE_S = 60
_cache: dict[str, tuple[float, str]] = {}     # token -> (expira_em, user_id)

# A ESCADA, NA ORDEM QUE O BANCO DECLARA.
#
# `core.tb_niveis_user` guarda a hierarquia em número: user 20, editor 40,
# supervisor 60, administrator 80, root 100. `editor` faltava aqui, e nível que
# não está na tupla estoura `ValueError` no `NIVEIS.index()` — ou seja, um
# usuário editor derrubava o portão inteiro em vez de ser recusado com 403.
NIVEIS = ("user", "editor", "supervisor", "admin", "root")

# O BANCO CHAMA DE `administrator`; ESTE CÓDIGO CHAMA DE `admin`.
#
# São 11 usos de "admin" no `server.py` e mais no frontend. Traduzir na
# fronteira — aqui, onde o crachá é montado — é uma linha; renomear os dois
# lados seria mexer em trinta pontos para o mesmo efeito.
_TRADUZ_NIVEL = {"administrator": "admin"}


@dataclass(frozen=True)
class Usuario:
    id: str
    id_empresa: str | None
    nivel: str
    nome: str | None
    email: str | None

    def pode(self, minimo: str) -> bool:
        """Nível é escada: admin faz o que supervisor faz, e por aí abaixo.

        Foi pedido explicitamente — 'admin pode ocupar qualquer função abaixo'."""
        return NIVEIS.index(self.nivel) >= NIVEIS.index(minimo)


def _validar(token: str) -> str:
    """Devolve o id do usuário, ou levanta 401. Cacheado por 60 s."""
    agora = time.time()
    achado = _cache.get(token)
    if achado and achado[0] > agora:
        return achado[1]

    req = urllib.request.Request(
        f"{_GW}/auth/v1/user",
        headers={"apikey": _ANON, "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            uid = json.loads(r.read())["id"]
    except urllib.error.HTTPError:
        raise HTTPException(401, "sessão inválida ou expirada")
    except Exception:
        raise HTTPException(503, "serviço de autenticação indisponível")

    # Poda preguiçosa: sem isto o dicionário cresce sem teto num processo longo.
    if len(_cache) > 5000:
        for k, (exp, _) in list(_cache.items()):
            if exp <= agora:
                _cache.pop(k, None)
    _cache[token] = (agora + CACHE_S, uid)
    return uid


def usuario_atual(authorization: str = Header(default="")) -> Usuario:
    """Dependência do FastAPI: exige `Authorization: Bearer <token>`."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "faltou o cabeçalho Authorization")
    uid = _validar(authorization.split(" ", 1)[1].strip())

    # O crachá vem do BANCO, não do token.
    #
    # E A CONEXAO PRECISA DIZER QUEM ESTA PERGUNTANDO. `core.tb_users` tem RLS
    # com `force`, e a politica de leitura e
    # `core.eh_suporte() or (id_empresa = core.empresa_atual())`. Uma conexao
    # anonima faz as duas funcoes responderem nulo, a politica nega, o `select`
    # volta vazio — e o codigo abaixo conclui "usuario sem vinculo com empresa".
    #
    # Foi o que a tela mostrou em 03/09/2026: login 200, e logo em seguida
    # "Conta sem vinculo com empresa. Fale com o root." para um usuario root,
    # ativo e com empresa. Nao faltava vinculo; faltava a conexao se apresentar.
    con = conectar_como(uid)
    try:
        with con.cursor() as cur:
            # `core.tb_users`, E NAO `usuarios`.
            #
            # A tabela `usuarios` nao existe mais: a migracao para o padrao A2L
            # (identidade em `core`) a substituiu por `core.tb_users`, e este
            # ponto do codigo ficou apontando para o nome antigo. O sintoma era
            # cruel — `POST /api/login` respondia 200, o token era valido, e a
            # chamada seguinte, `/api/eu`, morria com 500 e
            # `relation "usuarios" does not exist`. A tela caia de volta no
            # login dizendo "e-mail ou senha invalidos", com a senha certa.
            #
            # O nivel vem por juncao: `tb_users` guarda so o id do nivel.
            cur.execute("""
                select u.id_empresa, coalesce(n.codigo, 'user'),
                       u.name, u.email, u.ativo
                  from core.tb_users u
                  left join core.tb_niveis_user n on n.id = u.id_nivel_user
                 where u.id = %s
            """, (uid,))
            r = cur.fetchone()
    finally:
        con.close()
    if not r:
        raise HTTPException(403, "usuário autenticado mas sem vínculo com empresa")
    if not r[4]:
        raise HTTPException(403, "usuário desativado")
    nivel = _TRADUZ_NIVEL.get(str(r[1] or "user"), str(r[1] or "user"))
    if nivel not in NIVEIS:
        # NIVEL DESCONHECIDO E RECUSA, NAO ESTOURO. Se o banco ganhar um nivel
        # novo antes deste codigo, `pode()` levantaria `ValueError` e o portao
        # devolveria 500 para todo mundo daquele nivel. 403 diz o que houve.
        raise HTTPException(403, "nível de acesso desconhecido: %s" % r[1])
    return Usuario(id=uid, id_empresa=str(r[0]) if r[0] else None,
                   nivel=nivel, nome=r[2], email=r[3])


def exige(minimo: str):
    """Guarda de rota: `Depends(exige('admin'))`."""
    def _guarda(u: Usuario = Depends(usuario_atual)) -> Usuario:
        if not u.pode(minimo):
            raise HTTPException(403, f"exige nível {minimo} ou acima")
        return u
    return _guarda


# ── O usuário da requisição, alcançável sem passar por parâmetro ────────────
#
# Existe para que `realtime_ingest.conectar()` devolva a conexão CERTA sem que
# as 29 rotas antigas precisem ser reescritas uma a uma. Emendar `Depends` em
# cada uma resolveria hoje e falharia na trigésima — a rota nova entra sem
# ninguém lembrar, e o buraco não aparece em teste nenhum porque ninguém escreve
# teste para a rota que esqueceu de proteger.
#
# `ContextVar` e não variável global: cada requisição do FastAPI roda em seu
# próprio contexto, e requisições concorrentes não enxergam o usuário uma da
# outra. Variável de módulo aqui seria vazamento entre usuários simultâneos.
#
# Fora de requisição — o pipeline, os scripts — a variável fica vazia e a
# conexão continua sendo a do worker, como sempre foi.
USUARIO_DA_REQUISICAO: contextvars.ContextVar[Usuario | None] = \
    contextvars.ContextVar("usuario_da_requisicao", default=None)


def conectar_como(u):
    """Conexao com o cracha do usuario — e ela que a RLS filtra.

    UM PAPEL SO, e nao dois. Ate 30/08/2026 havia `app_user` (com
    BYPASSRLS) e `app_user` (sem), e o root enxergava todas as
    empresas por PRIVILEGIO DE PAPEL. No banco `a2l` isso deixou de existir:
    nenhum papel disponivel a aplicacao tem BYPASSRLS, e a travessia do root
    acontece DENTRO da politica, por `core.eh_suporte()`.

    A diferenca nao e de estilo. Papel com BYPASSRLS ignora TODA policy, de toda
    tabela, inclusive as que ninguem lembrou de conferir — e uma rota esquecida
    que use aquela conexao vaza tudo, calada. Pela politica, a excecao do root e
    uma linha de SQL que da para ler, e vale so onde foi escrita.

    UMA VARIAVEL SO, e nao tres. As policies antigas liam `request.jwt.claim.sub`,
    `app.nivel` e `app.usuario_id`. As do `core` leem `core.empresa_atual()`,
    `core.nivel_atual()` e `core.eh_suporte()` — e as tres saem de
    `(select auth.uid())`, que e `request.jwt.claim.sub`. Declarando o uuid do
    usuario, as tres respondem: empresa e nivel vem da linha dele em
    `core.tb_users`, que e a autoridade. Antes o nivel viajava na conexao e podia
    divergir do banco; agora nao ha o que divergir.

    `set_config(..., true)` = local a TRANSACAO: a variavel morre no commit e a
    conexao devolvida ao pool nao carrega o cracha do usuario anterior para a
    requisicao seguinte. Com o pooler em modo transacao isso deixou de ser
    cuidado e virou obrigacao — a conexao volta ao pool a cada transacao.
    """
    # ACEITA O CRACHA OU SO O UUID.
    #
    # `usuario_atual` precisa desta conexao ANTES de ter um `Usuario` — e para
    # montar o `Usuario` ele precisa ler `core.tb_users`, que so responde a quem
    # ja se declarou. Ovo e galinha. O uuid do token e suficiente: as funcoes da
    # policy sao `SECURITY DEFINER` e descobrem empresa e nivel sozinhas.
    uid = u if isinstance(u, str) else u.id

    dsn = (os.environ.get("A2L_DB_URL") or "").strip()
    if not dsn:
        raise RuntimeError(
            "A2L_DB_URL nao esta no .env. E a conexao da API, pela 7110 "
            "(modo transacao): muitas conexoes curtas, uma transacao por "
            "requisicao.\n\n"
            "  A2L_DB_URL=postgresql://app_user.a2l:<senha>@127.0.0.1:7110/a2l")

    con = psycopg2.connect(
        dsn,
        options="-c search_path=radar_comercial,extensions,public",
        connect_timeout=int(os.environ.get("PG_CONNECT_TIMEOUT", "20")))
    con.autocommit = False
    # Três variáveis, não uma. O tenant isola a empresa; o nível e o id do
    # usuário são o que permite à policy de `pois` estreitar o supervisor ao que
    # lhe foi atribuído — sem eles, "distribuir para supervisores" seria rótulo,
    # porque ele continuaria enxergando a base inteira da empresa.
    #
    # `set_config(..., true)` = local à TRANSAÇÃO: a variável morre no commit e
    # a conexão devolvida ao pool não carrega o crachá do usuário anterior. E é
    # `set_config` com parâmetro ligado porque `SET LOCAL` não aceita parâmetro
    # — interpolar o uuid na string seria injeção.
    with con.cursor() as cur:
        # O UUID DO USUARIO, e nada mais. Empresa e nivel o banco descobre
        # sozinho, lendo `core.tb_users`. Mandar nivel na conexao seria mandar
        # ao banco uma segunda versao de um dado que ele ja tem — e duas versoes
        # de uma verdade divergem no dia em que alguem muda uma so.
        cur.execute("select set_config('request.jwt.claim.sub', %s, true)", (uid,))
    return con
