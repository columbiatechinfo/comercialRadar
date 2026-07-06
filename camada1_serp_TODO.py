"""
camada1_serp_TODO.py — STUB (NÃO IMPLEMENTADO)

Camada 1 de validação/descoberta barata via SERP do Google + proxies ROTATING
residential. Roda ANTES da Camada 2 (search_pois_v2 / coleta rica no Maps).

═══════════════════════════════════════════════════════════════════════════
OBJETIVO
═══════════════════════════════════════════════════════════════════════════
Filtrar e validar POIs de forma muito barata (~400 KB/POI) antes de gastar
banda cara na coleta rica do Maps (Camada 2, ~2-3 MB/POI). Para cada POI:
  - confirma que o estabelecimento existe (nome oficial)
  - extrai place_id / coordenada canônica
  - classifica categoria (filtra ruído: pontos sem comércio real)
Só os POIs VALIDADOS seguem para a Camada 2.

═══════════════════════════════════════════════════════════════════════════
POR QUE OUTRO TIER DE PROXY
═══════════════════════════════════════════════════════════════════════════
- Camada 1 usa ROTATING residential (80M IPs, 1 GB/mês): cada request um IP
  novo, ideal para validação leve de altíssimo volume. SERP é leve.
- Camada 2 usa STATIC residential (100 IPs, 1 TB/mês): sessões persistentes,
  coleta rica com imagens. Banda alta, IPs estáveis.

Endpoint rotating (gateway backbone): ver Webshare_100_proxies.txt
(p.webshare.io:80 com usernames rotativos). A API v2 também expõe o modo
rotating; adicionar suporte em proxy_pool.ProxyPool (parâmetro `tier`).

═══════════════════════════════════════════════════════════════════════════
ESBOÇO DE IMPLEMENTAÇÃO (quando for a hora)
═══════════════════════════════════════════════════════════════════════════
1. ProxyPool(tier="rotating"): carregar gateway rotativo em vez de IPs estáticos.
2. HumanSession(layer="serp"): já suportado em human_browser
   - bloqueia TAMBÉM imagens (route blocking já trata layer="serp")
3. Para cada POI:
     query = f'{ocr_texto} {cidade}'
     goto: https://www.google.com/search?q=<query>  (ou /maps com parse leve)
     extrair do "knowledge panel" / primeiro resultado local:
       - nome oficial
       - place_id (do data-attribute / href do Maps)
       - lat/lng
       - categoria
4. Validar:
     - similaridade(ocr, nome_oficial) >= LIMIAR
     - distância(coordenada_ocr, coordenada_serp) <= LIMIAR_M
     - categoria ∈ categorias_comerciais
5. Saída: serp_validados.json com {idx, valido, nome_oficial, place_id, lat, lng, categoria}
   → search_pois_v2 passa a consumir só os validados (campo extra: --apenas-validados)

═══════════════════════════════════════════════════════════════════════════
MÉTRICAS-ALVO
═══════════════════════════════════════════════════════════════════════════
- Banda Camada 1: < 400 KB/POI
- Redução de POIs que chegam à Camada 2: depende do ruído do OCR (esperado 20-40%)

NÃO IMPLEMENTAR AINDA — placeholder estrutural conforme escopo atual.
"""


def main():
    raise NotImplementedError(
        "Camada 1 (SERP) ainda não implementada. Veja o cabeçalho deste arquivo "
        "para o esboço. Escopo atual cobre apenas a Camada 2 (search_pois_v2 / "
        "recover_pois_v2)."
    )


if __name__ == "__main__":
    main()
