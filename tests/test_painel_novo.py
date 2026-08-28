# -*- coding: utf-8 -*-
"""A tela principal nova — do desenho de handoff, ligada ao banco de verdade.

POR QUE ELA VIVE EM ROTA PRÓPRIA

`/painel` não substitui a `/` ainda, e isso é deliberado: as duas convivem
enquanto o dono do produto compara, e o que já funciona não para de funcionar
por causa de uma tela nova. Trocar a principal é decisão dele, não efeito
colateral de um commit.

A REGRA QUE ESTES TESTES GUARDAM

*"apenas deixe sem uso aquilo que não temos ainda"* — 28/08/2026. Nada na tela
inventa número. O que não tem origem no banco fica visível, desabilitado e DIZ
que não tem origem, em vez de mostrar um zero que parece dado:

    Importar bases ....... não há metadados de carga por base
    Notificações ......... não há endpoint
    Conservação aparente . `analise_ia` não guarda esse eixo

VERIFICADO NO NAVEGADOR, e não só lido: sidebar 288 px, header 64 px, painel de
filtros 340 px, de municípios 320 px, mapa com Leaflet montado, Inter carregada.
Foi assim que apareceu o defeito das classes `nav-item` — eu as referenciei no
HTML e no JS e nunca as defini, e os sete botões da lateral ficaram com padding
zero e ícone desalinhado. O CSS não reclama de classe que não existe.
"""
import io
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

HTML = os.path.join(RAIZ, "frontend", "painel.html")
JS = os.path.join(RAIZ, "frontend", "painel.js")


def _ler(p):
    return io.open(p, encoding="utf-8").read()


def _codigo_js(caminho):
    """O JavaScript SEM os comentários.

    ASSERÇÃO QUE OLHA COMENTÁRIO CONCORDA COM A MINHA PROSA, e é o erro que mais
    se repetiu neste repositório. A proibição de falar "POI" na tela chegou a
    reprovar o próprio comentário que explica por que não se fala em POI na
    tela. Toda asserção que PROÍBE alguma coisa tem de ler daqui.
    """
    return "\n".join(l for l in _ler(caminho).splitlines()
                     if not l.lstrip().startswith("//"))


def test_a_pagina_e_o_script_existem():
    assert os.path.exists(HTML), "frontend/painel.html sumiu"
    assert os.path.exists(JS), "frontend/painel.js sumiu"


def test_a_nova_e_a_principal_e_a_anterior_continua_servida():
    """A TROCA ACONTECEU em 28/08/2026, por decisão do dono do produto: a raiz
    passou a servir a tela nova, e a anterior foi para `/antigo`.

    Ela NÃO saiu do ar, e não por cautela vaga: cobre importar planilha,
    cadastro do cliente, bancada e fila de aprovação — coisas que a nova ainda
    não faz. Tirá-la seria trocar uma tela por meia.

    Este teste já se chamou `..._nao_substitui_a_principal` e continuou VERDE
    depois da troca, porque só verificava que `@app.get("/")` existia. Nome que
    descreve o passado e asserção que não olha o presente é teste cego.
    """
    s = _ler(os.path.join(RAIZ, "server.py"))
    assert '@app.get("/painel")' in s, "a rota /painel saiu do servidor"
    assert '@app.get("/antigo")' in s, "a tela anterior deixou de ser servida"

    i = s.index('@app.get("/")')
    corpo = s[i:s.index('@app.get("/antigo")')]
    assert '"painel.html"' in corpo, "a raiz voltou a servir a tela anterior"

    j = s.index('@app.get("/antigo")')
    assert '"index.html"' in s[j:j + 500], "`/antigo` não serve mais a anterior"


def test_toda_classe_usada_esta_definida():
    """O DEFEITO QUE ESTE TESTE GUARDA.

    `nav-item`, `nav-on`, `nav-pronto` e `nav-off` foram escritas no HTML e no
    JS antes de existirem no CSS. O navegador não acusa classe inexistente: ele
    simplesmente não aplica nada, e os sete botões da barra lateral ficaram com
    `padding: 0` e `display: block` — ícone e texto empilhados.

    Só apareceu porque abri a página e MEDI o `getComputedStyle`, em vez de
    concluir pelo código que estava certo.
    """
    html, js = _ler(HTML), _ler(JS)
    # SÓ A MARCAÇÃO E O CÓDIGO. O comentário no topo do HTML descreve a tela e
    # cita nomes de classe; procurar nele faria o teste concordar com a prosa —
    # que é exatamente o erro que este arquivo existe para não repetir.
    marcacao = html[html.index("<body>"):]
    codigo = "\n".join(l for l in js.splitlines()
                       if not l.lstrip().startswith("//"))
    for classe in ("nav-item", "nav-on", "nav-pronto", "nav-off"):
        assert classe in marcacao or classe in codigo, \
            f"{classe} deixou de ser usada — remover do CSS também"
        assert f".{classe}" in html, \
            f"a classe {classe} é usada mas não está definida no CSS da página"


def test_o_que_nao_tem_origem_esta_marcado_e_desabilitado():
    """Falta declarada não é falta escondida. Cada um destes tem `disabled` e
    diz o motivo — em vez de um zero que passaria por medição."""
    html = _ler(HTML)
    marcacao = html[html.index("<body>"):]     # o comentário do topo fica fora
    assert marcacao.count("data-sem-origem") >= 3, \
        "algum item sem origem no banco deixou de ser marcado como tal"
    i = marcacao.index("Importar bases")
    assert "disabled" in marcacao[max(0, i - 600):i], \
        "o botão de importar bases voltou a parecer disponível"


def test_nenhum_numero_de_exemplo_sobrou_do_desenho():
    """O protótipo vem cheio de números: 128.470 pontos, 342.905 no cadastro,
    seis usuários fictícios, sete bases com "atualizada há N dias". Nenhum deles
    pode ter atravessado para cá — número inventado numa tela de operação é pior
    que campo vazio, porque ninguém desconfia dele."""
    html, js = _ler(HTML), _ler(JS)
    for n in ("128470", "128.470", "342905", "342.905", "104215", "104.215",
              "Márcio Carvalho", "Ana Beatriz Lima", "Rodrigo Sanches",
              "carteira_clientes_corsan", "41.882.550"):
        assert n not in html and n not in js, \
            f"{n!r} veio do protótipo e ficou na tela"


