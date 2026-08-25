# -*- coding: utf-8 -*-
"""Os scripts que publicam e operam o painel no i9 precisam SOBREVIVER à viagem.

Três coisas quebraram a publicação em 24/08/2026, e as três falham em silêncio
ou apontam para o lugar errado. Nenhuma aparece em revisão de código lendo o
arquivo — todas dependem de como o arquivo chega do outro lado.

  CRLF        `/usr/bin/env: 'bash\\r': No such file or directory`. O `\\r` é
              invisível em editor e em `cat`, e a mensagem culpa o `bash`.
  bit de exec `Permission denied`. `chmod +x` no Windows não é registrado pelo
              Git; o arquivo entra no índice como 644 e o `git archive` leva
              644. Funciona até a primeira REPUBLICAÇÃO.
  crase       dentro de heredoc sem delimitador entre aspas, crase é
              substituição de comando. Um comentário com crase mandou o bash
              executar `127.0.0.1`; outro, em volta de `cat`, teria TRAVADO o
              script lendo stdin.
"""
import os
import re
import stat
import subprocess
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(RAIZ, "scripts", "i9")
SCRIPTS = sorted(f for f in os.listdir(DIR) if f.endswith(".sh"))


def _bytes(nome):
    with open(os.path.join(DIR, nome), "rb") as f:
        return f.read()


def test_ha_scripts_para_conferir():
    """Se a pasta esvaziar, os testes abaixo passariam sem olhar nada."""
    assert SCRIPTS, "nenhum .sh em scripts/i9 — os testes abaixo virariam decoração"


@pytest.mark.parametrize("nome", SCRIPTS)
def test_nao_esta_vazio(nome):
    """Arquivo vazio passa em TODOS os outros testes deste arquivo.

    `bash -n` num arquivo vazio: válido. CRLF: nenhum. Crase em heredoc:
    nenhuma. Bit de execução: intacto. Quatro testes verdes sobre nada.

    Aconteceu em 25/08/2026 com o `dataset_brasil.sh`, e a causa foi uma linha
    de normalização de fim de linha escrita ao contrário:

        io.open(p, 'wb').write(io.open(p, 'rb').read().replace(...))

    O `open(p,'wb')` é avaliado PRIMEIRO e trunca o arquivo; só então o `read()`
    acontece — lendo zero bytes. O commit e a publicação levaram o vazio adiante,
    e o sintoma foi um script que "rodava" sem fazer nada nem reclamar.

    O piso é baixo de propósito: qualquer script útil tem shebang e mais de uma
    linha. Não se está medindo qualidade, e sim que existe conteúdo.
    """
    dados = _bytes(nome)
    assert len(dados) > 200, f"{nome} tem {len(dados)} bytes — foi truncado?"
    assert dados.startswith(b"#!"), f"{nome} nao comeca com shebang"


@pytest.mark.parametrize("nome", SCRIPTS)
def test_sem_crlf(nome):
    """LF no disco, garantido pelo `.gitattributes` (`*.sh text eol=lf`)."""
    assert b"\r\n" not in _bytes(nome), (
        f"{nome} tem CRLF. No Linux o shebang vira 'bash\\r' e o erro culpa o bash.")


