# -*- coding: utf-8 -*-
"""A captura e o iFood rodam no i9, com o código de lá igual ao daqui.

POR QUE MUDOU DE MÁQUINA

O notebook é onde o operador trabalha, e a captura sobe dez navegadores reais.
Mas o que decidiu foi outra coisa: **a API da Webshare não responde do
notebook**. Medido 26/08/2026 — `urlopen error timed out` de lá, 1,1 s do i9.
Sem ela o pool cai num cache em disco e trabalha sobre a lista de ontem.

O QUE ESTA MUDANÇA CUSTOU DESCOBRIR

Três defeitos em sequência, e nenhum dizia o que era:

1. `chromium-1217` de LINUX carrega a Maps JS inteira (todas as requisições
   200) e nunca dispara `idle`. Os workers desistiam, a captura terminava
   dizendo "concluída" com 0 tiles e o OCR seguia com 0 recortes — sucesso
   relatado sobre nada. Mesma máquina, mesmo minuto: 1217 não inicializou em
   45,1 s, 1234 ficou pronto em 1,8 s. Não é a versão do pacote: o notebook roda
   o MESMO playwright 1.59.1 com o MESMO 1217 e captura bem, no build de
   Windows.

2. A lista de arquivos a sincronizar era digitada à mão e tinha
   `src/capture-cli.ts` — a porta de entrada — mas não `src/capture.ts`, que é
   quem abre o navegador. Corrigi o `capture.ts`, mandei sincronizar, o módulo
   respondeu "os 15 arquivos já estão iguais" e a captura falhou de novo com o
   mesmo erro.

3. `cod` só era atribuído dentro do `else` das bases públicas, então
   `--pular-bases` derrubava a etapa 6 com `UnboundLocalError` DEPOIS de a
   captura já ter rodado.
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


# ─── qual chromium ───────────────────────────────────────────────────────────

def test_o_chromium_da_captura_e_escolhivel():
    """Sem o override, o Linux pega o 1217 e a captura devolve 0 tiles em
    silêncio — o pior tipo de falha, porque parece área sem comércio."""
    s = _ler("src/capture.ts")
    assert "CAPTURE_CHROME" in s, "o override do executável sumiu"
    assert "executablePath" in s, "o launch voltou a aceitar só o padrão"


def test_o_motivo_do_override_esta_escrito():
    """Um `executablePath` sem explicação parece sobra de depuração e é
    removido na primeira limpeza — e o custo reaparece como 'a área não tem
    comércio'."""
    s = _ler("src/capture.ts")
    i = s.index("CAPTURE_CHROME")
    ctx = s[max(0, i - 1400):i]
    assert "1217" in ctx and "1234" in ctx, \
        "a medição que justifica o override deixou de estar junto dele"


def test_sem_a_variavel_o_playwright_decide():
    """O notebook não define `CAPTURE_CHROME` e captura bem. O override não
    pode virar obrigação."""
    s = _ler("src/capture.ts")
    assert "|| undefined" in s, \
        "vazio precisa devolver `undefined`, não string vazia — o playwright " \
        "trata '' como caminho inválido e recusa o launch"


# ─── a sincronização ─────────────────────────────────────────────────────────

def test_o_src_e_varrido_e_nao_digitado():
    """O erro que custou duas rodadas: `capture-cli.ts` na lista, `capture.ts`
    fora dela. Varrer a pasta não depende de eu lembrar."""
    import i9
    assert "src/capture.ts" in i9.ARQUIVOS, "o arquivo que abre o navegador ficou de fora"
    assert "src/capture-cli.ts" in i9.ARQUIVOS
    s = _ler("i9.py")
    assert "rglob" in s, "a lista voltou a ser digitada à mão"


def test_o_que_nao_pode_ser_sobrescrito():
    """`config.py` e `.env` são legitimamente diferentes no i9 — credencial e
    caminho daquela máquina. Mandá-los quebraria o i9."""
    import i9
    for proibido in ("config.py", ".env"):
        assert proibido not in i9.ARQUIVOS, f"{proibido} não pode ser sincronizado"


def test_o_node_do_i9_entra_no_path():
    """O `npx` que responde sem isto é o do WINDOWS, herdado pelo PATH do WSL —
    ele roda, e roda errado, sobre caminhos Linux."""
    s = _ler("i9.py")
    assert ".local/node/bin" in s, "o Node do i9 saiu do PATH"


def test_o_arquivo_vai_separado_do_roteiro():
    """Sem a quebra de linha o base64 cola no redirecionamento e o bash grava
    um arquivo com o nome mais os dados. O erro que volta é o aviso do ssh, que
    não tem relação nenhuma com a causa."""
    s = _ler("i9.py")
    assert "_script(linhas) + base64.b64encode" in s, \
        "o roteiro voltou a ser concatenado sem separador"


# ─── o mapa ao vivo ──────────────────────────────────────────────────────────

def test_o_resultado_da_captura_volta_para_ca():
    """Quem grava os POIs durante a mineração é o watcher do servidor, e ele
    observa um arquivo LOCAL. Com a captura no i9 o arquivo nasce lá."""
    s = _ler("minerar_tudo.py")
    # A INSTANCIAÇÃO, não a primeira menção: `i9.Espelho` aparece antes num
    # comentário que explica por que `CAPTURAS` existe, e procurar pela string
    # solta encontra o comentário e passa mesmo com o espelho removido.
    assert "espelho = i9.Espelho(" in s, \
        "o espelho sumiu — a tela ficaria parada até o fim da captura"
    i = s.index("espelho = i9.Espelho(")
    assert "_db.json" in s[i:i + 200], "o espelho deixou de trazer o arquivo certo"
    assert "espelho.start()" in s and "espelho.encerrar()" in s, \
        "o espelho precisa começar antes e fazer a última cópia depois"


def test_o_codigo_do_municipio_nao_depende_das_bases():
    """`--pular-bases` derrubava a etapa 6 com UnboundLocalError, depois de a
    captura já ter rodado — horas perdidas por uma variável já calculada."""
    s = _ler("minerar_tudo.py")
    i = s.index("cod = cod_previa")
    j = s.index("if a.pular_bases:")
    assert i < j, "`cod` voltou a nascer dentro do ramo das bases públicas"


def test_a_sincronizacao_acontece_antes_de_rodar():
    """Conferir e avisar não bastaria: o aviso chega quando o dado já entrou."""
    s = _ler("minerar_tudo.py")
    i = s.index("i9.sincronizar")
    j = s.index("i9.rodar", i)
    assert i < j, "o código passou a ser enviado depois de a etapa rodar"
