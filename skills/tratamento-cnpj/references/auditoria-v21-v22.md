# Auditoria: v2.1.0 × v2.2

## Como ler este documento

A v2.1 corrigiu bem os 12 defeitos que ela própria documenta em
`auditoria-v1-v2.md`. O que a auditoria de agosto/2026 encontrou é de outra
natureza: não está na lógica de negócio, está **no contrato com a fonte** e **na
física de execução**.

O padrão único dos achados críticos:

> a skill era rigorosa naquilo que declarava medir e silenciosa naquilo que assumia.

NV que ninguém checou contra o dicionário do IBGE, espécie que ninguém conferiu,
similaridade que pontuava 100 por construção, linha que o Excel descartava sem
avisar. A correção não foi afrouxar regra: foi **fazer o pipeline declarar o que
não sabe** — princípio que a v2 já aplicava ao fuzzy ambíguo e ao centróide S/N,
e que estava pela metade.

## Falhas críticas corrigidas

1. **Não havia piso de `NV_GEO_COORD`.** Uma coordenada de centróide de setor
   censitário (NV=6, erro de km) saía como `P1_EXATO`, `MATCH_CONF=ALTA`,
   `DESVIO_METROS=0.0` e `EXISTE_QUASE_CERTO`. O NV entrava apenas como desempate
   de duplicidade; se o endereço tinha uma só coordenada, ela passava qualquer que
   fosse o nível.

2. **`DESVIO_METROS` era a constante 0.0** em 7 dos 8 passes, inclusive P6/P7 com
   delta numérico até 100. O campo existia, o dicionário prometia "incerteza", e o
   valor era falso — com impacto direto no consumidor a jusante, que usa distância
   como trava espacial.

3. **O centróide sintético usava NV=4**, que no domínio oficial do IBGE é *face de
   quadra*. Era decisão documentada, o que fazia disso erro de doutrina, não
   deslize. Quem filtrasse `NV <= 4` recebia mediana de logradouro achando que
   recebia face de quadra.

4. **`ESP_COMERCIAL = {3,4,5,6,7}`** incluía a espécie 7 (edificação em construção
   ou reforma — 3,5 milhões de registros no Brasil) e excluía a 8 (estabelecimento
   religioso). Verificado contra o `Dicionario_CNEFE_Censo_2022.xls` oficial.
   A omissão do 8 era mascarada por acidente: 100% das linhas de espécie 8 têm
   `DSC_ESTABELECIMENTO` preenchido, e o `OR` da flag as capturava. O `OR` era
   simultaneamente o bug (trazia 7 e 2) e o remendo acidental.

5. **A similaridade usava `max(token_set_ratio, token_sort_ratio)`.**
   `token_set_ratio` devolve 100 quando um nome é subconjunto do outro, e o `max`
   garantia que o componente permissivo vencesse. `RUA BRASIL` casava com
   `RUA BRASIL NOVO` com score 100 e margem 50 — nenhum limiar barra isso.
   Em bateria de 18 casos a v2.1 acerta 10; a v2.2 acerta 18.

6. **O export não tinha guarda do limite de 1.048.576 linhas do Excel.**
   `xlsxwriter.write()` devolve `-1` e ignora em silêncio; o QA rodava antes do
   export e não reconferia o arquivo — contrariando o item 1 do próprio checklist.

7. **CNPJ alfanumérico destruído.** Em produção na RFB desde 31/07/2026
   (IN 2.229/2024). `digits()` apagava as letras, o CNPJ virava
   `TAMANHO_INVALIDO` e saía de `POTENCIAL_CRUZAMENTO` em silêncio — perda
   crescente e invisível.

8. **`validate_output` levantava `KeyError`** quando faltava coluna obrigatória:
   morria exatamente na situação que existe para detectar.

9. **CEP fora do índice era rotulado `SEM_LOGRADOURO`**, impedindo separar CEP
   errado no cadastro de logradouro não encontrado — que exigem ações diferentes.

10. **A contagem de unidades misturava domicílio com estabelecimento.**
    `IBGE_QTD_UNIDADES` contava registros CNEFE do endereço. Numa torre residencial
    com uma loja no térreo isso dá centenas, indistinguível de uma galeria. O IBGE
    já entrega `COD_INDICADOR_ESTAB_ENDERECO` classificado (1 único, 2 até 10,
    3 mais de 10, 4 quantidade desconhecida) e a coluna era ignorada.

