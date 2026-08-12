#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bateria de CORRUPÇÃO — o QA pós-emissão só existe se detectar dano real.
================================================================================

Escrita ANTES do `qualidade_artefato.py`, como a bateria P0 foi escrita antes
das correções. Testar um arquivo bom não prova nada: prova que o caminho feliz
funciona. O que precisa ser provado é que **cada dano é detectado**.

Cada teste gera um artefato válido, corrompe UMA coisa, e exige que o auditor
aponte aquela coisa — não um erro genérico. Um auditor que reprova tudo é tão
inútil quanto um que aprova tudo.

Divisão que o auditor tem de respeitar (R13):
  REPRESENTAÇÃO  id virou número, zero à esquerda sumiu, ordem de coluna mudou
                 → reparável, uma tentativa, revalidação completa
  SEMÂNTICA      unidade mudou de coletiva, hipótese virou observada, contagem
                 → NUNCA reparar. QUARENTENA.

    pytest tests/test_qa_artefato.py
"""

import csv
import json
import os
import shutil
import sys
import tempfile

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
for p in (SCRIPTS, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import cnefe_fixture as fx                                          # noqa: E402
import qualidade_artefato as qa                                     # noqa: E402
import radar_pipeline as rp                                         # noqa: E402
import radar_utils as xu                                            # noqa: E402
import rcc_emissor as R                                             # noqa: E402

DIC = os.path.join(os.path.dirname(SCRIPTS), 'RCC_dicionario.csv')


# ── infraestrutura ────────────────────────────────────────────────────────
def _colunas():
    with open(DIC, encoding='utf-8-sig') as fh:
        return [r['CAMPO'] for r in csv.DictReader(fh, delimiter=';')]


@pytest.fixture(scope='module')
def rcc(tmp_path_factory):
    """Um RCC válido, emitido pelo caminho canônico. Base de toda corrupção."""
    linhas = ([fx.apto('QA', 100, str(a * 100 + u))
               for a in (1, 2, 3) for u in (1, 2, 3)]
              + [fx.casa('QA', 200, str(i)) for i in (1, 2, 4)]
              + [fx.estabelecimento('QA', 300, f'PADARIA {i}') for i in range(3)]
              # Prédio com LACUNA (falta o 302): sem ele a base não tinha
              # nenhuma linha INFERIDO e `test_qa12`/`test_qa14` — os dois
              # testes que provam que hipótese não vira observação e que grau
              # não se altera — ficavam PULADOS. Corrupção não exercitada é
              # cobertura imaginária.
              # `dlat` afasta o prédio ~1,1 km: todas as linhas da fixture
              # nascem na MESMA coordenada e o remerge por proximidade (50 m)
              # funde os quatro endereços num só, misturando as numerações e
              # apagando a lacuna. O deslocamento é da FIXTURE, não do modelo.
              + [fx.apto('QA', 400, v, dlat=0.01)
                 for v in ('101', '102', '201', '202', '301')])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-qa', 'IBGE/RCC', '2022', 'h', cols), d)
    inf = R.linhas_inferidas(df, out, cols)
    if len(inf):
        out = pd.concat([out, inf], ignore_index=True)
    for c, cnt in (('COL_QTD_OBSERVADA', 'OBSERVADO'), ('COL_QTD_INFERIDA', 'INFERIDO')):
        out[c] = (out.assign(_x=(out['UND_NATUREZA'] == cnt).astype(int))
                  .groupby('COLETIVA_ID')['_x'].transform('sum').values)
    base = tmp_path_factory.mktemp('qa')
    caminho = str(base / 'rcc.csv')
    out.to_csv(caminho, sep=';', index=False, encoding='utf-8-sig')
    return {'modelo': out, 'caminho': caminho, 'colunas': cols, 'dir': str(base)}


def _copia(rcc, nome):
    dst = os.path.join(rcc['dir'], nome)
    shutil.copy(rcc['caminho'], dst)
    return dst


def _auditar(caminho, modelo, colunas, **kw):
    return qa.auditar(caminho, modelo=modelo, colunas=colunas,
                      contrato=qa.contrato_rcc(DIC), **kw)


def _tem(res, prefixo=None, severidade=None, campo=None):
    for i in res.issues:
        if prefixo and not i.regra.startswith(prefixo):
            continue
        if severidade and i.severidade != severidade:
            continue
        if campo and i.campo != campo:
            continue
        return True
    return False


def _reescrever(caminho, df):
    df.to_csv(caminho, sep=';', index=False, encoding='utf-8-sig')


# ══════════════════════════════════════════════════════════════════════════
#  CONTROLE — o artefato íntegro precisa passar. Auditor que reprova tudo
#  não distingue nada.
# ══════════════════════════════════════════════════════════════════════════
def test_qa00_artefato_integro_e_selado(rcc):
    res = _auditar(rcc['caminho'], rcc['modelo'], rcc['colunas'])
    assert res.estado == 'SEALED', (
        f'artefato integro reprovado: {[i.regra for i in res.issues][:5]}')
    assert res.artifact_sha256 and len(res.artifact_sha256) == 64
    assert res.semantic_sha256 and len(res.semantic_sha256) == 64


# ══════════════════════════════════════════════════════════════════════════
#  ESTRUTURA FÍSICA
# ══════════════════════════════════════════════════════════════════════════
def test_qa01_arquivo_truncado(rcc):
    """Truncar no MEIO de uma linha: o parser pode ate abrir, o dado nao."""
    p = _copia(rcc, 'trunc.csv')
    bruto = open(p, encoding='utf-8-sig').read()
    open(p, 'w', encoding='utf-8-sig').write(bruto[:int(len(bruto) * 0.82)])
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado in ('QUARANTINED', 'FAILED')
    assert _tem(res, 'QA-EST') or _tem(res, 'QA-LIN')


def test_qa02_coluna_removida(rcc):
    p = _copia(rcc, 'semcol.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig').drop(columns=['ATV_PRESENTE'])
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-EST')


def test_qa03_coluna_desconhecida(rcc):
    p = _copia(rcc, 'colextra.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    d['COLUNA_FANTASMA'] = 'x'
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-EST')


def test_qa04_cabecalho_duplicado(rcc):
    p = _copia(rcc, 'dupcab.csv')
    txt = open(p, encoding='utf-8-sig').read().split('\n')
    txt[0] = txt[0] + ';ATV_PRESENTE'
    open(p, 'w', encoding='utf-8-sig').write('\n'.join(txt))
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-EST')


def test_qa05_ordem_de_coluna_trocada(rcc):
    """REPARÁVEL: ordem é representação, não significado.

    A primeira versao deste teste auditava uma COPIA LIMPA da fixture no passo
    do reparo, entao o SEALED que ele afirmava nao provava reparo nenhum —
    provava que arquivo bom passa. Exatamente o padrao que esta bateria existe
    para impedir. Agora corrompe, repara e CONFERE A ORDEM FISICA no disco.
    """
    p = _copia(rcc, 'ordem.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    cols = list(d.columns)
    cols[3], cols[7] = cols[7], cols[3]
    _reescrever(p, d[cols])

    diag = _auditar(p, rcc['modelo'], rcc['colunas'], reparar=False)
    assert _tem(diag, 'QA-EST')
    assert any(i.auto_corrigivel for i in diag.issues), \
        'troca de ordem tem de ser classificada como reparavel'

    p2 = os.path.join(rcc['dir'], 'ordem_reparo.csv')
    shutil.copy(p, p2)                                # a COPIA CORROMPIDA
    lido_antes = list(pd.read_csv(p2, sep=';', nrows=0, encoding='utf-8-sig').columns)
    assert lido_antes != rcc['colunas'], 'a copia precisa estar corrompida'

    res = _auditar(p2, rcc['modelo'], rcc['colunas'], reparar=True)
    assert res.estado == 'CORRECTED_AND_SEALED', f'estado={res.estado}'
    assert res.ciclo == 1
    lido_depois = list(pd.read_csv(p2, sep=';', nrows=0, encoding='utf-8-sig').columns)
    assert lido_depois == rcc['colunas'], 'ordem fisica no disco nao foi restaurada'
    assert res.correcoes, 'reparo aplicado sem registro do que foi corrigido'


def test_qa05b_id_com_sufixo_ponto_zero_e_reparado(rcc):
    """`123.0` é rastro de float — representação, reparável."""
    p = _copia(rcc, 'idpz.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    i = d.index[d['COLETIVA_CHAVE_HASH'].fillna('').ne('')][0]
    original = d.loc[i, 'COLETIVA_CHAVE_HASH']
    d.loc[i, 'COLETIVA_CHAVE_HASH'] = original + '.0'
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'], reparar=True)
    assert res.estado == 'CORRECTED_AND_SEALED', f'estado={res.estado}'
    relido = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    assert relido.loc[i, 'COLETIVA_CHAVE_HASH'] == original
    assert any('QA-IDENT' in c['regra'] for c in res.correcoes)


@pytest.mark.parametrize('campo,novo', [
    ('UND_NATUREZA', 'INFERIDO'), ('COL_FORMA', 'COMERCIAL'),
    ('EVD_GRAU', 'ESPECULATIVA'), ('ACT_DESTINO_CAMPO', 'RETER'),
])
def test_qa05c_semantica_jamais_e_reparada(rcc, campo, novo):
    """Nenhum campo de significado pode entrar em autorreparo."""
    p = _copia(rcc, f'sem_{campo}.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    d.loc[0, campo] = novo
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'], reparar=True)
    assert res.estado == 'QUARANTINED', f'{campo} foi selado apos alteracao'
    assert not res.correcoes, f'{campo} entrou em autorreparo: {res.correcoes}'


# ══════════════════════════════════════════════════════════════════════════
#  LINHA — presença, duplicidade, identidade
# ══════════════════════════════════════════════════════════════════════════
def test_qa06_linha_removida(rcc):
    p = _copia(rcc, 'menos1.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig').drop(index=3)
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-LIN')


def test_qa07_linha_duplicada(rcc):
    p = _copia(rcc, 'dup1.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    _reescrever(p, pd.concat([d, d.iloc[[2]]], ignore_index=True))
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-LIN')


def test_qa08_linha_removida_e_outra_duplicada(rcc):
    """A contagem TOTAL não muda. Só hash por linha detecta."""
    p = _copia(rcc, 'troca.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    d = pd.concat([d.drop(index=5), d.iloc[[2]]], ignore_index=True)
    _reescrever(p, d)
    lido = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    assert len(lido) == len(rcc['modelo']), 'a fixture precisa manter o total igual'
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-LIN')


def test_qa09_id_truncado(rcc):
    p = _copia(rcc, 'idtrunc.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    i = d.index[d['COLETIVA_CHAVE_HASH'].notna()][0]
    d.loc[i, 'COLETIVA_CHAVE_HASH'] = str(d.loc[i, 'COLETIVA_CHAVE_HASH'])[:-2]
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, campo='COLETIVA_CHAVE_HASH')


def test_qa10_id_em_notacao_cientifica(rcc):
    """O defeito histórico: Excel/pandas transformando id de 19 dígitos."""
    p = _copia(rcc, 'idsci.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    i = d.index[d['COLETIVA_CHAVE_HASH'].notna()][0]
    d.loc[i, 'COLETIVA_CHAVE_HASH'] = f'{float(d.loc[i, "COLETIVA_CHAVE_HASH"]):.6e}'
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-IDENT')


def test_qa11_id_arredondado_em_float(rcc):
    """D15 de novo, agora no ARTEFATO: valor plausível, dígito perdido."""
    p = _copia(rcc, 'idfloat.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    i = d.index[d['COLETIVA_CHAVE_HASH'].notna()][0]
    d.loc[i, 'COLETIVA_CHAVE_HASH'] = str(int(float(d.loc[i, 'COLETIVA_CHAVE_HASH'])))
    if d.loc[i, 'COLETIVA_CHAVE_HASH'] == str(rcc['modelo'].loc[i, 'COLETIVA_CHAVE_HASH']):
        pytest.skip('id da fixture nao passa de 2^53 — sem dano a detectar')
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED'


# ══════════════════════════════════════════════════════════════════════════
#  SEMÂNTICA — nunca reparável
# ══════════════════════════════════════════════════════════════════════════
def test_qa12_hipotese_virou_observada(rcc):
    p = _copia(rcc, 'natureza.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    idx = d.index[d['UND_NATUREZA'] == 'INFERIDO']
    if not len(idx):
        pytest.skip('fixture sem hipotese')
    d.loc[idx[0], 'UND_NATUREZA'] = 'OBSERVADO'
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED'
    assert not any(i.auto_corrigivel for i in res.issues if i.severidade == 'FATAL'), \
        'divergencia semantica NUNCA pode ser marcada como auto-corrigivel'


def test_qa13_unidade_mudou_de_coletiva(rcc):
    p = _copia(rcc, 'grupo.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    ids = [x for x in d['COLETIVA_ID'].dropna().unique() if str(x).strip()]
    if len(ids) < 2:
        pytest.skip('fixture com uma coletiva so')
    i = d.index[d['COLETIVA_ID'] == ids[0]][0]
    d.loc[i, 'COLETIVA_ID'] = ids[1]
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED'


def test_qa14_grau_de_hipotese_alterado(rcc):
    p = _copia(rcc, 'grau.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    idx = d.index[d['EVD_GRAU'].fillna('') != '']
    if not len(idx):
        pytest.skip('fixture sem grau')
    d.loc[idx[0], 'EVD_GRAU'] = 'ESPECULATIVA'
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED'


def test_qa15_destino_de_campo_alterado(rcc):
    p = _copia(rcc, 'destino.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    d.loc[0, 'ACT_DESTINO_CAMPO'] = 'RETER' if d.loc[0, 'ACT_DESTINO_CAMPO'] != 'RETER' else 'ENVIAR'
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED'


def test_qa16_contagem_do_grupo_alterada(rcc):
    """R14 — o auditor RECALCULA a contagem, não confia na coluna."""
    p = _copia(rcc, 'contagem.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    d.loc[0, 'COL_QTD_OBSERVADA'] = str(int(d.loc[0, 'COL_QTD_OBSERVADA']) + 7)
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-COL')


def test_qa17_fechamento_do_setor_quebrado(rcc):
    """R14 — ocupados+ocasional+vagos tem de bater com particulares."""
    p = _copia(rcc, 'set.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    d['SET_DOM_PARTICULARES'] = '100'
    d['SET_DOM_OCUPADOS'] = '70'
    d['SET_DOM_USO_OCASIONAL'] = '10'
    d['SET_DOM_VAGOS'] = '5'                 # 70+10+5 = 85, não 100
    d['SET_COD'] = '431490205000001'
    _reescrever(p, d)
    res = _auditar(p, rcc['modelo'], rcc['colunas'], reconciliar_modelo=False)
    assert _tem(res, 'QA-SET'), [i.regra for i in res.issues][:6]


def test_qa18_manifest_divergente(rcc):
    p = _copia(rcc, 'man.csv')
    man = {'linhas': len(rcc['modelo']) + 5, 'observadas': 1, 'inferidas': 1}
    open(p + '.manifest.json', 'w', encoding='utf-8').write(json.dumps(man))
    res = _auditar(p, rcc['modelo'], rcc['colunas'], manifest=man)
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-MAN')


# ══════════════════════════════════════════════════════════════════════════
#  R14 ESTENDIDO — rederivar, não só comparar
# ══════════════════════════════════════════════════════════════════════════
def test_qa24_id_rederivado_do_endereco(rcc):
    """Comparar arquivo com modelo NÃO detecta erro se o modelo estiver errado.

    Foi o que o D15 ensinou: produtor gera id corrompido, grava o id
    corrompido, auditor compara os dois e sela. O auditor precisa REDERIVAR o
    identificador a partir dos campos END_*, por implementação própria.
    """
    p = _copia(rcc, 'idderiv.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    i = d.index[d['COLETIVA_CHAVE_HASH'].fillna('').ne('')][0]
    corrompido = str(int(d.loc[i, 'COLETIVA_CHAVE_HASH']) + 128)   # float-like
    d.loc[i, 'COLETIVA_CHAVE_HASH'] = corrompido
    _reescrever(p, d)
    modelo_ruim = rcc['modelo'].copy()
    modelo_ruim.loc[i, 'COLETIVA_CHAVE_HASH'] = corrompido   # produtor ja errado
    res = _auditar(p, modelo_ruim, rcc['colunas'])
    assert res.estado == 'QUARANTINED', (
        'arquivo bate com o modelo, mas o id nao deriva do endereco — '
        'comparacao sozinha nao viu nada')
    assert _tem(res, 'QA-DER')


# ══════════════════════════════════════════════════════════════════════════
#  LACRE
# ══════════════════════════════════════════════════════════════════════════
def test_qa19_arquivo_alterado_depois_do_lacre(rcc):
    p = _copia(rcc, 'poslacre.csv')
    res = _auditar(p, rcc['modelo'], rcc['colunas'])
    assert res.estado == 'SEALED'
    with open(p, 'a', encoding='utf-8-sig') as fh:
        fh.write(' ')
    assert not qa.verificar_lacre(p, res.selo()), \
        'alteracao apos o lacre nao foi detectada pelo hash do artefato'


def test_qa20_publicacao_e_atomica(rcc, tmp_path):
    """Arquivo intermediário não pode existir com o nome definitivo."""
    destino = str(tmp_path / 'final.csv')
    with qa.publicacao_atomica(destino) as pend:
        rcc['modelo'].to_csv(pend, sep=';', index=False, encoding='utf-8-sig')
        assert not os.path.exists(destino), \
            'o nome definitivo existiu antes do QA aprovar'
        assert os.path.exists(pend)
    assert os.path.exists(destino)

    destino2 = str(tmp_path / 'nunca.csv')
    with pytest.raises(RuntimeError):
        with qa.publicacao_atomica(destino2) as pend2:
            open(pend2, 'w').write('x')
            raise RuntimeError('QA reprovou')
    assert not os.path.exists(destino2), \
        'falha no QA deixou o arquivo publicado'


def test_qa21_modo_strict_x_exploratory(rcc):
    """Runtime divergente e bloco obrigatório ausente: strict não sela."""
    p = _copia(rcc, 'strict.csv')
    avisos = ['WARNING_RUNTIME_DIVERGENTE']
    r_exp = _auditar(p, rcc['modelo'], rcc['colunas'],
                     modo='exploratory', avisos=avisos)
    r_str = _auditar(p, rcc['modelo'], rcc['colunas'],
                     modo='strict', avisos=avisos)
    assert r_exp.estado in ('SEALED', 'SEALED_WITH_WARNINGS')
    assert r_str.estado == 'QUARANTINED', \
        'modo strict selou execucao com runtime divergente'


def test_qa22_bloco_obrigatorio_ausente(rcc):
    p = _copia(rcc, 'semset.csv')
    r = _auditar(p, rcc['modelo'], rcc['colunas'],
                 modo='strict', blocos_obrigatorios=['SET'],
                 blocos_falhos=['SET'])
    assert r.estado == 'QUARANTINED' and _tem(r, 'QA-BLOCO')



# ══════════════════════════════════════════════════════════════════════════
#  PACOTE — o artefato e seus sidecars publicam juntos ou não publicam
# ══════════════════════════════════════════════════════════════════════════
def test_qa25_pacote_publica_como_unidade(tmp_path):
    destino = str(tmp_path / 'p.csv')
    with qa.publicacao_de_pacote(destino, ['.manifest.json', '.seal.json']) as pk:
        open(pk[''], 'w').write('a;b\n1;2\n')
        open(pk['.manifest.json'], 'w').write('{}')
        open(pk['.seal.json'], 'w').write('{}')
        assert not os.path.exists(destino), 'artefato existiu antes do fim do bloco'
    for suf in ('', '.manifest.json', '.seal.json'):
        assert os.path.exists(destino + suf), f'sidecar {suf} nao publicado'


def test_qa26_falha_nao_deixa_entrega_parcial(tmp_path):
    destino = str(tmp_path / 'q.csv')
    with pytest.raises(RuntimeError):
        with qa.publicacao_de_pacote(destino, ['.manifest.json']) as pk:
            open(pk[''], 'w').write('x')
            open(pk['.manifest.json'], 'w').write('{}')
            raise RuntimeError('QA reprovou')
    assert not os.path.exists(destino)
    assert not os.path.exists(destino + '.manifest.json'), \
        'manifest publicado sem o artefato — entrega parcial'
    assert not [f for f in os.listdir(tmp_path) if f.startswith('.pending-')], \
        'restou arquivo pendente no diretorio'


def test_qa27_quarentena_por_execucao(tmp_path):
    """Duas falhas seguidas não podem sobrescrever a evidência uma da outra."""
    destino = str(tmp_path / 'r.csv')
    alvos = []
    for run in ('RUN-aaa111', 'RUN-bbb222'):
        pend = str(tmp_path / f'.pending-{run}')
        open(pend, 'w').write('x')
        r = qa.Resultado(estado='QUARANTINED')
        alvos.append(qa.quarentenar(pend, destino, r, run_id=run))
    assert alvos[0] != alvos[1] and all(os.path.exists(a) for a in alvos)
    assert all(os.path.exists(os.path.join(os.path.dirname(a), 'seal.json'))
               for a in alvos)


def test_qa27b_suite_roda_offline_em_modo_strict(tmp_path):
    """A suíte não pode depender de rede.

    Em `--qa-mode strict` o bloco SET é obrigatório; sem internet o download
    falhava, o gate CORRETAMENTE quarentenava e o teste quebrava por motivo
    errado. O gate estava certo — faltava fonte local determinística. O cache
    tambem passou a ser validado por CABECALHO, nao por tamanho: `size > 1 MB`
    aceitava download interrompido e recusava fixture legitima.
    """
    import subprocess
    cache = str(tmp_path / 'cache')
    fx.cache_set_offline(cache)
    saida = str(tmp_path / 'off.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', os.path.join(HERE, 'base_sintetica.csv'),
         '--output', saida, '--dicionario', DIC, '--safra', '2022',
         '--qa-mode', 'strict', '--cache-ibge', cache],
        capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f'strict offline falhou:\n{r.stdout[-1500:]}'
    selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
    assert selo['quality_seal'] in ('SEALED', 'CORRECTED_AND_SEALED')


def test_qa28_cli_produz_pacote_completo_e_selado(tmp_path):
    """Ponta a ponta: 4 arquivos, selo coerente e hash que confere."""
    import subprocess
    saida = str(tmp_path / 'e2e.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', os.path.join(HERE, 'base_sintetica.csv'),
         '--output', saida, '--dicionario', DIC, '--safra', '2022',
         '--sem-setor', '--qa-mode', 'exploratory'],
        capture_output=True, text=True)
    assert r.returncode == 0, f'{r.stdout[-1500:]}\n{r.stderr[-800:]}'
    for suf in ('', '.manifest.json', '.seal.json', '.qa_issues.csv'):
        assert os.path.exists(saida + suf), f'faltou {suf}'
    selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
    assert selo['quality_seal'] in ('SEALED', 'CORRECTED_AND_SEALED',
                                    'SEALED_WITH_WARNINGS')
    assert len(selo['input_sha256']) == 64, 'hash da entrada ainda truncado'
    assert selo['run_fingerprint'] and selo['manifest_sha256']
    assert qa.verificar_lacre(saida, selo), 'hash do artefato nao confere'
    # O manifest aponta para o selo; NAO o contem. Embutir o selo obrigaria a
    # regravar o manifest depois de o hash dele entrar no selo — foi assim que
    # o manifest_sha256 passou a lacrar um arquivo diferente do entregue.
    man = json.load(open(saida + '.manifest.json', encoding='utf-8'))
    assert man['seal_file'] == os.path.basename(saida) + '.seal.json'
    assert 'quality_seal' not in man, 'selo embutido quebra a cadeia de hash'
    assert man['runtime']['pandas'], 'evidencia de runtime ausente no manifest'


# ══════════════════════════════════════════════════════════════════════════
#  P0 da revisão externa — falhas de SEGUNDA ORDEM: no próprio sistema de QA
# ══════════════════════════════════════════════════════════════════════════
def test_qa29_cadeia_de_hash_do_pacote_fecha(tmp_path):
    """Todo hash do selo tem de corresponder ao arquivo ENTREGUE.

    Defeito real: o manifest era regravado com o selo embutido DEPOIS de o
    `manifest_sha256` ser calculado. O selo lacrava o manifest A e o cliente
    recebia o manifest B. `artifact` e `qa_report` conferiam; `manifest`, não.
    A cadeia tem de ser unidirecional: artefato → manifest → qa → SELO, e o
    selo é o TERMINAL. Nada é reescrito depois de entrar nele.
    """
    import hashlib as _h
    import subprocess
    saida = str(tmp_path / 'cad.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', os.path.join(HERE, 'base_sintetica.csv'),
         '--output', saida, '--dicionario', DIC, '--safra', '2022',
         '--sem-setor', '--qa-mode', 'exploratory'],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-1500:]
    selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
    for chave, arq in (('artifact_sha256', saida),
                       ('manifest_sha256', saida + '.manifest.json'),
                       ('qa_report_sha256', saida + '.qa_issues.csv')):
        real = _h.sha256(open(arq, 'rb').read()).hexdigest()
        assert selo[chave] == real, (
            f'{chave} do selo nao corresponde ao arquivo entregue '
            f'({selo[chave][:16]} != {real[:16]})')


@pytest.mark.parametrize('falha_em', [1, 2, 3, 4])
def test_qa30_publicacao_nunca_deixa_artefato_sem_linhagem(tmp_path, monkeypatch,
                                                           falha_em):
    """Vários `os.replace` sequenciais NÃO são uma transação.

    A versão anterior deixava o artefato publicado e o manifest ausente quando
    o 2º rename falhava — o estado que a arquitetura afirmava impedir.

    Com nomes planos, N renames atômicos são impossíveis em POSIX. O que este
    teste exige é a garantia que DÁ para dar: interrupção em QUALQUER ponto
    nunca pode deixar artefato sem sua linhagem. O inverso é aceitável — um
    sidecar órfão não vira entrega.
    """
    destino = str(tmp_path / 'tx.csv')
    real_replace = os.replace
    chamadas = {'n': 0}

    def replace_falha(src, dst):
        chamadas['n'] += 1
        if chamadas['n'] == 2:
            raise OSError('falha simulada no meio da publicacao')
        return real_replace(src, dst)

    def replace_n(src, dst):
        chamadas['n'] += 1
        if chamadas['n'] == falha_em:
            raise OSError(f'falha simulada no rename #{falha_em}')
        return real_replace(src, dst)

    monkeypatch.setattr(os, 'replace', replace_n)
    with pytest.raises(OSError):
        with qa.publicacao_de_pacote(destino, ['.manifest.json', '.seal.json']) as pk:
            for k in pk:
                open(pk[k], 'w').write('x')
    monkeypatch.undo()
    if os.path.exists(destino):
        for suf in ('.manifest.json', '.seal.json'):
            assert os.path.exists(destino + suf), (
                f'ARTEFATO publicado sem {suf} apos falha no rename '
                f'#{falha_em} — entrega sem procedencia')


def test_qa31_run_id_identifica_a_execucao(tmp_path):
    """Duas execuções da MESMA entrada precisam de RUN_IDs diferentes.

    O `exec_id` era blake2b(input+hash, 3 bytes): 24 bits e DETERMINÍSTICO da
    entrada. Não identificava execução — identificava arquivo. E isso quebrava
    a quarentena por RUN_ID: duas falhas seguidas caíam na mesma pasta e a
    segunda sobrescrevia a evidência da primeira.
    """
    import subprocess
    ids = []
    for i in range(2):
        saida = str(tmp_path / f'r{i}.csv')
        r = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
             '--input', os.path.join(HERE, 'base_sintetica.csv'),
             '--output', saida, '--dicionario', DIC, '--safra', '2022',
             '--sem-setor', '--qa-mode', 'exploratory'],
            capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-1200:]
        selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
        ids.append(selo)
    assert ids[0]['run_id'] != ids[1]['run_id'], (
        f'RUN_ID repetido entre execucoes: {ids[0]["run_id"]}')
    assert ids[0]['run_fingerprint'] == ids[1]['run_fingerprint'], (
        'mesma entrada e mesma configuracao deveriam dar o MESMO fingerprint')


def test_qa32_der_cobre_todos_os_identificadores(rcc):
    """Identificador estrutural não se audita por amostra.

    A versão anterior rederivava só os 5.000 primeiros endereços distintos.
    Com 6.001 endereços e o erro no 5.501, o artefato era SELADO — inclusive
    com o modelo carregando o mesmo erro, que é justamente o cenário do R14.
    """
    n = 6001
    d = pd.DataFrame({
        'COLETIVA_CHAVE_HASH': [str(qa._id_endereco_rederivado(
            4300604, 'RUA TESTE', i, 'CENTRO')) for i in range(1, n + 1)],
        'END_MUNICIPIO': ['4300604'] * n,
        'END_LOGRADOURO': ['RUA TESTE'] * n,
        'END_NUMERO': [str(i) for i in range(1, n + 1)],
        'END_LOCALIDADE': ['CENTRO'] * n,
    })
    alvo = 5500                                  # depois do antigo corte
    d.loc[alvo, 'COLETIVA_CHAVE_HASH'] = str(
        int(d.loc[alvo, 'COLETIVA_CHAVE_HASH']) + 128)
    res = qa.Resultado()
    qa._derivacao(d, res)
    assert _tem(res, 'QA-DER'), (
        'id corrompido na posicao 5.501 nao foi rederivado — auditoria por '
        'amostra deixa passar exatamente o que ela deveria pegar')


def test_qa33_manifest_reconcilia_todas_as_metricas(rcc):
    """`QA-MAN` conferia 3 campos; o manifest declara uma dúzia."""
    p = _copia(rcc, 'man2.csv')
    base = qa.manifest_observado(rcc['modelo'])
    for campo in ('com_atividade', 'grupos_coletivos', 'coletivas_distintas',
                  'unidades_em_coletiva'):
        assert campo in base, f'{campo} nao e derivado pelo auditor'
        ruim = dict(base)
        ruim[campo] = 999999
        res = _auditar(p, rcc['modelo'], rcc['colunas'], manifest=ruim)
        assert _tem(res, 'QA-MAN'), f'{campo} adulterado passou despercebido'


def test_qa34_xlsx_multiaba_nao_e_achatado(tmp_path):
    """Ler só a 1ª aba e regravar com `to_excel` destruiria o workbook."""
    p = str(tmp_path / 'wb.xlsx')
    with pd.ExcelWriter(p, engine='openpyxl') as w:
        pd.DataFrame({'A': [1, 2]}).to_excel(w, sheet_name='Um', index=False)
        pd.DataFrame({'B': [3]}).to_excel(w, sheet_name='Dois', index=False)
    abas = qa.abas_xlsx(p)
    assert set(abas) == {'Um', 'Dois'}
    lido = qa.reabrir(p)
    assert isinstance(lido, dict) and set(lido) == {'Um', 'Dois'}, (
        'reabrir() de XLSX precisa devolver TODAS as abas; ler so a primeira '
        'e depois regravar achataria o workbook em uma planilha')


# ══════════════════════════════════════════════════════════════════════════
#  v4.9.3 — auditoria do próprio auditor (2ª leva)
# ══════════════════════════════════════════════════════════════════════════
def test_qa35_fingerprint_distingue_fonte_set_e_runtime(tmp_path):
    """Duas fontes SET diferentes → resultados diferentes → fingerprints diferentes.

    Defeito real: o arquivo SET efetivamente usado não entrava no
    RUN_FINGERPRINT. Duas execuções com taxas de desocupação de 20% e 50%
    produziam artefatos distintos e o MESMO fingerprint — ou seja, o sistema
    declarava equivalentes duas execuções semanticamente diferentes.
    """
    import subprocess
    fps, arts = [], []
    for i, (v7, v8, v9) in enumerate([(90, 10, 20), (60, 20, 40)]):
        cache = str(tmp_path / f'cache{i}')
        os.makedirs(cache, exist_ok=True)
        fx.SETORES_FIXTURE[:] = [
            ('430060405000001', '4300604', 'M', 'C', v7 + v8 + v9, v7, v8, v9),
            ('355030805000001', '3550308', 'M', 'C', v7 + v8 + v9, v7, v8, v9),
        ]
        fx.cache_set_offline(cache)
        saida = str(tmp_path / f'fp{i}.csv')
        r = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
             '--input', os.path.join(HERE, 'base_sintetica.csv'),
             '--output', saida, '--dicionario', DIC, '--safra', '2022',
             '--qa-mode', 'exploratory', '--cache-ibge', cache],
            capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-1200:]
        selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
        fps.append(selo['run_fingerprint'])
        arts.append(selo['artifact_sha256'])
    assert arts[0] != arts[1], 'a fixture nao produziu artefatos diferentes'
    assert fps[0] != fps[1], (
        'fontes SET diferentes deram o MESMO run_fingerprint — o sistema '
        'declara equivalentes duas execucoes semanticamente distintas')


def test_qa36_substituicao_preserva_pacote_anterior(tmp_path, monkeypatch):
    """Publicar SOBRE uma entrega anterior é o caso não coberto.

    Com `rcc.csv` já publicado, uma falha no meio da substituição deixava o
    artefato ANTIGO no lugar e apagava os sidecars — artefato oficial sem
    linhagem, o estado que a arquitetura existe para impedir. Ou o pacote novo
    entra inteiro, ou o ANTIGO permanece inteiro.
    """
    destino = str(tmp_path / 'sub.csv')
    with qa.publicacao_de_pacote(destino, ['.manifest.json', '.seal.json']) as pk:
        open(pk[''], 'w').write('OLD')
        open(pk['.manifest.json'], 'w').write('{"v":"OLD"}')
        open(pk['.seal.json'], 'w').write('{"v":"OLD"}')
    assert open(destino).read() == 'OLD'

    real_replace = os.replace
    n = {'i': 0}

    def replace_falha(src, dst):
        n['i'] += 1
        if n['i'] == 3:
            raise OSError('falha na substituicao')
        return real_replace(src, dst)

    monkeypatch.setattr(os, 'replace', replace_falha)
    with pytest.raises(OSError):
        with qa.publicacao_de_pacote(destino, ['.manifest.json', '.seal.json']) as pk:
            open(pk[''], 'w').write('NEW')
            open(pk['.manifest.json'], 'w').write('{"v":"NEW"}')
            open(pk['.seal.json'], 'w').write('{"v":"NEW"}')
    monkeypatch.undo()

    for suf in ('', '.manifest.json', '.seal.json'):
        assert os.path.exists(destino + suf), (
            f'pacote ANTERIOR perdeu {suf!r} numa substituicao falha')
    conteudos = {open(destino + s).read() for s in
                 ('', '.manifest.json', '.seal.json')}
    assert all('OLD' in c for c in conteudos), (
        f'pacote ficou misturado entre execucoes: {conteudos}')


def test_qa37_quarentena_preserva_o_bundle(tmp_path):
    """Execução reprovada tem de ser tão forensicável quanto uma aprovada."""
    destino = str(tmp_path / 'q.csv')
    stage = str(tmp_path / 'stage')
    os.makedirs(stage)
    for nome, txt in (('q.csv', 'dados'), ('q.csv.manifest.json', '{"a":1}'),
                      ('q.csv.qa_issues.csv', 'x'), ('q.csv.seal.json', '{"s":1}')):
        open(os.path.join(stage, nome), 'w').write(txt)
    res = qa.Resultado(estado='QUARANTINED')
    pasta = qa.quarentenar_bundle(stage, destino, res, run_id='RUN-teste')
    presentes = set(os.listdir(pasta))
    for esperado in ('q.csv.manifest.json', 'q.csv.seal.json', 'q.csv.qa_issues.csv'):
        assert esperado in presentes, f'{esperado} nao preservado: {presentes}'
    assert any(f.endswith('.rejeitado') or f == 'q.csv' for f in presentes)


def test_qa38_der_cobre_os_ids_compostos(rcc):
    """`COLETIVA_ID`, `BLOCO_ID` e `UNIDADE_ID` também precisam ser rederivados.

    Cenário R14: adulterei `UNIDADE_ID` no artefato E no modelo esperado —
    produtor errado, artefato igualmente errado. A versão anterior selava,
    porque só `COLETIVA_CHAVE_HASH` era rederivado.
    """
    p = _copia(rcc, 'ids.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    d.loc[0, 'UNIDADE_ID'] = str(d.loc[0, 'UNIDADE_ID']) + 'X'
    _reescrever(p, d)
    modelo_ruim = rcc['modelo'].copy()
    modelo_ruim.loc[0, 'UNIDADE_ID'] = str(modelo_ruim.loc[0, 'UNIDADE_ID']) + 'X'
    res = _auditar(p, modelo_ruim, rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-DER'), \
        'UNIDADE_ID adulterado nos dois lados passou'


def test_qa39_manifest_reconcilia_inferidas_por_classe(rcc):
    base = qa.manifest_observado(rcc['modelo'])
    assert 'inferidas_por_classe' in base, 'metrica nao derivada pelo auditor'
    ruim = dict(base)
    ruim['inferidas_por_classe'] = {'X': 999999}
    res = _auditar(_copia(rcc, 'mc.csv'), rcc['modelo'], rcc['colunas'],
                   manifest=ruim)
    assert _tem(res, 'QA-MAN')


def test_qa40_verificar_pacote_cobre_todos_os_hashes(tmp_path):
    """`verificar_lacre` responde 'o CSV mudou?'. O lacre é do PACOTE."""
    import subprocess
    saida = str(tmp_path / 'pk.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', os.path.join(HERE, 'base_sintetica.csv'),
         '--output', saida, '--dicionario', DIC, '--safra', '2022',
         '--sem-setor', '--qa-mode', 'exploratory'],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-1200:]
    selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
    ok, falhas = qa.verificar_pacote(saida, selo)
    assert ok, f'pacote integro reprovado: {falhas}'
    with open(saida + '.manifest.json', 'a', encoding='utf-8') as fh:
        fh.write(' ')
    assert qa.verificar_artefato(saida, selo), 'o CSV nao mudou'
    ok2, falhas2 = qa.verificar_pacote(saida, selo)
    assert not ok2 and 'manifest_sha256' in falhas2, (
        'manifest alterado apos o lacre passou por verificar_pacote')


def test_qa41_versao_do_qa_acompanha_o_contrato():
    assert qa.VERSAO_QA != '1.0', (
        'o auditor mudou de contrato (derivacao 100%, manifest completo, '
        'publicacao, reader XLSX) e a versao ficou parada — versao que nao '
        'versiona nao serve para forense')


# ══════════════════════════════════════════════════════════════════════════
#  v4.9.4 — terceira leva
# ══════════════════════════════════════════════════════════════════════════
def test_qa42_fingerprint_captura_set_adquirido_na_execucao(tmp_path):
    """O SHA do SET era lido ANTES de `baixar_basico()`.

    Com o cache vazio, `_set_sha` saía '' e a fonte efetivamente adquirida
    naquela execução não entrava no fingerprint. Duas fontes criadas só no
    momento da aquisição davam artefatos diferentes e o mesmo fingerprint.
    """
    import subprocess
    # O cache NASCE VAZIO e é preenchido por um "downloader" que só roda DENTRO
    # da execução do emissor — que é a única forma de exercitar a aquisição em
    # tempo de execução. Preencher o cache antes de chamar o CLI testava o
    # caminho de cache quente, onde o defeito não aparece.
    plugin = tmp_path / 'aquisicao_falsa.py'
    fps, arts, shas = [], [], []
    for i, (v7, v8, v9) in enumerate([(90, 10, 20), (55, 25, 40)]):
        cache = tmp_path / f'novo{i}'           # NASCE VAZIO
        plugin.write_text(
            'import os, sys\n'
            f'sys.path.insert(0, {HERE!r})\n'
            f'sys.path.insert(0, {SCRIPTS!r})\n'
            'import cnefe_fixture as fx\n'
            'import ibge_setor_ocupacao as xs\n'
            f'fx.SETORES_FIXTURE[:] = [\n'
            f"    ('430060405000001','4300604','M','C',{v7+v8+v9},{v7},{v8},{v9}),\n"
            f"    ('355030805000001','3550308','M','C',{v7+v8+v9},{v7},{v8},{v9}),\n"
            f']\n'
            '_orig = xs.baixar_basico\n'
            'def _fake(cache_dir, timeout=300):\n'
            '    import pathlib\n'
            '    alvo = pathlib.Path(cache_dir) / "Agregados_por_setores_basico_BR.csv"\n'
            '    if not alvo.exists():\n'
            '        fx.cache_set_offline(str(cache_dir))\n'
            '    return alvo\n'
            'xs.baixar_basico = _fake\n'
            'sys.argv = sys.argv[1:]\n'
            'exec(open(sys.argv[0], encoding="utf-8").read(), '
            '{"__name__": "__main__", "__file__": sys.argv[0]})\n',
            encoding='utf-8')
        saida = str(tmp_path / f'nv{i}.csv')
        r = subprocess.run(
            [sys.executable, str(plugin),
             os.path.join(SCRIPTS, 'rcc_emissor.py'),
             '--input', os.path.join(HERE, 'base_sintetica.csv'),
             '--output', saida, '--dicionario', DIC, '--safra', '2022',
             '--qa-mode', 'exploratory', '--cache-ibge', str(cache)],
            capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-800:]
        assert (cache / 'Agregados_por_setores_basico_BR.csv').exists(), \
            'a fonte SET nao foi adquirida DURANTE a execucao'
        selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
        fps.append(selo['run_fingerprint']); arts.append(selo['artifact_sha256'])
        shas.append(selo['fonte_set_sha256'])
    assert all(shas), f'fonte_set_sha256 vazio: {shas}'
    assert arts[0] != arts[1], 'fontes SET distintas deram o mesmo artefato'
    assert fps[0] != fps[1], (
        'fontes SET distintas deram o MESMO run_fingerprint — a fonte '
        'adquirida na execucao ficou fora da impressao digital')


def test_qa43_unidade_id_rederiva_a_tag(rcc):
    """`...-LOJ-0001` → `...-XXX-0001` passava: a TAG não era derivada."""
    p = _copia(rcc, 'tag.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    alvo = None
    for i, u in d['UNIDADE_ID'].items():
        partes = str(u).split('-')
        if len(partes) >= 4 and partes[-2] not in ('REG',):
            alvo = i; break
    if alvo is None:
        pytest.skip('fixture sem UNIDADE_ID com TAG tipada')
    orig = str(d.loc[alvo, 'UNIDADE_ID'])
    partes = orig.split('-'); partes[-2] = 'XXX'
    d.loc[alvo, 'UNIDADE_ID'] = '-'.join(partes)
    _reescrever(p, d)
    modelo_ruim = rcc['modelo'].copy()
    modelo_ruim.loc[alvo, 'UNIDADE_ID'] = '-'.join(partes)
    res = _auditar(p, modelo_ruim, rcc['colunas'])
    assert res.estado == 'QUARANTINED' and _tem(res, 'QA-DER'), \
        f'TAG adulterada em {orig} passou'


def test_qa44_coletiva_id_validado_contra_o_store(rcc, tmp_path):
    """Trocar duas COLETIVA_ID entre si mantém tudo internamente coerente.

    Sem confrontar o STORE — a fonte da identidade histórica — o auditor não
    tem como saber que o hash X deveria ser COL-...-000001 e não 000002.
    """
    p = _copia(rcc, 'store.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    par = d[d['COLETIVA_CHAVE_HASH'].fillna('').ne('')][
        ['COLETIVA_ID', 'COLETIVA_CHAVE_HASH']].drop_duplicates()
    if len(par) < 2:
        pytest.skip('fixture com menos de duas coletivas')
    store = str(tmp_path / 'store.csv')
    par.to_csv(store, sep=';', index=False, encoding='utf-8')

    a, b = par.iloc[0], par.iloc[1]
    troca = {a.COLETIVA_ID: b.COLETIVA_ID, b.COLETIVA_ID: a.COLETIVA_ID}
    d['COLETIVA_ID'] = d['COLETIVA_ID'].map(lambda v: troca.get(v, v))
    _reescrever(p, d)
    res = qa.auditar(p, colunas=rcc['colunas'], contrato=qa.contrato_rcc(DIC),
                     store=store, reconciliar_modelo=False)
    assert _tem(res, 'QA-DER-005'), (
        'COLETIVA_ID trocado entre coletivas passou — identidade historica '
        'nao foi confrontada com o store')


def test_qa45_verificar_pacote_e_fail_closed(tmp_path):
    """Hash obrigatório AUSENTE do selo tem de invalidar o lacre."""
    import subprocess
    saida = str(tmp_path / 'fc.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', os.path.join(HERE, 'base_sintetica.csv'),
         '--output', saida, '--dicionario', DIC, '--safra', '2022',
         '--sem-setor', '--qa-mode', 'exploratory'],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-1200:]
    selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
    ok, _ = qa.verificar_pacote(saida, selo)
    assert ok
    for chave in ('manifest_sha256', 'qa_report_sha256', 'run_fingerprint'):
        mutilado = {k: v for k, v in selo.items() if k != chave}
        ok2, falhas = qa.verificar_pacote(saida, mutilado)
        assert not ok2 and any('AUSENTE' in f or chave in f for f in falhas), (
            f'selo sem {chave} continuou valido — lacre fail-open')


def test_qa46_contrato_faz_parte_do_pacote(tmp_path):
    """`contract_sha256` só é prova se o contrato viajar com o pacote."""
    import subprocess
    saida = str(tmp_path / 'ct.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', os.path.join(HERE, 'base_sintetica.csv'),
         '--output', saida, '--dicionario', DIC, '--safra', '2022',
         '--sem-setor', '--qa-mode', 'exploratory'],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-1200:]
    assert os.path.exists(saida + '.contract.csv'), 'contrato nao publicado'
    selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
    ok, falhas = qa.verificar_pacote(saida, selo)
    assert ok, falhas
    with open(saida + '.contract.csv', 'a', encoding='utf-8') as fh:
        fh.write('X;X;X\n')
    ok2, falhas2 = qa.verificar_pacote(saida, selo)
    assert not ok2 and 'contract_sha256' in falhas2


def test_qa47_bloco_set_reconciliado(rcc):
    base = qa.manifest_observado(rcc['modelo'])
    assert 'bloco_set' in base, 'metricas SET nao derivadas do artefato'
    ruim = dict(base)
    ruim['bloco_set'] = dict(base['bloco_set']); ruim['bloco_set']['pct_casado'] = 999999
    res = _auditar(_copia(rcc, 'bs.csv'), rcc['modelo'], rcc['colunas'],
                   manifest=ruim)
    assert _tem(res, 'QA-MAN')

def test_qa55_equivalencia_fonetica_e_marcada_nao_aplicada():
    """`AYRTON SENNA` e `AIRTON SENA`: marca, não funde.

    A fonética acerta a grafia e erra o ordinal — em POA ela fundiria `CEFER I`
    com `CEFER II`, que são ruas distintas. Então ela entra como EVIDÊNCIA
    graduada e o id não a enxerga. É o mesmo desenho das hipóteses de unidade:
    nada é descartado, nada é aplicado sozinho.
    """
    linhas = ([fx.apto('AYRTON SENNA', 100, str(v)) for v in (101, 102)]
              + [fx.apto('AIRTON SENA', 100, str(v)) for v in (201, 202)])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-f', 'IBGE', '2022', 'h', cols), d)
    assert out['COLETIVA_CHAVE_HASH'].nunique() == 2, \
        'a fonetica encostou no identificador — devia so marcar'
    sug = out['END_LOGR_EQUIV_SUGERIDA'].fillna('').astype(str)
    assert sug.str.strip().ne('').any(), 'equivalencia fonetica nao foi marcada'
    grau = set(out.loc[sug.ne(''), 'END_LOGR_EQUIV_GRAU'])
    assert grau <= set(xu.GRAUS_INFERENCIA), f'grau fora da regua R9: {grau}'
    assert 'SUSTENTADA' in grau, (
        'mesma coordenada e mesma numeracao deviam sustentar a equivalencia')


def test_qa56_ordinal_romano_nao_e_fundido_pela_marcacao():
    """`CEFER I` e `CEFER II` continuam duas ruas — inclusive na marcação."""
    linhas = ([fx.apto('CEFER I', 100, str(v)) for v in (101, 102)]
              + [fx.apto('CEFER II', 200, str(v), dlat=0.01) for v in (101, 102)])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-c', 'IBGE', '2022', 'h', cols), d)
    assert out['COLETIVA_CHAVE_HASH'].nunique() == 2
    g = set(out['END_LOGR_EQUIV_GRAU'].fillna('').astype(str)) - {''}
    assert 'SUSTENTADA' not in g, (
        'CEFER I e CEFER II marcados como equivalentes SUSTENTADOS — a '
        'fonetica come o ordinal e nao pode ser tratada como prova')


def test_qa53_g20_colisao_de_logradouro_e_medida_e_reconciliada(rcc):
    """O fator de colisão vira INSTRUMENTO, não medição de ocasião.

    Hoje, para decidir se largar o título do logradouro era seguro, eu medi à
    mão quantos endereços distintos a canônica funde. Uma decisão dessas volta
    a cada mudança de canonicalização — e sem instrumento fixo ela volta como
    argumento, não como número.
    """
    obs = qa.manifest_observado(rcc['modelo'])
    assert 'colisao_logradouro' in obs, 'fator de colisao nao derivado do artefato'
    c = obs['colisao_logradouro']
    for k in ('formas_distintas', 'canonicas_distintas', 'fator', 'grupos_fundidos'):
        assert k in c, f'colisao_logradouro sem {k}'
    ruim = dict(obs); ruim['colisao_logradouro'] = dict(c)
    ruim['colisao_logradouro']['fator'] = 9.99
    res = _auditar(_copia(rcc, 'col.csv'), rcc['modelo'], rcc['colunas'],
                   manifest=ruim)
    assert _tem(res, 'QA-MAN'), 'fator de colisao declarado errado passou'


def test_qa54_tipo_de_via_fora_do_vocabulario_e_apontado_nunca_corrigido(rcc):
    """Vocabulário canônico de tipos é VALIDAÇÃO, não corretor.

    Porto Alegre trouxe 10 tipos que as 7 UFs auditadas na skill de logradouro
    não tinham (CONJUNTO HABITACIONAL, RUA DE PEDESTRE, PONTE, TRILHA...).
    Isso mostra as duas coisas: o vocabulário serve para APONTAR o que é novo,
    e jamais para reescrever o que o IBGE gravou.
    """
    import radar_utils as ru
    assert 'CONJUNTO HABITACIONAL' in ru.LOGR_TIPOS, \
        'vocabulario nao incorporou o que POA revelou'
    p = _copia(rcc, 'tipo.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    i = d.index[d['END_LOGRADOURO'].fillna('').ne('')][0]
    d.loc[i, 'END_LOGRADOURO'] = 'ZORGLUB ' + str(d.loc[i, 'END_LOGRADOURO'])
    _reescrever(p, d)
    res = qa.auditar(p, colunas=rcc['colunas'], contrato=qa.contrato_rcc(DIC),
                     reconciliar_modelo=False, modo='exploratory')
    assert _tem(res, 'QA-END-010'), 'tipo de via desconhecido nao foi apontado'
    assert not any(i_.severidade == 'FATAL' and i_.regra == 'QA-END-010'
                   for i_ in res.issues), \
        'tipo desconhecido nao pode ser FATAL: vocabulario incompleto e o '\
        'estado normal, e o IBGE e a fonte'


def test_qa52_crosswalk_entre_versoes_viaja_lacrado(tmp_path):
    """Mudar a canônica sem ponte mata a série histórica em silêncio."""
    import subprocess
    saida = str(tmp_path / 'cw.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', os.path.join(HERE, 'base_sintetica.csv'),
         '--output', saida, '--dicionario', DIC, '--safra', '2022',
         '--sem-setor', '--qa-mode', 'exploratory'],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-1500:]
    cw_path = saida + '.crosswalk_canon.csv'
    assert os.path.exists(cw_path), 'ponte para a versao anterior nao publicada'
    cw = pd.read_csv(cw_path, sep=';', dtype=str, encoding='utf-8-sig')
    for c in ('COLETIVA_CHAVE_HASH_ANTERIOR', 'COLETIVA_CHAVE_CANONICA_ANTERIOR',
              'COLETIVA_CHAVE_HASH', 'COLETIVA_CHAVE_CANONICA', 'MUDOU',
              'CANON_VERSAO_DE', 'CANON_VERSAO_PARA'):
        assert c in cw.columns, f'ponte sem {c}'
    art = pd.read_csv(saida, sep=';', dtype=str, encoding='utf-8-sig',
                      usecols=['COLETIVA_CHAVE_HASH'])
    hashes = set(art['COLETIVA_CHAVE_HASH'].fillna('').str.strip()) - {''}
    assert hashes <= set(cw['COLETIVA_CHAVE_HASH'].str.strip()), \
        'endereco do artefato sem linha na ponte — id novo sem origem'
    # o lacre cobre a ponte: alterar a ponte depois do selo invalida o pacote
    selo = json.load(open(saida + '.seal.json', encoding='utf-8'))
    assert selo.get('canon_versao'), 'selo sem canon_versao'
    ok, _ = qa.verificar_pacote(saida, selo)
    assert ok
    with open(cw_path, 'a', encoding='utf-8') as fh:
        fh.write('X;X;X;X;X;X;X\n')
    ok2, falhas = qa.verificar_pacote(saida, selo)
    assert not ok2 and 'crosswalk_sha256' in falhas


def test_qa48_canonica_adulterada_quebra_a_derivacao(rcc):
    """Publicar a pré-imagem só vale se ela for CONFERIDA contra o hash."""
    p = _copia(rcc, 'canon.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    assert 'COLETIVA_CHAVE_CANONICA' in d.columns, \
        'a chave canonica nao esta publicada — o id nao e reproduzivel'
    idx = d.index[d['COLETIVA_CHAVE_CANONICA'].fillna('').ne('')]
    assert len(idx), 'fixture sem chave canonica preenchida'
    orig = str(d.loc[idx[0], 'COLETIVA_CHAVE_CANONICA'])
    d.loc[idx[0], 'COLETIVA_CHAVE_CANONICA'] = orig.replace('|N', '|N9')
    _reescrever(p, d)
    res = qa.auditar(p, colunas=rcc['colunas'], contrato=qa.contrato_rcc(DIC),
                     reconciliar_modelo=False)
    assert _tem(res, 'QA-DER-001'), 'canonica trocada e hash intacto passou'


def test_qa49_canonica_coerente_mas_de_outro_endereco(rcc):
    """Forjar canônica E hash juntos: coerentes entre si, falsos no endereço.

    Sem a amarração com `END_*`, publicar a pré-imagem apenas moveria a
    confiança de lugar — bastaria escrever qualquer string e o hash dela.
    """
    import hashlib as _h
    p = _copia(rcc, 'canon2.csv')
    d = pd.read_csv(p, sep=';', dtype=str, encoding='utf-8-sig')
    idx = d.index[d['COLETIVA_CHAVE_CANONICA'].fillna('').ne('')]
    if not len(idx):
        pytest.skip('fixture sem chave canonica')
    forjada = '9999999|RUA INEXISTENTE|N7|OUTRO BAIRRO'
    dig = _h.blake2b(forjada.encode('utf-8'), digest_size=8).digest()
    d.loc[idx[0], 'COLETIVA_CHAVE_CANONICA'] = forjada
    d.loc[idx[0], 'COLETIVA_CHAVE_HASH'] = str(int.from_bytes(dig, 'big') >> 1)
    _reescrever(p, d)
    res = qa.auditar(p, colunas=rcc['colunas'], contrato=qa.contrato_rcc(DIC),
                     reconciliar_modelo=False)
    assert _tem(res, 'QA-DER-006'), (
        'canonica internamente coerente mas de OUTRO endereco passou')


def test_qa50_endereco_com_titulo_e_reproduzivel_por_terceiro(tmp_path):
    """O caso de Porto Alegre: 23% das linhas têm título de logradouro.

    A canônica não leva o título e usa o nome harmonizado; `END_LOGRADOURO`
    leva. Quem recebe o arquivo tem de conseguir refazer o hash mesmo assim —
    eram 53.394 ids irreproduzíveis em POA.
    """
    import hashlib as _h
    linhas = [fx.linha(logr='VICENTE', titulo='CORONEL', num=529,
                       comps=[('APARTAMENTO', str(v))])
              for v in (101, 102, 201, 202)]
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-t', 'IBGE', '2022', 'h', cols), d)
    can = out['COLETIVA_CHAVE_CANONICA'].astype(str)
    assert can.str.strip().ne('').all(), 'linha sem pre-imagem publicada'
    assert (out['END_LOGRADOURO'].str.contains('CORONEL')).all(), \
        'a fixture nao reproduz o caso do titulo'
    for c, h in zip(can, out['COLETIVA_CHAVE_HASH'].astype(str)):
        esperado = int.from_bytes(
            _h.blake2b(c.encode('utf-8'), digest_size=8).digest(), 'big') >> 1
        assert str(esperado) == h.strip(), (
            f'terceiro nao reproduz o id a partir do arquivo: {c} -> {esperado} '
            f'!= {h}')


def test_qa51_logradouro_e_unico_no_grupo(tmp_path):
    """Um COLETIVA_ID com dois nomes de rua não é um endereço.

    'AVENIDA DOUTOR X 363' e 'AVENIDA X 363' compartilham o id (a canônica não
    leva título) e saíam com END_LOGRADOURO divergente DENTRO do grupo — 10
    grupos em Porto Alegre.
    """
    linhas = ([fx.linha(logr='PANATIERI', titulo='DOUTOR', num=363, tipo='AVENIDA',
                        cep='90000001', comps=[('APARTAMENTO', str(v))])
               for v in (101, 102)]
              + [fx.linha(logr='PANATIERI', titulo='', num=363, tipo='AVENIDA',
                          cep='90000002', comps=[('APARTAMENTO', str(v))])
                 for v in (201, 202)])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-g', 'IBGE', '2022', 'h', cols), d)
    com = out[out['COLETIVA_ID'].fillna('').astype(str).ne('')]
    n = com.groupby('COLETIVA_ID')['END_LOGRADOURO'].nunique()
    assert (n <= 1).all(), (
        f'grupo com mais de um logradouro: {n[n > 1].to_dict()}')
    # e a grafia de cada registro continua preservada
    assert com['END_LOGRADOURO_ORIG'].nunique() >= 2, \
        'a grafia original de cada registro foi perdida na resolucao do grupo'


# ══════════════════════════════════════════════════════════════════════════
#  R14 — o auditor não pode chamar a função que produziu o campo
# ══════════════════════════════════════════════════════════════════════════
def test_qa23_auditor_nao_importa_o_produtor():
    """Se o auditor chamar o produtor, os dois concordam no mesmo erro."""
    src = open(os.path.join(SCRIPTS, 'qualidade_artefato.py'), encoding='utf-8').read()
    for proibido in ('import rcc_emissor', 'from rcc_emissor',
                     'import radar_utils', 'from radar_utils',
                     'import ibge_setor_ocupacao', 'from ibge_setor_ocupacao'):
        assert proibido not in src, (
            f'auditor importa o produtor ({proibido}) — R14 exige recalculo '
            'independente, senao produtor e auditor confirmam o mesmo defeito')


# ══════════════════════════════════════════════════════════════════════════
#  LÉXICO AUTO-INCREMENTAL — aprende do dado, e só marca
# ══════════════════════════════════════════════════════════════════════════
def _emitir_com_lexico(linhas, lexico, tmp_path, nome):
    import subprocess
    csv_in = str(tmp_path / f'{nome}.csv')
    fx.csv_cnefe(linhas, destino=csv_in)
    saida = str(tmp_path / f'{nome}_rcc.csv')
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, 'rcc_emissor.py'),
         '--input', csv_in, '--output', saida, '--dicionario', DIC,
         '--safra', '2022', '--sem-setor', '--qa-mode', 'exploratory',
         '--lexico', lexico],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-600:]
    return pd.read_csv(saida, sep=';', dtype=str, encoding='utf-8-sig')


def _par_provado(nome_a, nome_b, num, dlat=0.0):
    """Mesmo número, mesma coordenada, um token trocado — prova do IBGE."""
    return ([fx.linha(logr=nome_a, num=num, dlat=dlat,
                      comps=[('APARTAMENTO', '101')])]
            + [fx.linha(logr=nome_b, num=num, dlat=dlat,
                        comps=[('APARTAMENTO', '201')])])


def test_qa57_lexico_promove_so_com_duas_provas(tmp_path):
    """support 1 é coincidência; support 2 em imóveis distintos é padrão."""
    lexico = str(tmp_path / 'lex.json')
    _emitir_com_lexico(_par_provado('CONS VICENTE', 'CONSELHEIRO VICENTE', 100),
                       lexico, tmp_path, 'a')
    lex = json.load(open(lexico, encoding='utf-8'))
    assert 'CONS' in lex['equiv'], 'par provado nao virou candidato'
    assert lex['equiv']['CONS']['status'] == 'candidato', \
        'uma prova so nao pode promover — seria aprender de coincidencia'

    _emitir_com_lexico(_par_provado('CONS VICENTE', 'CONSELHEIRO VICENTE', 100)
                       + _par_provado('CONS BENTO', 'CONSELHEIRO BENTO', 500,
                                      dlat=0.02),
                       lexico, tmp_path, 'b')
    lex = json.load(open(lexico, encoding='utf-8'))
    assert lex['equiv']['CONS']['status'] == 'ativo', \
        f"segunda prova nao promoveu: {lex['equiv']['CONS']}"


def test_qa58_lexico_nunca_aprende_adicao_de_token(tmp_path):
    """`SANTOS` ⊂ `SANTOS DUMONT` na mesma esquina não é abreviação."""
    lexico = str(tmp_path / 'lex2.json')
    _emitir_com_lexico(_par_provado('SANTOS', 'SANTOS DUMONT', 100),
                       lexico, tmp_path, 'c')
    lex = json.load(open(lexico, encoding='utf-8'))
    assert not lex['equiv'], f'aprendeu adicao de token: {lex["equiv"]}'


def test_qa59_lexico_aprendido_marca_mas_nao_muda_o_id(tmp_path):
    """O que o léxico aprende chega na MARCAÇÃO e nunca no identificador."""
    lexico = str(tmp_path / 'lex3.json')
    base = (_par_provado('CONS VICENTE', 'CONSELHEIRO VICENTE', 100)
            + _par_provado('CONS BENTO', 'CONSELHEIRO BENTO', 500, dlat=0.02))
    d1 = _emitir_com_lexico(base, lexico, tmp_path, 'd')
    ids1 = set(d1['COLETIVA_CHAVE_HASH'].fillna(''))
    d2 = _emitir_com_lexico(base, lexico, tmp_path, 'e')   # com léxico ativo
    assert set(d2['COLETIVA_CHAVE_HASH'].fillna('')) == ids1, \
        'o lexico mexeu no identificador — safra renumerada por aprendizado'
    assert d2['END_LOGR_EQUIV_GRAU'].fillna('').str.strip().ne('').any(), \
        'o lexico ativo nao produziu marcacao nenhuma'


def test_qa60_lexico_entra_no_fingerprint(tmp_path):
    """Duas execuções com léxicos diferentes não são equivalentes."""
    base = _par_provado('CONS VICENTE', 'CONSELHEIRO VICENTE', 100)
    fps = []
    for i, conteudo in enumerate([
            {'versao': '1.0.0', 'equiv': {}, 'quarentena': {}, 'blacklist': []},
            {'versao': '1.0.0', 'equiv': {'CONS': {
                'canonico': 'CONSELHEIRO', 'status': 'ativo', 'support': 9,
                'score': 9.0, 'provas': ['x'] * 9}},
             'quarentena': {}, 'blacklist': []}]):
        lexico = str(tmp_path / f'fp{i}.json')
        with open(lexico, 'w', encoding='utf-8') as fh:
            json.dump(conteudo, fh)
        _emitir_com_lexico(base, lexico, tmp_path, f'f{i}')
        selo = json.load(open(str(tmp_path / f'f{i}_rcc.csv.seal.json'),
                              encoding='utf-8'))
        fps.append(selo['run_fingerprint'])
    assert fps[0] != fps[1], (
        'lexicos distintos deram o MESMO run_fingerprint — o lacre declara '
        'equivalentes duas execucoes que marcam coisas diferentes')


def test_qa61_logradouro_tratado_cumpre_o_contrato_do_numeral():
    """O dicionário promete `END_LOGRADOURO` com "numeral expandido".

    A canônica v2 passou a expandir o numeral e o campo tratado ficou para
    trás: a canônica dizia `10 DE MAIO` e a linha publicava `RUA DEZ DE MAIO`.
    O auditor pegou em Porto Alegre — 10.147 linhas — ANTES da publicação, que
    é exatamente para isso que o ciclo existe. Campo tratado que não cumpre o
    que o contrato dele diz é defeito do produtor, não régua errada do QA.
    """
    linhas = [fx.apto('DEZ DE MAIO', 100, str(v)) for v in (101, 102)]
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-n', 'IBGE', '2022', 'h', cols), d)
    assert (out['END_LOGRADOURO'].str.contains('10 DE MAIO')).all(), \
        f"campo tratado sem numeral expandido: {out['END_LOGRADOURO'].iloc[0]}"
    assert (out['END_LOGRADOURO_ORIG'].str.contains('DEZ DE MAIO')).all(), \
        'a grafia original foi perdida — o par de auditoria e obrigatorio'


def test_qa62_aviso_de_vocabulario_nao_reprova_em_strict(tmp_path):
    """Um aviso que reprova a entrega não é um aviso.

    Desenhei `QA-END-010` como WARN "que nunca bloqueia" — e em `strict` todo
    WARN vira quarentena, então ele bloqueava. Porto Alegre reprovou por dois
    tipos de via (`ESCADA`, `TRAVESSIA`) que faltavam na NOSSA lista. Quem é a
    fonte é o IBGE; a lista incompleta é problema nosso.
    """
    linhas = [fx.linha(logr='DO MEIO', num=100, tipo='ZORGLUB',
                       comps=[('APARTAMENTO', str(v))]) for v in (101, 102)]
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-v', 'IBGE', '2022', 'h', cols), d)
    p = str(tmp_path / 'voc.csv')
    out.to_csv(p, sep=';', index=False, encoding='utf-8-sig')
    res = qa.auditar(p, colunas=cols, contrato=qa.contrato_rcc(DIC),
                     reconciliar_modelo=False, modo='strict')
    assert _tem(res, 'QA-END-010'), 'tipo de via desconhecido nao foi apontado'
    assert res.estado in ('SEALED', 'SEALED_WITH_WARNINGS'), (
        f'tipo de via desconhecido reprovou a entrega em strict: {res.estado}')


def test_qa63_lexico_nunca_aprende_irmao_enumerado():
    """`RUA 2` e `RUA 3` não são a mesma rua escrita de dois jeitos.

    Achado rodando em Porto Alegre: o léxico promoveu `2→3` (support 99),
    `B→C` (45), `R3→R4` (36), `01→02` (33). São ruas IRMÃS de loteamento —
    paralelas, mesmo número de casa, a 30 m uma da outra. O gate de
    plausibilidade aceitava porque edição ≤ 1 é verdade para qualquer par de
    ordinais consecutivos.

    A regra que fecha isso não é lista negra: um token cuja FUNÇÃO é enumerar
    (dígito puro, letra sozinha, letra+dígito) existe justamente para
    distinguir irmãos. Ele nunca é abreviação do vizinho.
    """
    import lexico_logradouro as lx
    for a, b in (('2', '3'), ('01', '02'), ('B', 'C'), ('R3', 'R4'),
                 ('S2', 'S3'), ('B1', 'B2'), ('I', 'II')):
        assert not lx.plausivel(a, b, xu.fonetica_token), \
            f'{a}->{b}: irmao enumerado aprendido como equivalencia'
    # e o que É abreviação continua passando
    for a, b in (('CONS', 'CONSELHEIRO'), ('SOUSA', 'SOUZA'),
                 ('EXP', 'EXPEDICIONARIOS')):
        assert lx.plausivel(a, b, xu.fonetica_token), \
            f'{a}->{b}: abreviacao legitima recusada'


def test_qa64_irmas_de_loteamento_nao_sao_marcadas_como_equivalentes():
    """`ACESSO S` e `ACESSO Z` são vias irmãs, não grafias.

    A chave fonética mapeia Z→S e as duas viravam a mesma chave: em Porto
    Alegre saíram marcadas como SUSTENTADAS. Mesmo defeito que o léxico teve
    com `2→3`, no outro canal — e a mesma regra fecha os dois: enumerador não
    é fonetizado.
    """
    assert xu.chave_equivalencia('ACESSO S') != xu.chave_equivalencia('ACESSO Z')
    assert xu.chave_equivalencia('RUA 2') != xu.chave_equivalencia('RUA 3')
    assert xu.chave_equivalencia('RUA B') != xu.chave_equivalencia('RUA C')
    # e o que É equivalente continua colapsando
    assert (xu.chave_equivalencia('RUA XV DE NOVEMBRO')
            == xu.chave_equivalencia('RUA 15 DE NOVEMBRO'))
    assert (xu.chave_equivalencia('RUA AYRTON SENNA')
            == xu.chave_equivalencia('RUA AIRTON SENA'))


def test_qa65_marcacao_nao_sugere_a_rua_para_ela_mesma():
    """Mesma rua em números diferentes não gera sugestão de equivalência."""
    linhas = ([fx.apto('UNICA', 100, str(v)) for v in (101, 102)]
              + [fx.apto('UNICA', 200, str(v)) for v in (101, 102)])
    df, _ = rp.preparar_base(fx.csv_cnefe(linhas))
    cols = _colunas()
    d = R.emitir(df, safra='2022')
    out = R.pos_agregados(R.montar_saida(d, 'RUN-s', 'IBGE', '2022', 'h', cols), d)
    sug = out['END_LOGR_EQUIV_SUGERIDA'].fillna('').astype(str).str.strip()
    assert sug.eq('').all(), (
        f'a rua foi sugerida para ela mesma: {sorted(set(sug))}')


# ══════════════════════════════════════════════════════════════════════════
#  GOLDEN — trava distributiva sobre artefato real
# ══════════════════════════════════════════════════════════════════════════
def test_qa66_golden_detecta_deriva_distributiva(rcc, tmp_path):
    """Golden que não trava nada é comentário com nome de teste.

    Canoas e Santa Maria estavam declaradas como golden e, duas linhas abaixo,
    como "precisam de re-baseline antes de voltarem a travar regressão". A
    suíte sintética prova invariante; ela não vê uma heurística que muda de
    escala. Aqui a baseline é gravada de um artefato real e a deriva reprova.
    """
    import golden
    art = _copia(rcc, 'gold.csv')
    ref = str(tmp_path / 'base.json')
    golden.gravar(art, ref)
    assert not golden.comparar(art, ref), 'baseline reprovou a si mesma'

    d = pd.read_csv(art, sep=';', dtype=str, encoding='utf-8-sig')
    idx = d.index[d['UND_NATUREZA'] == 'INFERIDO']
    if not len(idx):
        pytest.skip('fixture sem hipotese')
    # uma hipótese vira observação: contagem estrutural muda, tolerância zero
    d.loc[idx[0], 'UND_NATUREZA'] = 'OBSERVADO'
    _reescrever(art, d)
    falhas = golden.comparar(art, ref)
    assert falhas, 'deriva distributiva passou pelo golden'
    assert any('observadas' in f or 'inferidas' in f or 'por_grau' in f
               for f in falhas), falhas