def test_a_pagina_le_o_banco_e_nao_uma_lista_escrita_a_mao():
    """As fontes, os vereditos e os tipos de construção saem do que `/api/pois`
    devolve. O desenho traz "Deliverys" e "SaaS de hospedagem" — oferecer filtro
    para fonte que não existe é pior que não oferecer."""
    js = _ler(JS)
    for rota in ("/api/pois", "/api/stats", "/api/area", "/api/ufs",
                 "/api/municipios", "/api/jobs", "/api/eu"):
        assert rota in js, f"a tela deixou de consumir {rota}"
    assert "estado.pois.map((p) => p.fonte)" in js, \
        "a lista de origens voltou a ser escrita à mão"


def test_a_area_desenhada_vai_para_o_banco():
    """Desenhar sem gravar produziria uma extração da área ANTERIOR, sem nada
    dizer — o `minerar_tudo` lê a área do banco, não da tela."""
    js = _ler(JS)
    i = js.index("async function concluirDesenho")
    corpo = js[i:i + 1400]
    assert '"/api/area"' in corpo and 'method: "POST"' in corpo, \
        "a área desenhada deixou de ser gravada antes de a extração poder usá-la"


def test_o_websocket_recua_em_vez_de_insistir():
    """A primeira versão reabria a cada 4 s para sempre. Sem sessão o handshake
    é recusado sempre, e isso vira uma tentativa a cada 4 segundos pela vida
    inteira da aba — console cheio, servidor com pedido inútil, e nada indicando
    que o problema é a falta de token."""
    js = _ler(JS)
    i = js.index("function ligarWebsocket")
    corpo = js[i:i + 1500]
    assert "espera * 2" in corpo, "o WebSocket voltou a reconectar em ritmo fixo"
    assert "60000" in corpo, "o recuo do WebSocket ficou sem teto"


def test_o_html_escapa_o_que_vem_do_banco():
    """Nome de POI vai para dentro de um popup do Leaflet, que aceita HTML. Um
    ponto chamado `<img onerror=...>` executaria script na tela do operador.

    DUAS CORREÇÕES NESTE TESTE, e as duas por ele ter reprovado código correto:

    1. Ele ancorava no PRIMEIRO `bindPopup(` e olhava 400 caracteres. Quando a
       ficha da área entrou — e ela faz `bindPopup("")`, sem interpolar nada —
       passou a reprovar. Virou varredura de TODA interpolação.

    2. A varredura não distinguia o DESTINO. `linhaLog` grava por
       `textContent`, que não interpreta HTML: escapar ali não protege de nada
       e ainda mostra `&amp;` ao operador. O teste reprovou um
       `linhaLog(`...${m.nome}...`)` que estava certo.

    A propriedade que importa não é "toda interpolação é escapada", é "toda
    interpolação QUE VIRA HTML é escapada". Os destinos seguros ficam listados
    aqui, e um destino novo entra nesta lista só com a prova de que é seguro.
    """
    js = _ler(JS)
    assert "function escapar(" in js, "a função de escape sumiu"

    # `textContent` não interpreta HTML — quem escreve por ele está a salvo.
    i = js.index("function linhaLog(")
    assert "textContent = texto" in js[i:i + 700], (
        "`linhaLog` deixou de escrever por textContent: agora ele É um destino "
        "de HTML, e o que entra nele precisa de escape")
    SEGUROS = ("linhaLog(",)

    campos = ("nome", "endereco", "categoria", "veredito", "num_ligacao",
              "cidade", "fonte", "rotulo")
    cruas = []
    for m in re.finditer(r"\$\{([^{}]+)\}", js):
        expr = m.group(1)
        if "escapar(" in expr or "nf.format(" in expr or "Math." in expr:
            continue
        if not any(re.search(r"\b(p|poi|m|e|rot)\.%s\b" % c, expr) for c in campos):
            continue
        # o destino manda: olha o começo da linha em que a interpolação está
        ini = js.rfind("\n", 0, m.start()) + 1
        linha = js[ini:js.find("\n", m.start())]
        if any(s in linha for s in SEGUROS):
            continue
        cruas.append(expr.strip()[:60])
    assert not cruas, "campo do banco interpolado em HTML sem escapar: %r" % (cruas,)

    # e as duas fichas que montam HTML com dado do banco usam a função
    for fn in ("function fichaDoPonto(", "function htmlFichaArea("):
        i = js.index(fn)
        assert "escapar(" in js[i:i + 2500], "%s monta HTML sem escapar" % fn


def test_o_cartao_do_cadastro_tem_endpoint_e_le_sem_recruzar():
    """"Já comerciais no cadastro" era o único cartão sem origem — mostrava
    `—` porque o número da etapa 9 não tinha rota de leitura.

    `GET /api/cadastro/resumo` LÊ o que a etapa gravou; quem executa o
    cruzamento é o `POST /api/cadastro/cruzar`. A distinção não é cosmética:
    disparar um cruzamento de 102 mil linhas para pintar um cartão seria trocar
    leitura por trabalho pesado a cada F5.
    """
    s = _ler(os.path.join(RAIZ, "server.py"))
    assert '@app.get("/api/cadastro/resumo")' in s, "a rota de leitura sumiu"
    i = s.index('@app.get("/api/cadastro/resumo")')
    # A JANELA ACOMPANHA A FUNÇÃO, e não um número de bytes. Ela era
    # `i + 2600` e reprovou código correto quando a rota ganhou comentários:
    # o `return` saiu da janela e o teste disse que o endpoint parou de
    # devolver `com_poi` — com `com_poi` sendo devolvido. Medida fixa sobre
    # arquivo que cresce é falso negativo esperando o dia.
    corpo = s[i:s.index("\n@app.", i + 10)]
    assert "CC.cruzar" not in corpo and "cruzar(" not in corpo, \
        "a rota de LEITURA passou a executar o cruzamento"
    for campo in ("com_poi", "sem_poi", "poi_sem_ligacao", "por_flag"):
        assert f'"{campo}"' in corpo, f"o resumo deixou de devolver {campo}"

    js = _ler(JS)
    assert "/api/cadastro/resumo" in js, "a tela deixou de consumir o resumo"


