# -*- coding: utf-8 -*-
"""Semeia o catálogo de campos das abas, a partir do que a base REALMENTE tem.

POR QUE EXISTE, E POR QUE NÃO É UM `INSERT` NA MIGRATION

O catálogo diz o que cada aba mostra. Migration cria estrutura; conteúdo que
depende do tenant e que o operador vai ajustar não é estrutura — é semente.
Rodar de novo não duplica (`on conflict`), e um campo desligado à mão continua
desligado.

DE ONDE VIERAM OS CAMPOS

Da coluna que existe em `pois` e `analise_ia`, conferidas antes de escrever
esta lista. Campo inventado no catálogo vira linha vazia na tela — e linha
vazia é pior que campo ausente, porque parece dado perdido.

O PESO NÃO É ENFEITE

`forte`, `media`, `neutra` e `contraria` dizem o que o campo FAZ pela
conclusão. `contraria` existe porque evidência contra é evidência: o
`revisar_motivo` e o veredito negativo da IA precisam pesar contra, não sumir.
Esconder o que desmente é o jeito mais rápido de fabricar confiança falsa.

A PROCEDÊNCIA fica na coluna, mas o RBAC diz que só o `root` a enxerga. Está
aqui para ele; a API a omite para os demais.
"""
from __future__ import annotations

import sys

import base_comum as bc

