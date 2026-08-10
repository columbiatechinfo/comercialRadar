# Biblioteca de exemplos — calibração visual do descrever_imagens.py

Imagens representativas por tipo de situação, confirmadas 1-a-1 com o cliente.
Servem de referência (few-shot) pra o modelo se ambientar em cada motivo de reprovação.

## Categorias
- **pano_aereo/** — fotoesfera aérea de usuário (drone/torre) ou de dentro de guarita:
  copas, telhados vistos do alto, muito céu. Não serve pra conferir fachada.
  Motivo sugerido: `pano_aereo` (separar de `imagem_insuficiente`).
- **area_aberta/** — praça, terreno, muro, capoeira, obra parada — sem estabelecimento
  ativo. Motivo: `sem_estabelecimento`. Inclui terreno vazio (Estágio II de praça).
