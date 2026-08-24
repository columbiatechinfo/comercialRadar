"""Reconciliacao das tres geracoes de layout.

INVARIANTE R1 — nenhuma coluna de origem e' descartada. Rotulo conhecido vira
coluna canonica; rotulo desconhecido vai INTEGRO para `_extras` (JSON) e e'
registrado em colunas_desconhecidas.csv. O mapa e' ergonomia; a integridade nao
depende dele.

Campos semanticamente distintos NAO sao fundidos (ex.: 'Data de Abertura' da RFB
!= 'DATA INICIO OPERACAO' do legado; 'E-MAIL2' != 'E-mail Institucional').
"""
from __future__ import annotations

import json
import unicodedata

import pandas as pd

PROCEDENCIA = ["_dataset", "_atividade", "_recurso_id", "_recurso_nome",
               "_ref_ano", "_ref_trimestre", "_ref_periodo", "_formato_ckan",
               "_formato_real", "_encoding", "_separador", "_aba", "_sha256",
               "_linha_origem", "_extraido_em"]

MAPA: dict[str, str] = {
    # --- identidade ---------------------------------------------------------
    "ATIVIDADE TURISTICA": "atividade_turistica",
    "NUMERO DE INSCRICAO DO CNPJ": "cnpj", "CNPJ": "cnpj",
    "NOME DA PESSOA JURIDICA": "razao_social", "RAZAO SOCIAL": "razao_social",
    "NOME FANTASIA": "nome_fantasia",
    "NOME": "nome_pessoa_fisica", "NOME COMPLETO": "nome_pessoa_fisica",
    "SITUACAO CADASTRAL": "situacao_cadastral", "SITUACAO": "situacao_cadastral",
    "SITUACAO DA ATIVIDADE": "situacao_atividade",
    "SITUACAO TRAMITE": "situacao_tramite",
    "TIPO DE ESTABELECIMENTO": "tipo_estabelecimento",
    "NATUREZA JURIDICA": "natureza_juridica",
    "PORTE": "porte",
    "DATA DE ABERTURA": "data_abertura",
    "DATA INICIO OPERACAO": "data_inicio_operacao",
    "POSSUI EMPREGADO": "possui_empregado",
    # --- endereco -----------------------------------------------------------
    "ENDERECO COMPLETO RECEITA FEDERAL": "endereco_rfb",
    "ENDERECO COMPLETO COMERCIAL": "endereco_comercial",
    "LOGRADOURO": "logradouro", "BAIRRO": "bairro", "CEP": "cep",
    "UF": "uf", "MUNICIPIO": "municipio", "LOCALIDADE": "municipio",
    "MUNICIPIO DE ATUACAO": "municipio_atuacao",
    # --- contato ------------------------------------------------------------
    "NOME DO RESPONSAVEL": "nome_responsavel",
    "TELEFONE INSTITUCIONAL": "telefone_institucional",
    "TELEFONE COMERCIAL": "telefone_comercial", "TELEFONE": "telefone_comercial",
    "FAX": "fax",
    "E-MAIL DO USUARIO ADMINISTRADOR": "email_administrador",
    "E-MAIL INSTITUCIONAL": "email_institucional",
    "E-MAIL COMERCIAL": "email_comercial",
    "E-MAIL2": "email_2", "E-MAIL3": "email_3",
    "WEBSITE": "website", "SITE": "website",
    # --- certificado / documento -------------------------------------------
    "NUMERO DO CERTIFICADO": "numero_certificado",
    "CODIGO CERTIFICADO": "numero_certificado",
    "VALIDADE DO CERTIFICADO": "validade_certificado",
    "CPF": "cpf",
    # --- CNAE ---------------------------------------------------------------
    "CNAE(S) RELACIONADOS A ATIVIDADE": "cnae",
    "CODIGO E DESCRICAO CNAE": "cnae_codigo_descricao",
    "CODIGO CNAE": "cnae_codigo", "DESCRICAO CNAE": "cnae_descricao",
    # --- hospedagem ---------------------------------------------------------
    "TIPO DE HOSPEDAGEM": "tipo_hospedagem",
    "TIPO ATIVIDADE": "tipo_atividade", "SUBTIPO ATIVIDADE": "subtipo_atividade",
    "UNIDADE HABITACIONAIS": "uh", "UH": "uh",
    "LEITOS": "leitos", "TOTAL DE LEITOS": "leitos",
    "UHS ACESSIVEIS": "uh_acessiveis", "LEITOS ACESSIVEIS": "leitos_acessiveis",
    "UHS PARA CAO GUIA": "uh_cao_guia", "UHS TPS": "uh_tps",
    # --- atributos das demais atividades ------------------------------------
    "IDIOMAS": "idiomas", "IDIOMA": "idiomas", "LINGUAS": "idiomas",
    "TIPO": "tipo", "ESPECIALIDADE": "especialidade",
    "CAPACIDADE": "capacidade",
    "CAPACIDADE DE LUGARES": "capacidade_lugares",
    "AREA TOTAL CONSTRUIDA(M2)": "area_total_construida_m2",
    "AREA TOTAL CONSTRUIDA EM M2": "area_total_construida_m2",
    "AREA LOCAVEL(M2)": "area_locavel_m2", "AREA LOCAVEL EM M2": "area_locavel_m2",
    "AREA MONTAGEM": "area_montagem",
    "ESTRUTURA BASICA": "estrutura", "ESTRUTURA": "estrutura",
    "EQUIPAMENTOS": "equipamentos", "SERVICOS": "servicos",
    "TIPO DA ESTRUTURA NAUTICA": "tipo_estrutura_nautica",
    "CARACTERIZACAO DA MARINA": "caracterizacao_marina",
    "TIPO DE VEICULOS - AQUATICOS": "veiculos_aquaticos",
    "TIPO DE VEICULOS - TERRESTRES": "veiculos_terrestres",
    "VEICULOS TERRESTRES": "veiculos_terrestres",
    "QUANTIDADE DE VEICULOS": "qtd_veiculos",
    "QUANTIDADE DE EMBARCACOES": "qtd_embarcacoes",
    "EMBARCACOES CRUZEIRO MARITIMO": "embarcacoes_cruzeiro_maritimo",
    "EMBARCACOES CRUZEIRO FLUVIAL/BARCO-HOTEL": "embarcacoes_cruzeiro_fluvial",
    "CATEGORIA EVENTO (ATUACAO PRINCIPAL)": "categoria_evento",
    "TIPO DE EVENTOS": "tipo_eventos", "TIPO DE EVENTO": "tipo_eventos",
    "SEGMENTOS TURISTICOS": "segmentos_turisticos",
    "SEGMENTO(S)": "segmentos_turisticos",
    "MODALIDADES": "modalidades",
    "CATEGORIA DE ATUACAO": "categorias", "CATEGORIA(S)": "categorias",
    "CATEGORIAS": "categorias",
    "ATIVIDADES OBRIGATORIAS": "atividades_obrigatorias",
    "ATIVIDADES OPCIONAIS": "atividades_opcionais",
    "AREA TOTAL DO EMPREENDIMENTO": "area_total_empreendimento",
    "AREA TOTAL DO ESTABELECIMENTO": "area_total_empreendimento",
    "AMBIENTACAO TEMATICA PRINCIPAL": "ambientacao", "AMBIENTACAO": "ambientacao",
    "VOLUME DE AGUA": "volume_agua",
    "QUANTIDADE": "quantidade", "ORDEM": "ordem",
    "GUIA MOTORISTA": "guia_motorista",
    # --- guia de turismo (pessoa fisica) ------------------------------------
    "SEXO": "sexo", "DATA DE NASCIMENTO": "data_nascimento",
    "NACIONALIDADE": "nacionalidade", "NOME SOCIAL/TRATAMENTO": "nome_social",
    "DOCUMENTO DE IDENTIFICACAO": "documento_identificacao",
    "CARTEIRA DE ESTRANGEIRO": "carteira_estrangeiro",
    "ORGAO EXPEDIDOR": "orgao_expedidor",
    "VALIDADE": "validade", "DATA DE VALIDADE": "data_validade",
    "TIPO SANGUINEO": "tipo_sanguineo",
    # --- controle interno da leitura ----------------------------------------
    "_ABA": "_aba_origem",
    "_LINHA_FISICA_ORIGEM": "_linha_fisica_origem",
    "_TABELA_ORIGEM": "_tabela_origem",
}

