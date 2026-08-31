# -*- coding: utf-8 -*-
"""ferramenta_ponto.py — o chat fecha o ciclo: grava, fotografa e cruza.

O PROBLEMA QUE ELA RESOLVE

As ferramentas do chat sempre foram de LEITURA. `consultar_maps` abre o painel,
`consultar_receita` acha o CNPJ, `consultar_instagram` lê o perfil — e tudo isso
morre na conversa. Quem conversava com o agente descobria o telefone, o CNPJ e o
endereço de um estabelecimento, fechava a aba, e o banco continuava sem nada.

Aqui o chat ganha o último degrau, o mesmo que os outros caminhos já têm:

    1. GRAVA   pelo `realtime_ingest.ingerir_registro` — o escritor único, que
               conhece a deduplicação, o merge não-destrutivo e o resgate de
               fachada e foto já pagas.
    2. FOTOGRAFA a fachada no Street View, se ainda não houver.
    3. CRUZA   contra o cadastro do cliente, a Receita, o iFood e os demais
               POIs — que é o que diz se aquele ponto já é cobrado como
               comercial.

POR QUE UMA FERRAMENTA EXPLÍCITA, E NÃO GRAVAR EM TODA CONSULTA

Porque o chat também é usado para explorar. "Como se chama aquele hotel perto da
rodoviária?" é uma pergunta, não uma decisão de cadastrar. Se toda consulta
virasse POI, a base encheria de tentativa — e a fila de aprovação, que é trabalho
humano, encheria junto.

O agente chama esta ferramenta quando o item está CONFIRMADO. A decisão de
guardar é dele, com o contexto da conversa; a de como guardar é deste arquivo.

O QUE ELA NÃO FAZ

Não inventa coordenada. Sem `lat`/`lng` não há POI: o ponto no mapa é o que leva
alguém a campo, e um marcador no centro da cidade custa uma visita perdida. Ela
recusa e diz o que falta, em vez de gravar um ponto que ninguém consegue visitar.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import config  # noqa: F401
import base_comum as bc
import realtime_ingest

RAIZ = Path(__file__).resolve().parent


def _coordenada(dados: dict) -> tuple:
    for a, b in (("lat", "lng"), ("maps_lat", "maps_lng"),
                 ("lat_origem", "lng_origem")):
        la, lo = dados.get(a), dados.get(b)
        if la is not None and lo is not None:
            try:
                return float(la), float(lo)
            except (TypeError, ValueError):
                continue
    return None, None


def guardar_ponto(nome: str, lat: float = None, lng: float = None,
                  endereco: str = "", telefone: str = "", cnpj: str = "",
                  categoria: str = "", website: str = "", email: str = "",
                  instagram: str = "", razao_social: str = "",
                  cidade: str = "", uf: str = "", maps_url: str = "",
                  observacao: str = "",
                  capturar_fachada: bool = True,
                  cruzar: bool = True) -> dict:
    """Grava o estabelecimento como POI e completa o ciclo.

    Devolve o `poi_id` e o que cada etapa fez. Chamar de novo com o mesmo ponto
    ATUALIZA — a deduplicação por nome e coordenada é a mesma dos demais
    caminhos, e um campo vazio agora nunca apaga um campo bom já salvo.
    """
    nome = (nome or "").strip()
    if not nome:
        return {"erro": "informe o nome do estabelecimento"}

    la, lo = _coordenada({"lat": lat, "lng": lng})
    if la is None:
        return {"erro": "sem coordenada — não gravo ponto que ninguém "
                        "consegue visitar. Use `consultar_maps` para obter "
                        "lat/lng do estabelecimento e chame de novo."}

    registro = {
        "fonte": "chat",
        "nome": nome,
        "categoria": categoria or None,
        "endereco": endereco or None,
        "telefone": telefone or None,
        "website": website or None,
        "email": email or None,
        "instagram": instagram or None,
        "cnpj": cnpj or None,
        "razao_social": razao_social or None,
        "cidade": cidade or None,
        "uf": uf or None,
        "maps_url": maps_url or None,
        "lat_origem": la, "lng_origem": lo,
        "maps_lat": la, "maps_lng": lo,
        "fonte_dado": "chat",
        # O ingestor pula quem não tem `match_valido`. Aqui é True porque quem
        # confirmou foi o agente, com o contexto da conversa — e o `observacao`
        # guarda o porquê, para quem revisar depois saber de onde veio.
        "match_valido": True,
        "status": "chat",
        "ia_resposta": observacao or None,
    }

    resultado, poi_id = realtime_ingest.ingerir_registro(registro)
    if not poi_id:
        return {"erro": f"o ingestor recusou: {resultado}"}

    saida = {"poi_id": poi_id, "gravado": resultado, "nome": nome,
             "coordenada": [la, lo]}

    # ── Fachada ──────────────────────────────────────────────────────────
    if capturar_fachada:
        saida["fachada"] = _fachada(poi_id, la, lo)

    # ── Cruzamento ───────────────────────────────────────────────────────
    #
    # Em SUBPROCESSO, e não importando `cruzar_bases`: ele carrega as cinco
    # bases inteiras na memória do processo que o chamar. Dentro do agente, que
    # já divide RAM com Chromium, isso derrubaria a conversa.
    if cruzar:
        saida["cruzamento"] = _cruzar(cidade)

    return saida


def _fachada(poi_id: int, la: float, lo: float) -> dict:
    """Captura o Street View, se ainda não houver.

    Falha aqui NÃO derruba o cadastro: o ponto já está gravado, e a fachada é
    recuperável por qualquer rodada de enriquecimento depois. Trocar um POI bom
    por um erro de captura seria o pior negócio possível.
    """
    try:
        con = bc.conectar()
        try:
            with con.cursor() as k:
                k.execute("""select streetview_path is not null
                                    and streetview_path not in ('', 'NA')
                               from radar_comercial.pois where id = %s""",
                          (poi_id,))
                linha = k.fetchone()
        finally:
            con.close()
        if linha and linha[0]:
            return {"estado": "ja_tinha"}

        import asyncio

        import streetview_capture as SV

        # `run` já aceita uma lista de ids — é o mesmo caminho da captura em
        # lote, com um alvo só. Escrever uma captura própria aqui criaria uma
        # segunda versão da lógica de panorama, giro e recorte, e as duas
        # divergiriam no primeiro ajuste.
        asyncio.run(SV.run(workers=1, limit=0, refazer=False, ids=[poi_id]))

        con = bc.conectar()
        try:
            with con.cursor() as k:
                k.execute("""select streetview_path
                               from radar_comercial.pois where id = %s""",
                          (poi_id,))
                caminho = (k.fetchone() or [None])[0]
        finally:
            con.close()

        if caminho and caminho not in ("", "NA"):
            return {"estado": "capturada"}
        # TRÊS RESULTADOS, e confundi-los engana quem lê.
        #
        # 'NA' é o Street View respondendo que ali não há panorama — definitivo
        # por ora, e o enriquecimento tenta de novo quando cobertura nova
        # aparecer. Caminho NULO é outra coisa: a captura falhou (o painel não
        # abriu a tempo, o proxy caiu) e o ponto continua na fila.
        #
        # A primeira versão desta função dizia "o Google não tem foto de rua"
        # nos dois casos. Na prova, o motivo real foi tempo esgotado — e a
        # mensagem mandava desistir de algo que ia funcionar na tentativa
        # seguinte.
        if caminho == "NA":
            return {"estado": "sem_panorama",
                    "porque": "o Google não tem foto de rua nesta coordenada"}
        return {"estado": "na_fila",
                "porque": "a captura não concluiu agora — o ponto já está "
                          "gravado e a fachada sai na próxima rodada de "
                          "enriquecimento"}
    except Exception as e:
        return {"estado": "falhou", "erro": f"{type(e).__name__}: {str(e)[:120]}"}


def _cruzar(cidade: str) -> dict:
    """Roda o cruzamento do município. Devolve o resumo, não a saída inteira."""
    cmd = [sys.executable, "cruzar_bases.py"]
    if cidade:
        cmd += ["--cidade", cidade]
    try:
        r = subprocess.run(cmd, cwd=str(RAIZ), capture_output=True,
                           text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"estado": "demorou_demais",
                "porque": "o cruzamento passou de 10 min — rode "
                          "`cruzar_bases.py` à parte"}
    if r.returncode != 0:
        return {"estado": "falhou", "erro": (r.stderr or "")[-200:]}
    ultima = [l for l in (r.stdout or "").splitlines() if "cruzamentos" in l]
    return {"estado": "ok", "resumo": (ultima[-1].strip() if ultima else "")}


ESQUEMA = {
    "type": "function", "function": {
        "name": "guardar_ponto",
        "description": (
            "GUARDA no banco um estabelecimento CONFIRMADO e completa o ciclo: "
            "grava o POI (deduplicando com o que ja existe), captura a fachada "
            "no Street View e cruza com o cadastro do cliente, a Receita e os "
            "demais POIs. A partir dai o ponto aparece no mapa e pode ser "
            "distribuido a supervisor como qualquer outro.\n"
            "USE quando o item estiver confirmado — nome e coordenada certos. "
            "NAO use para explorar: consulta que vira cadastro enche a base de "
            "tentativa e a fila de aprovacao de trabalho que ninguem pediu.\n"
            "EXIGE lat/lng. Sem coordenada a ferramenta recusa, porque ponto "
            "sem posicao nao leva ninguem a lugar nenhum."),
        "parameters": {
            "type": "object",
            "properties": {
                "nome": {"type": "string",
                         "description": "nome do estabelecimento"},
                "lat": {"type": "number", "description": "latitude"},
                "lng": {"type": "number", "description": "longitude"},
                "endereco": {"type": "string"},
                "telefone": {"type": "string"},
                "cnpj": {"type": "string"},
                "categoria": {"type": "string"},
                "website": {"type": "string"},
                "email": {"type": "string"},
                "instagram": {"type": "string"},
                "razao_social": {"type": "string"},
                "cidade": {"type": "string"},
                "uf": {"type": "string"},
                "maps_url": {"type": "string"},
                "observacao": {
                    "type": "string",
                    "description": ("por que este ponto foi confirmado — fica "
                                    "gravado para quem revisar depois")},
            },
            "required": ["nome", "lat", "lng"],
        },
    },
}
