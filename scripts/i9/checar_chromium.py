# -*- coding: utf-8 -*-
"""O Chromium do i9 abre? Sai 0 se abre, 1 se nao.

POR QUE ISTO E UM ARQUIVO, E NAO UM `python -c` DENTRO DO publicar.sh

Entre o notebook e o Python do i9 ha TRES camadas de aspas: o `ssh` entra no
PowerShell do Windows, o PowerShell chama `wsl -- bash -lc '...'`, e o bash
chama o Python. Qualquer aspas no meio do comando e comida por uma das camadas.
A versao anterior tentou as duas formas e as duas falharam:

    python multilinha   ->  `from: command not found`   (a quebra saiu do -lc)
    python -c "..."     ->  `syntax error near ('`      (o PowerShell colapsou
                                                         as aspas internas)

Nos dois casos o `publicar.sh` concluia "o Chromium NAO abre" com o Chromium
abrindo, e mandava o dono da maquina rodar um `sudo install-deps` a toa.

Um arquivo nao tem aspas para colapsar. A chamada vira
`python scripts/i9/checar_chromium.py`, que atravessa as tres camadas intacta.
"""
import sys


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print("playwright ausente:", e)
        return 1
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch(headless=True)
            pagina = navegador.new_page()
            pagina.set_content("<h1>ok</h1>")
            texto = pagina.inner_text("h1")
            navegador.close()
    except Exception as e:                                     # noqa: BLE001
        # A mensagem do Playwright diz QUAL biblioteca falta; sem ela o aviso
        # do publicar.sh seria um palpite.
        print("chromium nao abre:", type(e).__name__, str(e)[:300])
        return 1
    print("abre" if texto == "ok" else "abriu mas nao renderizou")
    return 0 if texto == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
