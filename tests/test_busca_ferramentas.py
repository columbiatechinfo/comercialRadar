# -*- coding: utf-8 -*-
"""Trava os quatro defeitos consertados em 22/08/2026.

Todos vieram de teste manual, e todos são do tipo que volta em silêncio: o
sistema continua respondendo, só que errado ou vazio. Sem teste, a próxima
refatoração os traz de volta e ninguém percebe até o cliente reclamar.

    1. credencial de proxy no Chromium  -> `google.com/maps` pendura 35 s
    2. o ponto de "Av."                 -> cruzamento por endereço dá zero
    3. modelo repetindo o mesmo pedido  -> volta ao servidor até o tempo acabar
    4. rua em comum tratada como porta  -> CNPJ de outra empresa entregue

Nada aqui abre navegador nem fala com o modelo: são todos determinísticos e
rodam em segundos. O que precisa de rede foi testado à mão e está registrado
nos comentários do código.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 1. o proxy de relay não pode levar credencial ao Chromium ────────────────

def test_relay_nao_leva_credencial_ao_chromium():
    """Usuário vazio NÃO pode virar chave no dicionário do Playwright.

    Com `username: ""` o Chromium ainda entra no caminho de proxy autenticado,
    e é isso que pendura o `google.com`. Medido: 35,3 s de timeout com
    credencial contra 1,7 s pelo relay.
    """
    from proxy_pool import ProxyPool
    cfg = ProxyPool.to_playwright({"server": "http://127.0.0.1:18200",
                                   "username": "", "password": ""})
    assert cfg == {"server": "http://127.0.0.1:18200"}, \
        "credencial vazia voltou ao Chromium — o Maps vai pendurar de novo"


def test_proxy_com_credencial_continua_levando():
    """E o caminho antigo não pode ter sido quebrado no conserto."""
    from proxy_pool import ProxyPool
    cfg = ProxyPool.to_playwright({"server": "http://ip:1", "username": "u",
                                   "password": "p"})
    assert cfg["username"] == "u" and cfg["password"] == "p"


# ── 2. abreviação de logradouro com ponto ────────────────────────────────────

@pytest.mark.parametrize("entrada,proibido", [
    ("Av. Prefeito Wall Ferraz", "AV"),
    ("R. Ivan Tito de Oliveira", "R"),
    ("Rod. dos Tabajaras", "ROD"),
    ("Pça. da Liberdade", "PCA"),
])
def test_tipo_de_via_sai_mesmo_com_ponto(entrada, proibido):
    """"Av." tem de ser reconhecido como "AV".

    O filtro comparava com a lista sem tirar a pontuação, então "Av." passava
    inteiro para o ILIKE. Medido na base real: 'Av. Prefeito Wall Ferraz'
    devolvia 0 e 'Prefeito Wall Ferraz' devolvia 15 — o mesmo endereço.
    """
    TIPOS = {"RUA", "AVENIDA", "AV", "R", "TRAVESSA", "TV", "ALAMEDA", "AL",
             "PRACA", "PCA", "RODOVIA", "ROD", "ESTRADA", "ESTR"}
    nucleo = " ".join(
        p for p in (w.strip(".,;:") for w in entrada.upper().split())
        if p and p not in TIPOS)
    assert proibido not in nucleo.split(), \
        f"o tipo de via sobrou em {nucleo!r} — o cruzamento vai dar zero"
    assert nucleo, "sobrou nada do logradouro"


# ── 3. o freio de repetição ──────────────────────────────────────────────────

def test_pedido_identico_nao_executa_duas_vezes(monkeypatch):
    """Modelo teimoso não pode fazer a ferramenta rodar de novo.

    Sem suporte nativo a ferramentas o agente roda por texto, e nele o modelo
    repetia o mesmo pedido volta após volta — num teste real, `consultar_receita`
    com um endereço inventado, até o tempo estourar.

    O teste finge um modelo que SEMPRE pede a mesma coisa. A ferramenta pode
    rodar uma vez; a partir da segunda o agente devolve o resultado guardado.
    """
    import agente_local as A

    vezes = {"n": 0}

    def ferramenta_cara(**kw):
        vezes["n"] += 1
        return {"achado": "sempre o mesmo"}

    monkeypatch.setitem(A.FERRAMENTAS, "ferramenta_cara", ferramenta_cara)
    monkeypatch.setattr(A, "_NATIVO", True, raising=False)

    def modelo_teimoso(mensagens, usar_ferramentas=True):
        if not usar_ferramentas:      # a última pergunta, já sem ferramentas
            return {"choices": [{"message": {"content": "desisto", }}]}
        return {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "x", "function": {"name": "ferramenta_cara",
                                     "arguments": '{"a": 1}'}}]}}]}

    monkeypatch.setattr(A, "_chamar", modelo_teimoso)
    A.conversar("pergunta qualquer")

    # PISO E TETO. Só `<= 1` passaria num laço que nunca rodou — foi exatamente
    # esse o erro de um teste anterior meu, que dava verde sem provar nada.
    assert vezes["n"] >= 1, "a ferramenta nem chegou a rodar; o teste não provou nada"
    assert vezes["n"] == 1, (
        f"a ferramenta rodou {vezes['n']} vezes com argumentos idênticos — "
        f"o freio de repetição não segurou")


# ── 4. rua em comum não é endereço em comum ──────────────────────────────────

def _rodar(corrotina):
    return asyncio.new_event_loop().run_until_complete(corrotina)


def test_cnpj_de_outro_numero_nao_e_entregue(monkeypatch):
    """O caso que gerou o falso positivo: Assaí virando construtora.

    O cruzamento por endereço tinha um atalho — se ninguém batia com o número,
    devolvia qualquer empresa da rua. Resultado medido: o Assaí Atacadista
    (Gonçalo Nunes 1000) voltou como L.A.R ARQUITETURA E CONSTRUÇÃO (Gonçalo
    Nunes 2131), carimbado de "casado pela porta".

    Aqui a Receita responde VAZIO, como responde de verdade quando o número
    filtra no banco e nada bate. Nenhum CNPJ pode ser afirmado.
    """
    import agente_local as A
    import ferramenta_lote as L

    monkeypatch.setattr(A, "consultar_receita",
                        lambda **kw: {"empresas": [], "nota": None})

    d = {"nome_pedido": "Assai Atacadista",
         "endereco": "R. Gonçalo Nunes, 1000 - São Raimundo",
         "cnpj": None, "fontes": {}, "alertas": []}
    _rodar(L._pela_porta(d, "Teresina", "PI"))

    assert d["cnpj"] is None, "afirmou CNPJ sem empresa nenhuma na porta"
    assert any("Gonçalo Nunes" in a for a in d["alertas"]), \
        "ficou sem CNPJ e sem dizer por quê — silêncio parece dado ausente"


def test_numero_vai_para_a_consulta_e_nao_para_o_python(monkeypatch):
    """O número tem de FILTRAR NO BANCO, não depois do `limit`.

    Filtrando em Python sobre as 15 linhas que o `limit` deixou passar, a
    empresa do número certo quase nunca estava entre elas numa avenida grande —
    e o cruzamento voltava vazio parecendo ausência de registro.
    """
    import agente_local as A
    import ferramenta_lote as L

    visto = {}

    def espiao(**kw):
        visto.update(kw)
        return {"empresas": [{"cnpj": "1", "nome_fantasia": "X",
                              "razao_social": "X LTDA", "numero": "10197"}]}

    monkeypatch.setattr(A, "consultar_receita", espiao)
    d = {"nome_pedido": "Atacadao", "endereco": "Av. Prefeito Wall Ferraz, 10197",
         "cnpj": None, "fontes": {}, "alertas": []}
    _rodar(L._pela_porta(d, "Teresina", "PI"))

    assert visto.get("numero") == "10197", \
        f"o número não foi para a consulta: {visto!r}"
    assert d["cnpj"] == "1"


def test_todos_os_cnpjs_da_porta_sao_listados(monkeypatch):
    """Contagem e lista têm de bater.

    O corte em cinco fazia o aviso dizer "6 CNPJs" e a lista mostrar 5 — quem
    lê conclui que viu tudo e escolhe entre os errados.
    """
    import agente_local as A
    import ferramenta_lote as L

    seis = [{"cnpj": f"{i:014d}", "nome_fantasia": f"EMPRESA {i}",
             "razao_social": f"EMPRESA {i} LTDA", "numero": "10197"}
            for i in range(6)]
    monkeypatch.setattr(A, "consultar_receita",
                        lambda **kw: {"empresas": seis})

    d = {"nome_pedido": "Atacadao", "endereco": "Av. Prefeito Wall Ferraz, 10197",
         "cnpj": None, "fontes": {}, "alertas": []}
    _rodar(L._pela_porta(d, "Teresina", "PI"))

    assert len(d["cnpj"]) == 6, f"listou {len(d['cnpj'])} de 6 — truncou calado"
    aviso = " ".join(d["alertas"])
    assert "6 CNPJs" in aviso and str(len(d["cnpj"])) in aviso


# ── 5. o plano inteiro do modelo é executado, não só o primeiro passo ────────

def test_plano_com_varias_chamadas_executa_todas():
    """O modelo enfileira a escada inteira; todas têm de rodar.

    Medido: pedir "seguindo a escada de extração" fazia o modelo listar seis
    chamadas numa resposta só. O código pegava a PRIMEIRA e devolvia a pergunta,
    então ele replanejava tudo na volta seguinte — oito vezes, `parou por
    length` em todas, prompt inchando de 2885 para 17937 tokens. 304 s contra
    80 s depois do conserto.
    """
    import agente_local as A

    plano = "\n\n".join([
        '{"ferramenta": "recordar", "argumentos": {"assunto": "x"}}',
        '{"ferramenta": "consultar_banco", "argumentos": {"sql": "select 1"}}',
        '{"ferramenta": "buscar_lugar", "argumentos": {"nome": "y"}}',
        '{"ferramenta": "recordar", "argumentos": {"assunto": "x"}}',
    ])
    achados = A._extrair_pedidos(plano)
    nomes = [a["ferramenta"] for a in achados]
    assert nomes == ["recordar", "consultar_banco", "buscar_lugar"], \
        f"o plano não foi extraído inteiro: {nomes}"


def test_plano_nao_repete_a_mesma_chamada():
    """Chamada idêntica duas vezes no plano é engano, não intenção."""
    import agente_local as A
    plano = "\n\n".join(['{"ferramenta": "recordar", "argumentos": {"assunto": "x"}}'] * 4)
    assert len(A._extrair_pedidos(plano)) == 1


def test_plano_tem_teto():
    """Plano gigante é o modelo divagando — o resto fica para a próxima volta."""
    import agente_local as A
    plano = "\n\n".join(
        '{"ferramenta": "recordar", "argumentos": {"assunto": "%d"}}' % i
        for i in range(20))
    assert len(A._extrair_pedidos(plano)) == 6


def test_extrair_pedido_singular_continua_funcionando():
    """A função antiga não pode ter sido quebrada — outros pontos a usam."""
    import agente_local as A
    p = A._extrair_pedido('{"ferramenta": "recordar", "argumentos": {"assunto": "z"}}')
    assert p and p["ferramenta"] == "recordar"
    assert A._extrair_pedido("nao ha json aqui") is None


# ── 6. o histórico não devolve turnos de assistente vazios ──────────────────

class _CursorFalso:
    def __init__(self, linhas): self._l = linhas
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): pass
    def fetchall(self): return self._l


class _ConexaoFalsa:
    def __init__(self, linhas): self._l = linhas
    def cursor(self): return _CursorFalso(self._l)


def test_historico_nao_devolve_turno_de_assistente_vazio():
    """Chamada de ferramenta é gravada como `assistant` com conteúdo "".

    Devolvidas cruas, viravam exemplos de "aqui o assistente responde nada".
    Medido na conversa real: 111 mensagens, 28 turnos vazios, e a pergunta
    seguinte voltava em 0,6 s com ZERO caracteres. Cada vazio novo piorava o
    próximo — quanto mais se usava o chat, pior ficava.
    """
    import chat_api

    linhas = [
        ("user", "ache o Bussbier", None),
        ("assistant", "", "buscar_web"),          # a chamada
        ("tool", '{"achados": []}', "buscar_web"),  # o resultado
        ("assistant", "", None),                  # resposta que falhou
        ("user", "e agora?", None),
        ("assistant", "Aqui está.", None),
    ]
    msgs = chat_api._historico(_ConexaoFalsa(linhas), "cid")

    vazios = [m for m in msgs
              if m["role"] == "assistant" and not (m["content"] or "").strip()]
    assert not vazios, f"{len(vazios)} turnos vazios voltaram ao modelo"

    # e o que importa não pode ter sumido junto
    texto = " ".join(m["content"] for m in msgs)
    assert "Resultado de buscar_web" in texto, "o resultado da ferramenta sumiu"
    assert "Aqui está." in texto, "a resposta boa foi descartada"
    assert "ache o Bussbier" in texto, "a pergunta do usuário sumiu"


def test_historico_nao_inventa_texto_no_lugar_do_vazio():
    """Encher o vazio com frase própria só troca um padrão ruim por outro.

    A primeira tentativa punha "(chamei a ferramenta X)" no lugar. O modelo
    copiou isso como resposta ao usuário, literalmente — a resposta seguinte
    foi exatamente `(chamei a ferramenta consultar_banco)`.
    """
    import chat_api
    linhas = [("user", "oi", None), ("assistant", "", "consultar_banco"),
              ("tool", "{}", "consultar_banco")]
    msgs = chat_api._historico(_ConexaoFalsa(linhas), "cid")
    assert not any("chamei a ferramenta" in (m["content"] or "") for m in msgs), \
        "voltou a pôr frase de preenchimento que o modelo copia"


# ── 7. identidade é julgada pela IA, não pela régua de texto ────────────────

def _resposta_falsa(texto):
    """Finge a resposta HTTP do modelo, para o teste não depender da Spark."""
    import io as _io
    import json as _json

    class _R:
        status = 200
        def read(self): return _json.dumps(
            {"choices": [{"message": {"content": texto}}]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return lambda *a, **k: _R()


def test_veredito_da_ia_e_lido(monkeypatch):
    """O "sim" da IA tem de chegar inteiro, com o motivo.

    A régua de texto deu 0,548 para "Bussbier Cerveja Artesanal e Bebidas"
    contra "BussBier Chopp Para Festas" — mesmo endereço, mesmo telefone, mesmo
    ramo. Ela compara letras; não sabe que Chopp e Cerveja Artesanal são o
    mesmo negócio. Quem decide isso é a IA.
    """
    import urllib.request
    import julgar_identidade as JI

    monkeypatch.setattr(urllib.request, "urlopen", _resposta_falsa(
        '{"mesmo": "sim", "porque": "mesmo endereco e telefone"}'))
    v = JI.julgar("Bussbier Cerveja Artesanal", "BussBier Chopp Para Festas",
                  "R. Behrens, 200", "(51) 98496-9787", "Conveniencia", "Canoas")
    assert v["mesmo"] == "sim" and "endereco" in v["porque"]


def test_veredito_embrulhado_em_texto_ainda_e_lido(monkeypatch):
    """O modelo às vezes conversa antes do JSON. O objeto tem de ser achado."""
    import urllib.request
    import julgar_identidade as JI

    monkeypatch.setattr(urllib.request, "urlopen", _resposta_falsa(
        'Claro! Aqui vai:\n{"mesmo": "nao", "porque": "ramos diferentes"}\nEspero ter ajudado.'))
    assert JI.julgar("Padaria X", "Locadora Y")["mesmo"] == "nao"


@pytest.mark.parametrize("bruto", [
    "não consigo responder isso",          # sem JSON nenhum
    '{"mesmo": "talvez", "porque": "sei la"}',   # veredito fora do combinado
    '{"quebrado": ',                        # JSON inválido
])
def test_julgamento_que_falha_nunca_vira_aprovacao(monkeypatch, bruto):
    """Julgamento que não aconteceu NÃO pode virar "sim".

    É a postura fail-closed: na falha, o pior resultado possível é aprovar em
    silêncio um estabelecimento que é outro.
    """
    import urllib.request
    import julgar_identidade as JI

    monkeypatch.setattr(urllib.request, "urlopen", _resposta_falsa(bruto))
    assert JI.julgar("A", "B")["mesmo"] == "incerto"


def test_modelo_fora_do_ar_devolve_incerto(monkeypatch):
    """Spark caída não pode aprovar nem reprovar — só admitir que não sabe."""
    import urllib.request
    import julgar_identidade as JI

    def explode(*a, **k):
        raise OSError("conexao recusada")
    monkeypatch.setattr(urllib.request, "urlopen", explode)
    v = JI.julgar("A", "B")
    assert v["mesmo"] == "incerto" and "julgar" in v["porque"]


def test_sem_nome_nao_se_julga():
    """Sem um dos lados não há o que comparar — e comparar nada daria 'sim'."""
    import julgar_identidade as JI
    assert JI.julgar("", "BussBier")["mesmo"] == "incerto"
    assert JI.julgar("BussBier", "")["mesmo"] == "incerto"


# ── 8. o caso "Los Chiapas": interface, baixadas e CNPJ de trecho ───────────

def test_consultar_receita_aceita_cidade_como_apelido():
    """O modelo escreve `cidade`; o parâmetro se chamava só `municipio`.

    Visto no painel do chat:
        consultar_receita({"nome":"Los Chiapas","cidade":"Canoas","uf":"RS"})
        TypeError: got an unexpected keyword argument 'cidade'

    A Receita nunca foi consultada, e o CNPJ da resposta saiu de um trecho de
    página com a fonte inventada "Diário Cidade".
    """
    import inspect
    import agente_local as A
    p = inspect.signature(A.consultar_receita).parameters
    assert "cidade" in p and "municipio" in p, "o apelido `cidade` sumiu"


def test_argumento_errado_ensina_o_certo():
    """Erro de argumento tem de dizer qual é o certo, não só reclamar."""
    import agente_local as A
    dica = A._parecido("consultar_receita", {"cidadela": "Canoas"})
    assert "cidade" in dica or "municipio" in dica, f"dica inútil: {dica!r}"
    assert A._parametros_de("consultar_receita"), "não leu a assinatura"


def test_situacao_cadastral_vira_palavra():
    """Devolver "08" ao modelo é devolver nada — ele não sabe, e vai chutar."""
    import agente_local as A
    assert A.SITUACAO_CADASTRAL["02"] == "ATIVA"
    assert A.SITUACAO_CADASTRAL["08"] == "BAIXADA"


def test_cnpj_em_texto_de_pagina_vem_com_ordem_de_conferir(monkeypatch):
    """CNPJ tirado de página carrega o aviso COLADO nele.

    A regra "confirme na Receita antes de afirmar" já estava na mensagem de
    sistema e foi ignorada duas vezes — uma creditando "Econodata", outra
    "Diário Cidade". Regra longe do dado não segura.
    """
    import json as _json
    import urllib.request
    import agente_local as A

    class _R:
        def read(self): return _json.dumps({"results": [
            {"title": "Serasa", "url": "https://x.com",
             "content": "LOS CHIAPAS CNPJ 02.641.187/0001-38 Canoas"}]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _R())
    r = A.buscar_web("los chiapas canoas", 5, aprofundar=False)

    assert "02641187000138" in (r.get("cnpjs_no_texto") or []), \
        "o CNPJ do texto não foi destacado"
    assert "consultar_receita" in (r.get("nota_cnpj") or ""), \
        "o número veio sem a ordem de conferir"


# ── 9. o Maps é a fonte principal, e a busca por nome alarga ────────────────

def test_maps_vem_antes_da_busca_web_na_escada():
    """O Maps estava em 7º de 7 e a descrição mandava usá-lo por último.

    O modelo obedeceu: numa busca pelo Supermercado Vancosty, entregou endereço
    e telefone creditados ao "Guia Canoas" — os MESMOS que o Maps devolvia, mas
    sem o horário, sem as 6 fotos e sem a coordenada, que só o painel tem.
    """
    import agente_local as A
    i_maps = A.SISTEMA.find("`consultar_maps`")
    i_web = A.SISTEMA.find("`buscar_web`")
    assert 0 < i_maps < i_web, \
        "o Maps voltou a ficar depois da busca web na escada"
    assert "FONTE PRINCIPAL" in A.SISTEMA, "perdeu a marcação de fonte principal"


def test_descricao_do_maps_nao_manda_deixar_para_depois():
    """A descrição da ferramenta é o que o modelo lê ao decidir."""
    from ferramenta_maps import ESQUEMA
    d = ESQUEMA["function"]["description"]
    assert "PRINCIPAL" in d, "a descrição não diz que é a fonte principal"
    assert "use depois de" not in d, "voltou a empurrar o Maps para o fim"


def test_nome_generico_na_frente_nao_mata_a_busca(monkeypatch):
    """"Supermercado Vancosty" achava 0; "Vancosty" achava 1, ATIVA.

    A Receita registra "VANCOSTY COMERCIO E DISTRIBUICAO"; o "Supermercado" só
    existe na placa. Sem alargar, o degrau voltava vazio e o modelo ia buscar o
    CNPJ num trecho de página com fonte inventada.
    """
    import agente_local as A

    class _Cur:
        def __init__(s): s.chamadas = []
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def execute(s, sql, args=None):
            s.chamadas.append(args)
        def fetchall(s):
            # a primeira busca (frase inteira) não acha; a segunda acha
            padrao = (s.chamadas[-1] or ("", "", "", ""))[2]
            if padrao == "%Supermercado Vancosty%":
                return []
            return [("00890225000594", "VANCOSTY", "VANCOSTY LTDA", "4711302",
                     "02", "EE DOIS", "01", "GUAJUVIRAS", "92415000")]
        def fetchone(s): return ("8589",)

    class _Con:
        def cursor(s): return _Cur()
        def close(s): pass

    monkeypatch.setattr(A.bc, "conectar_referencia", lambda: _Con())
    monkeypatch.setattr(A, "_codigo_municipio", lambda n, u: "8589")

    r = A.consultar_receita(nome="Supermercado Vancosty", cidade="Canoas",
                            uf="RS")
    assert r["empresas"], "não alargou: o CNPJ certo continua invisível"
    assert "Vancosty" in (r.get("nota") or ""), \
        "alargou calado — quem lê precisa saber que o nome buscado foi outro"


# ── 10. CNPJ confirmado ou negado, e a data do último post ──────────────────

def test_confirmar_cnpj_esta_registrado_como_ferramenta():
    """`consultar_receita` acha CANDIDATOS; confirmar é outra coisa.

    No Supermercado Vancosty a resposta entregou 00.890.225/0005-94 como se
    fosse do ponto, quando a Receita registra aquele CNPJ em "EE DOIS, 01" e o
    Maps mostra a loja na Av. Dezessete de Abril. Número certo de empresa errada
    é pior que número nenhum: some a dúvida que faria alguém conferir.
    """
    import agente_local as A
    assert "confirmar_cnpj" in A.FERRAMENTAS
    nomes = {e["function"]["name"] for e in A.ESQUEMA}
    assert "confirmar_cnpj" in nomes, "existe mas não é anunciada ao modelo"


def test_cnpj_inexistente_e_negado_sem_consultar_o_modelo(monkeypatch):
    """CNPJ que não está na Receita é "nao" direto — não se pergunta à IA."""
    import agente_local as A
    import julgar_identidade as JI

    monkeypatch.setattr(A, "consultar_receita", lambda **kw: {"empresas": []})
    def nao_deveria(*a, **k):
        raise AssertionError("perguntou ao modelo sem precisar")
    monkeypatch.setattr(JI, "_perguntar", nao_deveria)

    r = JI.confirmar_cnpj("00000000000000", nome="X")
    assert r["confirma"] == "nao" and "não existe" in r["porque"]


def test_julgamento_de_cnpj_que_falha_vira_incerto(monkeypatch):
    """Modelo fora do ar não pode confirmar nem negar — só admitir."""
    import agente_local as A
    import julgar_identidade as JI

    monkeypatch.setattr(A, "consultar_receita", lambda **kw: {"empresas": [
        {"cnpj": "1", "razao_social": "R", "nome_fantasia": "F",
         "logradouro": "L", "numero": "1", "bairro": "B", "cnae": "C",
         "situacao": "ATIVA"}]})
    monkeypatch.setattr(JI, "_perguntar", lambda *a, **k: {"erro": "caiu"})

    r = JI.confirmar_cnpj("1", nome="X", endereco="Y")
    assert r["confirma"] == "incerto"
    assert r["registro_na_receita"]["razao_social"] == "R", \
        "o registro consultado sumiu junto com o veredito"


def test_marcador_de_plano_nao_pode_virar_resposta(monkeypatch):
    """"[plano: consultar_maps, ...]" vazou uma vez como resposta ao usuário.

    Mesma armadilha do "(chamei a ferramenta X)": texto meu no lugar do dele
    acaba virando fala dele. O plano agora não deixa marcador nenhum.
    """
    import agente_local as A

    chamou = {"n": 0}
    monkeypatch.setattr(A, "_NATIVO", False, raising=False)
    monkeypatch.setitem(A.FERRAMENTAS, "ferramenta_x",
                        lambda **kw: {"ok": True})

    def modelo(mensagens, usar_ferramentas=True):
        chamou["n"] += 1
        if chamou["n"] == 1:      # primeiro, um plano com duas chamadas
            return {"choices": [{"message": {"content":
                '{"ferramenta": "ferramenta_x", "argumentos": {"a": 1}}\n\n'
                '{"ferramenta": "ferramenta_x", "argumentos": {"a": 2}}'}}]}
        return {"choices": [{"message": {"content": "Resposta de verdade."}}]}

    monkeypatch.setattr(A, "_chamar", modelo)
    texto, _ = A.conversar("pergunta")
    assert "[plano:" not in texto, "o marcador interno vazou para o usuário"
    assert texto.strip() == "Resposta de verdade."


# ── 11. o perfil vem do href, e as avaliações vêm com data ─────────────────

@pytest.mark.parametrize("registro,esperado", [
    ({"website_url": "https://www.instagram.com/pkcfusion/",
      "website": "instagram.com"}, "pkcfusion"),
    ({"website_url": "https://instagram.com/loja.x?igsh=abc"}, "loja.x"),
    ({"website_url": "https://www.facebook.com/bussbier/"}, "bussbier"),
    ({"website_url": "https://gocoffee.com.br"}, None),
])
def test_handle_sai_do_href_e_nao_do_texto(registro, esperado):
    """O texto do link é truncado ao domínio; o href tem a URL inteira.

    O painel do PKC Fusion mostrava `website: "instagram.com"`, sem o usuário —
    e a resposta citava "@pkcfusion" ADIVINHADO do nome da loja. Adivinhar
    handle é como adivinhar CNPJ.

    O caso do `loja.x?igsh=` também é medido: a primeira versão juntava os três
    campos numa string só e devolvia `"pkcfusion instagram.com https:"`, com o
    separador do campo seguinte grudado no handle.
    """
    import ferramenta_maps as M
    d = (M._perfil_social(registro) or {}).get("perfil_social")
    assert (d or {}).get("usuario") == esperado


def test_perfil_de_instagram_traz_a_ordem_de_abrir():
    """Citar o @ sem a data é meia informação — o aviso vai colado no dado."""
    import ferramenta_maps as M
    d = M._perfil_social({"website_url": "https://instagram.com/loja/"})
    assert "consultar_instagram" in d["perfil_social"]["faca_agora"]


def test_avaliacoes_chegam_com_nota_e_data():
    """Nota sem data não diz nada: 5 estrelas de 2019 e ponto parado.

    Medido no PKC Fusion — 1.0 há três semanas, 2.0 há dois meses, 5.0 há três
    anos. A trajetória só aparece porque a data vem junto.
    """
    import ferramenta_maps as M
    r = M._limpar({"comentarios": [
        {"autor": "A", "nota": 1.0, "data": "3 semanas atrás", "texto": "ruim"},
        {"autor": "B", "nota": 5.0, "data": "3 anos atrás", "texto": "otimo"},
    ] * 4})
    assert len(r["avaliacoes"]) == 5, "o teto de cinco não foi respeitado"
    assert r["avaliacoes"][0]["data"] == "3 semanas atrás"
    assert r["avaliacoes"][0]["nota"] == 1.0


# ── 12. conversa longa não pode congelar o formato da resposta ──────────────

def test_lembrete_diz_que_resposta_antiga_nao_e_modelo(monkeypatch):
    """Numa conversa de 131 mensagens o modelo copiou as próprias respostas.

    As ferramentas passaram a devolver `avaliacoes` com data e `perfil_social`
    pronto, e nada disso aparecia: nenhuma resposta antiga tinha esses campos, e
    ele reproduzia o formato velho — com as OMISSÕES dele. Chamava só o Maps e
    parava.

    Medido depois do reforço, com o MESMO histórico: passou a chamar também
    `consultar_instagram` e a seção "Avaliações com data" apareceu.
    """
    import agente_local as A

    visto = {}
    monkeypatch.setattr(A, "_NATIVO", False, raising=False)

    def modelo(mensagens, usar_ferramentas=True):
        visto["msgs"] = mensagens
        return {"choices": [{"message": {"content": "pronto"}}]}

    monkeypatch.setattr(A, "_chamar", modelo)
    historico = [{"role": "system", "content": "s"}] + \
                [{"role": "user", "content": f"p{i}"} for i in range(10)]
    A.conversar("nova pergunta", historico)

    texto = " ".join(m["content"] for m in visto["msgs"]
                     if isinstance(m.get("content"), str))
    assert "NÃO SÃO MODELO" in texto, \
        "o lembrete de que respostas antigas não são modelo sumiu"
    for campo in ("avaliacoes", "perfil_social", "consultar_instagram"):
        assert campo in texto, f"o lembrete não cita `{campo}`"


def test_conversa_curta_nao_recebe_o_lembrete(monkeypatch):
    """Em conversa nova o sistema está perto; o lembrete só seria ruído."""
    import agente_local as A

    visto = {}
    monkeypatch.setattr(A, "_NATIVO", False, raising=False)
    monkeypatch.setattr(A, "_chamar", lambda m, usar_ferramentas=True: (
        visto.update(msgs=m),
        {"choices": [{"message": {"content": "ok"}}]})[1])

    A.conversar("primeira pergunta")
    texto = " ".join(m["content"] for m in visto["msgs"]
                     if isinstance(m.get("content"), str))
    assert "NÃO SÃO MODELO" not in texto


# ── 13. apagar conversa some da lista e para de alimentar o modelo ─────────

def test_conversa_apagada_sai_da_lista_e_o_registro_fica():
    """Apagar existe por QUALIDADE, não por arrumação.

    Numa conversa de 131 mensagens o modelo copiou o formato das próprias
    respostas antigas e omitiu avaliações e data do Instagram — campos que as
    ferramentas passaram a devolver depois. Poder fechar a conversa e começar
    limpa é ferramenta de qualidade.

    A rota ARQUIVA: some da lista (e portanto do contexto do modelo), mas o
    registro fica. Foi decisão do projeto — conversa é registro de decisão.
    """
    import base_comum as bc
    import chat_api as C

    cid = C.criar({"titulo": "ZZ TESTE AUTOMATICO APAGAR"})["id"]
    try:
        titulos = [c["titulo"] for c in C.listar()]
        assert "ZZ TESTE AUTOMATICO APAGAR" in titulos, "nem chegou a aparecer"

        C.arquivar(cid)
        titulos = [c["titulo"] for c in C.listar()]
        assert "ZZ TESTE AUTOMATICO APAGAR" not in titulos, \
            "continua na lista — seguiria voltando ao modelo como contexto"

        con = bc.conectar()
        try:
            with con.cursor() as k:
                k.execute("""select arquivada from comercialradar.chat_conversa
                              where id = %s""", (cid,))
                linha = k.fetchone()
            assert linha is not None, "o registro foi destruído, não arquivado"
            assert linha[0] is True
        finally:
            con.close()
    finally:
        con = bc.conectar()          # o teste não deixa lixo no banco
        try:
            with con.cursor() as k:
                k.execute("""delete from comercialradar.chat_conversa
                              where id = %s""", (cid,))
            con.commit()
        finally:
            con.close()


def test_botao_de_apagar_nao_abre_a_conversa():
    """O botão vive dentro da linha, que já abre a conversa no clique.

    Sem `stopPropagation` o apagar abriria a conversa antes de sumir com ela, e
    a tela piscaria no que acabou de ser removido.
    """
    import io
    import os
    html = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "frontend", "chat.html"),
        encoding="utf-8").read()
    assert "apagarConversa" in html, "a função de apagar sumiu do front"
    assert "ev.stopPropagation()" in html, \
        "o clique no botão voltaria a abrir a conversa"
    assert "confirm(" in html, "apagar sem confirmação é clique fatal"


# ── 14. o Maps lê o Instagram que ele mesmo apontou ────────────────────────

def test_maps_le_o_instagram_do_painel_sem_pedir_ao_modelo(monkeypatch):
    """Três formas de PEDIR falharam; a ferramenta passa a fazer.

    Numa conversa nova sobre o Espetão Vancosty, o retorno do Maps trazia
    `perfil_social: {"usuario": "supermercadovancosty", "faca_agora": "chame
    consultar_instagram..."}` e cinco avaliações — e a escada rodou SEIS
    degraus sem chamar o Instagram. Antes disso já haviam falhado a regra na
    escada e o reforço perto da pergunta.

    Não é código decidindo pela IA: é a ferramenta terminando o próprio
    trabalho. Quando o painel diz que o SITE da loja É um Instagram, ler esse
    Instagram faz parte de ler o painel — como `enriquecer_poi` já abre as abas
    de horário e fotos em vez de pedir que o modelo as abra.
    """
    import io
    import os
    fonte = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "ferramenta_maps.py"),
        encoding="utf-8").read()
    assert 'perfil.get("rede") == "instagram"' in fonte, \
        "o Maps voltou a depender de o modelo pedir o Instagram"
    assert 'r["instagram"]' in fonte, "o resultado não carrega o perfil lido"


def test_leitura_do_instagram_nunca_derruba_o_maps():
    """Instagram é bônus: se falhar, o endereço e o telefone continuam vindo."""
    import io
    import os
    fonte = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "ferramenta_maps.py"),
        encoding="utf-8").read()
    trecho = fonte[fonte.find('perfil.get("rede") == "instagram"'):]
    assert "except Exception" in trecho[:900], \
        "falha no Instagram derrubaria a consulta inteira ao Maps"


# ── 15. renomear conversa ──────────────────────────────────────────────────

def test_renomear_muda_o_titulo_e_recusa_vazio():
    """O título automático descreve o PEDIDO, não o assunto.

    Sai como "procure os dados seguindo a escada de extração e busca de…" —
    dez buscas começam igual e nenhuma se distingue na barra lateral.
    """
    import base_comum as bc
    import chat_api as C
    import pytest as _pytest

    cid = C.criar({"titulo": "ZZ TESTE RENOMEAR"})["id"]
    try:
        C.renomear(cid, {"titulo": "Vancosty — Guajuviras"})
        titulos = {c["id"]: c["titulo"] for c in C.listar()}
        assert titulos.get(cid) == "Vancosty — Guajuviras"

        with _pytest.raises(Exception):
            C.renomear(cid, {"titulo": "   "})     # vazio não é nome
        titulos = {c["id"]: c["titulo"] for c in C.listar()}
        assert titulos.get(cid) == "Vancosty — Guajuviras", \
            "o nome bom foi perdido numa tentativa inválida"
    finally:
        con = bc.conectar()
        try:
            with con.cursor() as k:
                k.execute("""delete from comercialradar.chat_conversa
                              where id = %s""", (cid,))
            con.commit()
        finally:
            con.close()


def test_botao_de_renomear_nao_abre_a_conversa():
    """Mesmo cuidado do apagar: sem `stopPropagation` abriria por baixo."""
    import io
    import os
    html = io.open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "frontend", "chat.html"),
        encoding="utf-8").read()
    assert "renomearConversa" in html
    i = html.find("lapis.onclick")
    assert "ev.stopPropagation()" in html[i:i + 160], \
        "o clique no lápis abriria a conversa"
    assert "slice(0, 120)" in html, \
        "o formulário deixaria o servidor cortar o nome calado"


# ── 16. resgate na web quando a Receita não acha pelo nome ─────────────────

def test_conferencia_de_bairro_e_feita_em_codigo():
    """Ensinar a regra do bairro fez o juiz ALUCINAR a comparação.

    Ele passou a aprovar CNPJs de PQ ESPÍRITO SANTO, SÃO VICENTE e JARDIM
    BETÂNIA dizendo "mesmo bairro" — comparando com GUAJUVIRAS. Não errou o
    raciocínio: errou a LEITURA. Comparar duas cadeias de texto é o que código
    faz bem e modelo faz mal, então o fato vem pronto e o julgamento continua
    dele.
    """
    from julgar_identidade import _mesmo_bairro as mb

    end = "Av. Dezessete de Abril, S/N - Guajuviras, Canoas - RS"
    assert "MESMA REGIAO" in mb(end, "GUAJUVIRAS")
    assert "REGIAO DIFERENTE" in mb(end, "SAO VICENTE")
    # acento e caixa não podem decidir identidade
    assert "MESMA REGIAO" in mb("Rua X - Espírito Santo, Canoas", "ESPIRITO SANTO")
    # sem dado não se inventa veredito
    assert "nao da para comparar" in mb(end, "")
    assert "nao da para comparar" in mb("", "GUAJUVIRAS")


def test_o_fato_do_bairro_chega_ao_julgador(monkeypatch):
    """De nada adianta calcular se o cálculo não entra no formulário."""
    import agente_local as A
    import julgar_identidade as JI

    monkeypatch.setattr(A, "consultar_receita", lambda **kw: {"empresas": [
        {"cnpj": "1", "razao_social": "R", "nome_fantasia": "F",
         "logradouro": "EE DOIS", "numero": "01", "bairro": "GUAJUVIRAS",
         "cnae": "4711302", "situacao": "ATIVA"}]})

    visto = {}
    monkeypatch.setattr(JI, "_perguntar", lambda t, s: (
        visto.update(texto=t), {"confirma": "sim", "porque": "ok"})[1])

    JI.confirmar_cnpj("1", nome="X",
                      endereco="Av. Dezessete de Abril - Guajuviras, Canoas")
    assert "MESMA REGIAO" in visto["texto"], \
        "a conferência foi calculada mas não chegou ao julgador"
    assert "CEP" in visto["texto"], \
        "a conferência de CEP — a segunda opinião quando o bairro falta — sumiu"
    assert "nao recalcule" in visto["texto"].lower() or \
           "NAO compare os textos" in visto["texto"], \
        "sem a ordem de não recalcular, o modelo compara de novo e erra"


def test_busca_empresarial_usa_vocabulario_de_cadastro():
    """Perguntar "endereço telefone" traz o site da rede; "cnpj razão social"
    traz o diretório de empresas. Mesmo buscador, mundos diferentes."""
    from buscar_empresa import CONSULTAS
    juntas = " ".join(CONSULTAS).lower()
    for termo in ("cnpj", "razao social", "inscricao"):
        assert termo in juntas, f"a busca não pergunta por `{termo}`"


def test_candidato_da_web_nunca_e_afirmado_sem_confirmar(monkeypatch):
    """A web DESCOBRE; a Receita PROVA.

    Sem este passo isto seria o "Fonte: Econodata" de novo, com outra roupa.
    """
    import agente_local as A
    import buscar_empresa as BE
    import julgar_identidade as JI

    monkeypatch.setattr(A, "buscar_web",
                        lambda *a, **k: {"cnpjs_no_texto": ["11111111111111"]})
    chamou = {"n": 0}

    def julgador(cnpj, **kw):
        chamou["n"] += 1
        return {"cnpj": cnpj, "confirma": "nao", "porque": "outra unidade"}

    monkeypatch.setattr(JI, "confirmar_cnpj", julgador)
    r = BE.buscar_dados_empresariais("Loja X", "Canoas", "RS",
                                     endereco="Rua Y, 1")
    assert chamou["n"] >= 1, "o candidato da web não passou pela Receita"
    assert r["confirmados"] == [], "afirmou um CNPJ que foi negado"
    assert "NEGADOS" in r["nota"]


# ── 17. o instrumento de medida da prova de carga ──────────────────────────

def test_campo_negado_nao_conta_como_achado():
    """Régua errada faz os números mentirem a favor.

    "Telefone: Não encontrado em nenhuma fonte" contém a palavra telefone e um
    número logo abaixo (o SAC do shopping). Sem olhar a vizinhança, isso
    contaria como sucesso e a taxa de acerto seria ficção.
    """
    from prova_carga_chat import _tem

    assert _tem("Telefone: (51) 3467-1688", "telefone")
    assert not _tem("Telefone: Não encontrado. (51) 4004-7179 é o SAC",
                    "telefone")
    assert not _tem("O CNPJ não foi confirmado. 00.890.225/0005-94 aparece "
                    "na Receita mas em outro endereço", "cnpj")
    assert _tem("CNPJ confirmado: 00.890.225/0005-94", "cnpj")


@pytest.mark.parametrize("campo,texto", [
    ("endereco", "Av. Dezessete de Abril, 100 - Guajuviras"),
    ("horario", "Domingo: 08:00 a 21:00"),
    ("avaliacoes", "Nota: 4,3 — 5 avaliações"),
    ("rede_social", "Instagram: @supermercadovancosty"),
    ("data_publicacao", "Última publicação: 29 de setembro de 2025"),
])
def test_regua_reconhece_o_campo_presente(campo, texto):
    """E o contrário também: régua que não reconhece nada dá 0% sempre."""
    from prova_carga_chat import _tem
    assert _tem(texto, campo), f"a régua não viu `{campo}` em {texto!r}"


def test_alvos_da_carga_nao_sao_todos_faceis():
    """Cinco lojas simples dariam 100% e não informariam nada.

    A lista tem rede com muitos CNPJs, ponto em shopping, um que o Maps nomeia
    diferente do pedido e um já fechado.
    """
    from prova_carga_chat import ALVOS
    assert len(ALVOS) >= 5
    nomes = " ".join(n for n, _, _ in ALVOS).lower()
    assert "los chiapas" in nomes, "faltou o caso do ponto fechado"
    assert "vancosty" in nomes, "faltou o caso da rede com muitos CNPJs"


# ── 18. o vazamento de relays, achado na prova de carga com 10 ─────────────

def test_relay_e_rastreado_do_nascimento_ate_a_morte():
    """Dez vagas viraram QUARENTA processos vivos, e a RAM caiu a 0,2 GB.

    Medido em `prova_carga_chat.py 10`:

        45 s   relays: 32 | RAM livre: 1,4 GB
        135 s  relays: 40 | RAM livre: 0,2 GB

    Precisei interromper. Ao matar tudo, a RAM saltou para 6,02 GB — o teste
    segurava ~5,6 GB, e a maior parte NÃO era navegador: eram os relays, cada um
    um processo Python inteiro.

    A causa: `mark_cooldown` tirava o processo de `_procs` e chamava
    `terminate()`. Dali em diante ninguém o rastreava, e `encerrar()` — que
    percorria só `_procs` — não alcançava o órfão.
    """
    from relay_proxy import PiscinaRelay

    p = PiscinaRelay(vagas=3)
    assert hasattr(p, "_nascidos"), "o registro de nascidos sumiu"
    assert p.max_nascimentos == 12, "o teto de nascimentos sumiu"

    # um processo que finge estar vivo e não morre com terminate()
    class Zumbi:
        def __init__(s): s.morto = False; s.pedidos = []
        def poll(s): return 0 if s.morto else None
        def terminate(s): s.pedidos.append("terminate")   # ignora, como no Windows
        def kill(s): s.pedidos.append("kill"); s.morto = True
        def wait(s, timeout=None): return 0

    z = Zumbi()
    p._nascidos.append(z)
    p._procs[0] = z
    p.encerrar()

    assert z.morto, "o relay sobreviveu ao encerrar — é assim que se vaza"
    assert z.pedidos == ["terminate", "kill"], \
        f"esperava pedir e depois obrigar, veio {z.pedidos}"
    assert p.vivos == 0


def test_cooldown_nao_perde_o_rastro_do_processo():
    """O caminho exato do vazamento: sair da rotação sem sair do registro."""
    import asyncio

    from relay_proxy import PiscinaRelay

    class Zumbi:
        def __init__(s): s.morto = False
        def poll(s): return 0 if s.morto else None
        def terminate(s): pass
        def kill(s): s.morto = True
        def wait(s, timeout=None): return 0

    p = PiscinaRelay(vagas=2)
    z = Zumbi()
    p._nascidos.append(z)
    p._procs[1] = z
    p._portas[1] = 18201

    asyncio.new_event_loop().run_until_complete(
        p.mark_cooldown({"_vaga": 1}, 600))

    assert 1 not in p._procs, "a vaga continuou em uso"
    assert z.morto, "o cooldown deixou o relay vivo e sem dono"


def test_nunca_ha_mais_relays_vivos_que_vagas():
    """O teto é a memória da máquina, não uma preferência."""
    import asyncio

    from relay_proxy import PiscinaRelay

    class Vivo:
        def poll(s): return None
        def terminate(s): pass
        def kill(s): pass
        def wait(s, timeout=None): return 0

    p = PiscinaRelay(vagas=2)
    p._nascidos.extend([Vivo(), Vivo()])          # as duas vagas ocupadas
    cabe = asyncio.new_event_loop().run_until_complete(p._garantir(5))
    assert cabe is False, "abriu um terceiro relay com duas vagas"
