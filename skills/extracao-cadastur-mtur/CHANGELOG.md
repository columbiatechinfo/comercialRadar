# CHANGELOG

## 3.0.0 — 2026-08-22

Versão de produção com reconciliação ponta a ponta, hashes do conjunto de artefatos, Data Quality não destrutivo, quarentena por evidência, observabilidade, DDL PostgreSQL idempotente, carga transacional opcional com staging/reconciliação e transformação concorrente por recurso. Inclui E2E sintético reproduzível e mantém os invariantes de checkpoint, histórico e zero perda da v2.

## 2.0.0 — 2026-08-22

Arquitetura industrial incremental. Remove a necessidade de concatenar o bronze inteiro em memória, adiciona shards por recurso, checkpoint transacional SQLite/WAL, retomada segura, SHA-256 dos shards, distinção entre escopo lógico e fingerprint do catálogo, histórico de runs, drift de schema por escopo e comparação temporal de entidades (`ENTROU`, `SAIU`, `PERMANECEU`, `ALTEROU`). Baselines só avançam depois de uma execução totalmente consistente e shards temporários são removidos após sucesso por padrão.

## 1.1.0 — 2026-08-22

Revisão de robustez, performance e integridade. Corrige descarte potencial de CSV irregular, colapso de registros sem CNPJ em `v_vigente`, cache validado apenas por tamanho, escolha arbitrária de coordenada conflitante e DDL frágil em recortes parciais. Adiciona downloads concorrentes, rastreio de linha física, preservação de aliases, escrita atômica e gates adicionais de geocodificação.
