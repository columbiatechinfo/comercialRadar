#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bateria P0 — cada teste REPRODUZ um defeito confirmado na v4.7.0.
================================================================================

Escrita ANTES das correções, e VERMELHA na v4.7.0 por construção. Um teste que
já passa antes da correção não está testando o defeito — está testando outra
coisa e emitindo confiança.

Origem de cada caso:
  D1  campo (usuário)   — hipótese criada para unidade que JÁ EXISTE
  D2  campo (usuário)   — linha inferida herda o complemento de um irmão
  D3..D12  auditoria externa de 2026-08, todos reproduzidos antes de aceitos

Método (a lição que motivou esta bateria): todo teste parte do CSV bruto no
layout oficial e atravessa `preparar_base()`. Nenhum monta dataframe à mão. O
defeito Q/L sobreviveu a uma suíte inteira porque o teste fabricava
`NUM_ENDERECO=10 + PADRAO_ENDERECO='QUADRA_LOTE'`, estado que o pipeline nunca
produz.

    python tests/test_p0.py          # scripts (modo histórico)
    pytest tests/test_p0.py          # CI
"""

import os
import subprocess
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
for p in (SCRIPTS, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import cnefe_fixture as fx                                          # noqa: E402
import radar_utils as xu                                            # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
#  D1 — hipótese para unidade QUE JÁ EXISTE  (achado de campo)
# ══════════════════════════════════════════════════════════════════════════
def test_d1_nao_infere_unidade_que_ja_existe():
    """`303 SINDICO` é o apartamento 303. Inferi-lo é fabricar duplicata.

    Caso real: COL-4314902-001003, RUA DOM DIOGO DE SOUZA 283, Porto Alegre.
    O parse estrito de `num_unidade()` — correto para CONSTRUIR a grade —
    estava sendo usado também para SUPRIMIR a hipótese. Inclusão tem de ser
    estrita; exclusão tem de ser permissiva.
    """
    linhas = ([fx.apto('DOM DIOGO', 283, v) for v in
               ('201', '202', '203', '204', '301', '302', '304')]
              + [fx.apto('DOM DIOGO', 283, '303 SINDICO')]
              + [fx.apto('DOM DIOGO', 283, v) for v in ('401', '402', '403', '404')])
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
    g = fx.hipoteses(df)
    inferidos = set(g['AUSENTE_INFERIDO'].astype(str)) if len(g) else set()
    assert '303' not in inferidos, (
        f'303 inferido, mas existe como "303 SINDICO". Inferidos: {sorted(inferidos)}')


def test_d1b_supressao_cobre_variantes_de_grafia():
    """A supressão vale para qualquer forma cujo prefixo numérico case."""
    for variante in ('203 FUNDOS', '203-A', '203 COBERTURA', '203/B'):
        # 103 tem de ficar no INTERIOR do intervalo observado, senão o teste
        # passa por vacuidade (nenhuma hipótese seria gerada de todo modo).
        # dois andares: a unidade 3 precisa ser OBSERVADA em algum andar para
        # entrar na grade cartesiana — senão o 203 nunca seria hipótese e o
        # teste passaria por vacuidade.
        linhas = ([fx.apto('X', 50, v) for v in ('101', '102', '103', '104')]
                  + [fx.apto('X', 50, v) for v in ('201', '202', '204')]
                  + [fx.apto('X', 50, variante)])
        df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
        g = fx.hipoteses(df)
        inf = set(g['AUSENTE_INFERIDO'].astype(str)) if len(g) else set()
        assert '203' not in inf, f'203 inferido apesar de existir como {variante!r}'


# ══════════════════════════════════════════════════════════════════════════
#  D2 — linha inferida não pode herdar atributo de unidade do irmão
# ══════════════════════════════════════════════════════════════════════════
def test_d2_hipotese_nao_herda_complemento_de_irmao():
    """A linha do 303 saía com END_COMPLEMENTO='APARTAMENTO 601'.

    `linhas_inferidas` copia o endereço-pai em bloco; campos de GRÃO DE
    UNIDADE precisam ser zerados, senão a hipótese sai vestindo a roupa de um
    irmão sorteado pelo drop_duplicates.
    """
    import csv as _csv
    import rcc_emissor as R
    linhas = [fx.apto('Y', 70, v) for v in
              ('201', '202', '203', '204', '301', '302', '304')]
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
    dic = os.path.join(os.path.dirname(SCRIPTS), 'RCC_dicionario.csv')
    if not os.path.exists(dic):
        dic = os.path.join(SCRIPTS, '..', 'RCC_dicionario.csv')
    cols = [r['CAMPO'] for r in
            _csv.DictReader(open(dic, encoding='utf-8-sig'), delimiter=';')]
    d = R.emitir(df, safra='T')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-t', 'IBGE/RCC', 'T', 'h', cols), d)
    inf = R.linhas_inferidas(df, out, cols)
    assert len(inf), 'fixture deveria gerar ao menos uma hipótese'
    for _, r in inf.iterrows():
        comp = str(r['END_COMPLEMENTO'] or '').strip()
        assert comp == '' or str(r['UND_VALOR']) in comp, (
            f'hipótese de {r["UND_VALOR"]} herdou complemento alheio: {comp!r}')


# ══════════════════════════════════════════════════════════════════════════
#  D3 — Quadra/Lote está morto no fluxo canônico
# ══════════════════════════════════════════════════════════════════════════
def test_d3_condominio_horizontal_por_quadra_lote():
    """Q/L nasce só com NUM_ENDERECO=0; o detector filtra NUM>0. Incompatível.

    O teste antigo passava porque fabricava NUM=10 com PADRAO='QUADRA_LOTE' —
    combinação que `preparar_base` não produz.
    """
    linhas = [fx.lote('DAS FLORES', 1, l) for l in (5, 6, 7, 8)]
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
    assert (df['PADRAO_ENDERECO'] == 'QUADRA_LOTE').all(), \
        'a fixture não virou Q/L no pipeline — revisar a fixture, não o teste'
    r = xu.detectar_condominio_horizontal(df)
    flag = pd.to_numeric(r['FLAG_COND_HORIZ'], errors='coerce').fillna(0)
    assert flag.sum() > 0, 'nenhum lote detectado como condomínio horizontal'


# ══════════════════════════════════════════════════════════════════════════
#  D4 — polos comerciais do RCC: fail-open + índice errado da tupla
# ══════════════════════════════════════════════════════════════════════════
def test_d4_polos_chegam_ao_rcc():
    """Em POA: COL_POLO_CLASSE = FORA_DE_POLO em 812.653 de 812.653 linhas.

    Duas causas somadas: `polos[0]` de `return agg, polos` seleciona os
    ENDEREÇOS, e o dataframe agregado que o RCC monta não tem a coluna NUMERO
    que a função exige. Tudo dentro de `except Exception` — fail-open.
    """
    import csv as _csv
    import rcc_emissor as R
    linhas = []
    for i in range(12):                      # densidade suficiente p/ DBSCAN
        linhas.append(fx.estabelecimento('COMERCIAL', 100 + i, f'LOJA {i}',
                                         dlat=i * 0.00012, dlon=i * 0.00012))
    linhas += [fx.apto('COMERCIAL', 200, str(200 + i)) for i in range(4)]
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))

    direto = xu.detectar_polos_comerciais(df)
    polos_direto = direto[1] if isinstance(direto, tuple) else direto
    assert len(polos_direto) > 0, \
        'a fixture não gera polo nem pela via direta — revisar a fixture'

    dic = os.path.join(os.path.dirname(SCRIPTS), 'RCC_dicionario.csv')
    cols = [r['CAMPO'] for r in
            _csv.DictReader(open(dic, encoding='utf-8-sig'), delimiter=';')]
    d = R.emitir(df, safra='T')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-t', 'IBGE/RCC', 'T', 'h', cols), d)
    assert (out['COL_POLO_CLASSE'] != 'FORA_DE_POLO').any(), (
        'o RCC zerou os polos que a função encontra na mesma base')


# ══════════════════════════════════════════════════════════════════════════
#  D5 — posição desconhecida virando evidência de fachada ativa
# ══════════════════════════════════════════════════════════════════════════
def test_d5_posicao_vazia_nao_e_terreo():
    """`UND_POSICAO.isin([... , ''])` transformava ausência em evidência.

    Ausência de informação não é prova de térreo. Estabelecimento sem posição
    declarada não pode sair com ATV_FLAG_FACHADA_ATIVA=1.
    """
    import csv as _csv
    import rcc_emissor as R
    linhas = [fx.estabelecimento('SEM POSICAO', 10 + i, f'ESTAB {i}')
              for i in range(3)]
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
    dic = os.path.join(os.path.dirname(SCRIPTS), 'RCC_dicionario.csv')
    cols = [r['CAMPO'] for r in
            _csv.DictReader(open(dic, encoding='utf-8-sig'), delimiter=';')]
    d = R.emitir(df, safra='T')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-t', 'IBGE/RCC', 'T', 'h', cols), d)
    sem_pos = out[out['UND_POSICAO'].fillna('').astype(str).str.strip() == '']
    assert len(sem_pos), 'fixture sem linha de posição vazia'
    fach = pd.to_numeric(sem_pos['ATV_FLAG_FACHADA_ATIVA'], errors='coerce').fillna(0)
    assert (fach == 0).all(), (
        f'{int((fach == 1).sum())} linha(s) sem posição saíram como fachada ativa')


# ══════════════════════════════════════════════════════════════════════════
#  D6 — downloader → loader: o separador não fecha
# ══════════════════════════════════════════════════════════════════════════
def test_d6_downloader_produz_entrada_valida():
    """`to_csv()` sem `sep` grava vírgula; `load_cnefe` lê com ';'.

    O downloader oficial da skill não produz entrada válida para o pipeline da
    própria skill. E2E que não existia.
    """
    import cnefe_coletivas as cc
    import cnefe_download as dl
    linhas = [fx.apto('E2E', 15, str(100 + i)) for i in range(3)]
    bruto = fx.csv_cnefe(linhas)
    df = cc.load_cnefe(bruto)
    saida = tempfile.mkstemp(suffix='.csv.gz')[1]
    if hasattr(dl, 'salvar'):
        dl.salvar(df, saida)
    else:
        df.to_csv(saida, index=False, compression='gzip')     # o que o código faz hoje
    relido = cc.load_cnefe(saida)
    assert len(relido.columns) > 5, (
        f'reler o arquivo do downloader deu {len(relido.columns)} coluna(s): '
        f'{list(relido.columns)[:2]} — separador incompatível')
    assert len(relido) == len(df)


# ══════════════════════════════════════════════════════════════════════════
#  D7 — merge do bloco SET não pode multiplicar linhas
# ══════════════════════════════════════════════════════════════════════════
def test_d7_set_preserva_cardinalidade():
    """2 linhas CNEFE + agregado duplicado davam 4 linhas: unidades fabricadas.

    O gate se chamava `gate_set_nao_multiplicado` e não verificava multiplicação.
    """
    import ibge_setor_ocupacao as xs
    cn = pd.DataFrame({'COD_SETOR': ['431490205000001P', '431490205000001P']})
    st = pd.DataFrame({'SET_COD': ['431490205000001', '431490205000001'],
                       'SET_DOM_PARTICULARES': [100, 100], 'SET_DOM_OCUPADOS': [70, 70],
                       'SET_DOM_USO_OCASIONAL': [10, 10], 'SET_DOM_VAGOS': [20, 20]})
    try:
        r = xs.enriquecer(cn, st)
    except xs.SetorError:
        return                                   # abortar é resposta correta
    assert len(r) == len(cn), (
        f'merge multiplicou {len(cn)} -> {len(r)} linhas: o agregado fabricou unidade')


# ══════════════════════════════════════════════════════════════════════════
#  D8 — CHAVE tem de separar RUA X de AVENIDA X
# ══════════════════════════════════════════════════════════════════════════
def test_d8_chave_separa_tipo_de_logradouro():
    """Medido em POA: 714 chaves fundidas, 3.001 endereços, mediana 687 m.

    Contamina tudo que roda depois por CHAVE: QTD_END, blocos, uso misto,
    coletiva, S2/S3, declarado×contado, gaps, porte, RCC.
    """
    linhas = ([fx.apto('PRESIDENTE VARGAS', 100, str(100 + i), tipo='RUA')
               for i in range(3)]
              + [fx.apto('PRESIDENTE VARGAS', 100, str(200 + i), tipo='AVENIDA',
                         dlat=0.02, dlon=0.02) for i in range(3)])
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
    n = df.groupby('CHAVE')['NOM_TIPO_SEGLOGR'].nunique()
    assert (n <= 1).all(), (
        'CHAVE fundiu tipos distintos de logradouro no mesmo grupo')


# ══════════════════════════════════════════════════════════════════════════
#  D9 — dedupe de linha bruta antes da normalização destrutiva
# ══════════════════════════════════════════════════════════════════════════
def test_d9_dedupe_e_de_linha_bruta():
    """`"001"` e `"1"` são linhas brutas DIFERENTES.

    O pipeline normaliza NUM_ENDERECO para int e só depois deduplica — então o
    que se chama de "dedup exato da linha bruta" é dedupe pós-transformação
    destrutiva. Duas linhas distintas na origem viram uma.
    """
    a = fx.apto('DUP', 1, '101'); a['NUM_ENDERECO'] = '001'
    b = fx.apto('DUP', 1, '101'); b['NUM_ENDERECO'] = '1'
    b['COD_UNICO_ENDERECO'] = a['COD_UNICO_ENDERECO']
    df, stats = fx.base_preparada(fx.csv_cnefe([a, b]))
    assert len(df) == 2, (
        f'"001" e "1" colapsaram em {len(df)} linha(s): dedupe destrutivo')


# ══════════════════════════════════════════════════════════════════════════
#  D10 — desconhecido não pode virar comércio
# ══════════════════════════════════════════════════════════════════════════
def test_d10_desconhecido_nao_vira_comercio():
    """Fallback `return ('COMERCIO_SERVICO', ...)` produz falso positivo comercial.

    O CNEFE é cadastro estatístico de endereços, não cadastro comercial: "não
    sei" tem de sair como não sei.
    """
    import cnefe_coletivas as cc
    linhas = [fx.linha(logr='OPACO', num=5, esp=6, estab='ZZZQWERTY XPTO',
                       ind=1, tesp='')]
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
    setor = set(df['SETOR_ATIVIDADE'].dropna().astype(str))
    assert 'COMERCIO_SERVICO' not in setor, (
        f'descrição sem casamento lexical foi classificada como comércio: {setor}')


# ══════════════════════════════════════════════════════════════════════════
#  D11 — nenhum gate pode ser decorativo
# ══════════════════════════════════════════════════════════════════════════
def test_d11_nenhum_gate_sempre_verdadeiro():
    """`_exigir(cond or True, 'G6')` nunca falha. Anunciar 19 gates é enganoso.

    Varredura textual: um gate que não pode reprovar tem de ser removido ou
    redefinido, não mantido como enfeite.
    """
    src = open(os.path.join(SCRIPTS, 'rcc_emissor.py'), encoding='utf-8').read()
    assert 'or True' not in src, 'gate neutralizado com `or True` em rcc_emissor.py'


# ══════════════════════════════════════════════════════════════════════════
#  D12 — a versão registrada tem de acompanhar o código
# ══════════════════════════════════════════════════════════════════════════
def test_d12_versao_unica_e_coerente():
    """Manifest gravava 4.5.3 enquanto o pacote se anunciava 4.7.0.

    A versão existe para a forense separar entrega afetada de entrega sã; se
    ela não acompanha o código, a forense deixa de funcionar. Deve haver UMA
    fonte da versão, importada por quem grava.
    """
    import radar_utils as ru
    assert hasattr(ru, 'VERSAO'), 'não existe fonte única de versão (radar_utils.VERSAO)'
    skill = os.path.join(os.path.dirname(SCRIPTS), 'SKILL.md')
    if os.path.exists(skill):
        txt = open(skill, encoding='utf-8').read()
        assert ru.VERSAO in txt, f'SKILL.md não menciona a versão {ru.VERSAO}'
    for arq in ('radar_potenciais.py', 'rcc_emissor.py'):
        src = open(os.path.join(SCRIPTS, arq), encoding='utf-8').read()
        import re
        cravadas = set(re.findall(r"'(\d+\.\d+\.\d+)'", src)) - {ru.VERSAO}
        assert not cravadas, f'{arq} tem versão cravada divergente: {cravadas}'


# ══════════════════════════════════════════════════════════════════════════
#  D13 — a suíte tem de ser descoberta por um CI padrão
# ══════════════════════════════════════════════════════════════════════════
def test_d13_suite_descoberta_por_pytest():
    """`pytest -q` retornava "no tests ran" (exit 5): asserts no topo do módulo.

    Testes que só rodam por invocação manual não protegem ninguém em CI.
    """
    import importlib.util
    if importlib.util.find_spec('pytest') is None:
        return                                   # ambiente sem pytest: não falha aqui
    r = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--collect-only', HERE],
                       capture_output=True, text=True)
    assert r.returncode == 0, f'pytest não coletou testes:\n{r.stdout[-800:]}'



# ══════════════════════════════════════════════════════════════════════════
#  D14 — a grade é do ENDEREÇO, não do subconjunto classificado
# ══════════════════════════════════════════════════════════════════════════
def test_d14_grade_le_o_endereco_inteiro():
    """Caso real: RUA CORONEL VICENTE 529, POA — 18 salas, FLAG_COL=1 em 7.

    `construir_simples_e_faltantes` recebia só as linhas com FLAG_COL==1, e
    essa flag NÃO é uniforme dentro da mesma CHAVE. O motor via 7 salas de um
    prédio com 18 e devolvia as outras 11 como lacuna — hipóteses para
    unidades presentes no arquivo, duas linhas abaixo na mesma tabela.

    Resíduo que este defeito sustentava em POA depois de corrigido o D1: 248
    hipóteses de unidade existente.
    """
    import radar_pipeline as rp
    # Mecanismo real: sala anotada VAGO recebe SETOR_ATIVIDADE='VAGO' e sai do
    # conjunto coletivo (FLAG_COL=0). O CNEFE diz "esta sala existe e esta
    # vazia" e o motor respondia "esta sala falta" — inferindo justamente as
    # unidades que a fonte declarou desocupadas.
    #
    # O padrao de salas ativas espelha o endereco real: com ele o motor devolve
    # exatamente 303, 401, 503 e 601, as mesmas quatro hipoteses observadas em
    # producao. Sem esse padrao o teste passa por vacuidade.
    # v4.11: a ESCADA DE ANOMALIA (1 sinal = observação) corrigiu este caso na
    # origem — 'VAGO' repetido é o recenseador dizendo que N salas estão
    # vazias, não anomalia de coleta, e um sinal isolado deixou de derrubar o
    # registro. O invariante que este teste protege continua valendo, então a
    # fixture passa a produzir FLAG_COL parcial por um caminho que a escada não
    # absorve: coordenada reprovada na sanidade geoespacial.
    ATIVAS = {(2, 1), (2, 3), (3, 1), (4, 3), (6, 3), (7, 1), (7, 3)}
    linhas = []
    for a in range(2, 8):
        for u in (1, 2, 3):
            v = f'{a}0{u}'
            ativa = (a, u) in ATIVAS
            linhas.append(fx.linha(
                logr='CORONEL VICENTE', num=529, esp=6, tesp='', ind=1,
                estab=f'CLINICA {v}' if ativa else 'VAGO',
                dlat=(3.0 if v in ('402', '502') else 0.0),
                comps=[('SALA', v)]))
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    col = df[df['FLAG_COL'] == 1]
    assert 0 < len(col) < len(df), (
        f'a fixture nao reproduz FLAG_COL parcial ({len(col)} de {len(df)}) — '
        'sem isso o teste passa por vacuidade e nao protege nada')
    _, g, _ = xu.construir_simples_e_faltantes(col, universo=df)
    inf = sorted(g['AUSENTE_INFERIDO'].astype(str)) if len(g) else []
    assert not inf, f'grade completa gerou lacunas: {inf}'




# ══════════════════════════════════════════════════════════════════════════
#  D15 — PDCA-01 na ORIGEM do identificador (encontrado no ARTEFATO)
# ══════════════════════════════════════════════════════════════════════════
def test_d15_id_de_endereco_nasce_exato():
    """`canonica.map(lut)` com NA presente infere float64 e ARREDONDA.

    Quarta instância do PDCA-01, e a mais cara: acontece na LINHA QUE CRIA o
    identificador. `df['RADAR_ID_ENDERECO'] = canonica.map(lut).astype('Int64')`
    — o `.map()` sobre uma Series com pd.NA produz float64, todo valor acima de
    2^53 perde dígito, e o `.astype('Int64')` seguinte CARIMBA o valor já
    corrompido como se fosse inteiro exato.

    Por que nenhum gate pegou: G11 verifica DTYPE (que é Int64, correto),
    `varredura_ids` verifica PADRÃO DE NOME, e o round-trip do teste 17 verifica
    SERIALIZAÇÃO. Todos olham a REPRESENTAÇÃO. O valor já estava errado antes.

    Medido no artefato de Porto Alegre ANTES da correção: 100,00% dos 279.297
    hashes acima de 2^53 satisfaziam `int(float(x)) == x`, contra 0,60% num
    controle de inteiros aleatórios da mesma ordem de grandeza. Ou seja: todos
    arredondados, no arquivo entregue e no store.

    Este teste compara o id do dataframe com o id RECOMPUTADO da chave canônica
    — valor contra valor, não tipo contra tipo.
    """
    linhas = [fx.apto('EXATIDAO', 100 + i, str(101 + i)) for i in range(30)]
    linhas += [fx.linha(logr='SEM IDENTIDADE', num=0)]      # gera NA na canônica
    df, _ = fx.base_preparada(fx.csv_cnefe(linhas))
    assert df['RADAR_ID_ENDERECO'].isna().any(), (
        'a fixture precisa de linha SEM identidade — é o NA que dispara a '
        'inferência para float64; sem ele o teste passa por vacuidade')

    com_id = df[df['RADAR_ID_ENDERECO'].notna()]
    assert len(com_id) >= 10
    ruins = []
    for _, r in com_id.iterrows():
        esperado = xu.id64_rastreio(str(r['RADAR_CHAVE_CANONICA']))
        obtido = int(r['RADAR_ID_ENDERECO'])
        if obtido != esperado:
            ruins.append((r['RADAR_CHAVE_CANONICA'], esperado, obtido))
    assert not ruins, (
        f'{len(ruins)} id(s) divergem do recomputado. ex.: esperado '
        f'{ruins[0][1]}, obtido {ruins[0][2]} (delta {ruins[0][1]-ruins[0][2]})')

    acima = [int(v) for v in com_id['RADAR_ID_ENDERECO'] if int(v) > 2**53]
    if len(acima) >= 8:
        repr_float = sum(int(float(x)) == x for x in acima) / len(acima)
        assert repr_float < 0.5, (
            f'{100*repr_float:.0f}% dos ids sao exatamente representaveis em '
            'float64 — assinatura de arredondamento (aleatorios dao ~0,6%)')



# ══════════════════════════════════════════════════════════════════════════
#  D16 — PDCA-01 na INGESTÃO: o valor do complemento lido como float
# ══════════════════════════════════════════════════════════════════════════
def test_d16_valor_de_complemento_nunca_e_numerico():
    """`VAL_COMP_ELEM*` inferido como float64 destrói a numeração.

    Encontrado ao investigar por que dois testes de corrupção ficavam PULADOS.
    O gatilho é uma LINHA VIZINHA sem complemento: com ela a coluna fica
    numérica-com-nulos, pandas infere float64 e o apartamento 101 vira
    '101.0'. Como `num_unidade()` exige dígitos puros, a grade deixa de ler
    aquele endereço e TODA hipótese daquele prédio desaparece — sem erro, sem
    aviso, sem linha na quarentena.

    Depende do DADO, não do código: em Porto Alegre alguma linha tem letra no
    complemento e a coluna vem object, então o defeito nunca apareceu. Num
    município cujos complementos sejam todos numéricos, a base inteira sai
    corrompida. É o mesmo PDCA-01 do D15, um estágio antes.
    """
    import cnefe_fixture as fx
    import radar_pipeline as rp
    predio = [fx.apto('D16', 400, v, dlat=0.02)
              for v in ('101', '102', '201', '202', '301')]
    # o MESMO prédio, com e sem um registro vizinho de complemento vazio
    so_predio, _ = rp.preparar_base(fx.csv_cnefe(predio))
    com_vizinho, _ = rp.preparar_base(fx.csv_cnefe(
        predio + [fx.estabelecimento('D16', 300, f'PADARIA {i}')
                  for i in range(3)]))

    def _valores(df):
        g = df[df['NUMERO'] == 400]
        return sorted(str(v) for v in g['UNIDADE_VALOR'].dropna())

    a, b = _valores(so_predio), _valores(com_vizinho)
    assert b == a, (
        f'a presenca de uma linha SEM complemento mudou a numeracao do predio: '
        f'{a} -> {b} (PDCA-01 na leitura de VAL_COMP_ELEM*)')
    assert not any('.' in v for v in b), f'valor de unidade com decimal: {b}'

    # e a consequência que importa: a lacuna do 302 continua sendo vista
    def _hips(df):
        col = df[df['FLAG_COL'] == 1]
        _, g, _ = xu.construir_simples_e_faltantes(col, universo=df)
        return sorted(str(x) for x in g['AUSENTE_INFERIDO']) if len(g) else []

    assert _hips(com_vizinho) == _hips(so_predio) != [], (
        'a lacuna do predio sumiu quando havia uma linha sem complemento na '
        'base — hipoteses perdidas em silencio')


# ══════════════════════════════════════════════════════════════════════════
#  D17–D19 — canônica v2: numeral por extenso entra, romano NÃO
# ══════════════════════════════════════════════════════════════════════════
def test_d17_numeral_por_extenso_unifica_o_endereco():
    """`RUA DEZ DE MAIO` e `RUA 10 DE MAIO` são a mesma rua.

    Medido no CNEFE de Porto Alegre: 66 grupos de logradouro que só diferem
    por dígito×extenso — 'VINTE E CINCO DE JULHO'/'25 DE JULHO', 'SEM
    DENOMINACAO UM'/'SEM DENOMINACAO 1'. Sem isto são dois endereços, dois
    ids, duas coletivas, e a contagem de economias sai partida ao meio.
    """
    import cnefe_fixture as fx
    import radar_pipeline as rp
    linhas = ([fx.apto('DEZ DE MAIO', 100, str(v)) for v in (101, 102)]
              + [fx.apto('10 DE MAIO', 100, str(v)) for v in (201, 202)])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    ids = set(df['RADAR_ID_ENDERECO'].dropna().astype(str))
    assert len(ids) == 1, (
        f'DEZ DE MAIO e 10 DE MAIO viraram {len(ids)} enderecos distintos')
    can = set(df['RADAR_CHAVE_CANONICA'].dropna().astype(str))
    assert len(can) == 1 and '10 DE MAIO' in can.pop(), \
        'a canonica publicada nao ficou na forma numerica'


def test_d18_numeral_mal_formado_nao_e_convertido():
    """`SESSENTA SESSENTA` não é 120.

    A acumulação ingênua (`atual += v`) soma dezenas repetidas e produz um
    número que ninguém escreveu — em POA isso fundiria a rua 'SESSENTA
    SESSENTA' com a rua '120'. Numeral PT-BR bem formado nunca repete a mesma
    classe decimal; o que não é bem formado fica como está, sem palpite.
    """
    import radar_utils as ru
    assert ru.expandir_numerais('SESSENTA SESSENTA') == 'SESSENTA SESSENTA'
    assert ru.expandir_numerais('VINTE E CINCO DE JULHO') == '25 DE JULHO'
    assert ru.expandir_numerais('DOIS MIL CENTO DEZOITO') == '2118'
    assert ru.expandir_numerais('SETE MIL E CINCO') == '7005'


def test_d19_romano_fica_fora_da_canonica():
    """Romano no nome corrompe rua real — fica na anotação, não no id.

    Medido em POA: converter romano rende UMA fusão legítima ('XV DE
    NOVEMBRO'≡'15 DE NOVEMBRO') e estraga pelo menos três nomes reais —
    'ANNIBAL DI PRIMIO BECK' vira 'ANNIBAL 501 PRIMIO BECK' (D+I=501) e
    'BEM TE VI' vira 'BEM TE 6'. Uma pré-imagem PUBLICADA que diz isso é
    errada na cara de quem lê. A equivalência do XV vira evidência graduada.
    """
    import radar_utils as ru
    for nome in ('ANNIBAL DI PRIMIO BECK', 'BEM TE VI', 'XV DE NOVEMBRO'):
        assert ru.expandir_numerais(nome) == nome, \
            f'romano convertido na canonica: {nome}'


TESTES = [v for k, v in sorted(globals().items()) if k.startswith('test_')]

if __name__ == '__main__':
    ok = falhas = 0
    for t in TESTES:
        nome = t.__name__
        try:
            t()
            ok += 1
            print(f'  PASSA   {nome}')
        except AssertionError as e:
            falhas += 1
            print(f'  FALHA   {nome}\n          {str(e)[:200]}')
        except Exception as e:                                    # noqa: BLE001
            falhas += 1
            print(f'  ERRO    {nome}\n          {type(e).__name__}: {str(e)[:200]}')
    print(f'\n{ok} passam · {falhas} falham · {len(TESTES)} total')
    sys.exit(1 if falhas else 0)


# ══════════════════════════════════════════════════════════════════════════
#  D20 — P0: o TÍTULO do logradouro não entrava no identificador
# ══════════════════════════════════════════════════════════════════════════
def test_d20_titulos_diferentes_dao_identidades_diferentes():
    """`RUA BARÃO DO GRAVATAÍ` e `RUA BARONESA DO GRAVATAÍ` são duas ruas.

    A CHAVE operacional foi corrigida na v4.8 para levar TIPO **e TÍTULO**; o
    identificador ficou para trás com `TIPO + NOME`. Resultado: duas entidades
    que o pipeline trata como distintas recebiam o MESMO
    `RADAR_ID_ENDERECO`.

    Não é hipótese: são 7 casos no CNEFE real de Porto Alegre —
    `BARAO`/`BARONESA DO GRAVATAI` (204 e 429) e `PAI`/`SAO JOAQUIM` (0, 11,
    35). São ruas diferentes da cidade, com o mesmo id.

    E o gate de colisão N13 não podia pegar: ele procura um hash apontando
    para duas canônicas distintas, e aqui as duas linhas já viraram a MESMA
    canônica antes do hash. O gate respondia certo a uma pergunta menor.
    """
    import cnefe_fixture as fx
    import radar_pipeline as rp
    linhas = ([fx.linha(logr='DO GRAVATAI', titulo='BARAO', num=204,
                        comps=[('APARTAMENTO', '101')])]
              + [fx.linha(logr='DO GRAVATAI', titulo='BARONESA', num=204,
                          dlat=0.02, comps=[('APARTAMENTO', '101')])])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    ids = df['RADAR_ID_ENDERECO'].dropna().astype(str)
    assert ids.nunique() == 2, (
        f'BARAO e BARONESA DO GRAVATAI compartilham identidade: {set(ids)}')
    can = set(df['RADAR_CHAVE_CANONICA'].dropna().astype(str))
    assert any('BARAO' in c for c in can) and any('BARONESA' in c for c in can), \
        f'o titulo nao aparece na pre-imagem publicada: {can}'


def test_d21_titulo_ausente_e_preenchido_pelo_unico_do_grupo():
    """`RUA RAUL MOREIRA` e `RUA DOUTOR RAUL MOREIRA` são a mesma rua.

    Levar o título para a identidade sem preencher a omissão do recenseador
    partiria 12 ruas de Porto Alegre em duas. Quando o município tem UM único
    título registrado para aquele tipo+nome, a ausência é omissão — não uma
    via diferente. Com DOIS títulos (BARAO/BARONESA), não se preenche nada:
    aí são vias distintas e cada uma fica com o seu.
    """
    import cnefe_fixture as fx
    import radar_pipeline as rp
    linhas = ([fx.linha(logr='RAUL MOREIRA', titulo='DOUTOR', num=1663,
                        cep='90000001', comps=[('APARTAMENTO', '101')])]
              + [fx.linha(logr='RAUL MOREIRA', titulo='', num=1663,
                          cep='90000002', comps=[('APARTAMENTO', '201')])])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    ids = df['RADAR_ID_ENDERECO'].dropna().astype(str)
    assert ids.nunique() == 1, (
        f'a mesma rua virou dois enderecos por omissao de titulo: {set(ids)}')
