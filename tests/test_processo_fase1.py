# -*- coding: utf-8 -*-
"""A fase 1 inteira roda pelo botão, e não pela conversa.

POR QUE ESTE ARQUIVO EXISTE

Em 27/08/2026 o dono do produto pediu a revisão de tudo que tínhamos construído
naquele dia, com uma pergunta só: *"o que ficou como resolução apenas de
conversa e o que foi de fato introduzido no processo que vai rodar via
frontend?"*

É a pergunta certa. Conserto aplicado à mão no banco resolve o dado de hoje e
não impede o de amanhã — e some sem deixar rastro quando alguém regenera o
arquivo, refaz a base ou minera outra cidade.

O frontend dispara `minerar_tudo.py`. Então "estar no processo" significa uma
destas três coisas, e este arquivo verifica as três:

    1. a etapa está em `minerar_tudo.py`
    2. a regra está no BANCO (trigger, índice), e vale para qualquer caminho
    3. a correção está no INSTALADOR, e não no arquivo gerado

DUAS LACUNAS APARECERAM NESSA REVISÃO, e as duas eram do tipo que só quebra
depois:

    `realtime_ingest` inseria POI sem endereço. Desde que o trigger
    `poi_comparavel` passou a recusá-lo, isso levantaria `CheckViolation` e
    derrubaria a ingestão da SESSÃO INTEIRA — não só daquele ponto.

    `povoar_vinculo` copiava `"nan"` para dentro do JSON do vínculo. Os 74.572
    campos limpos naquele dia voltariam na primeira mineração.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402


def _ler(nome):
    return io.open(os.path.join(RAIZ, nome), encoding="utf-8").read()


def test_o_frontend_dispara_a_mineracao_completa():
    """O botão do painel roda `minerar_tudo.py`. Tudo o que está nele chega ao
    usuário; o que não está, não existe para ele."""
    s = _ler("server.py")
    assert '"minerar_tudo.py"' in s, \
        "o painel deixou de disparar a mineração completa"


def test_todas_as_etapas_novas_estao_na_mineracao():
    """As etapas construídas em 26–27/08/2026. Cada uma resolveu um defeito
    medido; fora do `minerar_tudo` nenhuma roda sozinha."""
    s = _ler("minerar_tudo.py")
    for etapa, porque in (
            ("descobrir_maps.py", "descoberta por categoria — substituiu o iFood"),
            ("normalizar_bases.py", "base fixa normalizada antes da área"),
            ("ajuste_logradouro.py", "logradouro canônico, a chave de junção"),
            ("corrigir_coordenada.py", "endereço certo com coordenada absurda"),
            ("conferir_municipio.py", "CEP de outro município"),
            ("cruzar_fontes.py", "a fusão"),
    ):
        assert etapa in s, f"{etapa} saiu da mineração — {porque}"


def test_a_ordem_das_etapas_finais_protege():
    """`corrigir_coordenada` conserta; `conferir_municipio` APAGA. Invertidos,
    pontos consertáveis morreriam antes de terem a chance."""
    s = _ler("minerar_tudo.py")
    assert s.index("corrigir_coordenada.py") < s.index("conferir_municipio.py")
    # e a normalização vem antes das duas: elas casam o logradouro NORMALIZADO
    assert s.index("ajuste_logradouro.py") < s.index("corrigir_coordenada.py"), \
        "a correção de coordenada passou a rodar antes da normalização — " \
        "'Av. Gen. Flores da Cunha' não encontraria 'AVENIDA GENERAL FLORES DA CUNHA'"


def test_a_ingestao_nao_quebra_com_POI_sem_endereco():
    """LACUNA ACHADA NA REVISÃO. A busca no Maps nem sempre devolve endereço, e
    o trigger `poi_comparavel` recusa POI sem ele — o INSERT levantaria
    `CheckViolation` e derrubaria a ingestão da sessão inteira.

    Agora a coordenada vira endereço antes; quem nem assim obtiver é PULADO.
    Perder um ponto incomparável é barato; perder a sessão é caro."""
    s = _ler("realtime_ingest.py")
    assert "_endereco_pela_coordenada" in s, \
        "a ingestão voltou a inserir POI sem endereço — quebra a sessão inteira"
    i = s.index('if not _s(r.get("endereco")):')
    corpo = s[i:i + 320]
    assert 'return ("pulado", None)' in corpo, \
        "o ponto sem endereço deixou de ser pulado — passaria a estourar no banco"


def test_o_nan_nao_volta_pelo_vinculo():
    """LACUNA ACHADA NA REVISÃO. Foram 74.572 campos com `"nan"` limpos do JSON
    dos vínculos naquele dia — a limpeza foi uma vez, e `povoar_vinculo` os
    copiaria de volta na primeira mineração.

    O `nan` não fica inerte: `evidencia` já o trata como vazio, mas o PAINEL o
    mostra como se fosse um site chamado nan. Foi assim que 45–74% dos contatos
    exibidos eram falsos."""
    s = _ler("povoar_vinculo.py")
    assert "def _util(" in s, "a guarda contra `nan` sumiu do povoar_vinculo"
    assert "_util(cat)" in s and "_util(end)" in s and "_util(tel)" in s \
        and "_util(site)" in s, "algum campo voltou a ser gravado sem a guarda"

    import povoar_vinculo as pv
    for lixo in ("nan", "NaN", "none", "-", ""):
        assert pv._util(lixo) is None, f"{lixo!r} voltou a passar"
    assert pv._util("padaria.com.br") == "padaria.com.br", "a guarda comeu dado bom"


def test_a_guarda_do_nan_e_a_mesma_lista_da_evidencia():
    """Duas listas de buraco divergem, e no dia em que divergirem uma delas
    deixa passar. `_util` reaproveita `evidencia._VAZIO` de propósito."""
    s = _ler("povoar_vinculo.py")
    assert "_ev._VAZIO" in s, "a guarda passou a ter lista própria de buracos"


def test_as_regras_do_banco_valem_por_qualquer_caminho():
    """Trigger e índice não dependem de ninguém lembrar de chamar. É o que
    separa regra de disciplina."""
    import base_comum as bc
    try:
        con = bc.conectar()
    except Exception:
        import pytest
        pytest.skip("banco indisponível")
    try:
        cur = con.cursor()
        cur.execute("""select tgname from pg_trigger
                        where tgname in ('poi_comparavel','vinculo_comparavel')
                          and not tgisinternal""")
        gatilhos = {r[0] for r in cur.fetchall()}
        cur.execute("""select relname from pg_class
                        where relname in ('pois_sem_duplicata','vinculo_sem_duplicata')""")
        indices = {r[0] for r in cur.fetchall()}
    finally:
        con.close()
    assert gatilhos == {"poi_comparavel", "vinculo_comparavel"}, \
        f"faltam gatilhos: {gatilhos}"
    assert indices == {"pois_sem_duplicata", "vinculo_sem_duplicata"}, \
        f"faltam índices de duplicata: {indices}"


def test_a_bancada_e_corrigida_no_instalador():
    """`frontend/bancada.html` é gerado e está no `.gitignore`. Conserto feito
    no arquivo some na próxima instalação — foi o que quase aconteceu."""
    s = _ler("instalar_bancada.py")
    assert "_TROCAS" in s and "_esvaziar_dataset" in s
    assert "nao_casaram" in s, \
        "o instalador voltou a silenciar correção que deixou de casar"


def test_toda_etapa_com_navegador_roda_no_i9():
    """REGRA DO DONO DO PRODUTO, 27/08/2026: "todos esses passos devem rodar no i9".

    Só a captura ia para lá. A descoberta por categoria e o iFood abriam
    navegador com proxy NO NOTEBOOK do operador — e depois que a varredura subiu
    para 6 workers, eram seis Chromium disputando a CPU do painel que ele estava
    olhando.

    E havia um efeito que só aparecia no incidente: com os navegadores aqui,
    "derrubar os navegadores da mineração" derrubava o Chrome PESSOAL junto —
    não há como separá-los pelo nome do processo.
    """
    s = _ler("minerar_tudo.py")
    for etapa in ("descobrir_maps.py", "enriquecer_por_ifood.py",
                  "normalizar_bases.py", "segmentar_endereco.py",
                  "ajuste_logradouro.py", "corrigir_coordenada.py",
                  "conferir_municipio.py", "povoar_vinculo.py",
                  "cruzar_fontes.py"):
        assert f'_tolerante_i9(["{etapa}"' in s, \
            f"{etapa} voltou a rodar no notebook do operador"
        assert f'[PYTHON, "{etapa}"' not in s, \
            f"{etapa} tem uma chamada local sobrando"


# O TESTE `test_o_i9_tem_todos_os_arquivos_dessas_etapas` SAIU DAQUI.
#
# Ele conferia se a etapa estava na lista de arquivos sincronizados para o
# i9, e guardava um defeito real: etapa fora da lista rodava a versao VELHA
# la, e tres estiveram nessa situacao sem ninguem saber.
#
# Em 30/08/2026 a sincronia por SSH acabou — o sistema passou a RODAR no
# servidor. Sem duas copias nao ha lista, e o defeito que ele guardava
# deixou de ser possivel. Removido, e nao adaptado: teste que nao pode
# falhar nao protege nada.

# O TESTE `test_a_etapa_no_i9_cai_de_volta_para_o_local` SAIU DAQUI.
#
# Ele exigia que a etapa mandada por SSH caisse de volta para execucao local
# quando o `ssh` falhasse, com aviso de que ia pesar a maquina. Em 30/08/2026
# a viagem por SSH acabou: `_tolerante_i9` virou delegador de `_tolerante` e
# TODA etapa ja roda local. Nao ha de onde cair, entao nao ha queda a cobrar.

