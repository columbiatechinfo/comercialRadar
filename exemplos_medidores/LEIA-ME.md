# Exemplos de referência para a IA

Recortes **reais** de Street View, tirados pelo usuário em 14/08/2026. Vão junto
de toda leitura de fachada, rotulados e **antes** da foto do imóvel, com o aviso
de que não são o imóvel avaliado — exemplo solto no meio das fotos do alvo é
lido como se fosse do alvo, e o modelo passa a contar o hidrômetro do exemplo na
fachada do cliente.

## O nome do arquivo é a legenda

```
<categoria>_<o que a imagem mostra>.png
```

O prefixo define a categoria e o resto vira o texto que acompanha a imagem:
`agua_cavalete_com_registro_azul.png` chega ao modelo como
*"caixa de medição de ÁGUA (hidrômetro) — cavalete com registro azul"*.

| Prefixo | Categoria |
|---|---|
| `agua_` | caixa de medição de ÁGUA (hidrômetro) |
| `energia_` | caixa de medição de ENERGIA (padrão de entrada) |
| `esgoto_` | caixa de inspeção de ESGOTO |

Nome fora desses três prefixos é **ignorado com aviso no log** — nunca em
silêncio, senão a foto some do prompt e ninguém descobre. Valem `.jpg`, `.jpeg`,
`.png` e `.webp`; cada uma vira JPEG de no máximo 768 px antes de ir, porque
foto de celular de 4 MB multiplicaria por dezenas de milhares de imagens.

**A legenda é o que faz o acervo valer.** Nove fotos dizendo "caixa de água"
desperdiçam oito; o que ensina é a diferença entre elas — cavalete, caixa
embutida, caixa com telhado, bateria de relógios.

## O acervo — 19 recortes

**Água (9)** · `caixa_com_dois_relogios` · `caixa_com_telhado_atras_da_grade` ·
`caixa_em_mureta_verde` · `caixa_embutida_muro_azul` ·
`caixa_escura_junto_ao_portao` · `caixa_no_pilar_do_portao` ·
`caixa_rente_ao_chao_muro_amarelo` · `cavalete_com_registro_azul` ·
`cavalete_em_muro_de_tijolo`

**Energia (6)** · `caixa_alta_no_muro_com_poste` · `caixa_branca_vertical` ·
`caixa_pequena_em_parede_azul` · `medidor_em_nicho_de_tijolo` ·
`medidor_embutido_no_concreto` · `padrao_completo_sobre_base`

**Esgoto (4)** · `tampa_junto_ao_meio_fio` · `tampa_quadrada_no_passeio` ·
`tampa_redonda_em_frente_ao_portao` · `tampa_redonda_na_calcada`

## Custo, medido

267 KB e ~1.615 tokens de referência por POI, em `detail: low`. Sobre os 22,5
mil POIs de Canoas, no gpt-4o-mini, o acervo inteiro custa **US$ 5,46** — contra
US$ 1,72 se fossem só seis. A diferença não paga escolher por conta própria
quais variantes o modelo pode ver.

O teto do carregador é **24 arquivos**; passar disso faz ele **avisar no log**
quais ficaram de fora, com o nome de cada um.

## O que ainda pesa, e não é dinheiro

Muita referência antes do alvo dilui a atenção do modelo. Se as leituras
começarem a hesitar, ou a citar o exemplo em vez da fachada, **tirar arquivos da
pasta é o primeiro ajuste** — não mexer no prompt.

**Só vale para modelo remoto.** No `qwen2.5vl` do i9 o contexto já fica no
limite com fachada + foto do Maps, e imagem a mais devolve **400 em silêncio**.

**Nunca invente um exemplo.** Imagem inventada ensina o modelo a reconhecer a
coisa errada, e ele passa a errar com confiança.
