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
import re
import sys
import ssl
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
    with bc.conectar() as c, c.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s ORDER BY ordinal_position", (tab,))
        return [r[0] for r in cur.fetchall()]


def run(mes, recriar, so, indices):
    conn = bc.conectar()
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

    colcache = {}
    for i, nome in enumerate(alvos, 1):
        ref = f"{mes}/{nome}"
        if bc.ja_carregado(conn, "cnpj", ref):
            print(f"  {i}/{len(alvos)} {nome:28} — já carregado, pula")
            continue
        prefixo, tab = tabela_de(nome)
        if not tab:
            print(f"  {i}/{len(alvos)} {nome:28} — sem mapeamento, ignora")
            continue
        cols = colcache.setdefault(tab, _cols(tab))
        destino = bc.TMP / nome
        try:
            print(f"  {i}/{len(alvos)} {nome:28} baixando…", flush=True)
            bc.baixar(WEBDAV + mes + "/" + nome, destino, auth=AUTH)
            tam = destino.stat().st_size
            print(f"      COPY → {tab} ({tam/1e6:.0f} MB)…", flush=True)
            n = bc.copy_de_zip(conn, tab, destino, colunas=cols)
            bc.marcar(conn, "cnpj", ref, tab, n, tam)
            print(f"      ✓ {n:,} linhas".replace(",", "."), flush=True)
        except Exception as e:
            print(f"      ✗ erro: {str(e)[:140]}", flush=True)
        finally:
            bc.limpar_tmp(destino)

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
