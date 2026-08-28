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
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

HTML = os.path.join(RAIZ, "frontend", "painel.html")
JS = os.path.join(RAIZ, "frontend", "painel.js")


def _ler(p):
    return io.open(p, encoding="utf-8").read()


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
    ponto chamado `<img onerror=...>` executaria script na tela do operador."""
    js = _ler(JS)
    assert "function escapar(" in js, "a função de escape sumiu"
    i = js.index("bindPopup(")
    assert "escapar(" in js[i:i + 400], \
        "o popup do mapa voltou a interpolar dado do banco sem escapar"


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
    corpo = s[i:i + 2600]
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
