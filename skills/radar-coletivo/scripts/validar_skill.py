#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gate de EMPACOTAMENTO — valida o contrato do SKILL.md antes de gerar o .skill.
================================================================================

Origem: a v4.7.0 foi empacotada com `description` de 1.759 caracteres e o
instalador recusou (limite 1.024). O defeito não estava no motor nem nos dados
— estava no ENVELOPE, que nenhuma das 49 provas do produto olhava. Toda
mudança de versão vinha somando frase à description sem ninguém medir.

A lição é a mesma que vale para os gates de dado: restrição não verificada é
restrição que será violada. Este script roda ANTES do zip e aborta com exceção
real — não com aviso que se lê depois de o pacote já ter sido entregue.

    python validar_skill.py [caminho/do/SKILL.md]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LIMITES = {
    'name': 64,
    'description': 1024,
}

# nome de skill: minúsculas, dígitos e hífen — o instalador usa como diretório
RE_NAME = re.compile(r'^[a-z0-9][a-z0-9-]*$')


class SkillInvalida(RuntimeError):
    """Contrato do envelope violado — não empacota."""


def ler_frontmatter(texto: str) -> dict:
    if not texto.startswith('---'):
        raise SkillInvalida('SKILL.md nao comeca com frontmatter YAML (---)')
    fim = texto.index('\n---', 3)
    bruto = texto[3:fim]

    campos, chave, buf = {}, None, []
    for linha in bruto.splitlines():
        m = re.match(r'^([a-zA-Z_][\w-]*):\s*(.*)$', linha)
        if m and not linha.startswith((' ', '\t')):
            if chave:
                campos[chave] = ' '.join(buf).strip()
            chave = m.group(1)
            valor = m.group(2).strip()
            # '>' e '|' abrem bloco multi-linha; o conteudo vem indentado abaixo
            buf = [] if valor in ('>', '|', '>-', '|-') else [valor]
        elif chave and linha.strip():
            buf.append(linha.strip())
    if chave:
        campos[chave] = ' '.join(buf).strip()
    return campos


def validar(caminho: Path) -> dict:
    texto = caminho.read_text(encoding='utf-8')
    fm = ler_frontmatter(texto)
    erros = []

    for obrig in ('name', 'description'):
        if not fm.get(obrig):
            erros.append(f'campo obrigatorio ausente ou vazio: {obrig}')

    for campo, teto in LIMITES.items():
        v = fm.get(campo, '')
        if len(v) > teto:
            erros.append(f"campo '{campo}' tem {len(v)} caracteres; "
                         f'o maximo e {teto} (excesso: {len(v) - teto})')

    nome = fm.get('name', '')
    if nome and not RE_NAME.match(nome):
        erros.append(f"campo 'name' invalido ({nome!r}): use minusculas, "
                     'digitos e hifen')

    if nome and nome != caminho.parent.name:
        erros.append(f"'name' ({nome}) difere do diretorio "
                     f'({caminho.parent.name}) — o instalador usa o diretorio')

    if erros:
        raise SkillInvalida('SKILL.md invalido:\n  - ' + '\n  - '.join(erros))
    return fm


def main():
    alvo = Path(sys.argv[1] if len(sys.argv) > 1
                else Path(__file__).resolve().parent.parent / 'SKILL.md')
    fm = validar(alvo)
    print(f'{alvo}: OK')
    for campo, teto in LIMITES.items():
        print(f'  {campo:12s} {len(fm.get(campo, "")):>5d} / {teto}')


if __name__ == '__main__':
    try:
        main()
    except SkillInvalida as e:
        print(f'FALHA: {e}', file=sys.stderr)
        raise SystemExit(2)
