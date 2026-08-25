# Referência CNEFE/IBGE — Rio Grande do Sul

Base conceitual e quantitativa usada no hardening v3.3.5.

## Regra de número e modificador

O CNEFE 2022 define que o atributo **número comporta apenas valores numéricos**. O campo
**modificador** recebe identificação numérica ou textual adicional usada para localizar o endereço.
Exemplos históricos do padrão IBGE incluem `10 A` (número 10, modificador A), `7 KM` e `SN`.

A A2L adota o mesmo corte estrutural, mas integra o modificador à camada semântica de complemento:

- `1B` -> número `1` + `IMOVEL B`;
- `125-A` -> número `125` + `IMOVEL A`;
- `100 FUNDOS` -> número `100` + `FUNDOS`.

A anotação permanece separada internamente para impedir que `1A` e `1B` sejam tratados como a
mesma âncora durante o aprendizado.

## Modificadores no Rio Grande do Sul — CNEFE 2022

Tabela 5 das Notas metodológicas n. 04:

| Categoria | Endereços RS |
|---|---:|
| Sem modificador | 5.019.358 |
| Sem número (SN) | 920.886 |
| Quilometragem na via (KM) | 12.215 |
| Sistema alternativo | 89.562 |
| Distinção no mesmo número | 81.845 |
| Outros modificadores | 21.622 |

Isso mostra que a separação número/modificador não é um caso marginal no estado.

## Elementos de complemento mais comuns no Rio Grande do Sul

Tabela 6 das Notas metodológicas n. 04:

| Elemento | Endereços RS |
|---|---:|
| APARTAMENTO | 1.036.368 |
| CASA | 654.666 |
| FUNDOS | 277.009 |
| BLOCO | 248.173 |
| FRENTE | 214.179 |
| TERREO | 97.017 |
| SOBRADO | 89.335 |
| ANDAR | 64.051 |
| QUADRA | 17.937 |
| LOTE | 6.133 |

O padrão CNEFE também documenta elementos como ENTRADA, RUA INTERNA, SALA, COMODO, COBERTURA
e SUBSOLO. A nota de 2022 informa a inclusão de PORTARIA, SALAO e PALAFITA entre elementos
válidos. Esses rótulos foram adicionados de forma conservadora à taxonomia v3.3.5.

## Hierarquia

O IBGE orienta registrar complementos do mais abrangente para o mais específico. Entre as
sequências explicitamente tratadas estão:

- QUADRA -> LOTE;
- BLOCO -> APARTAMENTO;
- BLOCO -> ANDAR -> APARTAMENTO;
- ANDAR -> APARTAMENTO.

O Manual do Recenseador também exemplifica `ENTRADA 1 -> BLOCO A -> APARTAMENTO 304`.

## Fontes oficiais

- IBGE. *Censo Demográfico 2022: Cadastro Nacional de Endereços para Fins Estatísticos - CNEFE.
  Notas metodológicas n. 04*, 2024. https://biblioteca.ibge.gov.br/visualizacao/livros/liv102091.pdf
- IBGE. Arquivos CNEFE 2022 por município/UF. https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/
- IBGE. *Manual do Recenseador* / padrão de registro de endereços, exemplos de elemento/valor.
