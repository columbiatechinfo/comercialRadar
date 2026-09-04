# A galeria de auditoria da quadra

Gera **uma página HTML** com os POIs julgados: as imagens exatas que a IA
recebeu, a percepção cega, o prompt de julgamento como foi enviado, os links e
o veredito. Serve para conferir o trabalho da IA sem abrir o banco.

```bash
docker run --rm --name radar-galquadra --network host --env-file .env \
  -v "$PWD":/app -v /tmp/saida:/app/saida -w /app \
  radar-minerador:latest python -u scripts/galeria/gerar_galeria.py
```

`GALERIA_LIMITE=5` e `GALERIA_SAIDA=...` produzem uma amostra pequena — que é
o que se renderiza para conferir o leiaute antes de publicar a inteira.

**A escala das imagens não é fixa.** O destino tem teto de 16 MB e base64 infla
37%; o script tenta uma escada de (largura, qualidade) e para na primeira que
cabe. Assim a página fica com a melhor imagem que cabe, em vez de uma escolhida
no chute. Em 04/09/2026, com 143 POIs, coube o degrau de cima: 420 px q62,
11,7 MB.

**A página não pode depender do invólucro.** `#lupa` tem `display:flex`, e isso
vence o atributo `hidden`: sem a regra `#lupa[hidden]{display:none}` a lupa
nasce aberta por cima de tudo. Alguns invólucros trazem
`[hidden]{display:none!important}` e escondem o defeito — o arquivo solto não
tem esse socorro. Foi assim que o defeito apareceu, e é por isso que
`render_amostra.py` existe: ele abre a amostra num Chromium de verdade, nos
dois temas, e fotografa.
