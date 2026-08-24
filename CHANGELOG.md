# Changelog

Formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado
- **Cadastur/MTur como passo do processo e gerador de POI** (`cadastur.py` + skill
  `extracao-cadastur-mtur`): baixar → carregar → cruzar → gerar, com o gerador
  recusando rodar sem o cruzamento. Única fonte do sistema com **capacidade
  declarada** (leitos). Atualização incremental pelo botão ↻, em que quem SAIU do
  arquivo do MTur ganha `saiu_em` em vez de ser apagado.
- **Procedência declarada da coordenada** em todo POI: `porta` (15 m),
  `porta_aprox` (40 m), `via` (150 m), `desconhecida`. Aparece na ficha, no payload
  do mapa e numa fileira própria de filtro.
- **Geocodificador em cascata** (`geocodificar.py`): Photon → Nominatim → painel do
  Maps, na ordem de custo. Só depois dos três é que existe "não encontrado".
- **`via_confere`**: o geocodificador confere a via que devolveu. Três calibrações
  contra dado real levaram a reprovação de 29,7% para 10,2%.
- **CNEFE como terceira âncora de coordenada** do Cadastur — 16 dos 17 pontos de
  Esteio vieram só dela, todos com precisão de porta.
- `conferir_coordenadas.py` — varredura da base; move só o que cai **fora** do
  município que declara.
- `identificar_divergente.py` — acha foto e análise de IA capturadas na coordenada
  errada, com três destinos distintos conforme o que está provado.
- `verificar_servicos.py` — 7 verificações de **função**, não de porta: o Nominatim
  geocodifica, o OSRM traça rota, o Ollama gera palavra, o banco conta linhas.
- Tarefa agendada no i9 que **reaplica o encaminhamento WSL depois do boot**, roda
  como SYSTEM, acha o IP do WSL sem `wsl.exe` e confere se cada regra amarrou.
- `frontend/tokens.css` — o vocabulário visual, medido em vez de copiado.
- `guardar_ponto` no chat: o que o agente descobre passa a ser gravado pelo
  escritor único de POI, fechando o ciclo.
- As **26 migrações SQL** entraram no repositório — o `.gitignore` as levava junto
  com os dumps, e a história do schema vivia só no disco.
- Leitura de fachada por IA (`avaliar_fachada.py`) com o validador da skill
  `leitura-fachada-cadastral` como porteiro — anotação reprovada não entra na base.
- Unidades coletivas do CNEFE como terceira fonte independente
  (`coletivas_radar.py`, `coletivas_importar.py`), pelo método do Radar Coletivo.
- Aba **Avaliar candidatos** no painel, com menu flyout, cards próprios e ficha em
  tela cheia com as imagens que a IA usou.
- Aba **Dashboard** com cobertura, custo real × cenário Google e cálculo de
  retorno direto por faixa de qualidade.
- CNPJ pela base local da Receita Federal (`cnpj_local.py`), com a estrutura do
  número como critério de aceite e a base nacional como nível de confiança.
- Escrita atômica de JSON (`io_atomico.py`) que não derruba a rodada em
  `WinError 5`.
- Documentação de projeto: PRD, arquitetura em mermaid, RBAC, catálogo de módulos,
  checklist, ADRs e painel de acompanhamento.

### Alterado
- **O banco saiu do `localhost` para o i9** (Supabase self-hosted), os bytes de
  imagem saíram do banco para o **Storage**, e o schema virou `comercialradar`.
  O banco do produto caiu de 60 GB para 0,51 GB.
- **Distância não prova erro; município prova.** A varredura de coordenadas só move
  o ponto que cai fora do município que declara — foi 1 caso em 11.961. A primeira
  versão, que moveria tudo a mais de 2 km, teria sido destrutiva.
- **O campo `cidade` deixou de ser autoridade.** A trava só acusa quando a
  coordenada discorda do rótulo **e** do CEP — que vem do CNEFE e é terceira fonte
  independente. Antes disso, 8 fotos e 3 análises corretas foram apagadas por
  confiar no rótulo.
- **A conferência de município virou geométrica**, contra o polígono do IBGE com
  2 km de folga. Por nome, três pontos passaram e caíram a 171, 270 e 499 km.
