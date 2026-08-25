# -*- coding: utf-8 -*-
"""segmentar_endereco.py — o endereço grudado vira campos, pela IA da Spark.

POR QUE ESTE PASSO EXISTE

A skill `ajuste-logradouro` marca a forma canônica do logradouro e organiza o
complemento, e é ela que unifica a chave de junção das bases cruzadas. Mas ela
declara, no próprio contrato, que **não segmenta campo único grudado**: espera
`logradouro`, `numero` e `complemento` já separados.

O padronizado da `extracao-poi-estadual` entrega assim. A nossa tabela `pois`,
não — ali mora um campo `endereco` só, e ele chega em formatos diferentes na
MESMA coluna:

    R. Mal. Rondon, 1199 - Niterói, Canoas - RS, 92120-210, Brasil
    RUA A J RENNER Número: 813 Bairro: ESTANCIA VELHA Município: CANOAS UF: RS
    Av. Getúlio Vargas, 2575, Niterói, Canoas, RS
    Rua Epitácio Pessoa - Canoas, Canoas - RS, 92130-340

Regra de vírgula não resolve isso. Cada fonte escreve do seu jeito, e o formato
"do cadastro" nem separador tem — tem rótulo. É trabalho de leitura, e é por
isso que a IA entra AQUI e em nenhum outro lugar do processo: segmentar, não
julgar.

A IA RODA SÓ NA SPARK — e isto é trava, não combinado

`SPARK_LLM_URL` é o único endpoint aceito. Chamar modelo no i9 é recusado por
`_conferir_endpoint()`, com nome e motivo. O i9 é a máquina do trabalho pesado
de dados (DuckDB sobre o Overture, PBF do OSM, Postgres de produção): subir
modelo lá tira GPU/RAM de quem precisa e cria uma segunda verdade sobre qual
modelo respondeu o quê.

NADA É INVENTADO — o grounding é o que separa leitura de alucinação

Todo campo devolvido pelo modelo precisa **existir no texto original**. A
conferência é feita sobre a forma normalizada (maiúscula, sem acento, sem
pontuação): se `logradouro` não é trecho do endereço, ele é DESCARTADO e o
registro sai como `REVISAR`. O modelo pode ler o que está escrito; não pode
completar o que não está.

Isso pega o erro que importa: o modelo "corrigindo" `R. Tamôio` para
`Rua Tamoio` parece ajuda e é corrupção silenciosa da evidência — quem
canoniza é a skill, com léxico e prova, não o palpite do modelo.

UMA CHAMADA POR VALOR DISTINTO

Numa base municipal os endereços distintos são ordens de grandeza menos que os
registros, e o resultado é função pura do texto. Segmentar linha a linha
pagaria a mesma leitura milhares de vezes — é a mesma lição de memoização que a
própria skill documenta (69,0 s → 11,2 s em 300k registros).

USO
    python segmentar_endereco.py --amostra 20          # lê e mostra, não grava
    python segmentar_endereco.py --municipio 4304606 --aplicar
    python segmentar_endereco.py --texto "R. Mal. Rondon, 1199 - Niterói, Canoas - RS"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request

import config  # noqa: F401  (.env + UTF-8)

SPARK = os.environ.get("SPARK_LLM_URL", "http://100.85.164.54:8000/v1")
MODELO = os.environ.get("SPARK_MODELO", "qwen3vl-moe")

# O i9. Está aqui pelo nome para que a recusa diga QUAL máquina foi barrada e
# por quê, em vez de falhar com "endpoint inválido" e deixar quem leu sem saber
# que existe uma regra.
I9 = "100.115.117.49"

CAMPOS = ("logradouro", "numero", "complemento", "bairro", "cep", "cidade", "uf")

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "skills", "ajuste-logradouro")

# Congelado só para o caso de a skill não estar em disco. Não é a fonte da
# verdade — ver `_vocabulario_complemento`.
_COMPONENTES_RESERVA = (
    "QUADRA LOTE RUA_INTERNA ENTRADA BLOCO TORRE CASA SOBRADO ANDAR TERREO "
    "SUBSOLO COBERTURA APARTAMENTO CONJUNTO QUITINETE SALA SALAO LOJA "
    "SOBRELOJA BOX GARAGEM DEPOSITO QUARTO COMODO ANEXO PORTARIA").split()


def _vocabulario_complemento() -> list:
    """Os componentes que a `ajuste-logradouro` sabe tipar, VINDOS DELA.

    O modelo lê o complemento; quem o organiza em componentes tipados é a skill.
    Se os dois falarem vocabulários diferentes, o que eu entregar como
    complemento vira `aj_compl_identificador` — "valor sem rótulo", que a skill
    mantém e sinaliza mas não usa para nada. O ganho da leitura se perde na
    fronteira.

    Por isso a lista é IMPORTADA de `complemento_organizador.ORDEM` em vez de
    copiada: quando a skill ganhar um componente novo — a v3.3.5 acabou de
    acrescentar ENTRADA, COMODO, COBERTURA, PORTARIA e PALAFITA do padrão
    CNEFE —, o prompt acompanha sozinho. Cópia manual envelhece em silêncio, e
    o sintoma seria um complemento tipado a menos, que ninguém liga à causa.
    """
    try:
        if SKILL not in sys.path:
            sys.path.insert(0, SKILL)
        import complemento_organizador as CO
        termos = [t for t in CO.ORDEM if t not in ("MODIFICADOR", "IMOVEL", "POSICAO")]
        if termos:
            return termos
    except Exception as erro:  # noqa: BLE001 — qualquer falha aqui é degradação
        print(f"  ⚠️  não li a taxonomia da skill ({type(erro).__name__}: {erro}).\n"
              f"     Usando a lista congelada: se a skill mudou, algum complemento\n"
              f"     vai sair sem rótulo e ninguém vai ligar o sintoma à causa.",
              file=sys.stderr)
    return list(_COMPONENTES_RESERVA)

# Quantos endereços por chamada. Lote grande amortiza a latência; grande demais
# faz o modelo perder a correspondência entre entrada e saída no fim da lista.
LOTE = 20

# Lotes simultâneos contra a Spark. O vLLM agrupa requisições concorrentes num
# batch de GPU: em série a placa fica ociosa entre uma pergunta e outra.
THREADS = int(os.environ.get("SPARK_THREADS", "8"))

PROMPT = """Você separa endereços brasileiros em campos. Não corrige, não completa, não adivinha.