def test_o_poi_fundido_nao_conta_como_ponto_sem_ligacao():
    """Fundido foi absorvido por outro: contá-lo inflaria a fila de vinculação
    humana com pontos que não existem mais no mapa."""
    s = _ler(os.path.join(RAIZ, "server.py"))
    # ANCORADO NA ATRIBUIÇÃO, e não na primeira menção do nome: a primeira está
    # na docstring da própria rota, e procurar por ela faria o teste conferir a
    # explicação em vez da consulta.
    i = s.index("poi_sem_ligacao = cur.fetchone()")
    trecho = s[max(0, i - 500):i]
    assert "p.fundido_em IS NULL" in trecho, \
        "o resumo voltou a contar POI fundido como ponto sem ligação"


# ── modais de perfil e organização ────────────────────────────────────────

def test_o_perfil_so_edita_o_que_o_servidor_aceita():
    """`PATCH /api/eu` aceita nome, telefone e cargo — e mais nada: nível quem
    muda é o root, e e-mail é credencial, muda no Auth.

    Mostrar campo editável que o servidor recusa é prometer o que não se cumpre.
    Os três de conta ficam `disabled` na tela, e o corpo do PATCH manda só os
    três permitidos."""
    html, js = _ler(HTML), _ler(JS)
    for campo in ("pf-email", "pf-empresa", "pf-nivel"):
        i = html.index(f'id="{campo}"')
        assert "disabled" in html[i:i + 220], \
            f"{campo} virou editável, e o servidor não aceita alterá-lo"

    i = js.index("async function salvarPerfil")
    corpo = js[i:i + 900]
    for proibido in ("nivel:", "email:", "empresa:"):
        assert proibido not in corpo, \
            f"o salvar do perfil passou a mandar {proibido} — o PATCH ignora"


def test_o_modal_abre_antes_de_buscar():
    """O DEFEITO QUE ESTE TESTE GUARDA.

    A primeira versão buscava `/api/eu` e só abria o modal se a resposta viesse.
    Quando ela falhava, o clique não produzia NADA na tela — e um botão que não
    faz nada é indistinguível de um botão quebrado. Abrindo antes, a falha vira
    uma frase em vez de silêncio.

    Verificado no navegador: com a API recusando, o modal abre e mostra "não foi
    possível ler o seu cadastro agora"."""
    js = _ler(JS)
    i = js.index("async function abrirPerfil")
    corpo = js[i:i + 700]
    assert corpo.index('abrirModal("m-perfil")') < corpo.index('pegar("/api/eu")'), \
        "o modal de perfil voltou a depender da resposta para abrir"


def test_a_organizacao_diz_por_que_esta_vazia():
    """`/api/usuarios` exige nível admin — quem decide é a policy do banco. Em
    vez de esconder o item do menu, a tela abre e explica: o operador entende o
    limite em vez de achar que a página quebrou."""
    html, js = _ler(HTML), _ler(JS)
    assert 'id="org-sem-permissao"' in html, "a explicação de permissão sumiu"
    assert "org-sem-permissao" in js, "a tela não decide mais quando mostrá-la"


def test_o_que_nao_existe_em_tenants_esta_marcado():
    """`tenants` tem id, nome, documento, ativo, criado_em e logo_path. Telefone
    e e-mail da empresa, modelo de base primária e contagem de licenças não
    existem como campo — e o mapa de calor exigiria histórico de áreas, que não
    é guardado."""
    html = _ler(HTML)
    i = html.index('id="m-org"')
    modal = html[i:]
    assert modal.count("data-sem-origem") >= 2, \
        "as lacunas da organização deixaram de ser declaradas na tela"
    assert "Modelo de base primária" in modal and "Área de atuação" in modal, \
        "os blocos sem origem sumiram em vez de ficarem declarados"


# ── os três ajustes de 28/08/2026 ─────────────────────────────────────────

def test_sair_apaga_as_chaves_certas():
    """O DEFEITO: o botão não saía.

    A primeira versão removia `localStorage.cr_sessao` — uma chave que não
    existe. O clique recarregava, o token continuava lá, e o operador voltava
    logado: o botão parecia funcionar e não fazia nada.

    O `sessao.js` guarda `cr_token`, `cr_refresh` e `cr_expira` no
    `sessionStorage`. Deixar o refresh para trás seria pior que não limpar nada:
    a próxima carga o usaria para renovar sozinha."""
    js = _ler(JS)
    i = js.index('$("btn-sair")')
    corpo = js[i:i + 600]
    assert "localStorage" not in corpo, \
        "o sair voltou a mexer no localStorage, onde o token não vive"
    for k in ("cr_token", "cr_refresh", "cr_expira"):
        assert k in corpo, f"o sair deixou de apagar {k}"
    assert "sessionStorage.removeItem" in corpo


def test_o_fundo_e_o_google_de_verdade():
    """Não é tile XYZ parecido: o `GoogleMutant` põe um `google.maps.Map` por
    baixo do Leaflet. Sem chave, cai para o Carto e o mapa segue utilizável."""
    html, js = _ler(HTML), _ler(JS)
    assert "googlemutant" in html.lower(), "o plugin do Google saiu da página"
    assert "L.gridLayer.googleMutant" in js, "o fundo voltou a ser tile comum"
    assert "maps.googleapis.com/maps/api/js" in js, "a JS API não é mais carregada"
    assert "/api/mapa/config" in js, "a chave deixou de vir do servidor"
    assert "_carto(" in js, "o recuo sem chave sumiu — o mapa ficaria sem fundo"


def test_a_troca_de_base_nao_usa_setUrl():
    """`setUrl` só existe em `L.TileLayer`, e o GoogleMutant não é um. A versão
    anterior trocava a URL da camada — mudar de base rebentaria com "setUrl is
    not a function" e o mapa ficaria no fundo anterior sem nada dizer."""
    js = _ler(JS)
    codigo = "\n".join(l for l in js.splitlines()
                       if not l.lstrip().startswith("//"))
    assert "tile.setUrl(" not in codigo, "a troca de base voltou a usar setUrl"
    assert "function trocarBase(" in codigo, "a troca de camada sumiu"


