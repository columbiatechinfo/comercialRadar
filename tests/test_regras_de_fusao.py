# -*- coding: utf-8 -*-
"""As regras que decidem se dois POIs são o mesmo ponto.

Declaradas pelo dono do produto, 25/08/2026:

    "telefone nao pode decidir sozinho, tem que se somar a pelo menos outra
     evidencia. Site, por ser um dominio, tem mais peso que telefone. Ambos
     devem estar em um raio de menos de 20 metros. Endereço segue sendo o maior
     indicio e mesmo sem numero batendo a distancia < 20 metros serve como
     motivo pra enviar pra IA."

Cada teste aqui é uma dessas frases. Se um deles cair, a regra mudou — e mudar
regra de fusão sem querer é como 66,2% de fusão errada acontece (medido no RS,
onde telefone sozinho uniu estabelecimentos distintos em 74,4% dos casos).
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import config  # noqa: F401,E402
import evidencia as ev  # noqa: E402


def _poi(**kw):
    base = {"nome": "", "lat": -29.9, "lng": -51.1}
    base.update(kw)
    return base


# ─── telefone não decide sozinho ─────────────────────────────────────────────

def test_telefone_sozinho_nao_funde_nem_pergunta():
    r = ev.avaliar(_poi(nome="Lotérica Schneider", telefone="(51) 3470-1234"),
                   _poi(nome="Bazar Mega", telefone="51 3470-1234", lat=-29.90001))
    assert r["decisao"] == "descartar"
    assert "não decide sozinho" in r["porque"]


def test_telefone_fora_do_raio_nao_conta():
    """3 km com o mesmo telefone é a matriz e a filial, ou o número do dono."""
    a = _poi(nome="Loja Centro", telefone="5134701234")
    b = _poi(nome="Loja Centro", telefone="5134701234", lat=-29.873)
    r = ev.avaliar(a, b)
    assert not any("telefone" in m for m in r["motivos"])


# ─── site pesa mais que telefone ─────────────────────────────────────────────

def test_site_sozinho_dentro_do_raio_vai_para_a_ia():
    """A diferença de peso tem de aparecer no COMPORTAMENTO: o site sozinho
    manda perguntar, o telefone sozinho não."""
    r = ev.avaliar(_poi(nome="Padaria X Centro", site="https://www.padaria.com.br/"),
                   _poi(nome="Padaria X", site="padaria.com.br/contato", lat=-29.90005))
    assert r["decisao"] == "perguntar"


def test_o_site_pesa_mais_que_o_telefone():
    assert ev.PESO["site"] > ev.PESO["telefone"]
    assert ev.PESO["site"] >= ev.MIN_PARA_IA, \
        "o site precisa bastar sozinho para a pergunta; é o que o separa do telefone"


def test_mesmo_dominio_longe_e_filial():
    """Filial é outro ponto comercial para quem vai cobrar tarifa."""
    r = ev.avaliar(_poi(nome="Padaria X", site="padaria.com.br"),
                   _poi(nome="Padaria X", site="padaria.com.br", lat=-29.873))
    assert r["decisao"] == "descartar"


def test_rede_social_nao_identifica_negocio():
    """`instagram.com/lojaA` e `instagram.com/lojaB` têm o mesmo domínio e são
    dois negócios. Sem esta lista, toda loja com Instagram casaria com todas."""
    assert ev.dominio("instagram.com/lojaA") == ""
    assert ev.dominio("https://www.facebook.com/x") == ""
    assert ev.dominio("https://www.padaria.com.br/x") == "padaria.com.br"


# ─── endereço é o maior indício ──────────────────────────────────────────────

def test_endereco_exato_mais_nome_funde_sem_ia():
    r = ev.avaliar(
        _poi(nome="Padaria do Bairro", logr_marcado="AVENIDA GENERAL FLORES DA CUNHA",
             tier="ALTA", numero="1781"),
        _poi(nome="Padaria do Bairro", logr_marcado="AVENIDA GENERAL FLORES DA CUNHA",
             tier="CONFIRMA", numero="1781", lat=-29.90002))
    assert r["decisao"] == "fundir"
    assert r["confianca"] >= 8


def test_mesma_rua_sem_numero_dentro_de_20m_pergunta():
    """A frase literal da regra: "mesmo sem numero batendo a distancia < 20
    metros serve como motivo pra enviar pra IA"."""
    r = ev.avaliar(
        _poi(nome="Óptica Vision", logr_marcado="RUA POLONIA", tier="CONFIRMA"),
        _poi(nome="Vision Center", logr_marcado="RUA POLONIA", tier="CONFIRMA",
             lat=-29.90011))
    assert r["decisao"] == "perguntar"
    assert r["dist_m"] < ev.RAIO_M


