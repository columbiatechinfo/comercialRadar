# -*- coding: utf-8 -*-
"""Quem é o usuário da requisição, e com que conexão ele fala com o banco.

Duas responsabilidades que andam juntas e por isso moram no mesmo lugar:

1. **Identificar.** O token vem do GoTrue; daqui sai `id`, `tenant_id` e `nivel`.
2. **Conectar com esse crachá.** A conexão declara `app.tenant_id` por
   TRANSAÇÃO, e é essa variável que as policies de RLS leem.

O ponto que não pode ser afrouxado: **`tenant_id` e `nivel` vêm do banco, a
partir do id do token — nunca do corpo da requisição e nunca do
`user_metadata`.** Metadado do GoTrue é editável pelo próprio usuário em vários
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
_ANON = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

CACHE_S = 60
_cache: dict[str, tuple[float, str]] = {}     # token -> (expira_em, user_id)

NIVEIS = ("user", "supervisor", "admin", "root")   # do menos para o mais amplo


@dataclass(frozen=True)
class Usuario:
    id: str
    tenant_id: str | None
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
    import base_comum as bc
    con = bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""select tenant_id, nivel, nome, email, ativo
                             from usuarios where id = %s""", (uid,))
            r = cur.fetchone()
    finally:
        con.close()
    if not r:
        raise HTTPException(403, "usuário autenticado mas sem vínculo com empresa")
    if not r[4]:
        raise HTTPException(403, "usuário desativado")
    return Usuario(id=uid, tenant_id=str(r[0]) if r[0] else None,
                   nivel=r[1], nome=r[2], email=r[3])


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


def conectar_como(u: Usuario):
    """Conexão com o crachá do usuário — é ela que a RLS filtra.

    `root` usa o papel com BYPASSRLS, que enxerga todas as empresas e também o
    schema das outras ferramentas. Os demais usam `comercialradar_app`, que NÃO
    tem BYPASSRLS: para eles quem decide o que existe é a policy, não o `WHERE`
    da rota. É essa diferença que faz uma rota esquecida vazar nada.

    `app.tenant_id` entra por `SET LOCAL`, dentro da transação: a variável morre
    no commit e a conexão devolvida ao pool não carrega o tenant do usuário
    anterior para a requisição seguinte.
    """
    raiz = u.nivel == "root"
    con = psycopg2.connect(
        host=os.environ["I9_POSTGRES_HOST"],
        port=int(os.environ.get("I9_POSTGRES_PORT", "5444")),
        user="comercialradar_root" if raiz else "comercialradar_app",
        password=(os.environ.get("CR_ROOT_PASSWORD") if raiz
                  else os.environ.get("CR_APP_PASSWORD")) or "",
        dbname=os.environ.get("I9_POSTGRES_DB", "postgres"),
        options="-c search_path=comercialradar,public",
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
        if u.tenant_id:
            cur.execute("select set_config('app.tenant_id', %s, true)", (u.tenant_id,))
        cur.execute("select set_config('app.nivel', %s, true)", (u.nivel,))
        cur.execute("select set_config('app.usuario_id', %s, true)", (u.id,))
    return con
