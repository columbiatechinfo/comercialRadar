# -*- coding: utf-8 -*-
"""
base_cnpj.py — Base completa de CNPJ do Brasil (Receita Federal, Dados Abertos).

Fonte oficial: arquivos.receitafederal.gov.br (migrou p/ Nextcloud/SERPRO+). Acesso
via WebDAV do compartilhamento público — lista os meses e os .zip, baixa e faz
COPY direto (CSV ';' latin-1) pras tabelas rf_*. Atualização mensal.

Tabelas: rf_empresas, rf_estabelecimentos (endereço+CNAE), rf_socios, rf_simples,
e os de apoio rf_cnaes/municipios/naturezas/qualificacoes/paises/motivos.

USO:
  .venv\\Scripts\\python base_cnpj.py --recriar          # 1ª carga (limpa e baixa o mês + recente)
  .venv\\Scripts\\python base_cnpj.py                    # retoma (pula o que já carregou)
  .venv\\Scripts\\python base_cnpj.py --mes 2026-06 --so Cnaes,Municipios   # subset
"""
import os
import queue
import re
import sys
import ssl
import threading
import time
import argparse
import urllib.request

import base_comum as bc

WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav/"
SHARE_TOKEN = "YggdBLfdninEJX9"          # compartilhamento público oficial (Dados Abertos CNPJ)
AUTH = bc.b64_token(SHARE_TOKEN)
_SSL = ssl.create_default_context()

# prefixo do .zip → (tabela, nº de colunas). Tabelas são todas text (fidelidade + COPY robusto).
MAPA = {
    "Empresas":       ("rf_empresas", 7),
    "Estabelecimentos": ("rf_estabelecimentos", 30),
    "Socios":         ("rf_socios", 11),
    "Simples":        ("rf_simples", 7),
    "Cnaes":          ("rf_cnaes", 2),
    "Municipios":     ("rf_municipios", 2),
    "Naturezas":      ("rf_naturezas", 2),
    "Qualificacoes":  ("rf_qualificacoes", 2),
    "Paises":         ("rf_paises", 2),
    "Motivos":        ("rf_motivos", 2),
}

# Quantas colunas cada tabela DEVE ter, pelo layout da RFB. Sai do MAPA acima,
# e serve de trava: DDL editado com uma coluna a mais para de carregar em vez de
# embaralhar em silencio.
MAPA_COLS = {tab: n for tab, n in MAPA.values()}

DDL = """
CREATE TABLE IF NOT EXISTS rf_empresas (
  cnpj_basico text, razao_social text, natureza_juridica text,
  qualificacao_responsavel text, capital_social text, porte text, ente_federativo text);
CREATE TABLE IF NOT EXISTS rf_estabelecimentos (
  cnpj_basico text, cnpj_ordem text, cnpj_dv text, matriz_filial text, nome_fantasia text,
  situacao_cadastral text, data_situacao text, motivo_situacao text, cidade_exterior text,
  pais text, data_inicio text, cnae_principal text, cnae_secundaria text, tipo_logradouro text,
  logradouro text, numero text, complemento text, bairro text, cep text, uf text, municipio text,
  ddd1 text, tel1 text, ddd2 text, tel2 text, ddd_fax text, fax text, email text,
  situacao_especial text, data_situacao_especial text);
CREATE TABLE IF NOT EXISTS rf_socios (
  cnpj_basico text, identificador_socio text, nome_socio text, cnpj_cpf_socio text,
  qualificacao_socio text, data_entrada text, pais text, representante_cpf text,
  nome_representante text, qualificacao_representante text, faixa_etaria text);
CREATE TABLE IF NOT EXISTS rf_simples (
  cnpj_basico text, opcao_simples text, data_opcao_simples text, data_exclusao_simples text,
  opcao_mei text, data_opcao_mei text, data_exclusao_mei text);
CREATE TABLE IF NOT EXISTS rf_cnaes (codigo text, descricao text);
CREATE TABLE IF NOT EXISTS rf_municipios (codigo text, descricao text);
CREATE TABLE IF NOT EXISTS rf_naturezas (codigo text, descricao text);
CREATE TABLE IF NOT EXISTS rf_qualificacoes (codigo text, descricao text);
CREATE TABLE IF NOT EXISTS rf_paises (codigo text, descricao text);
CREATE TABLE IF NOT EXISTS rf_motivos (codigo text, descricao text);
"""

