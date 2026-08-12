# Changelog

Formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado
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

### Removido
- Processo de quadras, régua de numeração e biblioteca de recortes de construção —
  foram para o **radarTelhados** em 11/08/2026.

### Segurança
- `cache_ibge/` (135 MB) fora do repositório.
- Pendente: rotação da chave do Google exposta no commit `1c7f081`.
