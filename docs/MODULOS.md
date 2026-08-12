# Catálogo de módulos — ComercialRadar

> Em `saas-multi-cliente` cada módulo liga e desliga **por cliente**. A flag mora
> em tabela, não em constante: constante no código significa deploy para ativar
> módulo; em tabela é um `UPDATE`. Atualizado em 12/08/2026.

```sql
create table tenant_features (
  tenant_id  uuid    not null references tenants(id),
  module_key text    not null,
  enabled    boolean not null default false,
  primary key (tenant_id, module_key)
);
```

**Estado: a tabela ainda não existe.** Hoje todo módulo está sempre ligado para
o operador único. A coluna `flag` abaixo é o alvo.

---

## Módulos

| `module_key` | O que faz | Código | Custo externo | Padrão |
|---|---|---|---|---|
| `captura_ocr` | Fotografa o Maps em tiles, detecta ícone de POI por OpenCV, lê o nome por OCR e busca cada um | `detect_crops.py` · `ocr_pois.py` · `minerar_captura.py` | Proxies | ligado |
| `mineracao_area` | Varre um polígono desenhado e descobre os POIs úteis da região | `minerar_area.py` | Places API | ligado |
| `planilha` | Importa `.xlsx`/`.csv` e casa cada linha com o lugar real no Maps | `search_from_sheet.py` | Maps | ligado |
| `enriquecimento_cascata` | POI pobre passa por Maps → web (SearXNG) → Street View até completar | `enriquecer_tudo.py` · `enriquecer_maps.py` | Street View | ligado |
| `cnpj_receita` | CNPJ, CNAE, situação cadastral e sócios pela base local da Receita | `cnpj_local.py` · `base_cnpj.py` | zero (base local) | ligado |
| `coletivas_cnefe` | Unidades coletivas do CNEFE como terceira fonte independente | `coletivas_radar.py` · `coletivas_importar.py` | zero (base local) | ligado |
| `leitura_fachada` | Fachada + contexto vão ao modelo de visão; a anotação passa pelo validador da skill | `avaliar_fachada.py` | **US$ 0,017/POI** (`gpt-4o`) ou zero (i9) | ligado |
| `cadastro_cliente` | Importa a carteira de imóveis da empresa e cruza com os POIs | `cadastro_cliente.py` | zero | ligado |
| `dashboard_retorno` | Cobertura, custo real × cenário Google e cálculo de retorno por faixa | `server.py` · `frontend/app.js` | zero | ligado |
| `extracao_estadual` | Extração POI em escala estadual por OSM + Overture, com dedup e relatório de qualidade | **a integrar** — skill `extracao-poi-estadual` | zero | desligado |

---

## O que decide se um módulo é ligado por padrão

Módulo com **custo externo por item** nasce desligado quando o custo escala com a
base: `leitura_fachada` a US$ 0,017 por POI são US$ 356 nas 20.698 fachadas já
capturadas. Ligar para um cliente novo sem que ele saiba é gastar o dinheiro dele.

Módulo que só lê base local nasce ligado: o custo marginal é tempo de CPU.

---

## `extracao_estadual` — a integrar

Chegou em 12/08/2026 como skill pronta (`extracao-poi-estadual`), com saída já
produzida para o RS: POI bruto de OSM e Overture, padronizado, deduplicado com
vínculos, rejeitados e relatório de qualidade — todos em Parquet e CSV.

O que precisa ser decidido antes de virar módulo:

- **Onde o dado entra.** A saída é arquivo; a premissa do projeto é que dado mora
  no banco. A ingestão precisa de tabela e de chave que case com a `pois`.
- **Como convive com a `captura_ocr`.** As duas descobrem POI por caminhos
  diferentes; sem regra de precedência, o mesmo estabelecimento entra duas vezes.
- **Recorte por cliente.** Extração estadual atravessa municípios de vários
  clientes. Sem `tenant_id`, não há como recortar o que cada um vê.

Enquanto essas três não tiverem resposta, o módulo fica desligado e a skill fica
versionada como referência.