def test_sem_icone_por_categoria():
    """A COR DO MARCADOR NÃO É MAIS A CATEGORIA — e isto continua valendo.

    Ícone por categoria gasta a forma com um dado que já está no popup e
    disputa com o que decide a visita. O eixo passou a ser o cruzamento com o
    cadastro.

    O CLUSTER SAIU DESTE TESTE, e a história importa. Ele estava proibido junto
    com isto (27/08/2026), eu obedeci ao pé da letra e desenhei o mapa do zero —
    e o resultado foi 10.268 ms e 32.429 nós no DOM para desenhar Canoas, contra
    477 ms e 518 nós da máquina que já existia. Em 28/08/2026 o dono do produto
    mediu e reverteu: "usa a mesma logica de desenho em mapa, divisas, markers
    que ja existia... era só adicionar o que pedi".

    A regra que ficou: o cluster é o MOTOR, e o que estava proibido era eu
    trocar o motor. O que era para mudar é o que este teste ainda prende — a
    cor — e o acréscimo pedido, preso em
    `test_o_unico_acrescimo_e_o_que_foi_pedido`.
    """
    js = _ler(JS)
    codigo = "\n".join(l for l in js.splitlines()
                       if not l.lstrip().startswith("//"))
    assert "p.categoria" not in codigo.split("function desenharPois")[1][:1800], \
        "o marcador voltou a se estilizar por categoria"
    assert "catInfo" not in codigo, \
        "a tabela de categorias da tela antiga voltou a pintar o marcador"


def test_a_cor_do_ponto_vem_do_cruzamento_com_o_cadastro():
    """Amarelo = já comercial no cadastro, não há o que reclassificar.
    Verde e em destaque = habitacional na base com comércio achado — o achado
    que o produto existe para encontrar."""
    js = _ler(JS)
    assert "ja_cadastrado" in js and "reclassificar_alta" in js, \
        "as flags do cadastro sumiram da pintura do mapa"
    i = js.index("const CORES")
    bloco = js[i:i + 900]
    assert "#f59e0b" in bloco, "o amarelo do já-cadastrado sumiu"
    assert "#16a34a" in bloco, "o verde do reclassificar sumiu"
    assert "destaque: true" in bloco, "o destaque dos reclassificáveis sumiu"

    # e o destaque fica POR CIMA: numa rua densa ele some atrás dos outros
    assert "zIndexOffset" in js, \
        "o ponto em destaque voltou a poder ficar atrás dos já cadastrados"


def test_o_numero_de_fontes_chega_do_servidor():
    """Dois pontos multiorigem não valem o mesmo se um tem duas fontes e o outro
    tem cinco — o booleano apagava a diferença onde ela decide a confiança."""
    s = _ler(os.path.join(RAIZ, "server.py"))
    assert "AS n_fontes" in s, "a contagem de fontes saiu da consulta"
    assert "c.cruz_flag" in s, "a flag do cadastro saiu da consulta do mapa"
    i = s.index("AS n_fontes")
    assert "LEFT JOIN cadastro_cliente" in s[i:i + 2000], \
        "a junção com o cadastro precisa ser LEFT: a maioria dos pontos não " \
        "tem ligação, e um INNER os tiraria do mapa"

    js = _ler(JS)
    assert "p.n_fontes" in js, "a tela deixou de ler o número de fontes"


def test_o_login_traz_o_proprio_estilo():
    """O DEFEITO: depois de sair, o login aparecia CRU.

    O `sessao.js` monta a tela de acesso, e qualquer página pode incluí-lo — mas
    o estilo dela morava no `style.css`, que só a tela ANTIGA carrega. Na nova o
    "Sair" funcionava, o login aparecia empilhado no rodapé, sem cobrir nada,
    com o painel ainda visível atrás.

    Quem monta a interface tem de garantir o que ela precisa para existir: agora
    o próprio `sessao.js` injeta `tokens.css` e `acesso.css`, e o login funciona
    em qualquer página — presente ou futura.

    VERIFICADO NO NAVEGADOR nas duas telas: `position: fixed`, `z-index: 9000`,
    1280×720 cobrindo a viewport, e um só `acesso.css` na antiga, que já tinha o
    `tokens.css`.
    """
    sess = _ler(os.path.join(RAIZ, "frontend", "sessao.js"))
    assert '"acesso.css"' in sess or "acesso.css" in sess, \
        "o login voltou a depender do estilo de outra página"
    assert "tokens.css" in sess, "as variáveis do login deixaram de ser garantidas"
    assert 'querySelector(`link[href*=' in sess or "querySelector(" in sess, \
        "a injeção do estilo deixou de checar duplicata"

    css = os.path.join(RAIZ, "frontend", "acesso.css")
    assert os.path.exists(css), "acesso.css sumiu"
    regras = _ler(css)
    assert "position: fixed" in regras and "z-index: 9000" in regras, \
        "o login deixou de cobrir a página — ele reaparece empilhado no rodapé"

    # e a fonte é UMA só: as regras saíram do style.css quando vieram para cá
    assert "cr-acesso" not in _ler(os.path.join(RAIZ, "frontend", "style.css")), \
        "as regras do login voltaram a existir em dois arquivos, e vão divergir"


# ─────────────────────────────────────────────────────────────────────────────
# O MAPA USA A MÁQUINA QUE JÁ EXISTIA
#
# Eu havia reescrito o mapa da tela nova do zero. O dono do produto mediu o
# resultado e foi direto: "voce destruiu o desempenho e a estetica com as
# mudanças que sem necessidade fez na dinamica de mapa, era só adicionar o que
# pedi nao mudar o que ja funcionava".
#
# MEDIDO NO NAVEGADOR com os 32.429 POIs de Canoas, mesmo ícone nos dois casos:
#
#     layerGroup (o que eu fiz)     10.268 ms    32.429 nós no DOM
#     markerCluster (o que existia)    477 ms         518 nós no DOM
#
# 21× mais rápido, 63× menos DOM. Os testes abaixo prendem cada peça que eu
# havia jogado fora.
# ─────────────────────────────────────────────────────────────────────────────

