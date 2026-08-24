# Inventário LGPD — extracao-cadastur-mtur

**Decisão registrada (do usuário):** carregar tudo, sem camada minimizada.
Esta skill não suprime dado; classifica e avisa. `inventario_pii.csv` é gerado
a cada execução.

## Classe de risco por dataset

| Grupo | Datasets | Natureza |
|---|---|---|
| PJ | 14 datasets (hospedagem, agência, restaurante, transportadora, parque…) | dado de pessoa jurídica + **dados pessoais de contato** do responsável (nome, CPF, e-mails, telefones). MEI e empresário individual: o próprio CNPJ e o endereço identificam pessoa natural |
| PF | `prestadores-de-servicos-turisticos-guia-turismo_2` | cadastro de **pessoas naturais**: nome, CPF, data de nascimento, sexo, nacionalidade, documento de identificação, carteira de estrangeiro, município de atuação e **tipo sanguíneo** |

## O item que muda a classe

`tipo_sanguineo` é **dado pessoal sensível** (saúde) — LGPD art. 5º II. É
tratamento de dado sensível em massa: exige base legal específica do art. 11, não
bastando a do art. 7º. Dado tornado público **não** dispensa base legal para novo
tratamento (art. 7º §3º), e a finalidade original do Cadastur é verificação de
regularidade de prestador.

## Antes de compartilhar qualquer saída

1. Definir finalidade e base legal do tratamento — por dataset, não em bloco.
2. Compartilhamento com terceiro (cliente, PJ subcontratada) exige DPA e a
   pergunta operador × controlador respondida por escrito.
3. Se o destino não precisa do dado pessoal, gerar a projeção sem as colunas de
   `inventario_pii.csv` no momento da entrega — a supressão sai da carga e vai
   para o compartilhamento.
4. Acesso em repouso: RLS ou schema separado para as colunas de PF.

Aprofundamento: acionar `lgpd-seginfo-utilities`.
