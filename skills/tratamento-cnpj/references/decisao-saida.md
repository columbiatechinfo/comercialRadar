# Decisão e Estrutura de Saída

> **Atualizado na v2.2.** Ver `auditoria-v21-v22.md` para o que mudou.

## Eixos independentes

### Aptidão geográfica

```text
APTO_COORDENADA
APTO_ENDERECO_COMPLETO
APTO_ENDERECO_PARCIAL
APTO_CEP_APENAS
NAO_APTO
```

### Perfil comercial

```text
COMERCIO_ATENDIMENTO
COMERCIO_EVENTUAL
PONTO_COMERCIAL_OUTRO
PRESENCIAL_SEM_PUBLICO
RESIDENCIAL_NAO_COMERCIAL
NAO_COMERCIAL
```

### Evidência de existência

```text
EXISTE_QUASE_CERTO
EXISTE_PROVAVEL
EXISTE_POSSIVEL
EXISTE_INCERTO
SEM_EVIDENCIA
INDETERMINADA_POS_CNEFE
INDETERMINADA_DATA_AUSENTE
```

### Potencial de cruzamento

É a decisão operacional final, mas não apaga os demais eixos. Um cadastro pode
ser potencial comercial e ainda estar pendente de coordenada.

### Rota de tratamento (v2.2)

```text
RECLASSIFICACAO_1_1      indicador IBGE = 1 (estabelecimento único)
INDIVIDUALIZACAO_MULTI   indicador IBGE = 2, 3 ou 4
VERIFICAR_CAMPO          sem indicador, ou só domicílio
SEM_ROTA                 sem match
```

Deriva do `COD_INDICADOR_ESTAB_ENDERECO`, nunca da contagem de registros CNEFE.

## Abas

Além do workbook, a v2.2 gera Parquet e CSV por padrão — é o contrato de handoff
para as skills a jusante e a fonte de verdade acima do limite do Excel.

A `BASE_COMPLETA` é a fonte de auditoria. As abas `POTENCIAL_CRUZAMENTO` e
`SEM_POTENCIAL` são visões derivadas, nunca substitutas da base completa.

## Segurança de Excel

A exportação desativa interpretação automática de fórmulas e URLs em strings,
reduzindo risco de formula injection em campos vindos de arquivos externos.

## Limite físico do Excel (v2.2)

1.048.576 linhas por aba. `xlsxwriter.write()` acima disso devolve `-1` e ignora
em silêncio, e o QA roda antes da gravação. A v2.2 tem guarda explícita, fallback
para Parquet/CSV com aviso na aba `LEIA_ME`, e relê o arquivo gravado para
reconferir a contagem.
