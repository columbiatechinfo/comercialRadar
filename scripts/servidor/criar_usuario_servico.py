# -*- coding: utf-8 -*-
"""Cria o usuário de SERVIÇO de uma empresa — aquele que o pipeline assume.

POR QUE O PIPELINE PRECISA DE UM USUÁRIO

As políticas do `core` decidem por `core.empresa_atual()`, que faz
`select id_empresa from core.tb_users where id = (select auth.uid())`. Ou seja:
elas partem de um USUÁRIO e descobrem a empresa. O painel tem quem esteja
logado; o pipeline é lote e não tem ninguém.

Sem um usuário, `auth.uid()` volta nulo, `empresa_atual()` volta nulo, e toda
política nega. A rodada roda inteira e grava ZERO linha — sem erro, que é o pior
jeito de falhar.

POR QUE ELE É CONTA DE VERDADE, e não só uma linha

`core.tb_users.id` tem chave estrangeira para `auth.users`, a tabela do GoTrue.
Inserir só no `core` é recusado. Isso foi descoberto tentando: o teste de
isolamento levantou `ForeignKeyViolation: Key is not present in table "users"`.

POR QUE PELA API DE IDENTIDADE, e não direto no GoTrue

Criar a conta e a linha do `core` são duas operações. Feitas à mão, podem falhar
pela metade — e conta sem linha no `core` é um usuário que AUTENTICA e não
pertence a empresa nenhuma: ele entra e não vê nada, com o log dizendo que o
login deu certo. A API de identidade faz as duas como uma só. É também onde a
chave de serviço é usada, em exatamente um lugar, como manda o doc 23.

USO
    python scripts/servidor/criar_usuario_servico.py --empresa "Corsan - Aegea RS"

A senha do root é pedida no terminal, sem eco, e não fica em lugar nenhum: este
script roda uma vez por empresa, não é parte da mineração.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request

import config  # noqa: F401  — carrega o .env
import endpoints

#: Nível 1 (`user`). O pipeline GRAVA, mas não administra nada: ele não cria
#: usuário, não muda empresa e não distribui trabalho. Dar-lhe nível maior seria
#: dar privilégio para algo que ele nunca faz — e que um dia alguém usaria.
NIVEL_SERVICO = 1


def _pedir(url: str, corpo: dict | None = None, token: str | None = None,
           metodo: str = "GET") -> dict:
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    cab = {"Content-Type": "application/json"}
    # O `apikey` E OBRIGATORIO NO GATEWAY, INCLUSIVE NO LOGIN.
    #
    # Sem ele o envoy recusa antes de o GoTrue ver a senha, e a resposta e um
    # 401 seco — indistinguivel de senha errada. Foi o que aconteceu com uma
    # credencial que funcionava no Postman: a colecao manda `apikey` e este
    # script nao mandava. `SUPABASE_ANON_KEY` e o nome que o projeto usa; o
    # antigo fica como queda.
    chave = (os.environ.get("SUPABASE_ANON_KEY")
             or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if chave:
        cab["apikey"] = chave
    if token:
        cab["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=dados, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"\n❌ {metodo} {url}\n   HTTP {e.code}: {detalhe}")
    except urllib.error.URLError as e:
        raise SystemExit(
            f"\n❌ não alcancei {url}: {e.reason}\n\n"
            f"   A API de identidade publica SÓ em {endpoints.LAN}:7710 — sem\n"
            f"   127.0.0.1, ao contrário dos outros serviços da stack. De fora\n"
            f"   do servidor, um túnel para a loopback não a alcança.")


def entrar(email: str) -> str:
    """JWT do GoTrue. A senha é digitada agora e não vai para lugar nenhum."""
    senha = getpass.getpass(f"senha de {email}: ")
    r = _pedir(f"{endpoints.SUPABASE}/auth/v1/token?grant_type=password",
               {"email": email, "password": senha}, metodo="POST")
    tok = r.get("access_token")
    if not tok:
        raise SystemExit("o GoTrue não devolveu access_token")
    return tok


def achar_empresa(token: str, nome: str) -> dict:
    empresas = _pedir(f"{endpoints.IDENTIDADE}/empresas", token=token)
    # A API DE IDENTIDADE DEVOLVE `{"empresas": [...]}`, e nao `dados`.
    #
    # Lendo a chave errada, a lista vinha vazia e a mensagem dizia
    # "empresa 'A2L' nao existe. Visiveis: (nenhuma)" — enquanto o mesmo token
    # no Postman listava A2L e Corsan. `dados` fica como queda para nao quebrar
    # se outra rota usar esse formato.
    lista = (empresas if isinstance(empresas, list)
             else (empresas.get("empresas") or empresas.get("dados") or []))
    alvo = [e for e in lista
            if (e.get("name") or "").strip().lower() == nome.strip().lower()]
    if not alvo:
        nomes = ", ".join(sorted((e.get("name") or "?") for e in lista)) or "(nenhuma)"
        raise SystemExit(f"empresa {nome!r} não existe. Visíveis: {nomes}")
    return alvo[0]


def criar(token: str, empresa: dict) -> dict:
    # O E-MAIL SEGUE UM PADRÃO, e é por ele que o pipeline se encontra depois.
    # `bc.assumir_empresa` procura `email like 'pipeline@%'` dentro da empresa —
    # mudar este formato aqui e não lá quebraria a mineração com "a empresa não
    # tem usuário de serviço", que é erro claro mas em outro arquivo.
    apelido = re.sub(r"[^a-z0-9]+", "-", (empresa.get("name") or "").lower()).strip("-")
    email = f"pipeline@{apelido or empresa['id']}.servico.invalido"

    print(f"  empresa : {empresa.get('name')} ({empresa['id']})")
    print(f"  e-mail  : {email}")
    print(f"  nível   : {NIVEL_SERVICO} (user — grava, não administra)")

    novo = _pedir(f"{endpoints.IDENTIDADE}/usuarios",
                  {"email": email,
                   "name": f"Pipeline — {empresa.get('name')}",
                   "id_empresa": empresa["id"],
                   "id_nivel_user": NIVEL_SERVICO},
                  token=token, metodo="POST")
    return novo


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--empresa", required=True, help="nome da empresa, como no core")
    p.add_argument("--root", default="servidor.dados@a2lsolucoes.com",
                   help="e-mail do root que vai criar o usuário")
    a = p.parse_args(argv)

    token = entrar(a.root)
    empresa = achar_empresa(token, a.empresa)
    novo = criar(token, empresa)

    # A CRIACAO DEVOLVE `{"usuario": {...}, "senha_inicial": ...}`.
    # A colecao do Postman confirma: ela le `d.usuario.id`. Ler `dados`
    # deixaria o uid nulo logo depois de o usuario ter sido criado — o
    # pior desfecho, porque a proxima tentativa esbarraria em e-mail
    # duplicado sem nunca ter mostrado o que deu certo.
    uid = (novo.get("id")
           or (novo.get("usuario") or {}).get("id")
           or (novo.get("dados") or {}).get("id"))
    print("\n✅ usuário de serviço criado.")
    print(f"\n   Ponha no .env do servidor:\n\n     RADAR_USUARIO_SERVICO={uid}\n")
    print("   É esse uuid que o pipeline declara em `request.jwt.claim.sub`, e")
    print("   é dele que `core.empresa_atual()` tira a empresa de cada gravação.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