@pytest.mark.parametrize("nome", SCRIPTS)
def test_e_executavel_no_indice_do_git(nome):
    """O modo que importa é o do ÍNDICE, não o do disco.

    `git archive` leva o modo do índice. No Windows o bit do disco não diz nada:
    o arquivo pode estar `-rwxr-xr-x` aqui e entrar como 644 no repositório.
    """
    saida = subprocess.run(
        ["git", "ls-files", "-s", f"scripts/i9/{nome}"],
        cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    if not saida:
        pytest.skip(f"{nome} ainda não está versionado")
    modo = saida.split()[0]
    assert modo == "100755", (
        f"{nome} está {modo} no índice. Corrija com:\n"
        f"  git update-index --chmod=+x scripts/i9/{nome}")


@pytest.mark.parametrize("nome", SCRIPTS)
def test_sem_crase_dentro_de_heredoc_nao_citado(nome):
    """Heredoc com delimitador SEM aspas expande crase — e crase executa.

    O delimitador citado (`<<'EOF'`) desliga toda expansão e é o caso seguro.
    O não citado existe quando o corpo precisa de variáveis — é o do Caddyfile,
    que injeta as portas. Nesses, crase é bomba-relógio: funciona até alguém
    escrever um comentário.
    """
    texto = _bytes(nome).decode("utf-8")
    problemas = []
    # `<<EOF` / `<<-EOF` sem aspas. `<<'EOF'` e `<<"EOF"` ficam de fora.
    for m in re.finditer(r"<<-?\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", texto, re.M):
        marca = m.group(1)
        fim = re.search(rf"^\s*{marca}\s*$", texto[m.end():], re.M)
        corpo = texto[m.end(): m.end() + (fim.start() if fim else 0)]
        if "`" in corpo:
            linha = texto[:m.start()].count("\n") + 1
            problemas.append(f"heredoc <<{marca} na linha {linha}")
    assert not problemas, (
        f"{nome}: crase dentro de heredoc não citado — {problemas}. "
        "Ou tire a crase, ou cite o delimitador (<<'EOF') se o corpo não "
        "precisar expandir variável.")


@pytest.mark.parametrize("nome", SCRIPTS)
def test_bash_aceita(nome):
    """`bash -n` — análise sem executar. Pega o que a leitura humana perdoa."""
    if not os.path.exists("/bin/bash") and sys.platform == "win32":
        bash = "bash"
    else:
        bash = "/bin/bash"
    r = subprocess.run([bash, "-n", os.path.join(DIR, nome)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"{nome} não passa em bash -n:\n{r.stderr}"


def test_a_conferencia_do_chromium_nao_tem_aspas():
    """Entre este notebook e o Python do i9 ha TRES camadas de aspas.

    ssh -> PowerShell -> `wsl -- bash -lc` -> python. Duas versoes ja morreram
    ali: um python de quatro linhas (`from: command not found`, a quebra saiu do
    `-lc`) e um `python -c "..."` (`syntax error near (`, o PowerShell colapsou
    as aspas internas). Nas duas o script anunciava "o Chromium NAO abre" com o
    Chromium abrindo, e mandava o dono da maquina rodar um sudo a toa.

    A forma que atravessa e um ARQUIVO, sem aspas nenhuma no comando.
    """
    txt = _bytes("publicar.sh").decode("utf-8", "replace")
    linha = next((l for l in txt.splitlines() if "checar_chromium.py" in l), None)
    assert linha, "a conferencia do Chromium sumiu do publicar.sh"
    depois = linha.split("checar_chromium.py")[0]
    for aspa in ('\\"', "'"):
        assert aspa not in depois.split("remoto")[-1], (
            "aspas no comando remoto voltam a ser comidas pelas tres camadas: %s"
            % linha[:100])
    # Só linhas EXECUTÁVEIS. O comentário logo acima da conferência cita
    # `python -c "..."` para explicar por que ele não serve aqui — proibir a
    # palavra no arquivo inteiro proibiria documentar o próprio defeito.
    codigo = [l for l in txt.splitlines() if not l.lstrip().startswith("#")]
    assert not any("python -c" in l for l in codigo), \
        "o `python -c` inline voltou ao publicar.sh"
    assert os.path.exists(os.path.join(RAIZ, "scripts", "i9", "checar_chromium.py"))


def test_a_producao_estadual_tem_trava_de_instancia_unica():
    """Duas copias do mesmo laco rodaram juntas por quatro horas em 25/08/2026.

    O `dataset_brasil.sh` sempre disse, em comentario, que roda uma UF por vez.
    Comentario nao impede o segundo disparo — e o segundo nao produziu erro
    nenhum ate ser tarde: o cache da skill e endereçado por hash de escopo,
    entao os dois processos CONCORDAVAM sobre o caminho de cada arquivo e
    escreviam por cima um do outro. O que apareceu foi consequencia, longe da
    causa: `.parquet.tmp` sumindo no `os.replace` (BA e SP), SP morto pelo OOM
    com dois `dedup` disputando a RAM, e o `validate` do RS reprovando por
    comparar um funil de tres passadas com a tabela de rejeitados de uma.

    A trava e por PROCESSO, nao por convencao, e nos dois niveis: o laco inteiro
    (uma producao do Brasil por maquina) e cada UF (um pipeline por diretorio,
    venha do laco, de disparo manual ou do painel).
    """
    for nome in ("dataset_brasil.sh", "dataset_estadual.sh"):
        txt = _bytes(nome).decode("utf-8", "replace")
        codigo = [l for l in txt.splitlines() if not l.lstrip().startswith("#")]
        assert any("flock -n" in l for l in codigo), (
            f"{nome} perdeu a trava de instancia unica. Sem ela, dois disparos "
            "gravam no mesmo diretorio e o estrago so aparece horas depois.")
        assert any(l.lstrip().startswith("exec ") and ">" in l for l in codigo), (
            f"{nome} tem flock sem descritor aberto por `exec N>` — a trava "
            "morreria no fim do comando em vez de durar a execucao inteira.")