INDICES = [
    "CREATE INDEX IF NOT EXISTS ix_estab_basico ON rf_estabelecimentos (cnpj_basico)",
    "CREATE INDEX IF NOT EXISTS ix_estab_ufmun ON rf_estabelecimentos (uf, municipio)",
    "CREATE INDEX IF NOT EXISTS ix_estab_cnae ON rf_estabelecimentos (cnae_principal)",
    "CREATE INDEX IF NOT EXISTS ix_estab_cep ON rf_estabelecimentos (cep)",
    "CREATE INDEX IF NOT EXISTS ix_empresas_basico ON rf_empresas (cnpj_basico)",
    "CREATE INDEX IF NOT EXISTS ix_socios_basico ON rf_socios (cnpj_basico)",
    "CREATE INDEX IF NOT EXISTS ix_simples_basico ON rf_simples (cnpj_basico)",
]


def _propfind(url: str, tentativas: int = 5) -> list:
    """Lista os hrefs (subpastas/arquivos) de um diretório WebDAV público.
    Com retry: um soluço de DNS/rede não deve derrubar a descoberta."""
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, method="PROPFIND",
                                         headers={"Depth": "1", "User-Agent": bc.UA,
                                                  "Authorization": "Basic " + AUTH})
            with urllib.request.urlopen(req, timeout=60, context=_SSL) as r:
                xml = r.read().decode("utf-8", "ignore")
            hrefs = re.findall(r"<d:href>([^<]+)</d:href>", xml, re.I)
            return [urllib.parse.unquote(h) for h in hrefs]
        except Exception as e:
            if t == tentativas - 1:
                raise
            print(f"    (rede instável: {str(e)[:60]} — tentando de novo)", flush=True)
            time.sleep(5 * (t + 1))


def meses_disponiveis() -> list:
    base = "/public.php/webdav/"
    got = _propfind(WEBDAV)
    ms = sorted({h[len(base):].strip("/") for h in got
                 if re.search(r"/\d{4}-\d{2}/?$", h)})
    return ms


def arquivos_do_mes(mes: str) -> list:
    got = _propfind(WEBDAV + mes + "/")
    nomes = [h.rstrip("/").split("/")[-1] for h in got if h.lower().endswith(".zip")]
    return sorted(nomes)


def tabela_de(nome_zip: str):
    for prefixo, (tab, _) in MAPA.items():
        if nome_zip.lower().startswith(prefixo.lower()):
            return prefixo, tab
    return None, None


def _cols(tab):
    """A ordem das colunas para o COPY, tirada do DDL ACIMA — nunca do banco.

    ESTE ERA O DEFEITO, e ele custou 220 milhoes de linhas embaralhadas.
    A versao anterior perguntava ao banco:

        SELECT column_name FROM information_schema.columns
         WHERE table_name = %s ORDER BY ordinal_position

    Parece defensivo e e o contrario. O CSV da Receita vem numa ordem fixa, que
    e a do DDL aqui em cima. Perguntar ao banco faz o carregador concordar com
    a ordem que a tabela POR ACASO tem — e se ela foi criada por outra mao, o
    COPY despeja o campo 1 do CSV na primeira coluna FISICA da tabela, seja ela
    qual for.

    Foi o que aconteceu em 31/08/2026. A `migrations_a2l/0002_resources_root.sql`
    criou as tabelas com as colunas em ordem ALFABETICA (bairro, cep,
    cidade_exterior, ...). O `CREATE TABLE IF NOT EXISTS` deste arquivo, que tem
    a ordem certa, nao fez nada — a tabela ja existia. E o COPY carregou 45
    minutos de dado com tudo no lugar errado:

        motivo_situacao = 'CE'          (era para ser UF)
        ddd2            = 'RUA'         (era tipo_logradouro)
        logradouro      = 'ALDEOTA'     (era bairro)
        bairro          = '59160166'    (era CEP)
        uf              = ''            (vazio em 100% das linhas)

    Nenhuma linha falhou, nenhum erro apareceu: todas as colunas sao `text`, e
    text aceita qualquer coisa. A carga terminou com codigo 0.

    Tirando a ordem do DDL, os dois nao podem mais discordar: se alguem mudar o
    DDL, o COPY muda junto; se o banco tiver outra ordem, o COPY continua certo
    porque nomeia cada coluna.
    """
    m = re.search(r"CREATE TABLE IF NOT EXISTS\s+%s\s*\((.*?)\);" % tab, DDL, re.S)
    if not m:
        raise RuntimeError("tabela %s nao esta no DDL deste arquivo" % tab)
    cols = re.findall(r"([a-z_][a-z0-9_]*)\s+text", m.group(1))
    esperado = MAPA_COLS.get(tab)
    if esperado and len(cols) != esperado:
        raise RuntimeError("DDL de %s tem %d colunas, o layout da RFB tem %d"
                           % (tab, len(cols), esperado))
    return cols