# Classificacao LGPD por coluna canonica. Nao suprime nada: documenta.
PII = {
    "cpf": "identificador de pessoa natural",
    "nome_pessoa_fisica": "dado pessoal",
    "nome_responsavel": "dado pessoal",
    "nome_social": "dado pessoal",
    "documento_identificacao": "identificador de pessoa natural",
    "carteira_estrangeiro": "identificador de pessoa natural",
    "orgao_expedidor": "dado pessoal",
    "data_nascimento": "dado pessoal",
    "sexo": "dado pessoal",
    "nacionalidade": "dado pessoal (origem nacional)",
    "tipo_sanguineo": "DADO PESSOAL SENSIVEL (saude) - LGPD art. 5o II",
    "telefone_institucional": "dado pessoal de contato",
    "telefone_comercial": "dado pessoal de contato",
    "fax": "dado pessoal de contato",
    "email_administrador": "dado pessoal de contato",
    "email_institucional": "dado pessoal de contato",
    "email_comercial": "dado pessoal de contato",
    "email_2": "dado pessoal de contato", "email_3": "dado pessoal de contato",
    "endereco_rfb": "dado pessoal quando MEI/empresario individual",
    "endereco_comercial": "dado pessoal quando MEI/empresario individual",
    "cnpj": "dado pessoal quando MEI/empresario individual",
}