A ORDEM DOS CAMPOS NO ENDEREÇO BRASILEIRO — é onde mais se erra:

    LOGRADOURO, NÚMERO - BAIRRO, CIDADE - UF, CEP, Brasil

O que vem logo depois do número é o BAIRRO. A CIDADE é o que vem imediatamente
antes da sigla do estado. Ler ao contrário troca os dois, e é o erro mais comum.

REGRAS:
- Copie os trechos EXATAMENTE como aparecem no texto, sem trocar abreviação por extenso, sem arrumar acento, sem mudar maiúscula.
- Campo que não aparece no texto: devolva string vazia.
- `numero`: o que identifica o número do imóvel, copiado do texto. "Nº 1772" -> "1772". "S/N", "KM 12" e "125-A" ficam como estão — quem classifica isso é a etapa seguinte, não você.
- `complemento`: o que localiza DENTRO do imóvel. Copie do texto, sem traduzir o rótulo. Os tipos que interessam são: {COMPONENTES}
- `bairro` pode ter o mesmo nome da cidade. Se estiver escrito duas vezes, é isso mesmo: repita nos dois campos.
- `cep`: como está no texto, com ou sem hífen.
- Não invente "Brasil", "RS" ou cidade que não esteja escrita.

EXEMPLOS:

1. R. Mal. Rondon, 1199 - Niterói, Canoas - RS, 92120-210, Brasil
{"logradouro":"R. Mal. Rondon","numero":"1199","complemento":"","bairro":"Niterói","cep":"92120-210","cidade":"Canoas","uf":"RS"}

2. RUA A J RENNER Número: 813 Bairro: ESTANCIA VELHA Município: CANOAS UF: RS
{"logradouro":"RUA A J RENNER","numero":"813","complemento":"","bairro":"ESTANCIA VELHA","cep":"","cidade":"CANOAS","uf":"RS"}

3. Rua Gaspar Lemos, 19 - Canoas, Canoas - RS, 92025-500
{"logradouro":"Rua Gaspar Lemos","numero":"19","complemento":"","bairro":"Canoas","cep":"92025-500","cidade":"Canoas","uf":"RS"}

4. Av. Getúlio Vargas, 2575, sala 302, Niterói, Canoas, RS
{"logradouro":"Av. Getúlio Vargas","numero":"2575","complemento":"sala 302","bairro":"Niterói","cep":"","cidade":"Canoas","uf":"RS"}

Responda APENAS um array JSON, um objeto por endereço, na MESMA ORDEM, com as chaves:
logradouro, numero, complemento, bairro, cep, cidade, uf