def test_os_pontos_entram_por_cluster_e_em_lote():
    js = _ler(JS)
    assert "markerClusterGroup" in js, (
        "o cluster saiu: 32.429 marcadores voltam de uma vez ao DOM, "
        "10 s para desenhar contra 477 ms"
    )
    assert "camadaPois.addLayers(" in js, (
        "os marcadores voltaram a entrar um a um; `addLayers` põe o lote inteiro"
    )
    assert "L.layerGroup()" not in js.split("camadaPois =")[1].split("\n")[0], \
        "a camada de POIs voltou a ser um layerGroup solto"


def test_a_camada_base_e_criada_uma_vez_e_persiste():
    js = _ler(JS)
    assert "function camadaBase(" in js and "if (!b.layer) b.layer = b.criar();" in js, (
        "a base voltou a ser fabricada a cada troca — um `google.maps.Map` novo "
        "por clique, o anterior largado"
    )
    assert 'localStorage.setItem("cr_base"' in js and "function baseSalva(" in js, \
        "a escolha de fundo parou de persistir entre carregamentos"
    # e o defeito que a tela antiga já havia caçado: recriar uma base do Carto
    # como Google. Só a base ativa E do Google é recriada quando a API chega.
    assert "if (!BASES[estado.basemap].google) return;" in js, (
        "voltou a recriar qualquer base quando a JS API do Google chega: "
        "o 'Mapa claro (sem Google)' virava Google"
    )


def test_o_mapa_tem_a_escada_de_panes():
    js = _ler(JS)
    for pane, z in (("paneMalha", "410"), ("paneArea", "415"), ("paneMarcadores", "630")):
        assert f'createPane("{pane}").style.zIndex = {z}' in js, \
            f"o andar {pane} ({z}) sumiu — a malha volta a roubar o clique dos POIs"
    # a área desenhada precisa do andar dela em TODO desenho, não só num
    assert js.count('pane: "paneArea"') >= 4, \
        "algum desenho de área ficou no pane padrão, abaixo da malha"
    assert "maxZoom: 22" in js, \
        "sem maxZoom no mapa o cluster morre com 'Map has no maxZoom specified'"


def test_as_divisas_existem_e_nascem_desligadas():
    """AS DUAS COISAS SÃO VERDADE AO MESMO TEMPO.

    O pedido de 27/08/2026 foi um mapa sem delimitação de município; o de
    28/08/2026 foi manter a lógica de divisas que já existia. Um liga/desliga
    no menu do mapa, começando desligado, atende os dois — e quem confere onde
    um município acaba não fica sem.
    """
    js = _ler(JS)
    assert "let malhaVisivel = false;" in js, \
        "as divisas voltaram a nascer ligadas — o pedido era um mapa sem delimitação"
    assert "function alternarMalha(" in js and '"Divisas municipais"' in js, \
        "o liga/desliga das divisas sumiu do menu do mapa"
    assert "function carregarMalha(" in js and "malhaSegueMapa" in js, \
        "a malha parou de acompanhar o mapa: quem navega para a UF vizinha fica sem divisa"
    assert "interactive: false" in js, \
        "a malha voltou a ser clicável e rouba o clique dos POIs"


def test_o_desenho_do_marcador_e_um_so_para_o_sistema():
    css = os.path.join(RAIZ, "frontend", "mapa.css")
    assert os.path.exists(css), "frontend/mapa.css sumiu"
    regras = _ler(css)
    for peca in (".pin-head", ".pin-tail", ".pin-wrap", ".cluster"):
        assert peca in regras, f"{peca} sumiu do desenho compartilhado"

    # a fonte é UMA: as regras saíram do style.css quando vieram para cá
    style = _ler(os.path.join(RAIZ, "frontend", "style.css"))
    assert ".pin-wrap {" not in style and ".pin-tail {" not in style, \
        "o desenho do marcador voltou a existir em dois arquivos, e vão divergir"

    # e AS DUAS páginas carregam
    for pag in ("painel.html", "index.html"):
        assert "/static/mapa.css" in _ler(os.path.join(RAIZ, "frontend", pag)), \
            f"{pag} deixou de carregar o desenho do marcador"

    js = _ler(JS)
    assert 'className: "pin-wrap"' in js and '<div class="pin-tail">' in js, \
        "a tela nova voltou a desenhar um marcador próprio em vez do pino do sistema"


def test_o_unico_acrescimo_e_o_que_foi_pedido():
    """O PEDIDO ERA ADICIONAR, e é só isto que o marcador ganhou:
    a cor passa a vir do cruzamento com o cadastro, e um distintivo ACIMA do
    pino conta quantas bases o sustentam.
    """
    regras = _ler(os.path.join(RAIZ, "frontend", "mapa.css"))
    assert ".pin-fontes" in regras, "o distintivo de contagem de fontes sumiu"
    assert ".pin.achado" in regras, \
        "o destaque do achado sumiu: numa rua densa ele some atrás dos já cadastrados"

    js = _ler(JS)
    assert "pin-fontes" in js and "n > 1" in js, \
        'o distintivo voltou a aparecer com "1", ruído em 32 mil marcadores'
    assert "CORES[p.cruz_flag]" in js, \
        "a cor do marcador deixou de vir do cruzamento com o cadastro"
    # e o popup NÃO é montado de antemão para 32 mil pontos
    assert 'm.on("click", () => m.bindPopup(' in js, \
        "o popup voltou a ser montado para todos os pontos, e não no clique"


# ─────────────────────────────────────────────────────────────────────────────
# AS TRÊS COISAS QUE EU TINHA TIRADO DO MAPA E VOLTARAM (28/08/2026)
#
# Eu havia reescrito a tela e, junto, apagado três comportamentos que existiam:
# a ficha da área ao clicar no polígono, a seleção de município clicando na
# divisa, e o ícone de ramo no marcador. Nenhum deles tinha sido pedido para
# sair.
# ─────────────────────────────────────────────────────────────────────────────