def run(mes, recriar, so, indices):
    # Base publica da Receita: banco de REFERENCIA.
    conn = bc.conectar_referencia()
    bc.garantir_controle(conn)
    with conn, conn.cursor() as cur:
        cur.execute(DDL)
    if not mes:
        ms = meses_disponiveis()
        if not ms:
            print("Não achei meses no WebDAV da RFB."); return
        mes = ms[-1]
    print(f"📦 CNPJ Brasil | mês {mes} | fonte RFB (WebDAV)", flush=True)

    if recriar:
        with conn, conn.cursor() as cur:
            for _, (tab, _n) in MAPA.items():
                cur.execute(f"TRUNCATE {tab}")
            cur.execute("DELETE FROM fonte_arquivos WHERE fonte='cnpj'")
        print("  🧹 tabelas rf_* limpas (recriar)")

    arquivos = arquivos_do_mes(mes)
    if so:
        alvos = [a for a in arquivos if any(a.lower().startswith(p.lower()) for p in so)]
    else:
        alvos = arquivos
    print(f"  {len(alvos)} arquivos a processar", flush=True)

    # ── DUAS CONEXOES BAIXANDO A FRENTE, E DUAS E NAO QUATRO ────────────────
    #
    # A versao anterior era estritamente sequencial: baixa, COPY, baixa o
    # proximo. Com o download levando 10 minutos por arquivo de 1,5 GB e o COPY
    # rodando com a CPU em 1,3%, a maquina ficava parada esperando a rede na
    # maior parte do tempo.
    #
    # MEDIDO em 31/08/2026 contra o servidor da Receita:
    #
    #     1 conexao   ->  2,4 MB/s
    #     2 conexoes  ->  5,0 MB/s   (2,5 + 2,5 — dobrou exato)
    #     4 conexoes  ->  TODAS deram timeout
    #     8 conexoes  ->  TODAS deram timeout
    #
    # O teto e POR CONEXAO, nao do nosso link — por isso duas dobram. Mas o
    # servidor para de responder a partir de quatro vindas do mesmo IP, e nao
    # devolve erro: as conexoes penduram ate estourar o timeout. Subir este
    # numero nao acelera, QUEBRA a carga. Por isso o limite e duro e nao
    # configuravel para cima.
    #
    # Os downloads correm em duas threads; o COPY continua UM DE CADA VEZ na
    # thread principal. Nao e limitacao: o banco nunca foi o gargalo, e
    # serializar a escrita mantem a garantia que importa — cada arquivo grava
    # dado e marca na mesma transacao, numa conexao so.
    CONEXOES = min(2, max(1, int(os.environ.get("CR_CONEXOES", "2"))))

    colcache = {}
    pendentes = []
    for i, nome in enumerate(alvos, 1):
        ref = f"{mes}/{nome}"
        if bc.ja_carregado(conn, "cnpj", ref):
            print(f"  {i}/{len(alvos)} {nome:28} — já carregado, pula")
            continue
        prefixo, tab = tabela_de(nome)
        if not tab:
            print(f"  {i}/{len(alvos)} {nome:28} — sem mapeamento, ignora")
            continue
        pendentes.append((i, nome, ref, tab))

    if not pendentes:
        print("  nada a baixar — tudo já carregado")
    else:
        print(f"  {len(pendentes)} a baixar, {CONEXOES} conexões em paralelo", flush=True)

    a_fazer = queue.Queue()
    for item in pendentes:
        a_fazer.put(item)
    # `maxsize` limita quantos arquivos BAIXADOS esperam em disco. Sem ele, os
    # downloads correriam ate o fim da fila e 20 arquivos de 1,5 GB ocupariam
    # 30 GB de temporario enquanto o COPY do primeiro ainda roda.
    prontos = queue.Queue(maxsize=CONEXOES)

    def baixador():
        """Todo caminho de saida POE algo na fila. Nenhum item sai calado.

        O consumidor faz exatamente `len(pendentes)` leituras. Se uma thread
        morrer sem enfileirar — uma excecao fora do `try`, um erro montando o
        caminho do arquivo —, o consumidor fica esperando um item que nunca vem
        e a carga PENDURA, sem erro e sem log, parecendo um download lento.
        Por isso o `try` cobre tudo, inclusive o que parece nao poder falhar.
        """
        while True:
            try:
                i, nome, ref, tab = a_fazer.get_nowait()
            except queue.Empty:
                return
            destino = None
            try:
                destino = bc.TMP / nome
                print(f"  {i}/{len(alvos)} {nome:28} baixando…", flush=True)
                bc.baixar(WEBDAV + mes + "/" + nome, destino, auth=AUTH)
                prontos.put((i, nome, ref, tab, destino, destino.stat().st_size, None))
            except BaseException as e:      # BaseException: nem KeyboardInterrupt escapa
                prontos.put((i, nome, ref, tab, destino, 0, e))

    threads = [threading.Thread(target=baixador, daemon=True) for _ in range(CONEXOES)]
    for th in threads:
        th.start()

    for _ in range(len(pendentes)):
        # TIMEOUT NA ESPERA, e ele e a ultima rede de seguranca. O `baixador`
        # ja enfileira em todo caminho de saida; se ainda assim faltar um item,
        # e melhor a carga terminar reclamando do que ficar parada a noite toda
        # parecendo que baixa. Uma hora e folgado para o maior arquivo.
        try:
            i, nome, ref, tab, destino, tam, erro = prontos.get(timeout=3600)
        except queue.Empty:
            print("      ✗ nenhum download respondeu em 1h — parando", flush=True)
            break
        if erro is not None:
            print(f"      ✗ {nome}: {str(erro)[:120]}", flush=True)
            if destino is not None:
                bc.limpar_tmp(destino)
            continue
        cols = colcache.setdefault(tab, _cols(tab))
        try:
            print(f"      COPY → {tab} ({tam/1e6:.0f} MB)…", flush=True)
            # O COPY E A MARCA NA MESMA TRANSAÇÃO.
            #
            # Antes eram dois commits: o `copy_csv` fechava o dele e o `marcar`
            # o seu. Uma queda entre os dois deixaria o arquivo carregado e NÃO
            # marcado — e a retomada, que pula pelo marcador, carregaria tudo de
            # novo. Milhões de linhas em dobro, sem erro nenhum, e a única
            # pista seria uma contagem maior que a soma dos arquivos.
            #
            # Não é hipótese de laboratório: o servidor caiu duas vezes em
            # 31/08/2026 no meio de cargas longas.
            n = bc.copy_de_zip(conn, tab, destino, colunas=cols, commit=False)
            bc.marcar(conn, "cnpj", ref, tab, n, tam)   # `with conn` fecha as duas
            print(f"      ✓ {nome}: {n:,} linhas".replace(",", "."), flush=True)
        except Exception as e:
            print(f"      ✗ erro: {str(e)[:140]}", flush=True)
        finally:
            bc.limpar_tmp(destino)

    for th in threads:
        th.join(timeout=5)

    if indices:
        print("  criando índices (pode demorar)…", flush=True)
        with conn, conn.cursor() as cur:
            for ix in INDICES:
                cur.execute(ix)
        print("  ✓ índices criados")
    conn.close()
    print("✅ CNPJ concluído.", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mes", default="", help="AAAA-MM (padrão: mais recente)")
    p.add_argument("--recriar", action="store_true", help="limpa as tabelas rf_* antes")
    p.add_argument("--so", default="", help="subset por prefixo, ex: Cnaes,Municipios")
    p.add_argument("--indices", action="store_true", help="cria os índices ao final")
    a = p.parse_args()
    so = [s.strip() for s in a.so.split(",") if s.strip()]
    run(a.mes, a.recriar, so, a.indices)