11. **Dado ausente virava sinal negativo.** Capital ausente vira 0 e não atinge o
    corte de `+1`; data ausente vira `NaN` e derruba `FLAG_ESTABELECIDO`. Os
    status existiam (`AUSENTE_ASSUMIDO_ZERO`) e o scorer não os consultava.

12. **O selftest não capturava nenhum dos achados** e rodava com `fuzzy_thr=88`,
    diferente do default de produção 90.

## Resultados medidos

### Precisão — CNEFE real de São Leopoldo/RS × 9.000 CNPJ

| Indicador | v2.1.0 | v2.2.0 |
|---|---|---|
| Geocodificados | 8.312 | 8.103 |
| `MATCH_CONF=ALTA` | 6.621 | 6.613 |
| `POTENCIAL_CRUZAMENTO` | 6.567 | 6.770 |
| CNPJ válido | 8.562 | 8.832 |
| ALTA sobre NV ≥ 4 | 17 | **0** |
| Desvio declarado zero | 7.143 | **0** |

As 251 perdas de match são **100% `FUZZY_ABAIXO_LIMIAR`** e correspondem a
truncamentos de logradouro que a v2.1 casava com score 100. Nenhuma perda
legítima. Em troca: 42 ganhos, 110 pontos reposicionados (mediana 9,2 m, efeito do
sufixo entrando na chave) e 270 CNPJ alfanuméricos recuperados.

### Escala — medido, por etapa

| Etapa | Ganho | Observação |
|---|---|---|
| Leitura do CNEFE | **11×** em memória | pico 119,5 MB → 10,7 MB no CNEFE real |
| Regras de negócio | **9–11×** | 6 `.apply(axis=1)` → máscaras e `np.select` |
| Match | **4–5×** | logradouro resolvido uma vez por chave distinta |
| Construção do índice | **1,7–1,9×** | ver ressalva abaixo |

### Ressalva honesta sobre o índice

A auditoria classificou o termo quadrático de `build_cnefe_index` como
**BLOQUEADOR**. A medição não sustenta esse rótulo.

O scan booleano dentro do laço de grupos existe e cresce superlinearmente, mas o
custo dominante estava em outro lugar:

| Grupos | Endereços | Scan booleano | Resto do laço | Scan % | Escala do scan |
|---|---|---|---|---|---|
| 1.000 | 20.000 | 2,87 s | 4,40 s | 39% | — |
| 2.000 | 40.000 | 6,26 s | 8,64 s | 42% | ×2,18 |
| 4.000 | 80.000 | 15,61 s | 17,70 s | 47% | ×2,49 |

O scan é ~40–47% do tempo de construção do índice e sua participação cresce
(×2,5 por duplicação, contra ×2 do linear), então ele **domina em município
grande** — mas não torna a execução inviável em município médio, que era o que o
rótulo BLOQUEADOR implicava.

A classificação correta é **CRÍTICO**. O bloqueador real, confirmado por medição,
era o consumo de memória da leitura do ZIP.

Registrado aqui porque corrigir a própria auditoria vale mais do que defendê-la.

## Mudanças de comportamento intencionais

- NV ≥ 4 nunca produz confiança ALTA (a cobertura cai um pouco; a precisão sobe);
- fuzzy por subconjunto é recusado, mesmo que isso reduza cobertura;
- `0`, `00` e `000` seguem a convenção da fonte e são tratados como S/N, com
  status próprio — a v2.1 divergia entre eles;
- o campo de tipo de logradouro é autoritativo sobre a primeira palavra do nome;
- ser fuzzy não soma no score de existência;
- capital e data ausentes não pontuam nem penalizam, e a omissão é registrada;
- a rota de tratamento vem do indicador oficial do IBGE, nunca da contagem bruta.

Todas priorizam precisão e rastreabilidade sobre cobertura artificial.

## Regra de aceite para a próxima versão

Um caso que passa nas duas versões não prova correção nenhuma. Toda correção nova
entra com o par: **falha na versão anterior, passa na nova**, verificado por
`tests/test_gate_discrimina.py` contra o motor vendorizado.