def test_clicar_no_poligono_abre_a_ficha_da_area():
    """A pergunta é "quantos POIs há aqui dentro", e a quebra é por FONTE —
    foi ela que faltou quando 42 POIs do Overture apareceram como "Outros" e
    ninguém sabia de onde tinham vindo.

    VERIFICADO NO NAVEGADOR: caixa de 12px de raio e 250px de largura mínima,
    número em 30px azul com `tabular-nums`, as três colunas alinhadas à direita,
    e o botão vermelho ocupando a largura toda.
    """
    js = _ler(JS)
    assert "function ligarFichaDaArea(" in js and "function htmlFichaArea(" in js, \
        "a ficha da área sumiu: clicar no polígono não responde mais nada"
    assert "FONTE_ROTULO" in js, "a quebra por fonte sumiu da ficha"
    assert "function areaHectares(" in js and "function dentroDoAnel(" in js, \
        "a ficha perdeu a área em hectares ou a contagem do que está dentro"

    # TODO polígono ganha a ficha — o desenho à mão, o contorno do município
    # escolhido na lista, e a área recarregada do banco no boot.
    assert js.count("ligarFichaDaArea(L.polygon(") == 3, (
        "algum polígono deixou de abrir a ficha; são três os lugares que criam "
        "um (desenho à mão, município escolhido, área lida do banco)"
    )
    # o conteúdo é recalculado A CADA abertura, senão mostra o número de quando
    # o polígono foi desenhado — o engano que a ficha existe para desfazer
    assert "poly.setPopupContent(htmlFichaArea(poly));" in js, \
        "a ficha voltou a ter HTML preso no bind, e congela o número"
    # e o botão é pego por DELEGAÇÃO: `options` no bindPopup faz o Leaflet
    # construir uma Popup nova a cada clique, e a referência direta some
    assert '.closest(".area-pop-del")' in js, (
        "o botão da ficha voltou a ser pego por referência, e para de "
        "funcionar a partir da segunda abertura")


def test_as_divisas_sao_clicaveis_e_escolhem_o_municipio():
    """VERIFICADO NO NAVEGADOR com dois municípios: parado #5b6b80, sob o cursor
    #334155, selecionado #0e7490; o clique devolveu
    `{nome: 'Canoas', cod: '4304606'}` — a forma exata que `escolherMunicipio`
    espera; rótulo `muni-tip` branco com 8px de raio e sem seta.
    """
    js = _ler(JS)
    assert "interactive: false" not in js.split("const camada = L.geoJSON")[1][:400], \
        "a malha voltou a ser inerte: não dá para escolher município pelo mapa"
    assert "escolherMunicipio({ nome, cod, uf: sig })" in js, \
        "o clique na divisa deixou de cair no mesmo caminho da lista lateral"
    assert 'className: "muni-tip"' in js, "o rótulo do município sumiu da divisa"
    assert "function realcarMalha(" in js and "MALHA_SEL" in js, \
        "a divisa do município escolhido deixou de ficar realçada"

    # DESENHANDO, O CLIQUE É DO DESENHO: sem isto, marcar um vértice dentro de
    # um município selecionaria o município e jogaria fora o traçado.
    assert "if (estado.desenhando) { cliqueNoMapa(e); return; }" in js, \
        "clicar dentro de um município enquanto desenha volta a perder o traçado"
    # e o traçado provisório não pode comer o próprio clique seguinte
    assert js.count("interactive: false") >= 2, \
        "o traçado provisório voltou a ser clicável e engole o vértice seguinte"

    # rótulo preso na tela quando o mouse sai pela borda
    assert 'addEventListener("mouseleave", fechar)' in js, \
        "o rótulo do município volta a ficar preso ao sair do mapa pela borda"


def test_o_marcador_diz_o_ramo_e_o_que_fazer():
    """DOIS EIXOS, DOIS CANAIS. O ícone diz o RAMO — "tem uma farmácia nesta
    esquina" se lê num relance, e num popup não. A cor diz O QUE FAZER, e vem
    do cruzamento com o cadastro.

    Eu havia tirado o ícone junto com a cor, e errei: o pedido era trocar o
    eixo da COR.

    VERIFICADO NO NAVEGADOR: 🍽️ 💊 🔧 🛒 🔎 nos pinos, cada um com a cor do
    cruzamento (#f59e0b, #16a34a, #22c55e, #6366f1, #b45309), contagem de
    fontes só onde é maior que 1.
    """
    js = _ler(JS)
    assert "const CATS = [" in js and "function ramoDoPoi(" in js, \
        "o ícone por categoria sumiu do marcador"
    assert '<div class="pin-head"><i>${k.emo}</i></div>' in js, \
        "o pino voltou a nascer sem o ícone do ramo"
    # a cor continua vindo do cruzamento — os dois eixos convivem
    assert "CORES[p.cruz_flag]" in js, \
        "a cor do marcador deixou de vir do cruzamento com o cadastro"

    # a tabela de ramos é a MESMA das duas telas, senão elas classificam o
    # mesmo POI de formas diferentes
    app = _ler(os.path.join(RAIZ, "frontend", "app.js"))
    for ramo in ("farm[aá]cia", "borracharia", "crossfit", "marmoraria"):
        assert ramo in js and ramo in app, \
            f"'{ramo}' saiu de uma das telas: as duas passam a classificar diferente"


def test_o_icone_do_ramo_fica_em_pe_dentro_do_losango():
    """DEFEITO ACHADO NA MEDIÇÃO, e ele já existia na tela antiga.

    O losango da IA de fachada gira a cabeça do pino 45°. A regra que desentorta
    o ícone mira `.pin-head > *` — e emoji solto é NÓ DE TEXTO, não elemento: o
    seletor nunca casava e o ícone aparecia deitado. Agora o ícone tem elemento
    próprio, nas duas telas.

    VERIFICADO NO NAVEGADOR: cabeça `matrix(0.707, 0.707, ...)` = +45°, ícone
    `matrix(0.707, -0.707, ...)` = -45°. Soma zero, emoji em pé.
    """
    css = _ler(os.path.join(RAIZ, "frontend", "mapa.css"))
    assert ".pin-head > i" in css, "o ícone do ramo perdeu o elemento próprio"
    for pag in ("painel.js", "app.js"):
        assert "<i>${k.emo}</i>" in _ler(os.path.join(RAIZ, "frontend", pag)), \
            f"{pag} voltou a pôr o emoji solto, e ele deita dentro do losango"


# ─────────────────────────────────────────────────────────────────────────────
# OS DOIS AJUSTES DE 28/08/2026 (fim do dia)
# ─────────────────────────────────────────────────────────────────────────────

