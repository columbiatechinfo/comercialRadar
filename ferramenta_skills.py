# -*- coding: utf-8 -*-
"""Deixa o modelo carregar as skills do projeto durante a conversa.

O QUE É UMA SKILL, SEM MISTÉRIO

Um arquivo markdown com o método de fazer alguma coisa. Não há infraestrutura
especial: quando o assunto aparece, o texto entra no contexto e o modelo passa
a seguir aquele método em vez de improvisar.

O projeto já tem quatro, escritas ao longo de meses:

    extracao-poi-estadual      POIs em escala estadual, multifonte
    tratamento-cnpj            CNPJ × CNEFE, geocodificação, potencial
    radar-coletivo             CNEFE/IBGE, coletivas, polos comerciais
    leitura-fachada-cadastral  leitura de fachada por IA

DUAS DECISÕES QUE IMPORTAM

**O catálogo entra no prompt, o conteúdo não.** As quatro juntas passam de
100 mil caracteres — carregar tudo sempre encheria a janela e deixaria a
pergunta espremida. O modelo vê nome e descrição, e pede a que precisar.

**Vem cortada.** Uma skill inteira tem dezenas de milhares de caracteres, e a
maior parte é detalhe de execução em lote que não ajuda numa conversa. O corte
preserva o começo — objetivo, princípios e regras —, que é onde está o método.
"""
from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parent
PASTA = RAIZ / "skills"
MAX_CHARS = 12_000


def _cabecalho(texto: str) -> tuple[str, str]:
    """Separa o frontmatter YAML do corpo, e tira a descrição de dentro dele."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", texto, re.S)
    if not m:
        return ("", texto)
    fm, corpo = m.group(1), m.group(2)
    d = re.search(r"^description:\s*(?:>-?\s*\n)?(.*?)(?=\n\w+:|\Z)", fm,
                  re.S | re.M)
    desc = " ".join((d.group(1) if d else "").split())
    return (desc, corpo)


def catalogo() -> list[dict]:
    """Nome e descrição de cada skill disponível — o que vai no prompt."""
    saida = []
    if not PASTA.exists():
        return saida
    for pasta in sorted(PASTA.iterdir()):
        arq = pasta / "SKILL.md"
        if not arq.is_dir() and arq.exists():
            try:
                desc, _ = _cabecalho(arq.read_text(encoding="utf-8",
                                                   errors="replace"))
            except Exception:
                desc = ""
            saida.append({"nome": pasta.name, "descricao": desc[:400]})
    return saida


def usar_skill(nome: str) -> dict:
    """Carrega o método de uma skill do projeto.

    Devolve o texto para o modelo SEGUIR, não para resumir ao usuário.
    """
    nome = (nome or "").strip().lower()
    disponiveis = [s["nome"] for s in catalogo()]
    if not nome:
        return {"erro": "informe o nome", "disponiveis": disponiveis}

    # aceita nome parcial: o modelo escreve "cnpj" para "tratamento-cnpj"
    exatos = [d for d in disponiveis if d == nome]
    parciais = [d for d in disponiveis if nome in d]
    escolhido = (exatos or parciais or [None])[0]
    if not escolhido:
        return {"erro": f"não achei skill '{nome}'", "disponiveis": disponiveis}
    if not exatos and len(parciais) > 1:
        # ambiguidade é devolvida, não resolvida no chute
        return {"erro": f"'{nome}' casa com mais de uma", "candidatas": parciais}

    arq = PASTA / escolhido / "SKILL.md"
    try:
        bruto = arq.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"erro": f"{type(e).__name__}: {str(e)[:150]}"}

    desc, corpo = _cabecalho(bruto)
    cortado = len(corpo) > MAX_CHARS
    return {
        "skill": escolhido,
        "descricao": desc,
        "metodo": corpo[:MAX_CHARS],
        "cortado": cortado,
        "aviso": ("texto cortado — o começo traz objetivo e princípios, que é "
                  "o que orienta; o resto é detalhe de execução em lote"
                  if cortado else None),
    }


ESQUEMA = {
    "type": "function", "function": {
        "name": "usar_skill",
        "description": (
            "Carrega o MÉTODO documentado do projeto para um assunto. Use "
            "ANTES de responder sobre tratamento de CNPJ, extração de POIs, "
            "bases do IBGE/CNEFE ou leitura de fachada — essas coisas têm "
            "método definido aqui, e improvisar produz resposta que contradiz "
            "o que o sistema faz. Skills: "
            + "; ".join(f"{s['nome']}" for s in catalogo())),
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"}}, "required": ["nome"]}},
}