def chave(rotulo: str) -> str:
    s = unicodedata.normalize("NFKD", str(rotulo)).encode("ascii", "ignore").decode()
    s = " ".join(s.replace("?", "").replace(":", "").split())
    return s.upper().strip()


def canoniza(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Canoniza sem perder valores de aliases concorrentes.

    Quando dois rotulos conhecidos convergem para a mesma coluna canonica, o
    primeiro valor nao-vazio alimenta a coluna principal e TODAS as colunas
    secundarias ficam tambem em `_extras`. Assim, divergencias de origem nunca
    sao apagadas pelo processo de normalizacao.
    """
    conhecidas: dict[str, list[str]] = {}
    desconhecidas: list[str] = []
    for col in df.columns:
        alvo = MAPA.get(chave(col))
        if alvo:
            conhecidas.setdefault(alvo, []).append(col)
        else:
            desconhecidas.append(col)

    out = pd.DataFrame(index=df.index)
    aliases_secundarios: list[str] = []
    for alvo, origens in conhecidas.items():
        s0 = df[origens[0]].astype(str)
        for extra in origens[1:]:
            sx = df[extra].astype(str)
            s0 = s0.where(s0.str.strip() != "", sx)
            aliases_secundarios.append(extra)
        out[alvo] = s0

    # Desconhecidas + aliases secundarios: preservacao integral de tudo que
    # nao cabe 1:1 na coluna canonica.
    extras_cols = list(dict.fromkeys(desconhecidas + aliases_secundarios))
    if extras_cols:
        sub = df[extras_cols].astype(str)
        out["_extras"] = [json.dumps(r, ensure_ascii=False)
                          for r in sub.to_dict(orient="records")]
    else:
        out["_extras"] = "{}"

    cobertas = {c for cols in conhecidas.values() for c in cols} | set(desconhecidas)
    faltando = set(df.columns) - cobertas
    if faltando:
        raise AssertionError(f"colunas perdidas na canonizacao: {sorted(faltando)}")
    return out, desconhecidas