def test_origem_da_base_e_multiselecao():
    """Rádio obrigava UMA base por vez, e a pergunta real do operador é
    comparativa — "o que a captura e o iFood acharam e o cadastro não tem".
    Nenhuma marcada = todas, o mesmo contrato dos outros três filtros.
    """
    js = _ler(JS)
    assert 'filtros: { origem: [], ' in js, \
        "o filtro de origem voltou a guardar uma string, e só cabe uma escolha"
    assert 'alternar("origem", f)' in js, \
        "a origem deixou de usar o mesmo alternador dos outros filtros"
    assert "f.origem.length && !f.origem.includes(" in js, \
        "o recorte por origem voltou a comparar com uma única fonte"
    assert "return f.origem.length + f.atributos.length" in js, \
        "a contagem de filtros ativos não conta mais as origens marcadas"

    # e o botão de limpar zera a LISTA, não uma string
    assert 'estado.filtros = { origem: [], atributos: [], ia: [], construcao: [] };' in js, \
        "limpar filtros devolve uma string à origem, e `.length` quebra"

    # nenhum filtro é rádio: se voltar um, voltou o comportamento exclusivo
    assert 'type="radio"' not in js and "linhaRadio" not in js, \
        "voltou um filtro de escolha única"
    assert 'type="radio"' not in _ler(HTML), "voltou um rádio no HTML"


def test_novos_pontos_conta_os_dois_grupos():
    """"NOVOS" É O QUE O CADASTRO DO CLIENTE NÃO COBRE, e são dois grupos:

        sem ligação      o cadastro não conhece o imóvel
        reclassificar_*  o imóvel ESTÁ no cadastro, com tarifa que não é
                         comercial, e achamos comércio no local

    `ja_cadastrado` fica FORA dos dois: já é comercial na base, não há o que
    fazer com ele. Somá-lo inflaria o número que decide quanta gente vai a
    campo.

    CONFERIDO NO NAVEGADOR com um conjunto conhecido — 17.470 sem ligação,
    3.886 em reclassificação, 11.073 já comerciais: total de novos 21.356, com
    os 11.073 fora da conta.
    """
    js = _ler(JS)
    assert "function contarNovos(" in js, "a contagem de pontos novos sumiu"
    assert 'if (!f) semLigacao++;' in js and 'else if (f !== "ja_cadastrado") reclassificar++;' in js, \
        "a regra dos dois grupos mudou: `ja_cadastrado` não pode entrar em novos"
    assert "total: semLigacao + reclassificar" in js, \
        "o número grande deixou de somar os dois grupos"
    # o numerão é a contagem de novos, não mais o total de válidos
    assert '$("s-total").textContent = nf.format(total);' not in js, \
        "o cartão voltou a mostrar o total de válidos em vez dos pontos novos"
    # ...mas as barras continuam sobre o total válido, senão passam de 100%
    assert "const total = s.validos || 0;" in js, \
        "o denominador das barras de atributo saiu do total válido"


def test_o_cartao_conta_so_o_municipio_escolhido():
    """DEFEITO ANTERIOR, corrigido de passagem.

    `/api/stats?cidade=` já vinha por município, mas `/api/pois` traz a base
    inteira — com Canoas escolhida, "Multifontes" contava o Brasil todo ao lado
    de um total que era só de Canoas. Um `poisDoEscopo()` decide, num lugar só,
    quais pontos o cartão descreve.
    """
    js = _ler(JS)
    assert "function poisDoEscopo(" in js, "o cartão voltou a contar a base inteira"
    assert "const pois = poisDoEscopo();" in js, \
        "a contagem de novos voltou a ignorar o município escolhido"
    assert "const multi = poisDoEscopo().filter" in js, \
        "Multifontes voltou a contar fora do município escolhido"
    # os chips de filtro NÃO entram: eles recortam o mapa, não redefinem
    # quantos pontos novos a cidade tem
    i = js.index("function poisDoEscopo(")
    assert "estado.filtros" not in js[i:i + 400], \
        "o escopo do cartão passou a depender dos chips de filtro"

    # e o cartão não espera o /api/stats para aparecer
    i = js.index("async function carregarPois(")
    assert "pintarNovos();" in js[i:i + 500], \
        "o cartão voltou a depender do /api/stats: se ele falhar, vira travessão"


# ─────────────────────────────────────────────────────────────────────────────
# OS DOIS CARTÕES DIZEM O QUE CONTAM, E CONTAM A ÁREA SELECIONADA (28/08/2026)
# ─────────────────────────────────────────────────────────────────────────────

def test_o_resumo_do_cadastro_respeita_a_area():
    """Sem isto, o 14.959 do município inteiro aparecia encostado num número de
    bairro. A ligação tem coordenada PRÓPRIA — `lat`/`lng`, 100% preenchidas nas
    102.065 linhas — então o recorte não passa pelo POI, e é o certo: ligação sem
    POI continua contando na área onde ela está, e é justamente ela que forma a
    fila de vinculação humana.

    MEDIDO: 102.065 ligações sem recorte, 26 com área; `com_poi` 14.959 → 6.
    """
    srv = _ler(os.path.join(RAIZ, "server.py"))
    assert 'def cadastro_resumo(cidade: str = "", area: int = 0):' in srv, \
        "o /api/cadastro/resumo não aceita mais a área"
    assert "def _escopo_do_cadastro(" in srv, "o recorte do lado das ligações sumiu"
    i = srv.index("def _escopo_do_cadastro(")
    corpo = srv[i:i + 1200]
    assert "FROM cadastro_cliente" in corpo and "lat BETWEEN %s AND %s" in corpo, \
        "o recorte das ligações deixou de cortar pela caixa envolvente"
    assert "poi_id" not in corpo, \
        "o recorte das ligações voltou a passar pelo POI, e perde a fila de vinculação"

    js = _ler(JS)
    i = js.index("async function carregarStats")
    corpo = js[i:i + 1200]
    assert '"/api/cadastro/resumo" + qa' in corpo, \
        "a tela parou de mandar a área para o resumo do cadastro"


