# -*- coding: utf-8 -*-
"""Estrutura, nao tipo: quem tem RLS, qual e a chave natural, quais indices.

Isto NAO e copia da estrutura velha — e o levantamento do que precisa existir. A
diferenca importa: `pois` sair sem isolamento por empresa nao seria uma escolha
de desenho, seria um vazamento. O gerador da DDL me avisou que 14 tabelas iam
sair sem RLS, e a causa foi um `do $$ ... format('alter table %I ...') $$` que
nenhum extrator de literal consegue atribuir a tabela.
"""
import io
import json
import os
import re

D = "migrations"
rls = set()
indices = []          # (unico, nome, tabela, colunas, where)
chaves = {}           # tabela -> colunas do ON CONFLICT

RE_RLS = re.compile(r"alter\s+table\s+(?:comercialradar\.)?([a-z_]+)\s+"
                    r"enable\s+row\s+level\s+security", re.I)
# SO ate o `(` da lista de colunas. O conteudo sai por contagem de parenteses:
# `[^)]*` para no primeiro fecha, e indice de EXPRESSAO tem parenteses dentro —
# `(COALESCE(maps_lat, lat_origem))` saia partido em dois pedacos que so por
# sorte voltavam a formar SQL valido depois de concatenados.
RE_IX = re.compile(r"create\s+(unique\s+)?index\s+(?:concurrently\s+)?"
                   r"(?:if\s+not\s+exists\s+)?([a-z_0-9]+)\s+on\s+"
                   r"(?:comercialradar\.)?([a-z_]+)\s*\(", re.I | re.S)


def dentro_e_depois(s: str) -> tuple:
    """`(conteudo dos parenteses, o que vem depois ate o ponto-e-virgula)`."""
    prof, ini = 0, 0
    for i, ch in enumerate(s):
        if ch == "(":
            prof += 1
            if prof == 1:
                ini = i + 1
        elif ch == ")":
            prof -= 1
            if prof == 0:
                resto = s[i + 1:]
                return s[ini:i], resto.split(";")[0]
    return s, ""
RE_CONF = re.compile(r"on\s+conflict\s*\(([^)]+)\)", re.I)
# a lista dentro de `foreach t in array array[...]`
RE_ARR = re.compile(r"array\s*\[([^\]]+)\]", re.I | re.S)

for arq in sorted(os.listdir(D)):
    if not arq.endswith(".sql"):
        continue
    s = io.open(os.path.join(D, arq), encoding="utf-8").read()
    limpo = "\n".join(l.split("--")[0] for l in s.split("\n"))

    for m in RE_RLS.finditer(limpo):
        rls.add(m.group(1).lower())

    # As tabelas citadas num `array[...]` dentro de bloco DO que fala de RLS ou
    # de tenant_id. E daqui que vem a lista que o extrator de literal perde.
    for m in RE_ARR.finditer(limpo):
        trecho = limpo[m.end():m.end() + 400].lower()
        if "row level security" in trecho or "tenant_id" in trecho:
            for nome in re.findall(r"'([a-z_]+)'", m.group(1)):
                rls.add(nome)

    for m in RE_IX.finditer(limpo):
        cols, resto = dentro_e_depois(limpo[m.end() - 1:])
        indices.append((bool(m.group(1)), m.group(2).lower(), m.group(3).lower(),
                        " ".join(cols.split()), " ".join(resto.split()).strip()))

    for m in RE_CONF.finditer(limpo):
        i = limpo.rfind("insert into", 0, m.start())
        if i >= 0:
            t = limpo[i:i + 80].split()[2].replace("comercialradar.", "").lower()
            chaves[t] = " ".join(m.group(1).split())

print("=== tabelas com RLS / tenant_id (%d) ===" % len(rls))
print("  " + ", ".join(sorted(rls)))
print("\n=== indices UNICOS (a chave natural) ===")
for u, nome, tab, cols, resto in indices:
    if u:
        print("  %-30s %-22s (%s) %s" % (nome, tab, cols, resto[:40]))
print("\n=== indices comuns: %d ===" % sum(1 for i in indices if not i[0]))
print("\n=== ON CONFLICT visto nas migrations ===")
for t, c in sorted(chaves.items()):
    print("  %-24s (%s)" % (t, c))

io.open("docs/estrutura_das_migrations.json", "w", encoding="utf-8").write(
    json.dumps({"com_empresa": sorted(rls),
                "indices": [{"unico": u, "nome": n, "tabela": t,
                             "colunas": c, "resto": r}
                            for u, n, t, c, r in indices],
                "on_conflict": chaves}, ensure_ascii=False, indent=1))