def test_o_endereco_pesa_mais_que_tudo():
    assert ev.PESO["endereco_exato"] > ev.PESO["site"] > ev.PESO["telefone"]


# ─── o tier da normalização ──────────────────────────────────────────────────

def test_tier_confiavel_usa_a_forma_canonica():
    """É o que torna "Gen." e "General" a mesma rua — o motivo de a etapa 6
    existir antes desta."""
    a = _poi(logr_marcado="AVENIDA GENERAL FLORES DA CUNHA", tier="ALTA",
             logr_original="Avenida Gen. Flores da Cunha")
    b = _poi(logr_marcado="AVENIDA GENERAL FLORES DA CUNHA", tier="CONFIRMA",
             logr_original="Avenida General Flores da Cunha")
    assert ev.logradouro_de(a)[0] == ev.logradouro_de(b)[0]


def test_tier_de_revisao_cai_para_o_original():
    """Em REVISAR/HUMANO houve PERDA DE TEXTO: a forma marcada não representa
    mais a rua inteira, e casar por ela produziria par falso."""
    p = _poi(logr_marcado="RUA", tier="REVISAR", logr_original="Rua Nova Imbé")
    assert ev.logradouro_de(p)[0] == "rua nova imbe"


# ─── o "nan" que veio do pandas ──────────────────────────────────────────────

def test_nan_nao_e_evidencia():
    """Medido: 28.394 POIs com `website='nan'` e 17.193 com `telefone='nan'`.

    Tratá-los como valor real fazia os 28 mil compartilharem o mesmo domínio.
    Antes desta guarda o cruzamento de Cachoeirinha propunha 1.460 fusões; com
    ela, 126. As outras 1.334 (91%) eram evidência fabricada.
    """
    assert ev.dominio("nan") == ""
    assert ev.dominio("NaN") == ""
    assert ev.so_digitos("nan") == ""
    r = ev.avaliar(_poi(nome="Loja A", site="nan", telefone="nan"),
                   _poi(nome="Loja B", site="nan", telefone="nan", lat=-29.90005))
    assert r["decisao"] == "descartar"
    assert not any("domínio" in m for m in r["motivos"])


def test_endereco_nan_nao_casa_ninguem():
    a = _poi(nome="A", logr_original="nan", numero="10")
    b = _poi(nome="B", logr_original="nan", numero="10", lat=-29.90002)
    assert ev.logradouro_de(a)[0] == ""
    assert ev.avaliar(a, b)["decisao"] == "descartar"


# ─── semelhança de nome ──────────────────────────────────────────────────────

def test_ordem_das_palavras_nao_separa_o_mesmo_negocio():
    """"Padaria Silva" e "Silva Padaria" — por isso Jaccard e não distância de
    edição."""
    assert ev.semelhanca_nome("Padaria Silva", "Silva Padaria") == 1.0


def test_nome_de_um_token_so_nao_vale_cem_por_cento():
    """`tokens()` descarta o que tem 2 letras ou menos, e isso transformava
    "Loja A" e "Loja B" em {loja} e {loja} — dois negócios com semelhança
    perfeita."""
    assert ev.semelhanca_nome("Loja A", "Loja B") == 0.0
    assert ev.semelhanca_nome("Loja A", "loja a") == 1.0


# ─── o corte de volume ───────────────────────────────────────────────────────

def test_vizinhanca_pura_nao_vai_para_a_ia():
    """MEDIDO em Cachoeirinha: 18.220 dos 19.879 pares do balde da IA não tinham
    UM token de nome, domínio ou telefone em comum — eram a loja do lado.
    4.970 chamadas viravam 415."""
    import cruzar_fontes as cf
    vizinhos = [{"a": _poi(nome="Mercado Pop Latino"),
                 "b": _poi(nome="Óptica Caelum"), "evidencia": {}}]
    fica, sai = cf.filtrar_para_ia(vizinhos)
    assert not fica and len(sai) == 1

    ligados = [{"a": _poi(nome="Farmácia São João"),
                "b": _poi(nome="Farmácia São João"), "evidencia": {}}]
    fica, sai = cf.filtrar_para_ia(ligados)
    assert len(fica) == 1 and not sai


def test_o_corte_de_volume_pode_ser_desligado():
    """É julgamento sobre CUSTO, e quem paga a Spark decide."""
    import cruzar_fontes as cf
    vizinhos = [{"a": _poi(nome="Mercado Pop"), "b": _poi(nome="Óptica"),
                 "evidencia": {}}]
    fica, sai = cf.filtrar_para_ia(vizinhos, tudo=True)
    assert len(fica) == 1 and not sai


# ─── a fusão não destrói ─────────────────────────────────────────────────────