ENDEREÇOS:
"""


def _conferir_endpoint(url: str) -> None:
    """A IA roda na Spark. Só nela.

    Recusa explícita em vez de "funciona e ninguém percebe": se um dia alguém
    apontar `SPARK_LLM_URL` para o i9 para "resolver rápido", a rodada para aqui
    com o motivo escrito, em vez de consumir a máquina que segura o Postgres de
    produção e a extração estadual.
    """
    if I9 in url:
        raise SystemExit(
            f"\n❌ RECUSADO: {url} é o i9, e a IA não roda no i9.\n\n"
            f"   O i9 é a máquina do trabalho pesado de dados — DuckDB sobre o\n"
            f"   Overture, o PBF do OSM e o Postgres de produção. Modelo ali\n"
            f"   disputa a mesma RAM e cria uma segunda verdade sobre qual\n"
            f"   modelo respondeu o quê.\n\n"
            f"   Aponte SPARK_LLM_URL para a Spark.")
    if not url.startswith("http"):
        raise SystemExit(f"SPARK_LLM_URL inválida: {url!r}")


def _cru(s: str) -> str:
    """Forma de comparação: maiúscula, sem acento, só alfanumérico.

    É sobre ela que o grounding é medido. Sem tirar acento e pontuação, um
    `R. Tamôio` lido como `R Tamôio` seria acusado de invenção — e a pergunta
    que interessa é se o modelo TROCOU PALAVRA, não se mexeu na pontuação.
    """
    s = unicodedata.normalize("NFD", (s or "").upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z0-9]+", "", s)


def _ancorado(valor: str, original: str) -> bool:
    """O valor existe no texto original?"""
    v = _cru(valor)
    return bool(v) and v in _cru(original)


_PROMPT_PRONTO = None


def _prompt() -> str:
    """O prompt com a taxonomia da skill dentro. Montado uma vez."""
    global _PROMPT_PRONTO
    if _PROMPT_PRONTO is None:
        _PROMPT_PRONTO = PROMPT.replace(
            "{COMPONENTES}", ", ".join(_vocabulario_complemento()))
    return _PROMPT_PRONTO


def _chamar(enderecos: list) -> list:
    _conferir_endpoint(SPARK)
    numerado = "\n".join(f"{i + 1}. {e}" for i, e in enumerate(enderecos))
    corpo = {
        "model": MODELO,
        "messages": [{"role": "user", "content": _prompt() + numerado}],
        # Zero, e não 0.2: segmentar é leitura, e leitura não deve variar entre
        # duas execuções sobre o mesmo texto. A skill a jusante depende de
        # idempotência para que o léxico não aprenda de ruído.
        "temperature": 0,
        "max_tokens": 220 * len(enderecos) + 200,
    }
    req = urllib.request.Request(
        f"{SPARK}/chat/completions", method="POST",
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read())
    txt = (d["choices"][0]["message"].get("content") or "").strip()
    if not txt:
        # O modelo "thinking" devolve `content` vazio quando gasta o orçamento
        # inteiro no raciocínio. Dizer isso é melhor que devolver lista vazia e
        # o chamador contabilizar como "nenhum endereço lido".
        raise ValueError("a Spark devolveu conteúdo vazio (orçamento de tokens?)")
    return _extrair_json(txt)


def _extrair_json(txt: str) -> list:
    """O array JSON, venha ele limpo ou dentro de cerca de código."""
    m = re.search(r"```(?:json)?\s*(.+?)```", txt, re.S)
    if m:
        txt = m.group(1).strip()
    i, j = txt.find("["), txt.rfind("]")
    if i < 0 or j < 0:
        raise ValueError(f"sem array JSON na resposta: {txt[:200]!r}")
    return json.loads(txt[i:j + 1])


def _julgar(lote: list, lidos: list) -> dict:
    """Confere o que o modelo devolveu contra o texto original. Puro."""
    saida = {}
    for pos, e in enumerate(lote):
        d = lidos[pos] if pos < len(lidos) and isinstance(lidos[pos], dict) else {}
        reg, descartados = {}, []
        for c in CAMPOS:
            v = str(d.get(c) or "").strip()
            if not v:
                reg[c] = ""
            elif _ancorado(v, e):
                reg[c] = v
            else:
                # O modelo devolveu texto que não está no endereço. Pode ser
                # expansão de abreviação, pode ser cidade deduzida do CEP —
                # e as duas corrompem a evidência do mesmo jeito.
                reg[c] = ""
                descartados.append(f"{c}={v!r}")
        if not reg["logradouro"]:
            reg["metodo"], reg["motivo"] = "revisar", "logradouro nao ancorado"
        elif descartados:
            reg["metodo"] = "parcial"
            reg["motivo"] = "descartado por nao estar no texto: " + ", ".join(descartados)
        else:
            reg["metodo"], reg["motivo"] = "ia", ""
        saida[e] = reg
    return saida


def segmentar(enderecos, verboso: bool = False, threads: int = THREADS,
              ao_lote=None) -> dict:
    """`{endereco_original: {campos..., metodo, motivo}}`.

    Uma chamada por valor DISTINTO. O chamador pode passar a coluna inteira sem
    se preocupar: a deduplicação acontece aqui.

    OS LOTES VÃO EM PARALELO, e a razão é a máquina do outro lado: o vLLM da
    Spark agrupa requisições concorrentes num batch de GPU. Em série, 60
    endereços levaram 50,7 s (845 ms cada) — Canoas inteira, com 17.094
    distintos, custaria quatro horas de fila com a GPU ociosa entre as
    perguntas. O paralelismo não pede nada da Spark que ela já não faça melhor
    sozinha; só para de deixá-la esperando.
    """
    from concurrent.futures import ThreadPoolExecutor

    distintos = sorted({(e or "").strip() for e in enderecos if (e or "").strip()})
    lotes = [distintos[k:k + LOTE] for k in range(0, len(distintos), LOTE)]
    saida = {}

    def _um(lote):
        try:
            return lote, _julgar(lote, _chamar(lote))
        except (urllib.error.URLError, ValueError, json.JSONDecodeError,
                OSError, KeyError, IndexError) as erro:
            # Lote que falha NÃO derruba a rodada e NÃO some: cada endereço dele
            # sai marcado com o motivo. Silenciar aqui produziria uma base com
            # buraco que ninguém consegue explicar depois.
            return lote, {e: {c: "" for c in CAMPOS} | {
                "metodo": "falhou", "motivo": f"{type(erro).__name__}: {erro}"}
                for e in lote}

    feitos, pendente = 0, {}
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for lote, reg in pool.map(_um, lotes):
            saida.update(reg)
            pendente.update(reg)
            feitos += 1
            # GRAVA NO CAMINHO, e não só no fim. Canoas são 855 lotes e mais de
            # uma hora de leitura paga à Spark: uma queda no minuto 60 apagaria
            # a hora inteira, e a rodada seguinte a compraria de novo. É a mesma
            # lição do save que derrubava a rodada (seção 21) — o trabalho já
            # feito precisa sobreviver ao trabalho que ainda falta.
            if ao_lote and (feitos % 25 == 0 or feitos == len(lotes)):
                ao_lote(pendente)
                pendente = {}
            if verboso and (feitos % 10 == 0 or feitos == len(lotes)):
                falhos = sum(1 for r in saida.values() if r["metodo"] == "falhou")
                print(f"  lote {feitos}/{len(lotes)} · {len(saida):,} endereços"
                      + (f" · {falhos} em lote que falhou" if falhos else ""), flush=True)
    return saida


# ─── uso pelo banco ──────────────────────────────────────────────────────────

def nome_do_municipio(cod: str) -> tuple:
    """`(nome, uf)` pelo código IBGE, do banco de REFERÊNCIA.

    A `pois` guarda `cidade`/`uf` por nome, não código — então o recorte por
    município precisa do nome. A comparação de nome acontece em Python, sem
    `unaccent`: a extensão não está garantida no banco de referência, e é a
    mesma regra que o `minerar_tudo` já usa.
    """
    import base_comum as bc
    ref = bc.conectar_referencia()
    try:
        with ref.cursor() as cur:
            cur.execute("select nome, uf from ibge_malha where cod_municipio = %s",
                        (str(cod).strip(),))
            r = cur.fetchone()
    finally:
        ref.close()
    if not r:
        raise SystemExit(f"município {cod} não está na malha IBGE do banco de referência")
    return r[0], r[1]


def do_municipio(cod: str, limite: int = 0, aplicar: bool = False,
                 refazer: bool = False) -> None:
    import base_comum as bc

    nome, uf = nome_do_municipio(cod)
    print(f"  {nome}/{uf} ({cod})")

    con = bc.conectar()
    cur = con.cursor()
    # `translate` faz o papel do `unaccent` sem depender da extensão: só os
    # acentos que de fato aparecem em nome de município brasileiro.
    cur.execute("""
        select p.endereco
          from pois p
         where p.endereco is not null and p.endereco <> ''
           and upper(translate(coalesce(p.cidade, ''),
                     'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'aaaaeeiooouucAAAAEEIOOOUUC'))
               = upper(translate(%s,
                     'áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'aaaaeeiooouucAAAAEEIOOOUUC'))
           and (p.uf = %s or p.uf is null)
         limit %s""", (nome, uf, limite or 200000))
    enderecos = [r[0] for r in cur.fetchall()]
    distintos = sorted(set(enderecos))
    print(f"  {len(enderecos):,} endereços · {len(distintos):,} distintos")

    # RETOMÁVEL: o que já foi lido não é lido de novo.
    #
    # Sem isto, uma rodada interrompida no minuto 60 recomeçava do zero e
    # comprava de novo o que já estava pago. E não é caso raro: a leitura de um
    # município grande passa de uma hora, e é justamente aí que alguém reinicia
    # a máquina, o Wi-Fi cai ou a Spark é reiniciada.
    if not refazer:
        cur.execute("select endereco from endereco_segmentado where endereco = any(%s)",
                    (distintos,))
        ja = {r[0] for r in cur.fetchall()}
        if ja:
            print(f"  {len(ja):,} já lidos antes — ficam como estão (--refazer força)")
        distintos = [e for e in distintos if e not in ja]
    if not distintos:
        print("  nada novo a ler")
        con.close()
        return

    if not aplicar:
        reg = segmentar(distintos, verboso=True)
        _resumir(reg)
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        con.close()
        return

    import psycopg2.extras
    gravados = [0]

    def _gravar(parcial: dict) -> None:
        linhas = [(e, r["logradouro"], r["numero"], r["complemento"], r["bairro"],
                   r["cep"], r["cidade"], r["uf"], r["metodo"], r["motivo"], MODELO)
                  for e, r in parcial.items()]
        if not linhas:
            return
        psycopg2.extras.execute_values(cur, """
            insert into endereco_segmentado
              (endereco, logradouro, numero, complemento, bairro, cep, cidade, uf,
               metodo, motivo, modelo)
            values %s
            on conflict (endereco) do update set
              logradouro = excluded.logradouro, numero = excluded.numero,
              complemento = excluded.complemento, bairro = excluded.bairro,
              cep = excluded.cep, cidade = excluded.cidade, uf = excluded.uf,
              metodo = excluded.metodo, motivo = excluded.motivo,
              modelo = excluded.modelo, segmentado_em = now()
            """, linhas, page_size=500)
        con.commit()
        gravados[0] += len(linhas)

    reg = segmentar(distintos, verboso=True, ao_lote=_gravar)
    _resumir(reg)
    con.close()
    print(f"\n  GRAVADO: {gravados[0]:,} endereços distintos em endereco_segmentado")


def _resumir(reg: dict) -> None:
    from collections import Counter
    c = Counter(r["metodo"] for r in reg.values())
    print(f"\n  {len(reg):,} distintos · " + " · ".join(f"{k}={v}" for k, v in c.most_common()))
    for metodo in ("revisar", "parcial", "falhou"):
        exemplos = [(e, r) for e, r in reg.items() if r["metodo"] == metodo][:3]
        for e, r in exemplos:
            print(f"    [{metodo}] {e[:70]}")
            print(f"              {r['motivo'][:90]}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--texto", help="segmenta um endereço e mostra")
    p.add_argument("--amostra", type=int, default=0,
                   help="lê N endereços do banco, mostra e NÃO grava")
    p.add_argument("--municipio", default="", help="código IBGE")
    p.add_argument("--aplicar", action="store_true")
    p.add_argument("--refazer", action="store_true",
                   help="rele quem ja esta em endereco_segmentado")
    a = p.parse_args()

    if a.texto:
        r = segmentar([a.texto])[a.texto.strip()]
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if a.amostra:
        import base_comum as bc
        con = bc.conectar(); cur = con.cursor()
        cur.execute("""select distinct endereco from pois
                        where endereco is not null and endereco <> ''
                        order by endereco limit %s""", (a.amostra,))
        enderecos = [r[0] for r in cur.fetchall()]
        con.close()
        reg = segmentar(enderecos, verboso=True)
        for e in enderecos:
            r = reg[e]
            print(f"\n  {e}")
            print(f"    logr={r['logradouro']!r} num={r['numero']!r} "
                  f"compl={r['complemento']!r} bairro={r['bairro']!r} cep={r['cep']!r}")
            print(f"    [{r['metodo']}] {r['motivo']}")
        _resumir(reg)
        return 0
    if a.municipio:
        do_municipio(a.municipio, aplicar=a.aplicar, refazer=a.refazer)
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