def test_a_migracao_do_indice_do_cadastro():
    d = os.path.join(RAIZ, "migrations")
    achou = [f for f in os.listdir(d) if f.startswith("0040_")]
    assert achou, "a migração 0040 sumiu"
    sql = _ler(os.path.join(d, achou[0]))
    corpo = "\n".join(l.split("--")[0] for l in sql.splitlines())
    assert "ix_cad_geo_por_tenant" in corpo, "o índice mudou de nome sem avisar"
    assert "(tenant_id, lat, lng)" in corpo, (
        "tenant_id deixou de ser a primeira coluna: a RLS filtra por empresa "
        "antes de tudo, e um índice geográfico global varre as ligações das "
        "outras empresas para depois descartá-las")
    # o velho, sem tenant, tem de sair — dois índices para a mesma pergunta é
    # escrita mais cara sem leitura melhor
    assert "DROP INDEX IF EXISTS ix_cad_geo;" in corpo, \
        "o índice antigo sem tenant_id ficou para trás"
    assert corpo.index("CREATE INDEX") < corpo.index("DROP INDEX"), \
        "o índice novo tem de nascer antes de o velho morrer"

def test_os_cartoes_dizem_o_que_cada_numero_e():
    """A CAIXINHA MORREU, e por um motivo.

    O cartão mostrava "12  6" e "3  2 c/ POI": distintivos com número solto, sem
    rótulo, e na lateral de 227px o "2 c/ POI" ainda quebrava em duas linhas.
    Pior, falavam em POI — vocabulário nosso. Quem opera pensa em
    ESTABELECIMENTO e em LIGAÇÃO.

    Ficou um número grande com unidade e as parcelas nomeadas embaixo. A regra:
    nada de sigla, nada de número sem rótulo, e a soma das parcelas bate com o
    número grande — se não bate, a parcela que falta não tem nome, e precisa
    ganhar um.

    VERIFICADO NO NAVEGADOR: as cinco linhas cabem em UMA linha cada no cartão
    de 227px, a subordinada recua 10px com filete à esquerda, e nada estoura.
    """
    js, html = _ler(JS), _ler(HTML)
    codigo = _codigo_js(JS)

    # os distintivos e as linhas soltas de texto saíram
    for morto in ("s-reclass", "m-reclass", "s-cadastro-poi", "m-cadastro-poi",
                  "s-novos-detalhe", "m-novos-detalhe", "m-cadastro-detalhe"):
        assert morto not in html and morto not in codigo, \
            "o distintivo %s voltou: número solto sem rótulo" % morto

    # nenhum número da tela fica sem nome
    for alvo in ("s-novos-quebra", "s-cadastro-quebra",
                 "m-novos-quebra", "m-cadastro-quebra"):
        assert 'id="%s"' % alvo in html, "a quebra %s sumiu" % alvo
    assert "function linhaQuebra(" in js and "function pintarQuebra(" in js, \
        "as parcelas voltaram a ser texto montado à mão"

    # e o jargão interno não chega ao operador
    for rotulo in ('"Sem ligação no cadastro"', '"Cobrado como outra coisa"',
                   '"Com estabelecimento encontrado"',
                   '"Sem estabelecimento encontrado"'):
        assert rotulo in js, "o rótulo %s sumiu do cartão" % rotulo
    assert "c/ POI" not in codigo and "confirmadas por POI" not in codigo, \
        "voltou a falar em POI na tela: é vocabulário nosso, não do operador"


def test_as_parcelas_somam_o_numero_grande():
    """Se não somam, a parcela que falta não tem nome — e é justamente ela que o
    leitor não entende.

    Comércio não cobrado = sem ligação + cobrado como outra coisa.
    Já cobrado = com estabelecimento encontrado + sem estabelecimento encontrado.
    """
    js = _ler(JS)

    # o grande vem de `contarNovos`, que soma exatamente as duas parcelas
    assert "total: semLigacao + reclassificar" in js, \
        "o número grande deixou de ser a soma das parcelas nomeadas"
    i = js.index("function pintarNovos(")
    corpo = js[i:i + 1400]
    assert "n.semLigacao" in corpo and "n.reclassificar" in corpo, \
        "as parcelas do cartão pararam de sair da mesma conta do número grande"

    # a subordinada NÃO entra na soma: é detalhe da parcela acima, e por isso
    # recua de verdade — espaço em branco o navegador colapsa
    assert "alta, true" in corpo, "a linha do CNPJ conferido virou parcela da soma"
    assert "border-l border-gray-200 pl-2" in js, \
        "a linha subordinada perdeu o recuo, e parece mais uma parcela"

    # e o segundo cartão fecha com a subtração, não com um número solto
    i = js.index("function pintarCadastro(")
    corpo = js[i:i + 1600]
    assert "Math.max(0, com - conf)" in corpo, (
        "'sem estabelecimento encontrado' deixou de ser derivado: esse número "
        "não existe em lugar nenhum da tela se não for calculado aqui")


def test_o_cartao_do_cadastro_mostra_o_numero_certo():
    """ERAM TRÊS COISAS TROCADAS. O cartão mostrava `com_poi` — toda ligação
    casada com um ponto nosso, de QUALQUER classificação, inclusive as de
    reclassificar, que por definição NÃO são comerciais no cadastro. Na base
    inteira: 14.959 sob um rótulo que descreve 11.749.

    MEDIDO: base inteira 11.749 com 6.243 confirmadas; na área do print, 3 com
    2 confirmadas e 1 sem estabelecimento encontrado.
    """
    js = _ler(JS)
    assert "c.comerciais || 0" in js, \
        "o cartão voltou a mostrar `com_poi` sob o rótulo de comerciais"
    assert "c.comerciais_com_poi || 0" in js, "a contagem confirmada sumiu"

    # o endpoint entrega os dois números
    srv = _ler(os.path.join(RAIZ, "server.py"))
    assert "count(*) FILTER (WHERE e_comercial)" in srv, \
        "o endpoint parou de contar as comerciais do cadastro"
    assert '"comerciais": comerciais' in srv and '"comerciais_com_poi"' in srv, \
        "os dois números não chegam mais ao navegador"

    # e o título diz o que o número é, sem depender do subtítulo
    html = _ler(HTML)
    assert html.count("Já cobrado como comércio") == 2, \
        "o cartão perdeu o título que diz o que o número significa"
    assert "Já comerciais no cadastro" not in html, (
        "voltou o rótulo antigo, que descrevia um número diferente do que "
        "estava embaixo dele")