def test_o_poi_absorvido_nao_e_apagado():
    """Uma junção errada viraria PERDA. Ele vira `status='fundido'`, mantém a
    linha e o place_id, e o `x` da ficha desfaz."""
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    assert "status = 'fundido'" in s
    assert "delete from pois" not in s.lower()
    assert "drop " not in s.lower()


def test_a_transitividade_e_tratada():
    """Se A absorve B e depois B absorveria C, C tem de ir para A — senão o
    vínculo de C aponta para um POI já fundido e a ficha fica órfã na tela."""
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    assert "def raiz(" in s, "a resolução de transitividade sumiu"


def test_a_confianca_e_gravada_com_a_origem():
    """Cuidado ao ler este teste: ele checa a INTENCAO, nao a forma do SQL.

    A versao um-a-um escrevia `confianca = %s`; a escrita em lote passou a
    escrever `confianca = f.conf`, lendo da lista de VALUES. As duas colunas
    continuam sendo gravadas, e e isso que importa — quem abre a ficha do POI
    precisa saber o quanto se confia na fusao e o que a decidiu.
    """
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    i = s.index("def aplicar(")
    corpo = s[i:s.index("def cruzar(", i)]
    assert "confianca =" in corpo and "confianca_origem =" in corpo,         "a fusao deixou de gravar a confianca ou a origem dela"
    assert "motivo" in corpo, "o motivo em texto sumiu da gravacao"
    assert 'origem="regra"' in s and 'origem="ia"' in s, \
        "deixou de distinguir a fusão por regra da fusão decidida pela IA"


def test_a_ia_e_a_da_spark():
    """O i9 nunca carrega modelo. `_conferir_endpoint` recusa o endereço dele."""
    import io
    s = io.open(os.path.join(RAIZ, "julgar_par_banco.py"), encoding="utf-8").read()
    assert "_conferir_endpoint(SPARK)" in s
    assert "100.115.117.49" not in s


# ─── o tamanho do lote e o paralelismo ───────────────────────────────────────

def test_o_lote_do_julgamento_continua_pequeno():
    """MEDIDO em 26/08/2026 sobre 400 pares reais, e o resultado contraria a
    intuição: lote maior não acelera E muda a resposta.

        lote  thr    seg   concorda com o lote 4
           4   16   57,2   (referência)
           4   16   57,2   99%   <- o modelo discordando de SI MESMO
          10   32   52,0   94%
          20   32   83,5   94%
           4   64   25,6   99%

    A linha de controle é o que dá sentido às outras: 99% é o não-determinismo
    próprio do modelo. O lote 10 concorda em 94% — seis vezes o ruído, ou seja
    6% dos pares mudam de veredito por causa do tamanho do lote.

    O ganho estava no paralelismo. Se alguém subir o lote de novo achando que
    acelera, este teste é o aviso.
    """
    import julgar_par_banco as jb
    assert jb.LOTE <= 6, (
        "lote maior foi MEDIDO como mais lento E menos fiel: 6% dos pares mudam "
        "de veredito, contra 1% de ruído do próprio modelo")


def test_o_julgamento_e_paralelo():
    import io
    import julgar_par_banco as jb
    assert jb.THREADS >= 32, "o paralelismo é onde estava o ganho: 16→64 corta o tempo pela metade"
    s = io.open(os.path.join(RAIZ, "julgar_par_banco.py"), encoding="utf-8").read()
    assert "ThreadPoolExecutor" in s


# ─── a coordenada original conta ─────────────────────────────────────────────

def test_a_fusao_enxerga_quem_so_tem_coordenada_de_origem():
    """DEIXAVA DUPLICATA NA TELA DO OPERADOR, 26/08/2026.

    Na Av. Farroupilha, Canoas, "Bico de Pão" aparecia TRÊS vezes. Dois dos
    registros tinham `maps_lat` nulo e só `lat_origem` — e o
    `where p.maps_lat is not null` os tornava invisíveis para a fusão.

    Um deles era IDÊNTICO ao que a fusão via: mesmo nome, mesmo telefone
    (51 3478-4848), mesmo site (bicodepaors.com) e a MESMA coordenada. Fundiria
    com confiança 10 — e não fundia porque nunca era carregado.

    Medido na mesma área: 86 POIs e 0 fusões antes; 118 POIs e 7 fusões depois.
    Eram 32 POIs invisíveis, e são justamente os de planilha — os que mais
    duplicam o que as bases públicas já trouxeram.
    """
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    assert "coalesce(p.maps_lat, p.lat_origem)" in s, \
        "a fusão voltou a exigir `maps_lat` e a ignorar a coordenada de origem"
    assert " where p.maps_lat is not null" not in s


