# -*- coding: utf-8 -*-
"""O vocabulário do prompt, por setor da concessionária.

POR QUE ESTE MÓDULO EXISTE. O prompt da IA nasceu escrito para água: "a
companhia cobra este hidrômetro como RESIDENCIAL", "o imóvel que ele abastece",
"fica a 11 m do hidrômetro". Apontado pelo dono do produto em 10/09/2026: isso
serve ao cliente de saneamento e a mais ninguém — energia elétrica e gás têm o
mesmo problema de tarifa mal classificada e nenhum hidrômetro.

TROCAR AS PALAVRAS NA MÃO A CADA CLIENTE é o caminho para o dia em que metade
do prompt fala de um setor e metade de outro. Aqui elas ficam num lugar só, e
o texto pede a palavra pelo papel que ela cumpre — `medidor`, `servico`,
`verbo` — em vez de dizer "hidrômetro".

O GENÉRICO NÃO É UM ERRO, é a saída honesta para quem ainda não declarou o
setor: "o medidor", "a concessionária", "atende". Fica correto para todos e
específico para ninguém, que é melhor do que falar de água para uma
distribuidora de energia.
"""

#: `{setor: {papel: palavra}}`. Os papéis são os mesmos em todos, e é isso que
#: permite ao prompt ser escrito uma vez só.
#:
#: `ligacao_mai` existe separado porque o texto usa a forma em CAIXA ALTA em
#: alguns títulos, e "LIGAÇÃO DE ÁGUA" com acento em maiúscula não sai de
#: `.upper()` em toda plataforma do mesmo jeito — melhor escrever.
VOCABULARIO = {
    "agua": {
        "servico": "água",
        "medidor": "hidrômetro",
        "medidores": "hidrômetros",
        "concessionaria": "a companhia de saneamento",
        "verbo": "abastece",
        "ligacao": "ligação de água",
        "ligacao_mai": "LIGAÇÃO DE ÁGUA",
        "consumo": "consumo de água",
    },
    "energia": {
        "servico": "energia elétrica",
        "medidor": "medidor de energia",
        "medidores": "medidores de energia",
        "concessionaria": "a distribuidora de energia",
        "verbo": "atende",
        "ligacao": "ligação de energia",
        "ligacao_mai": "LIGAÇÃO DE ENERGIA",
        "consumo": "consumo de energia",
    },
    "gas": {
        "servico": "gás canalizado",
        "medidor": "medidor de gás",
        "medidores": "medidores de gás",
        "concessionaria": "a distribuidora de gás",
        "verbo": "atende",
        "ligacao": "ligação de gás",
        "ligacao_mai": "LIGAÇÃO DE GÁS",
        "consumo": "consumo de gás",
    },
    "generico": {
        "servico": "o serviço",
        "medidor": "medidor",
        "medidores": "medidores",
        "concessionaria": "a concessionária",
        "verbo": "atende",
        "ligacao": "ligação",
        "ligacao_mai": "LIGAÇÃO",
        "consumo": "consumo",
    },
}

PADRAO = "generico"

_CACHE = {}


def qual(con):
    """O setor da empresa da sessão. Uma consulta por processo.

    LÊ PELA IDENTIDADE DO PIPELINE, e não por parâmetro: quem monta o dossiê já
    está conectado como a empresa dona dos dados, e passar o setor de mão em
    mão por seis assinaturas seria seis lugares para esquecer.

    SEM LINHA, GENÉRICO. Não é um defeito silencioso: o prompt sai dizendo "o
    medidor" e "a concessionária", que é verdade para qualquer utility. O
    silencioso seria falar de hidrômetro para uma distribuidora de energia.
    """
    if "setor" in _CACHE:
        return _CACHE["setor"]
    s = PADRAO
    try:
        cur = con.cursor()
        cur.execute("""select setor from radar_comercial.empresa_setor
                        where id_empresa = (select core.empresa_atual())""")
        r = cur.fetchone()
        if r and r[0] in VOCABULARIO:
            s = r[0]
    except Exception:                                          # noqa: BLE001
        # ENGOLIR O ERRO NAO BASTA: E PRECISO DESFAZER.
        #
        # A primeira versao so dava `pass`, e o efeito foi pior que a falha
        # original. Em 10/09/2026 a tabela existia mas sem GRANT; o SELECT
        # morreu com "permission denied", a excecao foi engolida — e a
        # conexao ficou com a transacao ABORTADA. Tudo que veio depois, na
        # MESMA conexao emprestada do poco, caiu com "current transaction is
        # aborted", e a extracao inteira morreu longe da causa.
        #
        # Cair no vocabulario generico e uma degradacao aceitavel; devolver
        # uma conexao inutilizavel para o proximo nao e.
        try:
            con.rollback()
        except Exception:                                      # noqa: BLE001
            pass
    _CACHE["setor"] = s
    return s


def palavras(con):
    """`{papel: palavra}` para interpolar no prompt e no dossiê."""
    return VOCABULARIO[qual(con)]


def esquecer():
    """Descarta o cache. Só os testes precisam disto."""
    _CACHE.clear()
