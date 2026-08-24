# Contrato de Dados

> **Atualizado na v2.2.** Ver `auditoria-v21-v22.md` para o que mudou.

## Base CNPJ

Campos obrigatórios canônicos:

| Campo | Uso |
|---|---|
| `cnpj_completo` | identificação e validação |
| `situacao` | regra de atividade |
| `logradouro` | match de endereço |
| `numero` | match individual ou S/N |
| `cep` | restrição de candidatos |
| `cnae_principal_cod` | perfil físico e segmento |

Campos opcionais enriquecem a decisão: razão social, nome fantasia, tipo
matriz/filial, data de abertura, natureza jurídica, porte, capital, complemento,
bairro, município e UF.

A leitura aceita aliases definidos no script. Ambiguidade entre aliases causa
erro. Isso evita selecionar silenciosamente a coluna errada.

A v2.2 acrescenta `tipo_logradouro` como campo canônico próprio — o layout de
estabelecimentos da RFB traz o tipo separado do nome, e sem ele o passe P1 nunca
dispara. E aplica guarda de conteúdo no alias `tipo`: uma coluna `TIPO` cujos
valores são tipos de logradouro é remapeada com alerta, em vez de ser lida como
matriz/filial.

## CNEFE

Colunas obrigatórias:

```text
COD_UNICO_ENDERECO
NOM_TIPO_SEGLOGR
NOM_TITULO_SEGLOGR
NOM_SEGLOGR
NUM_ENDERECO
CEP
NV_GEO_COORD
LATITUDE
LONGITUDE
COD_ESPECIE
DSC_ESTABELECIMENTO
```

Colunas OPCIONAIS ingeridas quando presentes (v2.2). A ausência degrada com
alerta registrado no resumo, nunca em silêncio:

```text
COD_MUNICIPIO                  gate de município
NUM_FACE, NUM_QUADRA, COD_SETOR   face de quadra (exposta; ainda não usada no match)
DSC_MODIFICADOR                modificador do número — entra na chave de match
COD_INDICADOR_ESTAB_ENDERECO   1 único / 2 até 10 / 3 mais de 10 / 4 desconhecido
COD_TIPO_ESPECIE, DSC_LOCALIDADE
NOM_COMP_ELEM1..5, VAL_COMP_ELEM1..5   complemento estruturado
```

O `COD_INDICADOR_ESTAB_ENDERECO` é o que decide a rota de tratamento. A contagem
de registros do endereço mistura domicílio com estabelecimento e não serve para
isso.

O ZIP é inspecionado pelo cabeçalho. Se nenhum ou mais de um arquivo interno
satisfizer o esquema, a execução falha.

## Regras de tipo

- identificadores permanecem texto;
- coordenadas e NV são convertidos explicitamente;
- flags não são comparadas por string solta;
- `pd.NA` nunca é convertido diretamente com `bool()`;
- datas usam convenção brasileira;
- valores monetários registram status de parsing.

## Integridade

`_RID` é criado antes de qualquer transformação. Toda recomposição deve ordenar
por `_RID` antes do QA e da exportação.