def test_a_mineracao_usa_a_INVERSAO_do_ifood():
    """`extrair_ifood` DESCOBRE pelo feed, e essa metade morreu com o Turnstile
    interativo — deixá-la na etapa era manter algo que só sabia falhar.

    `enriquecer_por_ifood` vai no sentido contrário e não abre o iFood: parte do
    POI, acha o link por busca, tira o id da URL e chama `/extra`, que responde
    200 sem navegador (1.598 respostas, zero falhas, CNPJ em 99,7%).
    """
    import io
    s = io.open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    i = s.index('_etapa(6, "iFood')
    j = s.index("_etapa(7,", i)
    corpo = s[i:j]
    assert "enriquecer_por_ifood.py" in corpo, "a etapa 6 não usa a inversão"
    assert "extrair_ifood.py" not in corpo, \
        "a descoberta pelo feed voltou — ela está bloqueada por fora"


def test_mesmo_nome_e_mesma_rua_e_confianca_maxima():
    """REGRA DO DONO DO PRODUTO, 26/08/2026: "se tiver o mesmo nome e mesmo
    logradouro é confiança máxima, mesmo a 50 metros ou 100".

    E ela vence a medição. Dos pares de mesmo nome e mesma rua em Canoas, 89%
    estão a menos de 20 m — mas 11% ficam além, e o excedente dessas duplicatas
    era de 11.983 POIs. A distância que os separa não é o estabelecimento ser
    outro: é a coordenada de uma das fontes errar.

    O teto de 1 km existe para não unir a filial do outro bairro: "Farmácia São
    João" na "Avenida Brasil" pode ser duas lojas se a avenida cruza a cidade.
    """
    def par(dist_m):
        dlat = dist_m / 111000.0
        return ev.avaliar(
            _poi(nome="Bico de Pão", logr_marcado="AVENIDA FARROUPILHA",
                 tier="CONFIRMA"),
            _poi(nome="Bico de Pão", logr_marcado="AVENIDA FARROUPILHA",
                 tier="CONFIRMA", lat=-29.9 + dlat))
    for d in (5, 80, 400):
        r = par(d)
        assert r["decisao"] == "fundir" and r["confianca"] == 10, \
            f"a {d} m deixou de ser confiança máxima"
    assert par(3000)["decisao"] != "fundir", \
        "a 3 km pode ser filial — isso tem de ir para a IA, não fundir sozinho"


def test_o_cruzamento_compara_a_CIDADE_e_nao_so_a_area():
    """Ao minerar uma área nova, o que ela descobriu tem de ser confrontado com
    toda a base daquela cidade.

    Recortando pela área, um POI novo era comparado com ~118 vizinhos em vez dos
    49.559 da cidade. Medido: 7 fusões na área contra 13.271 na cidade — e
    Canoas caiu de 49.629 para 36.854 POIs ativos (26%).
    """
    import io
    s = io.open(os.path.join(RAIZ, "minerar_tudo.py"), encoding="utf-8").read()
    i = s.index("cruzar_fontes.py")
    corpo = s[i:i + 300]
    assert '"--area"' not in corpo, \
        "o cruzamento voltou a comparar só a área — a cidade fica com duplicata"
    assert '"--cidade"' in corpo


def test_a_gravacao_das_fusoes_e_em_lote():
    """MEDIDO em 26/08/2026, sobre 400 fusões de POIs reais de Canoas:

        um a um    180,6 s  ·      2 fusões/s  ·  13.271 levariam 99,8 min
        EM LOTE      0,3 s  ·  1.596 fusões/s  ·  13.271 levam    0,1 min

    602× — e o custo não era o banco, era a latência: dois comandos por fusão
    contra o Postgres do i9 viram 26.542 idas e voltas.

    A DECISÃO continua uma a uma em Python, e tem de continuar: a transitividade
    exige ordem (se A absorve B e depois B absorveria C, C vai para A, não para
    B, que já é POI fundido). O que virou lote é só a ESCRITA, depois de as
    cadeias já estarem resolvidas.
    """
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    i = s.index("def aplicar(")
    j = s.index("def cruzar(", i)
    corpo = s[i:j]
    assert "execute_values" in corpo, "a gravação voltou a ser uma a uma"
    assert "id = any(" in corpo, "o `update pois` voltou a ser um por vez"
    # a decisão continua sequencial — é o que preserva a transitividade
    assert "def raiz(" in corpo and "sorted(decisoes" in corpo, \
        "a resolução de cadeia saiu de antes da escrita"


def test_o_lote_tem_teto():
    """`execute_values` embute os valores no texto do comando. Com 13 mil linhas
    ele passa de megabytes e o parser do Postgres vira o gargalo no lugar da
    rede — troca-se um problema por outro."""
    import io
    s = io.open(os.path.join(RAIZ, "cruzar_fontes.py"), encoding="utf-8").read()
    i = s.index("def aplicar(")
    assert "LOTE = 1000" in s[i:i + 3000], "o teto do bloco sumiu"