- **O teste de coerência mede contenção no polígono**, não distância do centroide,
  e separa deslocamento (>25 km, derruba a suíte) de rótulo de cidade errado
  (até 25 km, pinado no número).
- **`nv_geo_coord`**: a semântica dos níveis 3 e 4 estava descrita errada; o piso
  continua em 2, agora por motivo medido — aceitar 3 e 4 resgataria zero prestador.
- **O front inteiro passou a ler `tokens.css`**: de duas famílias de cinza, dois
  azuis e 92 tons únicos para um vocabulário só. Auditoria de contraste em 796
  elementos: de 15 reprovações para zero.
- POI reencontrado é **atualizado**, não recriado; POI sem `place_id` deixou de
  duplicar a cada reingestão.
- **Remoção da interface do Google das imagens** antes de qualquer modelo vê-las.
  Pedir que ignorasse não funcionava: o modelo lia o endereço da caixa preta como
  número do imóvel.
- **O endereço do cadastro saiu do prompt.** Com ele, o `gpt-4o` devolvia o número
  informado — 3 de 5 leituras mudaram ao removê-lo — e a conferência do código
  ficava circular.
- **Divergência de numeração exige segunda vista**: o número é relido num recorte
  ampliado da mesma foto. Autodeclaração de legibilidade não serve e repetir a
  chamada também não.
- Aptidão da imagem virou chamada separada, com perguntas de sim ou não.
- Street View passou a ser opcional, restrito a POIs pobres.

### Corrigido
- **A área de trabalho desenhada não era gravada, e a tela dizia que sim.** O
  `root` não pertence a empresa nenhuma, `auth.conectar_como` não declarava
  `app.tenant_id`, a trigger não tinha o que carimbar e o `NOT NULL` recusava a
  linha — `POST /api/area` respondia 500. O front chamava `fetch` sem olhar a
  resposta e anunciava "Área salva ✔". Resultado: a mineração varreu 264 tiles
  do município errado, a partir de uma área de nove dias antes.
- 13 POIs tinham foto de fachada e análise de IA capturadas na coordenada errada —
  três pousadas do Piauí com seis fotos cada, a até 20 km do próprio endereço.
  Tratados em três destinos; 41 objetos órfãos removidos do Storage.
- 245 POIs rotulados `bairro`/`municipio` porque o geocodificador só resolveu até a
  cidade — o rótulo dizia que o ponto **é** um centroide, o que ele não é. Desfeitos.
- 4.442 POIs ficaram sem prestador apontando para eles quando uma execução estadual
  foi interrompida: o vínculo só era gravado no fim. Agora grava em lote de 100.
- O complemento roubava o número do endereço ("… 2586 LOJA 3 SALA 1" lia 1) e a
  notação de faixa da Receita ("de 3501 a 5101 - lado impar") virava número.

### Removido
- **Google Places API.** Cobrava por chamada e trazia o mesmo tipo de ponto que
  a captura + OCR traz de graça. Saíram com ela o "passo da grade" e a "coleta
  profunda", que eram opções só dela. A rota recusa `motor=places` com 410 em
  vez de ignorar em silêncio — aba antiga no navegador dispararia outro job sem
  dizer por quê. `minerar_area.py` fica no repositório como histórico.
- Processo de quadras, régua de numeração e biblioteca de recortes de construção —
  foram para o **radarTelhados** em 11/08/2026.

### Segurança
- A chave do Google exposta no commit `1c7f081` foi **revogada e confirmada morta**
  (`REQUEST_DENIED`/expired). Três chaves novas, separadas por função; a de servidor
  deixou de ser a mesma do navegador. gitleaks no pre-commit e no CI.
- Backup diário com **restauração validada** em máquina limpa.
- `cache_ibge/` (135 MB) fora do repositório.
- **Pendente:** 4 tabelas criadas depois do passe de RLS ficaram sem política —
  `atribuicao_divergente`, `fachada_triagem`, `foto_maps_triagem`, `ifood_merchant`.
- **Pendente:** 25.432 linhas de código nunca commitadas existem só em disco.