# fonte, chave, rótulo, grupo, ordem, peso, formato, procedência, ajuda
CAMPOS = [
    # ── POI: o que a captura e o painel do Maps deram ────────────────────────
    ("poi", "nome", "Nome no Maps", "Identificação", 10, "forte", "texto",
     "Google Maps", None),
    ("poi", "categoria", "Categoria", "Identificação", 20, "media", "texto",
     "Google Maps", None),
    ("poi", "endereco", "Endereço", "Localização", 30, "forte", "texto",
     "Google Maps", None),
    ("poi", "cidade", "Cidade", "Localização", 40, "neutra", "texto",
     "Google Maps", None),
    ("poi", "uf", "UF", "Localização", 50, "neutra", "texto",
     "Google Maps", None),
    ("poi", "maps_lat", "Latitude", "Localização", 60, "media", "numero",
     "Google Maps", "coordenada do painel; prevalece sobre a do IBGE"),
    ("poi", "maps_lng", "Longitude", "Localização", 70, "media", "numero",
     "Google Maps", None),
    ("poi", "distancia_m", "Distância da origem", "Localização", 80, "media",
     "numero", "calculado",
     "metros entre a coordenada de origem e a que o Maps devolveu"),
    ("poi", "telefone", "Telefone", "Contato", 90, "forte", "texto",
     "Google Maps", None),
    ("poi", "website", "Site", "Contato", 100, "media", "url",
     "Google Maps", None),
    ("poi", "similaridade", "Semelhança do nome", "Casamento", 110, "media",
     "numero",
     "calculado",
     "0 a 1. Abaixo de 0,90 sem coordenada, a IA julga a identidade"),
    ("poi", "match_valido", "Confere com o pedido", "Casamento", 120, "forte",
     "texto", "calculado", None),
    ("poi", "revisar_motivo", "Motivo de revisão", "Casamento", 130,
     "contraria", "texto", "calculado",
     "quando presente, algo no cruzamento não fechou"),

    # ── Informações Google: o que o painel enriquecido trouxe ────────────────
    ("google", "avaliacao", "Nota", "Reputação", 10, "media", "numero",
     "Google Maps", None),
    ("google", "total_avaliacoes", "Nº de avaliações", "Reputação", 20,
     "media", "numero", "Google Maps",
     "nota alta com duas avaliações não é reputação, é amostra"),
    ("google", "resumo_avaliacoes", "O que dizem", "Reputação", 30, "media",
     "texto", "Google Maps", None),
    ("google", "status_horario", "Situação agora", "Funcionamento", 40,
     "forte", "texto", "Google Maps",
     "'Permanentemente fechado' é evidência forte CONTRA o ponto existir"),
    ("google", "plus_code", "Plus Code", "Localização", 50, "neutra", "texto",
     "Google Maps", None),
    ("google", "maps_url", "Ficha no Maps", "Localização", 60, "neutra", "url",
     "Google Maps", None),

    # ── Receita Federal ──────────────────────────────────────────────────────
    ("receita", "cnpj", "CNPJ", "Registro", 10, "forte", "texto",
     "Receita Federal", "confirmado contra a base, não copiado de página"),
    ("receita", "razao_social", "Razão social", "Registro", 20, "forte",
     "texto", "Receita Federal", None),
    ("receita", "nome_fantasia", "Nome fantasia", "Registro", 30, "forte",
     "texto", "Receita Federal",
     "é o nome da placa; a razão social quase nunca é"),
    ("receita", "situacao_cadastral", "Situação", "Registro", 40, "forte",
     "texto", "Receita Federal",
     "BAIXADA no endereço de loja aberta é achado, não descarte"),
    ("receita", "cnae", "CNAE principal", "Atividade", 50, "media", "texto",
     "Receita Federal",
     "CNAE incompatível com a categoria do Maps derruba o casamento"),
    ("receita", "natureza_juridica", "Natureza jurídica", "Registro", 60,
     "neutra", "texto", "Receita Federal", None),
    ("receita", "socios", "Sócios", "Registro", 70, "neutra", "lista",
     "Receita Federal", None),
    ("receita", "cnpj_conf", "Confiança do CNPJ", "Registro", 80, "media",
     "numero", "calculado", None),

    # ── Redes Sociais ────────────────────────────────────────────────────────
    ("redes_sociais", "instagram", "Instagram", "Perfis", 10, "media", "url",
     "busca web", "o handle sai do painel do Maps, não é adivinhado do nome"),
    ("redes_sociais", "facebook", "Facebook", "Perfis", 20, "media", "url",
     "busca web", None),
    ("redes_sociais", "email", "E-mail", "Contato", 30, "media", "texto",
     "busca web", None),

    # ── Delivery ─────────────────────────────────────────────────────────────
    ("delivery", "presente_no_ifood", "Está no iFood", "Presença", 10, "forte",
     "texto", "iFood",
     "loja no iFood é loja operando: a plataforma tira quem fecha"),
    ("delivery", "ifood_visto_em", "Visto no iFood em", "Presença", 20,
     "media", "data", "iFood", None),
    ("delivery", "preco_medio", "Preço médio", "Porte", 30, "neutra", "moeda",
     "iFood", None),

    # ── IA das Imagens ───────────────────────────────────────────────────────
    ("imagens", "veredito", "Veredito", "Conclusão", 10, "forte", "texto",
     "IA de visão", None),
    ("imagens", "veredito_motivo", "Por quê", "Conclusão", 20, "forte",
     "texto", "IA de visão", None),
    ("imagens", "confere", "Confere com o cadastro", "Conclusão", 30, "forte",
     "texto", "IA de visão", None),
    ("imagens", "atividade_real", "Atividade vista na imagem", "Leitura", 40,
     "forte", "texto", "IA de visão",
     "o que a fachada mostra, sem olhar o cadastro"),
    ("imagens", "porte", "Porte", "Leitura", 50, "media", "texto",
     "IA de visão", None),
    ("imagens", "pessoas_estimadas", "Pessoas estimadas", "Leitura", 60,
     "media", "numero", "IA de visão", None),
    ("imagens", "tipo_construcao", "Tipo de construção", "Leitura", 70,
     "media", "texto", "IA de visão", None),
    ("imagens", "outro_estabelecimento", "Outro estabelecimento na imagem",
     "Leitura", 80, "contraria", "texto", "IA de visão",
     "quando verdadeiro, a fachada pode ser do vizinho"),
    ("imagens", "n_imagens", "Imagens analisadas", "Leitura", 90, "neutra",
     "numero", "calculado",
     "conclusão sobre uma imagem só é conclusão frágil"),
    ("imagens", "recomendar_visita", "IA recomenda visita", "Conclusão", 100,
     "media", "texto", "IA de visão", None),
    ("imagens", "recomendacao_motivo", "Motivo da recomendação", "Conclusão",
     110, "media", "texto", "IA de visão", None),
]

SQL = """
insert into comercialradar.campo_catalogo
    (fonte, chave, rotulo, grupo, ordem, peso, formato, procedencia, ajuda)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (tenant_id, fonte, chave) do update set
    rotulo = excluded.rotulo, grupo = excluded.grupo, ordem = excluded.ordem,
    peso = excluded.peso, formato = excluded.formato,
    procedencia = excluded.procedencia, ajuda = excluded.ajuda
"""


def semear() -> dict:
    con = bc.conectar()
    try:
        with con.cursor() as k:
            k.executemany(SQL, CAMPOS)
            k.execute("""select fonte::text, count(*)
                           from comercialradar.campo_catalogo
                          where ativo group by fonte order by fonte""")
            por_aba = dict(k.fetchall())
        con.commit()
        return {"campos": len(CAMPOS), "por_aba": por_aba}
    finally:
        con.close()


if __name__ == "__main__":
    r = semear()
    print(f"{r['campos']} campos no catálogo")
    for aba, n in r["por_aba"].items():
        print(f"  {aba:15} {n}")
    sys.exit(0)
