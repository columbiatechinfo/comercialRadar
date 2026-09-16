/* ==== 97-seek-radar.js ==== */
/* 97-seek-radar.js — SEEK da A2L sobre o Comercial Radar
 *
 * A HOME DO SISTEMA desde 13/09/2026. A tela de extração mudou para /extrair.
 * O eixo continua sendo a LIGAÇÃO do cadastro; cada fonte é hipótese sobre ela.
 *
 * TRÊS TIPOS DE VEREDITO, E SÓ UM É OFICIAL (decisão do dono do produto):
 *   fonte   cada uma diz "achou" ou "não achou" registro para a ligação;
 *   IA      aprovado, revisão humana ou reprovado — uma fonte como as outras,
 *           filtrável como as outras;
 *   pessoa  a decisão OFICIAL, gravada à parte em `seek_decisao`. Ela nunca
 *           altera o veredito da IA nem o das fontes, e pode ser aplicada a um
 *           caso ou a todos os filtrados de uma vez.
 *
 * DUAS CHAMADAS: a fila leve (/api/seek/fila, uma linha por ligação, tudo o que
 * filtra) chega inteira ao abrir; a ficha (/api/seek/caso) só quando alguém
 * escolhe a ligação. Ordens de serviço e impacto financeiro não existem no
 * Comercial Radar — a tela mostra "sem dado", e não número inventado.
 *
 * A SESSÃO É A DO SISTEMA: `sessao.js` faz o login, põe o token em toda
 * chamada a /api/ e avisa com o evento `cr:sessao` quando o crachá chegou.
 */
var SEEK = (function () {
  'use strict';

  /* ===================================================================
   * 1 · CONTRATO — grupos, fontes, vereditos e ações
   * =================================================================== */
  var GRUPOS = [
    {id: 'ia', rot: 'Julgamento da IA', cor: '#6D4AC7',
     diz: 'o parecer da IA sobre os registros e as imagens — uma fonte como as outras'},
    {id: 'documento', rot: 'Documento', cor: '#0B2E59',
     diz: 'registro com CNPJ vinculado ao endereço da ligação'},
    {id: 'plataforma', rot: 'Plataformas', cor: '#006DFF',
     diz: 'estabelecimento publicado em diretório ou aplicativo'},
    {id: 'oficial', rot: 'Bases públicas', cor: '#1F9D62',
     diz: 'endereço declarado em base censitária ou estadual'},
    /* WEB E IMAGEM SEPARADOS, e verdes só quando CONFIRMAM (dono do produto,
     * 13/09/2026): coletar a foto ou achar página no endereço não é prova. */
    {id: 'web', rot: 'Web', cor: '#F28C28',
     diz: 'resultado da busca pelo endereço que a IA usou para confirmar um registro'},
    {id: 'imagem', rot: 'Imagem', cor: '#138A9E',
     diz: 'foto de rua ou publicada em que a IA identificou o comércio ou serviço'}
  ];
  /* os grupos cujo "achou" quer dizer "confirmou" */
  var PROVA = {web: 1, imagem: 1};

  /* os centros são as dez áreas do motor, uma por fonte */
  var FONTES = [
    {id: 'ia', grupo: 'ia', curto: 'IA', interno: 'IA · julgamento',
     cliente: 'ANÁLISE AUTOMÁTICA', centro: [0.00, 0.26, -0.36]},
    {id: 'receita', grupo: 'documento', curto: 'FISCAL', interno: 'Receita Federal',
     cliente: 'CADASTRO FISCAL', centro: [-0.42, 0.20, 0.54]},
    {id: 'cadastur', grupo: 'documento', curto: 'TURISMO', interno: 'Cadastur',
     cliente: 'CADASTRO DE TURISMO', centro: [-0.36, -0.04, 0.70]},
    {id: 'maps', grupo: 'plataforma', curto: 'DIRETÓRIO', interno: 'Google Maps',
     cliente: 'DIRETÓRIO DE ESTABELECIMENTOS', centro: [0.38, -0.04, 0.68]},
    {id: 'ifood', grupo: 'plataforma', curto: 'DELIVERY', interno: 'iFood',
     cliente: 'PLATAFORMA DE DELIVERY', centro: [0.42, 0.18, 0.52]},
    {id: 'airbnb', grupo: 'plataforma', curto: 'HOSPEDAGEM', interno: 'Airbnb',
     cliente: 'PLATAFORMA DE HOSPEDAGEM', centro: [-0.26, 0.30, 0.02]},
    {id: 'ibge', grupo: 'oficial', curto: 'CENSO', interno: 'IBGE · CNEFE',
     cliente: 'BASE CENSITÁRIA', centro: [0.00, 0.08, -0.74]},
    {id: 'estadual', grupo: 'oficial', curto: 'ESTADUAL', interno: 'Base estadual',
     cliente: 'CADASTRO ESTADUAL', centro: [-0.34, -0.30, 0.20]},
    {id: 'busca', grupo: 'web', curto: 'WEB', interno: 'Busca web · DuckDuckGo e Yahoo',
     cliente: 'BUSCA NA WEB', centro: [0.00, -0.50, -0.48]},
    {id: 'foto', grupo: 'imagem', curto: 'IMAGEM', interno: 'Fotos · rua e publicadas',
     cliente: 'LEITURA DE IMAGEM', centro: [0.34, -0.30, 0.22]}
  ];
  function ehProva(f) { return !!PROVA[f.grupo]; }
  function achouTxt(f) { return ehProva(f) ? 'confirmou' : 'achou'; }
  var FONTES_DADO = FONTES.slice(1);   /* as que dizem achou/não achou */

  /* A CLASSE DO CASO (dono do produto, 16/09/2026): ramo do negócio e o que a IA viu nas imagens. Os ids são os
   * mesmos de `classificacao.py`, que grava tudo em `percepcao.classe` na hora do julgamento. */
  var SEGMENTOS = [
    {id: 'restaurante', rot: 'restaurante / padaria'}, {id: 'loja', rot: 'loja / varejo'},
    {id: 'beleza', rot: 'salão / barbearia'}, {id: 'oficina', rot: 'oficina / auto'},
    {id: 'supermercado', rot: 'supermercado / atacado'}, {id: 'industria', rot: 'indústria'},
    {id: 'supermat', rot: 'construção'}, {id: 'transporte', rot: 'transporte'},
    {id: 'servico', rot: 'serviço / escritório'}, {id: 'saude', rot: 'saúde / farmácia'},
    {id: 'escola', rot: 'escola / curso'}, {id: 'academia', rot: 'academia'},
    {id: 'posto', rot: 'posto'}, {id: 'hotel', rot: 'hotel / pousada'},
    {id: 'banca', rot: 'banca'}, {id: 'religioso', rot: 'igreja / associação'},
    {id: 'outros', rot: 'outros'}
  ];
  var VISUAL = [
    {id: 'fachada_rua', rot: 'fachada na rua'}, {id: 'letreiro', rot: 'letreiro / placa'},
    {id: 'vitrine', rot: 'vitrine / loja'}, {id: 'foto_google', rot: 'foto do Google'},
    {id: 'sem_imagem', rot: 'sem imagem'}
  ];

  var VEREDITOS = {
    aprovado:       {rot: 'aprovado', cor: '#1F9D62', tom: 'aprova'},
    revisao_humana: {rot: 'revisão humana', cor: '#F28C28', tom: 'atencao'},
    reprovado:      {rot: 'reprovado', cor: '#D64545', tom: 'nega'}
  };

  var ACOES = {
    aprovar:  {rot: 'aprovar', curto: 'aprovada', cor: '#14603D',
               diz: 'a evidência sustenta a cobrança como comercial'},
    campo:    {rot: 'mandar a campo', curto: 'campo', cor: '#006DFF',
               diz: 'só uma visita resolve'},
    revisar:  {rot: 'deixar em revisão', curto: 'revisão', cor: '#C96F16',
               diz: 'fica na fila, sem decidir agora'},
    rejeitar: {rot: 'rejeitar', curto: 'rejeitada', cor: '#D64545',
               diz: 'não há caso aqui — exige o motivo escrito'}
  };

  var DECISOES = [
    {id: 'todos', rot: 'todas'},
    {id: 'sem', rot: 'sem decisão oficial', cor: '#98A2B3'},
    {id: 'aprovar', rot: 'aprovadas', cor: ACOES.aprovar.cor},
    {id: 'campo', rot: 'mandadas a campo', cor: ACOES.campo.cor},
    {id: 'revisar', rot: 'em revisão', cor: ACOES.revisar.cor},
    {id: 'rejeitar', rot: 'rejeitadas', cor: ACOES.rejeitar.cor}
  ];

  /* PRIORIDADE BAIXA (15/09/2026): o julgamento põe no fim da fila a revisão humana
   * que só a Receita sustenta e em que nenhuma imagem mostra sinal */
  var TXT_PRIORIDADE = 'prioridade baixa: revisão só pela Receita e sem sinal nas imagens — vai para o fim da fila';

  /* quem pode gravar decisão — o servidor confere de novo (403) */
  var DECIDEM = ['editor', 'supervisor', 'admin', 'root'];
  var ALT_FI = 66;          /* altura fixa da linha da fila: é o que deixa virtualizar */
  var MAX_FICHAS = 150;     /* fichas guardadas em memória */
  var MAX_LOTE = 50000;     /* o mesmo teto do servidor */

  var CAMPOS_BASE = [
    ['ligacao', 'ligação', 1], ['titular', 'titular'], ['documento', 'documento', 1],
    ['telefone', 'telefone', 1], ['endereco', 'endereço'], ['logradouro', 'logradouro'],
    ['numero', 'número', 1], ['bairro', 'bairro'], ['cidade', 'cidade'], ['cep', 'CEP', 1],
    ['categoria', 'categoria'], ['subcategoria', 'subcategoria'], ['situacao', 'situação'],
    ['qualificacao', 'qualificação'], ['qualificacao_motivo', 'motivo da qualificação'],
    ['eco_res', 'economias residenciais', 1], ['eco_com', 'economias comerciais', 1],
    ['eco_ind', 'economias industriais', 1], ['eco_pub', 'economias públicas', 1],
    ['tipo_faturamento', 'tipo de faturamento'], ['medidor', 'hidrômetro', 1],
    ['lat', 'latitude', 1], ['lng', 'longitude', 1]
  ];

  var CAMPOS_REG = [
    ['razao_social', 'razão social'], ['cnpj', 'CNPJ'], ['situacao_cadastral', 'situação cadastral'],
    ['cnae', 'CNAE'], ['complemento', 'complemento'], ['abertura', 'abertura'],
    ['categoria', 'categoria'], ['endereco', 'endereço'], ['telefone', 'telefone'],
    ['site', 'site'], ['instagram', 'instagram'], ['metros', 'distância do hidrômetro'],
    ['mesmo_endereco', 'mesmo endereço'], ['mesmo_numero', 'mesmo número'],
    ['criterios_ok', 'critérios do vínculo'], ['confianca', 'confiança do vínculo'],
    ['origem', 'origem do vínculo'], ['aceito_por', 'aceito por'],
    ['avaliacao', 'nota no Maps'], ['horario', 'horário'], ['estado_ifood', 'estado no iFood']
  ];

  var VISADAS = {sv_frente: 'frente', sv_fundo: 'fundo', sv_lado_a: 'lado A', sv_lado_b: 'lado B',
                 /* a foto de rua tirada no hidrômetro (15/09/2026): sem registro com pin do Maps a até 60 m */
                 sv_hidrometro: 'hidrômetro'};

  /* A COR DE CADA FACHADA NA FOTO DE RUA (15/09/2026): o papel vem do código
   * (`fachada_da_seta`), e não da IA — a fachada da seta sai da posição da ponta. */
  var PAPEIS = {
    seta:             {rot: 'fachada da seta', cor: '#1F9D62'},
    divisa:           {rot: 'divisa indefinida', cor: '#E0A800'},
    vizinho_com_nome: {rot: 'vizinho com o nome em outra fonte', cor: '#F28C28'},
    vizinho_sem_nome: {rot: 'vizinho com placa ou sinal sem nome', cor: '#D64545'},
    sem_sinal:        {rot: 'sem sinal', cor: '#98A2B3'}
  };

  /* ===================================================================
   * 2 · ESTADO
   * =================================================================== */
  var S = {
    casos: [], porLig: {}, vistos: [], quals: [], gerado: null, erro: null,
    fichas: {}, ordemFichas: [], carregando: {},
    sel: null, abertos: {}, mudos: false, rotuloCliente: true, tira: true,
    busca: '', termo: '', fIA: 'todos', fDec: 'todos', fQual: 'todos', fFonte: {},
    /* os recortes de classe são multiescolha: lista de ids marcados */
    fSeg: [], fVis: [],
    imgs: [], pendente: null, chamado: null,
    /* A TRAVA (14/09/2026): quem está com cada ligação aberta, a aba desta tela e
     * o caso aberto aqui que outra pessoa segura */
    travas: {}, sessao: novoId(), minha: null, bloqueio: null, vivo: false
  };

  function novoId() {
    if (window.crypto && window.crypto.randomUUID) { return window.crypto.randomUUID(); }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (ch) {
      var r = Math.random() * 16 | 0;
      return (ch === 'x' ? r : (r & 3 | 8)).toString(16);
    });
  }

  function $(s) { return document.querySelector(s); }
  function esc(t) {
    return String(t === null || t === undefined ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fonte(id) {
    for (var i = 0; i < FONTES.length; i++) { if (FONTES[i].id === id) { return FONTES[i]; } }
    return null;
  }
  function grupo(id) {
    for (var i = 0; i < GRUPOS.length; i++) { if (GRUPOS[i].id === id) { return GRUPOS[i]; } }
    return GRUPOS[0];
  }
  function corFonte(f) { return grupo(f.grupo).cor; }
  function rotuloFonte(f) { return S.rotuloCliente ? f.cliente : f.interno; }
  function outroNome(f) { return S.rotuloCliente ? f.interno : f.cliente; }
  function caso(lig) { return S.porLig[lig] || null; }
  function ficha(lig) {
    var f = S.fichas[lig === undefined ? S.sel : lig];
    return f && !f.erro ? f : null;
  }
  function num(n) { return (+n || 0).toLocaleString('pt-BR'); }
  function veredito(v) {
    return VEREDITOS[v] || {rot: v ? String(v) : 'sem julgamento', cor: '#8FA0BA', tom: 'nulo'};
  }
  function podeDecidir() { return !!(window.EU && DECIDEM.indexOf(window.EU.nivel) >= 0); }
  /* O QUE É DE CASA (dono do produto, 16/09/2026): a checagem do código, o "como foi julgada" e a leitura da
   * foto de rua contam COMO a máquina decidiu — servem para auditar o processo, não para decidir o caso. Só
   * administrador e root veem. */
  function ehAdmin() { return !!(window.EU && ['admin', 'root'].indexOf(window.EU.nivel) >= 0); }
  /* a trava de OUTRA pessoa nesta ligação; a mesma pessoa em outra aba não bloqueia */
  function travaDeOutro(lig) {
    var t = S.travas[lig];
    return t && window.EU && t.quem !== window.EU.id ? t : null;
  }

  /* data do servidor → "11/09/26 21:50", no fuso de quem olha */
  function quando(iso) {
    if (!iso) { return ''; }
    var s = String(iso);
    if (/T\d{2}:\d{2}/.test(s) && /([zZ]|[+-]\d{2}:?\d{2})$/.test(s)) {
      var d = new Date(s);
      if (!isNaN(d.getTime())) {
        var p = function (n) { return (n < 10 ? '0' : '') + n; };
        return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + String(d.getFullYear()).slice(2)
          + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
      }
    }
    var m = /^(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2}))?/.exec(s);
    if (!m) { return s; }
    return (m[3] ? m[3] + '/' : '') + m[2] + '/' + m[1].slice(2) + (m[4] ? ' ' + m[4] + ':' + m[5] : '');
  }
  function agoraIso() {
    return new Date().toISOString();
  }
  function metrosTxt(m) {
    if (m === null || m === undefined || !isFinite(+m)) { return ''; }
    m = +m;
    return m >= 1000 ? (m / 1000).toFixed(1).replace('.', ',') + ' km' : Math.round(m) + ' m';
  }
  function valorTxt(v) {
    if (v === null || v === undefined || v === '') { return ''; }
    if (v === true) { return 'sim'; }
    if (v === false) { return 'não'; }
    if (Array.isArray(v)) { return v.join(', '); }
    if (typeof v === 'object') { return JSON.stringify(v); }
    return String(v);
  }

  function torrada(msg, ruim) {
    var d = document.createElement('div');
    d.className = 't' + (ruim ? ' ruim' : '');
    d.textContent = msg;
    $('#torradas').appendChild(d);
    window.setTimeout(function () { d.classList.add('vai'); }, ruim ? 6000 : 3400);
    window.setTimeout(function () { if (d.parentNode) { d.parentNode.removeChild(d); } }, ruim ? 6500 : 3900);
  }
  function chipApi(txt, estado) {
    $('#api-txt').textContent = txt;
    $('#chip-api').className = 'chip' + (estado ? ' ' + estado : '');
  }
  function lerJson(r) {
    return r.json().catch(function () { return {}; }).then(function (b) {
      if (!r.ok) {
        var e = new Error(b.erro || (typeof b.detail === 'string' ? b.detail : '') || ('HTTP ' + r.status));
        e.status = r.status;
        e.corpo = b;
        throw e;
      }
      return b;
    });
  }

  /* ===================================================================
   * 3 · A FILA — carga, filtros e a lista virtual
   * =================================================================== */
  function carregaFila() {
    chipApi('carregando a fila…', '');
    var t0 = Date.now();
    return fetch('/api/seek/fila').then(lerJson).then(function (d) {
      var ix = {};
      d.colunas.forEach(function (nome, i) { ix[nome] = i; });
      var quals = {};
      S.casos = d.linhas.map(function (l) {
        var c = {
          ligacao: String(l[ix.ligacao]), titular: l[ix.titular] || '', endereco: l[ix.endereco] || '',
          bairro: l[ix.bairro] || '', cidade: l[ix.cidade] || '', qualificacao: l[ix.qualificacao] || '',
          economias: l[ix.economias] || 0, ia: l[ix.ia], checagem: l[ix.checagem],
          avaliado_em: l[ix.avaliado_em], decisao: l[ix.decisao], decidido_em: l[ix.decidido_em],
          decidido_por: l[ix.decidido_por], prioridade: l[ix.prioridade] || null, f: {}
        };
        FONTES_DADO.forEach(function (f) { c.f[f.id] = !!l[ix['f_' + f.id]]; });
        c.seg = l[ix.segmentos] || [];
        c.vis = l[ix.visual] || [];
        c.txt = (c.ligacao + ' ' + c.titular + ' ' + c.endereco + ' ' + c.bairro).toLowerCase();
        if (c.qualificacao) { quals[c.qualificacao] = 1; }
        return c;
      });
      S.porLig = {};
      S.casos.forEach(function (c) { S.porLig[c.ligacao] = c; });
      S.quals = Object.keys(quals).sort();
      /* a hora é a da chegada aqui: `gerado_em` sai do contêiner em UTC, sem
       * fuso, e aparecia três horas adiantada */
      S.gerado = agoraIso();
      S.erro = null;
      chipApi(num(S.casos.length) + ' ligações · ' + ((Date.now() - t0) / 1000).toFixed(1) + ' s', 'on');
    }).catch(function (e) {
      S.erro = e.message;
      chipApi('a fila não carregou', 'ruim');
      torrada('A fila não carregou: ' + e.message, true);
    });
  }

  /* `sem` diz qual recorte ignorar: é assim que cada lista mostra quantas
   * ligações ela traria com os OUTROS recortes valendo */
  function passa(c, sem) {
    if (sem !== '#ia' && S.fIA !== 'todos' && c.ia !== S.fIA) { return false; }
    if (sem !== '#dec' && S.fDec !== 'todos' && (c.decisao || 'sem') !== S.fDec) { return false; }
    if (sem !== '#qual' && S.fQual !== 'todos' && c.qualificacao !== S.fQual) { return false; }
    for (var k in S.fFonte) {
      if (k === sem || !S.fFonte.hasOwnProperty(k)) { continue; }
      if (S.fFonte[k] === 'sim' && !c.f[k]) { return false; }
      if (S.fFonte[k] === 'nao' && c.f[k]) { return false; }
    }
    /* MULTIESCOLHA: nada marcado não recorta; marcadas somam (OU dentro do mesmo filtro) */
    if (sem !== '#seg' && S.fSeg.length && !S.fSeg.some(function (s) { return (c.seg || []).indexOf(s) >= 0; })) { return false; }
    if (sem !== '#vis' && S.fVis.length && !S.fVis.some(function (s) { return (c.vis || []).indexOf(s) >= 0; })) { return false; }
    if (sem !== '#texto' && S.termo && c.txt.indexOf(S.termo) < 0) { return false; }
    return true;
  }

  function filtra() {
    S.termo = S.busca.trim().toLowerCase();
    var r = {vistos: [], ia: {}, dec: {}, qual: {}, fs: {}, ft: {}, seg: {}, vis: {}, tIA: 0, tDec: 0, tQual: 0};
    FONTES_DADO.forEach(function (f) { r.fs[f.id] = 0; r.ft[f.id] = 0; });
    for (var i = 0; i < S.casos.length; i++) {
      var c = S.casos[i];
      if (passa(c, null)) { r.vistos.push(c); }
      if (passa(c, '#ia')) { r.ia[c.ia] = (r.ia[c.ia] || 0) + 1; r.tIA++; }
      if (passa(c, '#dec')) { var d = c.decisao || 'sem'; r.dec[d] = (r.dec[d] || 0) + 1; r.tDec++; }
      if (passa(c, '#qual')) { r.qual[c.qualificacao] = (r.qual[c.qualificacao] || 0) + 1; r.tQual++; }
      for (var j = 0; j < FONTES_DADO.length; j++) {
        var id = FONTES_DADO[j].id;
        if (passa(c, id)) { r.ft[id]++; if (c.f[id]) { r.fs[id]++; } }
      }
      if (passa(c, '#seg')) {
        (c.seg || []).forEach(function (s) { r.seg[s] = (r.seg[s] || 0) + 1; });
      }
      if (passa(c, '#vis')) {
        (c.vis || []).forEach(function (s) { r.vis[s] = (r.vis[s] || 0) + 1; });
      }
    }
    return r;
  }

  function seletor(id, rot, cor, valor, opcoes) {
    return '<span class="fl-rot">' + esc(rot) + '</span>'
      + '<span class="pt" style="background:' + (cor || '#8FA0BA') + '"></span>'
      + '<select id="' + id + '" class="fl-sel" aria-label="filtrar por ' + esc(rot) + '">'
      + opcoes.map(function (o) {
          return '<option value="' + esc(o.id) + '"' + (valor === o.id ? ' selected' : '') + '>'
            + esc(o.rot) + ' · ' + num(o.n) + '</option>';
        }).join('') + '</select>';
  }

  function pintaFila() {
    var r = filtra();
    S.vistos = r.vistos;

    var opIA = [{id: 'todos', rot: 'todos', n: r.tIA}];
    Object.keys(VEREDITOS).forEach(function (v) {
      opIA.push({id: v, rot: VEREDITOS[v].rot, n: r.ia[v] || 0});
    });
    $('#f-ia').innerHTML = seletor('f-sel-ia', 'IA', S.fIA === 'todos' ? null : VEREDITOS[S.fIA].cor,
                                   S.fIA, opIA);

    var opQ = [{id: 'todos', rot: 'todas', n: r.tQual}].concat(S.quals.map(function (q) {
      return {id: q, rot: q.replace(/_/g, ' ').toLowerCase(), n: r.qual[q] || 0};
    }));
    $('#f-qual').innerHTML = seletor('f-sel-qual', 'cadastro', null, S.fQual, opQ);

    var opD = DECISOES.map(function (d) {
      return {id: d.id, rot: d.rot, n: d.id === 'todos' ? r.tDec : (r.dec[d.id] || 0)};
    });
    var corD = (DECISOES.filter(function (d) { return d.id === S.fDec; })[0] || {}).cor;
    $('#f-decisao').innerHTML = seletor('f-sel-dec', 'decisão', corD, S.fDec, opD);

    $('#f-fontes').innerHTML = FONTES_DADO.map(function (f) {
      var e = S.fFonte[f.id] || 'todos';
      var n = e === 'nao' ? r.ft[f.id] - r.fs[f.id] : r.fs[f.id];
      var dica = rotuloFonte(f) + ' — ' + (ehProva(f)
                 ? (e === 'sim' ? 'só as que a IA CONFIRMOU por esta fonte'
                    : e === 'nao' ? 'só as que esta fonte NÃO confirmou' : 'sem recorte')
                 : (e === 'sim' ? 'só as que ACHARAM registro'
                    : e === 'nao' ? 'só as que NÃO acharam' : 'sem recorte'))
               + ' · clique para alternar (todas → achou → não achou)';
      return '<button class="fch" data-ff="' + f.id + '" data-estado="' + e + '" title="' + esc(dica) + '">'
        + '<i>' + (e === 'sim' ? '✓' : e === 'nao' ? '✕' : '·') + '</i>'
        + '<span>' + esc(f.curto) + '</span><b>' + num(n) + '</b></button>';
    }).join('');
    $('#f-limpa').hidden = !Object.keys(S.fFonte).length;

    function chips(lista, marcadas, conta, atrib, dica) {
      return lista.map(function (o) {
        var on = marcadas.indexOf(o.id) >= 0;
        return '<button class="fch' + (on ? ' on' : '') + '" data-' + atrib + '="' + o.id + '"'
          + ' title="' + esc(o.rot + ' — ' + dica) + '">'
          + '<i>' + (on ? '✓' : '·') + '</i><span>' + esc(o.rot) + '</span><b>' + num(conta[o.id] || 0) + '</b></button>';
      }).join('');
    }
    $('#f-segmentos').innerHTML = chips(SEGMENTOS, S.fSeg, r.seg, 'fseg',
      'clique para incluir este ramo no recorte; várias marcadas somam');
    $('#f-visual').innerHTML = chips(VISUAL, S.fVis, r.vis, 'fvis',
      'clique para incluir esta prova visual no recorte; várias marcadas somam');
    $('#f-limpa-seg').hidden = !S.fSeg.length;
    $('#f-limpa-vis').hidden = !S.fVis.length;

    var lote = $('#f-lote');
    lote.innerHTML = '<span>decidir as filtradas</span><b>' + num(S.vistos.length) + '</b>';
    lote.disabled = !S.vistos.length || !podeDecidir();
    lote.title = podeDecidir()
      ? 'grava a decisão oficial em todas as ' + num(S.vistos.length) + ' ligações do recorte'
      : 'decidir exige nível editor ou acima';

    pintaLista();
    $('#f-pe').innerHTML = '<span>' + num(S.vistos.length) + ' de ' + num(S.casos.length)
      + '</span>' + (S.gerado ? '<span title="hora em que a fila chegou">· ' + esc(quando(S.gerado).slice(-5))
      + '</span>' : '')
      + '<button data-recarrega="1" title="buscar a fila de novo no servidor">atualizar</button>';
  }

  function linhaFila(c) {
    var V = veredito(c.ia);
    var A = c.decisao ? ACOES[c.decisao] : null;
    var mini = FONTES_DADO.map(function (f) {
      return c.f[f.id] ? '<i class="on" style="--mc:' + corFonte(f) + '"></i>' : '<i></i>';
    }).join('');
    var achou = FONTES_DADO.filter(function (f) { return c.f[f.id]; })
      .map(function (f) { return f.curto; }).join(', ');
    /* EM ANÁLISE POR OUTRA PESSOA (14/09/2026): a linha fica cinza, diz com quem
     * está e não abre para decidir */
    var t = travaDeOutro(c.ligacao);
    if (t) {
      return '<button class="fi travada' + (S.sel === c.ligacao ? ' on' : '') + '" data-caso="'
        + esc(c.ligacao) + '" aria-disabled="true" title="' + esc('em análise por ' + (t.quem_nome || 'outro usuário')
        + ' — não abre para decidir enquanto a pessoa estiver nela') + '">'
        + '<span class="l1"><span class="pt" style="background:' + V.cor + '"></span><b class="mono">'
        + esc(c.ligacao) + '</b>'
        + (A ? '<span class="dchip" style="--dc:' + A.cor + '">' + esc(A.curto) + '</span>' : '')
        + '<span class="tchip">em análise</span></span>'
        + '<span class="l2">por ' + esc(t.quem_nome || 'outro usuário') + '</span>'
        + '<span class="l3"><span>' + esc(c.endereco) + (c.bairro ? ' · ' + esc(c.bairro) : '')
        + '</span><em>' + esc(c.economias) + ' ec.</em></span>'
        + '</button>';
    }
    return '<button class="fi' + (S.sel === c.ligacao ? ' on' : '') + '" data-caso="'
      + esc(c.ligacao) + '">'
      + '<span class="l1"><span class="pt" style="background:' + V.cor + '" title="IA: '
      + esc(V.rot) + '"></span><b class="mono">' + esc(c.ligacao) + '</b>'
      + (A ? '<span class="dchip" style="--dc:' + A.cor + '" title="decisão oficial: '
             + esc(A.rot) + (c.decidido_por ? ' · ' + esc(c.decidido_por) : '') + '">'
             + esc(A.curto) + '</span>' : '')
      + (c.prioridade === 'baixa' ? '<span class="pchip" title="' + esc(TXT_PRIORIDADE) + '">↓ baixa</span>' : '')
      + '<span class="mini" title="' + esc(achou ? 'acharam: ' + achou : 'nenhuma fonte achou') + '">'
      + mini + '</span></span>'
      + '<span class="l2">' + esc(c.titular || '—') + '</span>'
      + '<span class="l3"><span>' + esc(c.endereco) + (c.bairro ? ' · ' + esc(c.bairro) : '')
      + '</span><em>' + esc(c.economias) + ' ec.</em></span>'
      + '</button>';
  }

  var pedidoLista = 0;
  function pintaLista() {
    var lista = $('#f-lista'), vl = $('#f-vl'), jan = $('#f-jan'), vazio = $('#f-vazio');
    var n = S.vistos.length;
    if (!n) {
      vl.style.height = '0px'; jan.innerHTML = '';
      vazio.hidden = false;
      vazio.innerHTML = S.casos.length ? 'nenhuma ligação com este recorte'
        : S.erro ? 'A fila não carregou: ' + esc(S.erro) : 'carregando a fila…';
      return;
    }
    vazio.hidden = true;
    vl.style.height = (n * ALT_FI) + 'px';
    var topo = lista.scrollTop, alt = lista.clientHeight || 600;
    var i0 = Math.max(0, Math.floor(topo / ALT_FI) - 6);
    var i1 = Math.min(n, Math.ceil((topo + alt) / ALT_FI) + 6);
    var h = '';
    for (var i = i0; i < i1; i++) { h += linhaFila(S.vistos[i]); }
    jan.style.transform = 'translateY(' + (i0 * ALT_FI) + 'px)';
    jan.innerHTML = h;
  }
  function agendaLista() {
    if (pedidoLista) { return; }
    pedidoLista = window.requestAnimationFrame(function () { pedidoLista = 0; pintaLista(); });
  }
  function indiceNaFila(lig) {
    for (var i = 0; i < S.vistos.length; i++) { if (S.vistos[i].ligacao === lig) { return i; } }
    return -1;
  }
  function mostraNaFila(lig) {
    var i = indiceNaFila(lig), lista = $('#f-lista');
    if (i < 0) { return; }
    var y = i * ALT_FI;
    if (y < lista.scrollTop) { lista.scrollTop = y; }
    else if (y + ALT_FI > lista.scrollTop + lista.clientHeight) {
      lista.scrollTop = y + ALT_FI - lista.clientHeight;
    }
    pintaLista();
  }
  function refiltra() {
    $('#f-lista').scrollTop = 0;
    pintaFila();
  }

  /* ===================================================================
   * 4 · A FICHA — carregada no clique, guardada em memória
   * =================================================================== */
  function carregaFicha(lig, silenciosa) {
    if (S.fichas[lig] && !S.fichas[lig].erro) { return Promise.resolve(S.fichas[lig]); }
    if (S.carregando[lig]) { return S.carregando[lig]; }
    var p = fetch('/api/seek/caso/' + encodeURIComponent(lig)).then(lerJson).then(function (d) {
      S.fichas[lig] = d;
      S.ordemFichas.push(lig);
      while (S.ordemFichas.length > MAX_FICHAS) {
        var velha = S.ordemFichas.shift();
        if (velha !== S.sel) { delete S.fichas[velha]; }
      }
      return d;
    }).catch(function (e) {
      if (!silenciosa) { S.fichas[lig] = {erro: e.message}; }
      return null;
    }).then(function (d) {
      delete S.carregando[lig];
      if (S.sel === lig && !silenciosa) { pintaCaso(); }
      return d;
    });
    S.carregando[lig] = p;
    return p;
  }

  /* a próxima ligação do recorte que ninguém mais está analisando, a partir de i,
   * andando no sentido `passo` */
  function livreDesde(i, passo) {
    while (i >= 0 && i < S.vistos.length) {
      if (!travaDeOutro(S.vistos[i].ligacao)) { return i; }
      i += passo;
    }
    return -1;
  }

  function seleciona(lig, rolar) {
    if (!caso(lig)) { return false; }
    var t = travaDeOutro(lig);
    if (t && S.sel !== lig) {
      torrada('Ligação ' + lig + ' em análise por ' + (t.quem_nome || 'outro usuário') + ' — não abre para decidir', true);
      return false;
    }
    var mudou = S.sel !== lig;
    S.sel = lig;
    if (mudou) { S.bloqueio = null; pedeTrava(lig); }
    try { window.history.replaceState(null, '', '#' + lig); } catch (e) { /* sem histórico */ }
    if (rolar) { mostraNaFila(lig); } else { pintaLista(); }
    if (mudou) { $('#arvore').scrollTop = 0; }
    pintaCaso();
    if (CER.motor) { CER.motor.cobertura(coberturaDoCaso()); }
    carregaFicha(lig).then(function () {
      /* a próxima da fila já vem de carona: quem confere anda para baixo */
      var i = indiceNaFila(lig);
      if (i >= 0 && S.vistos[i + 1] && S.sel === lig) {
        carregaFicha(S.vistos[i + 1].ligacao, true);
      }
    });
    return true;
  }

  /* ===================================================================
   * 4a · A TRAVA E OS AVISOS EM TEMPO REAL (14/09/2026)
   *
   * Abrir a ligação pede a trava; na fila dos outros ela fica cinza, "em análise
   * por <nome>". A trava é renovada a cada 30 s (o servidor solta com 2 min sem
   * sinal), troca quando esta aba abre outra ligação e sai ao fechar a aba. Os
   * avisos chegam pelo /ws, só os da própria empresa; a lista das travas é pedida
   * de novo ao conectar, ao voltar para a aba e a cada 2 min — o aviso perdido
   * se corrige sozinho. A autoridade é o servidor: decidir travada volta 409.
   * =================================================================== */
  var filaTrava = Promise.resolve();

  function pedeTrava(lig) {
    if (!podeDecidir() || !lig) { return; }
    /* EM FILA, uma por vez: duas trocas rápidas fora de ordem deixariam a trava
     * na ligação anterior */
    filaTrava = filaTrava.then(function () {
      return fetch('/api/seek/trava', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ligacao: lig, sessao: S.sessao})
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          if (r.status === 409) {
            S.travas[lig] = {quem: b.quem, quem_nome: b.quem_nome, id_empresa: b.id_empresa};
            if (S.minha === lig) { S.minha = null; }
            if (S.sel === lig) {
              if (!S.bloqueio) {
                torrada('Ligação ' + lig + ' em análise por ' + (b.quem_nome || 'outro usuário')
                        + ': a decisão fica bloqueada até a pessoa sair dela', true);
              }
              S.bloqueio = {ligacao: lig, quem_nome: b.quem_nome};
              pintaVeredito();
            }
            agendaLista();
            return;
          }
          if (!r.ok || !b.trava) { return; }
          S.minha = lig;
          S.travas[lig] = {quem: window.EU.id, quem_nome: window.EU.nome || window.EU.email, sessao: S.sessao,
                           id_empresa: b.id_empresa};
          if (S.bloqueio && S.bloqueio.ligacao === lig) {
            S.bloqueio = null;
            if (S.sel === lig) { torrada('Ligação ' + lig + ' liberada: agora ela está com você'); pintaVeredito(); }
          }
        });
      }).catch(function () { /* sem rede: o próximo sinal tenta de novo */ });
    });
  }

  function liberaAba(saindo) {
    if (!podeDecidir()) { return; }
    try {
      fetch('/api/seek/trava/liberar', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, keepalive: !!saindo,
        body: JSON.stringify({sessao: S.sessao})
      }).catch(function () { /* a trava vence sozinha em 2 min */ });
    } catch (e) { /* idem */ }
    S.minha = null;
  }

  function carregaTravas() {
    return fetch('/api/seek/travas').then(lerJson).then(function (d) {
      var novas = {};
      (d.travas || []).forEach(function (t) { novas[t.ligacao] = t; });
      /* a minha trava continua minha até o servidor dizer o contrário */
      if (S.minha && !novas[S.minha] && S.sel === S.minha) {
        novas[S.minha] = {quem: window.EU && window.EU.id, quem_nome: window.EU && window.EU.nome, sessao: S.sessao};
      }
      S.travas = novas;
      var t = S.sel && travaDeOutro(S.sel);
      if (t) { S.bloqueio = {ligacao: S.sel, quem_nome: t.quem_nome}; }
      else if (S.bloqueio && S.sel) { pedeTrava(S.sel); }
      agendaLista();
      if (S.sel) { pintaVeredito(); }
    }).catch(function () { /* a próxima rodada tenta de novo */ });
  }

  var repinta = 0;
  function agendaFila() {
    if (repinta) { return; }
    repinta = window.setTimeout(function () {
      repinta = 0;
      pintaFila();
      if (S.sel) { pintaVeredito(); pintaLado(); }
    }, 250);
  }

  /* ESPALHADA NO TEMPO: depois de um lote grande todas as telas abertas da empresa
   * buscam a fila. Todas no mesmo segundo seriam centenas de montagens da fila de
   * uma vez no servidor (o lote acabou de limpar o cache); entre 3 e 15 s, a
   * primeira monta e as outras pegam o cache. */
  var recarga = 0;
  function recarregaFilaDepois() {
    if (recarga) { return; }
    recarga = window.setTimeout(function () {
      recarga = 0;
      carregaFila().then(function () { pintaFila(); if (S.sel && caso(S.sel)) { pintaCaso(); } });
    }, 3000 + Math.floor(Math.random() * 12000));
  }

  function aoAviso(ev) {
    var eu = window.EU && window.EU.id;
    if (ev.ev === 'assinado') { vivo(true); carregaTravas(); return; }
    if (ev.ev === 'ressincronizar') { carregaTravas(); return; }
    if (ev.ev === 'trava') {
      S.travas[ev.ligacao] = {quem: ev.quem, quem_nome: ev.quem_nome, sessao: ev.sessao, id_empresa: ev.id_empresa};
      if (ev.ligacao === S.sel && ev.quem !== eu) {
        S.bloqueio = {ligacao: ev.ligacao, quem_nome: ev.quem_nome};
        if (S.minha === ev.ligacao) { S.minha = null; }
        torrada('Ligação ' + ev.ligacao + ' passou a estar em análise por ' + (ev.quem_nome || 'outro usuário'), true);
        pintaVeredito();
      }
      agendaLista();
      return;
    }
    if (ev.ev === 'liberada') {
      (ev.ligacoes || []).forEach(function (l) {
        var t = S.travas[l];
        /* só solta se a trava ainda for de quem soltou: a ligação pode já ter novo dono */
        if (t && (!ev.quem || t.quem === ev.quem) && !(t.quem === eu && t.sessao === S.sessao && S.minha === l)) {
          delete S.travas[l];
        }
        if (l === S.sel && S.bloqueio && S.bloqueio.ligacao === l && !travaDeOutro(l)) {
          /* quem decide assume a ligação que acabou de soltar; quem só consulta
           * apenas deixa de ver o aviso */
          if (podeDecidir()) { pedeTrava(l); } else { S.bloqueio = null; }
          pintaVeredito();
        }
      });
      agendaLista();
      return;
    }
    if (ev.ev === 'decisao') {
      (ev.ligacoes || []).forEach(function (l) {
        var c = S.porLig[l];
        if (c) { c.decisao = ev.acao; c.decidido_em = ev.em; c.decidido_por = ev.quem_nome; }
        var fi = S.fichas[l];
        if (fi && !fi.erro) {
          var topo = (fi.decisoes || [])[0];
          if (topo && topo.local && topo.acao === ev.acao && ev.quem === eu) {
            topo.em = ev.em || topo.em;
            delete topo.local;
          } else {
            var d = {acao: ev.acao, motivo: ev.motivo || null, quem: ev.quem_nome, em: ev.em, lote: ev.lote};
            fi.decisoes = [d].concat(fi.decisoes || []);
            fi.decisao = d;
          }
        }
      });
      agendaFila();
      return;
    }
    if (ev.ev === 'decisao_lote') {
      if (ev.quem !== eu) { torrada((ev.quem_nome || 'Alguém') + ' decidiu ' + num(ev.n) + ' ligações em lote: atualizando a fila'); }
      recarregaFilaDepois();
    }
  }

  function vivo(on) {
    S.vivo = on;
    var chip = $('#chip-vivo');
    if (!chip) { return; }
    chip.className = 'chip' + (on ? ' on' : ' ruim');
    $('#vivo-txt').textContent = on ? 'tempo real' : 'tempo real caiu';
    chip.title = on ? 'recebendo travas e decisões das outras telas da empresa'
      : 'sem aviso em tempo real: a fila se corrige a cada 2 min e a decisão continua protegida pelo servidor';
  }

  var WS = {sock: null, espera: 2000, ping: 0, volta: 0};
  function ligaTempoReal() {
    if (WS.sock || !window.EU) { return; }
    function abre() {
      var tok = '';
      try { tok = window.sessionStorage.getItem('cr_token') || ''; } catch (e) { tok = ''; }
      if (!tok) { return; }
      var proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
      var ws;
      try { ws = new WebSocket(proto + window.location.host + '/ws?token=' + encodeURIComponent(tok)); }
      catch (e) { agendaVolta(); return; }
      WS.sock = ws;
      ws.onopen = function () {
        WS.espera = 2000;
        ws.send(JSON.stringify({assina: 'seek'}));
        window.clearInterval(WS.ping);
        /* sinal de vida: proxies derrubam WebSocket calado */
        WS.ping = window.setInterval(function () { try { ws.send('ping'); } catch (e) { /* caiu */ } }, 25000);
      };
      ws.onmessage = function (m) {
        var ev;
        try { ev = JSON.parse(m.data); } catch (e) { return; }
        if (ev && ev.tipo === 'seek') { aoAviso(ev); }
      };
      ws.onclose = function () {
        window.clearInterval(WS.ping);
        if (WS.sock === ws) { WS.sock = null; }
        vivo(false);
        agendaVolta();
      };
    }
    /* token vencendo: renova antes, senão o handshake sai com o token morto */
    if (window.crVencendo && window.crVencendo() && window.crRenovar) { window.crRenovar().then(abre); }
    else { abre(); }
  }
  function agendaVolta() {
    window.clearTimeout(WS.volta);
    WS.volta = window.setTimeout(ligaTempoReal, WS.espera);
    WS.espera = Math.min(WS.espera * 2, 30000);
  }

  function pintaCaso() { pintaFaixa(); pintaArvore(); pintaVeredito(); pintaLado(); }

  /* ===================================================================
   * 5 · FAIXA DO CASO
   * =================================================================== */
  function coordenada(fi) {
    var b = fi && fi.base;
    if (!b || !isFinite(+b.lat) || !isFinite(+b.lng) || b.lat === null || b.lng === null) { return null; }
    return {lat: +b.lat, lon: +b.lng, origem: 'hidrômetro no cadastro da concessionária'};
  }

  function blocoCoordenada(fi) {
    var k = coordenada(fi);
    if (!k) {
      return '<div class="cf vazio"><i>coordenada</i><b>'
        + (fi ? 'sem coordenada no cadastro' : '…') + '</b></div>';
    }
    var par = k.lat.toFixed(6) + ', ' + k.lon.toFixed(6);
    var rota = 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(k.lat + ',' + k.lon);
    var rua = 'https://www.google.com/maps/@?api=1&map_action=pano&viewpoint='
      + encodeURIComponent(k.lat + ',' + k.lon);
    return '<div class="cf coord"><i>coordenada</i>'
      + '<b class="mono" tabindex="0" data-copia="' + esc(par) + '" data-copia-rot="coordenada" title="' + esc(par)
      + '&#10;clique para copiar">' + esc(par) + '</b>'
      + '<span class="prec" title="' + esc(k.origem) + '">cadastro</span>'
      + '<a class="ir" href="' + rota + '" target="_blank" rel="noopener noreferrer"'
      + ' title="abrir rota até esta ligação no mapa">rota ↗</a>'
      + '<a class="ir" href="' + rua + '" target="_blank" rel="noopener noreferrer"'
      + ' title="abrir a vista de rua neste ponto">rua ↗</a>'
      + '<button class="ir copia" data-copia="' + esc(par) + '" data-copia-rot="coordenada"'
      + ' title="copiar a coordenada">copiar</button>'
      + '</div>';
  }

  function economiasTxt(c, fi) {
    var b = fi && fi.base;
    if (!b) { return String(c.economias); }
    var partes = [];
    if (+b.eco_res) { partes.push(b.eco_res + ' res'); }
    if (+b.eco_com) { partes.push(b.eco_com + ' com'); }
    if (+b.eco_ind) { partes.push(b.eco_ind + ' ind'); }
    if (+b.eco_pub) { partes.push(b.eco_pub + ' púb'); }
    return b.economias + (partes.length ? ' (' + partes.join(' · ') + ')' : '');
  }

  function pintaFaixa() {
    var c = caso(S.sel);
    if (!c) { $('#faixa').innerHTML = ''; return; }
    var fi = ficha();
    var doc = fi && fi.base ? fi.base.documento : '';
    /* O VALOR INTEIRO NO PASSAR DO MOUSE E COPIADO NO CLIQUE (13/09/2026): o
     * campo corta com reticências quando não cabe, e o valor é o que se cola
     * em outro sistema. */
    function cf(rot, val, mono, vazio) {
      var v = val === null || val === undefined ? '' : String(val);
      return '<div class="cf' + (vazio ? ' vazio' : '') + '"><i>' + esc(rot) + '</i>'
        + '<b' + (mono ? ' class="mono"' : '')
        + (v ? ' tabindex="0" data-copia="' + esc(v) + '" data-copia-rot="' + esc(rot) + '" title="' + esc(v)
               + '&#10;clique para copiar"' : '')
        + '>' + esc(v || '—') + '</b></div>';
    }
    $('#faixa').innerHTML = cf('ligação', c.ligacao, true)
      + cf('titular', c.titular)
      + cf('endereço', c.endereco + (c.bairro ? ' · ' + c.bairro : ''))
      + blocoCoordenada(fi)
      + cf('economias', economiasTxt(c, fi), true)
      + cf('cadastro', (c.qualificacao || '').replace(/_/g, ' '))
      + cf('documento', doc, true, !doc);
  }

  /* ===================================================================
   * 6 · A ÁRVORE — grupo · fonte · registro
   * =================================================================== */
  function aberto(lig, chave, padrao) {
    var m = S.abertos[lig] || {};
    return m[chave] === undefined ? padrao : m[chave];
  }
  function seta(ab) { return '<span class="seta">' + (ab ? '▾' : '▸') + '</span>'; }

  /* o que a IA e a checagem disseram de cada POI, para marcar os registros */
  function contexto(fi) {
    var ia = fi.ia || {}, ch = ia.checagem || {};
    var ctx = {ader: {}, nao: {}, rem: {}, val: {}, nome: {}};
    function poi(a) { return typeof a === 'number' || typeof a === 'string' ? {poi: a} : (a || {}); }
    (ia.aderentes || []).forEach(function (a) { a = poi(a); if (a.poi !== undefined) { ctx.ader[a.poi] = a; } });
    (ia.nao_combinam || []).forEach(function (a) { a = poi(a); if (a.poi !== undefined) { ctx.nao[a.poi] = a; } });
    (ch.removidos || []).forEach(function (a) { a = poi(a); if (a.poi !== undefined) { ctx.rem[a.poi] = a.porque || ''; } });
    (ch.validos || []).forEach(function (p) { ctx.val[p] = 1; });
    Object.keys(fi.fontes || {}).forEach(function (k) {
      ((fi.fontes[k] || {}).registros || []).forEach(function (r) { ctx.nome[r.poi_id] = r.nome; });
    });
    return ctx;
  }

  function achouNa(c, fi, id) {
    var d = fi && fi.fontes && fi.fontes[id];
    return d ? !!d.achou : !!(c && c.f[id]);
  }

  function kv(pares) {
    var h = pares.filter(function (p) { return p[1] !== '' && p[1] !== null && p[1] !== undefined; })
      .map(function (p) {
        return '<div><i>' + esc(p[0]) + '</i><b' + (p[2] ? ' class="mono"' : '') + '>'
          + esc(p[1]) + '</b></div>';
      }).join('');
    return h ? '<div class="kv">' + h + '</div>' : '';
  }

  function ramoBase(c, fi) {
    var chave = 'c:base', ab = aberto(c.ligacao, chave, false), b = fi.base || {};
    var cab = '<button class="no-crit base" data-abre="' + chave + '" data-aberto="' + (ab ? '1' : '0')
      + '" style="--cc:#5B6274">' + seta(ab)
      + '<span class="nm">Cadastro</span>'
      + '<span class="res">' + esc(b.categoria || '') + (b.situacao ? ' · ' + esc(b.situacao) : '') + '</span>'
      + '<span class="cresce"></span>'
      + '<span class="regra">a linha da concessionária: toda fonte é hipótese sobre ela</span></button>';
    if (!ab) { return '<section class="ramo n-base">' + cab + '</section>'; }
    return '<section class="ramo n-base">' + cab + '<div class="ramo-corpo"><div class="no-corpo base-corpo">'
      + '<div class="kv-caixa">' + kv(CAMPOS_BASE.map(function (x) {
          return [x[1], valorTxt(b[x[0]]), x[2]];
        })) + '</div></div></div></section>';
  }

  function linhasPoi(lista, ctx, limite) {
    return lista.slice(0, limite || lista.length).map(function (a) {
      var id = a.poi !== undefined ? a.poi : a;
      return '<div class="ia-lin"><b>#' + esc(id) + '</b><span>'
        + (ctx.nome[id] ? '<em>' + esc(ctx.nome[id]) + '</em>' + (a.por || a.porque ? ' — ' : '') : '')
        + esc(a.por || a.porque || '') + '</span></div>';
    }).join('');
  }

  function ramoIA(c, fi, ctx) {
    var g = grupo('ia'), chave = 'g:ia', ab = aberto(c.ligacao, chave, true);
    var ia = fi.ia, V = veredito(ia ? ia.veredito : c.ia);
    var ch = ia && ia.checagem;
    var mudou = !!(ch && ch.veredito_ia && ia && ch.veredito_ia !== ia.veredito);
    var cab = '<button class="no-crit' + (ia && ia.veredito === 'aprovado' ? ' tem' : '') + '" data-abre="'
      + chave + '" data-aberto="' + (ab ? '1' : '0') + '" style="--cc:' + g.cor + '">' + seta(ab)
      + '<span class="nm">' + esc(g.rot) + '</span>'
      + '<span class="res" style="color:' + V.cor + '">' + esc(V.rot)
      + (mudou ? ' · mudado pela checagem' : '') + '</span>'
      + '<span class="cresce"></span><span class="regra">' + esc(g.diz) + '</span></button>';
    if (!ab) { return '<section class="ramo n-ia">' + cab + '</section>'; }
    if (!ia) {
      return '<section class="ramo n-ia">' + cab + '<div class="ramo-corpo"><div class="no-corpo">'
        + '<div class="sem-dado">A IA ainda não julgou esta ligação.</div></div></div></section>';
    }
    var nao = ia.nao_combinam || [], rem = (ch && ch.removidos) || [], ader = ia.aderentes || [];
    var todosNao = aberto(c.ligacao, 'ia:nao', false);
    var corpo = '<div class="ia-txt">' + esc(ia.motivo || ia.justificativa || 'A IA não deixou motivo.') + '</div>'
      + (mudou ? '<div class="ia-aviso"><b>checagem do código</b>A IA disse <b>'
                 + esc(veredito(ch.veredito_ia).rot) + '</b>; a checagem mudou para <b>' + esc(V.rot) + '</b>'
                 + (ch.porque ? ': ' + esc(ch.porque) : '.') + '</div>' : '')
      + ((fi.avisos || []).length ? '<div class="ia-aviso"><b>parte da ficha não carregou</b>'
                                    + esc(fi.avisos.join(' · ')) + '</div>' : '')
      + oQueAIADisse(ia)
      + '<div class="grupo-cab">aderentes para a IA · ' + ader.length + '</div>'
      + (ader.length ? '<div class="ia-lista">' + linhasAderentes(ader, ctx) + '</div>'
                     : '<div class="sem-dado">A IA não apontou nenhum registro aderente.</div>')
      + (rem.length ? '<div class="grupo-cab">tirados pela checagem do código · ' + rem.length + '</div>'
                      + '<div class="ia-lista">' + linhasPoi(rem, ctx) + '</div>' : '')
      + (nao.length ? '<div class="grupo-cab">não combinam · ' + nao.length + '</div>'
                      + '<div class="ia-lista">' + linhasPoi(nao, ctx, todosNao ? 0 : 6) + '</div>'
                      + (nao.length > 6 ? '<button class="mais" data-abre="ia:nao" data-aberto="'
                          + (todosNao ? '1' : '0') + '">' + (todosNao ? 'mostrar só os 6 primeiros'
                          : 'mostrar os ' + nao.length) + '</button>' : '') : '')
      + checagemCompleta(ch, ctx)
      + (ehAdmin()
         ? '<div class="grupo-cab">como foi julgada</div><div class="kv-caixa">'
           + kv([['julgada em', quando(ia.avaliado_em)], ['modelo', ia.modelo || '', 1],
                 ['processo', ia.processo || ''],
                 ['prioridade na fila', ia.prioridade === 'baixa' ? TXT_PRIORIDADE : ia.prioridade || ''],
                 ['fotos que a IA viu', (ia.fotos_vistas || []).length
                    ? (ia.fotos_vistas || []).join(' · ') : 'nenhuma']])
           + (ia.tem_dados ? '<button class="lk ia-dados-lk" data-dados-ia="' + esc(c.ligacao) + '">'
                             + 'ver o texto que a IA recebeu</button>' : '')
           + '</div>'
         : '');
    return '<section class="ramo n-ia">' + cab + '<div class="ramo-corpo"><div class="no-corpo">'
      + corpo + '</div></div></section>';
  }

  /* O QUE A IA DISSE ALÉM DO MOTIVO (julgamento leve, 15/09/2026): o uso, o estado
   * do imóvel, o sinal concreto nas imagens, os comentários e as fontes que citou. */
  function oQueAIADisse(ia) {
    var uso = ia.uso || {}, im = ia.imovel || {}, co = ia.comentarios || {};
    var ESTADO = {em_uso: 'em uso', abandonado: 'abandonado ou sem uso', nao_visto: 'não dá para ver'};
    var pares = [
      ['uso não residencial', ia.uso ? (uso.nao_residencial === true ? 'sim' : uso.nao_residencial === false ? 'não' : '—')
                                        + (uso.o_que ? ' · ' + uso.o_que : '') : ''],
      ['estado do imóvel', ia.imovel ? (ESTADO[im.estado] || im.estado || '—') + (im.por ? ' · ' + im.por : '') : ''],
      ['sinal nas imagens', ia.sinal || ''],
      ['comentários de clientes', ia.comentarios ? (co.confirmam === true ? 'confirmam' : 'não confirmam')
                                                   + (co.por ? ' · ' + co.por : '') : ''],
      ['fontes que a IA citou', (ia.fontes || []).length ? ia.fontes.join(' · ') : '']
    ];
    var h = kv(pares);
    return h ? '<div class="grupo-cab">o que a IA disse</div><div class="kv-caixa">' + h + '</div>' : '';
  }

  /* os aderentes com o que a IA disse de cada um: confirmado, número, prova recente e fontes */
  function linhasAderentes(lista, ctx) {
    var NUM = {igual: ['ok', 'mesmo número'], diferente: ['nao', 'número diferente'], nao_visto: ['cinza', 'número não visto']};
    return lista.map(function (a) {
      a = typeof a === 'number' || typeof a === 'string' ? {poi: a} : (a || {});
      var selos = [];
      if (a.confirmado === true) { selos.push(['ok', 'confirmado']); }
      else if (a.confirmado === false) { selos.push(['cinza', 'não confirmado']); }
      if (a.numero) { selos.push(NUM[a.numero] || ['cinza', 'número ' + a.numero]); }
      (a.fontes || []).forEach(function (f) { selos.push(['', f]); });
      return '<div class="ia-lin"><b>#' + esc(a.poi) + '</b><span>'
        + (ctx.nome[a.poi] ? '<em>' + esc(ctx.nome[a.poi]) + '</em>' + (a.por ? ' — ' : '') : '')
        + esc(a.por || '')
        + (selos.length ? '<span class="ia-chips">' + selos.map(function (s) {
            return '<span class="selo ' + s[0] + '">' + esc(s[1]) + '</span>';
          }).join('') + '</span>' : '')
        + (a.prova_recente ? '<span class="ia-prova"><i>prova recente</i>' + esc(a.prova_recente) + '</span>' : '')
        + '</span></div>';
    }).join('');
  }

  /* A CHECAGEM DO CÓDIGO INTEIRA: a regra, o porquê, a ficha do Maps que não conta
   * como fonte e as redes sociais que o código confirmou */
  function checagemCompleta(ch, ctx) {
    if (!ch || !ehAdmin()) { return ''; }
    var redes = ch.redes_sociais || [];
    return '<div class="grupo-cab">checagem do código</div><div class="kv-caixa">'
      + kv([['regra', ch.regra || ''],
            ['veredito', ch.veredito_ia ? veredito(ch.veredito_ia).rot + ' → ' + veredito(ch.veredito).rot : ''],
            ['por quê', ch.porque || (ch.veredito ? 'a checagem manteve o veredito' : '')],
            ['ficha do Google Maps', ch.ficha_do_maps || ''],
            ['contraprova', ch.contraprova || ''],
            ['registros válidos', (ch.validos || []).length ? ch.validos.map(function (p) {
              return '#' + p + (ctx.nome[p] ? ' ' + ctx.nome[p] : '');
            }).join(' · ') : ''],
            ['conferida em', quando(ch.em)]])
      + (redes.length ? '<div class="reg-bloco"><i>redes sociais confirmadas pelo código · ' + redes.length + '</i>'
                        + redes.map(linhaRede).join('') + '</div>' : '')
      + '</div>';
  }

  function linhaRede(x) {
    var link = linkSeguro(x.url);
    return '<div class="reg-coment"><i>' + esc(x.rede || 'rede social') + ' · #' + esc(x.poi)
      + ' · ' + esc(x.data ? quando(x.data) : 'sem data do post') + '</i>'
      + (link ? '<a class="lk" href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">abrir ↗</a> ' : '')
      + esc(x.texto || '') + '</div>';
  }

  /* O TEXTO INTEGRAL QUE A IA RECEBEU, pedido só no clique (3 a 12 mil caracteres) */
  function abreDadosIA(lig) {
    $('#modal-tit').textContent = 'O texto que a IA recebeu · ligação ' + lig;
    $('#modal').querySelector('.modal-caixa').classList.add('largo');
    $('#modal-corpo').innerHTML = '<div class="sem-dado">carregando…</div>';
    $('#modal-pe').innerHTML = '<button class="bt p" data-fecha="1">fechar</button>';
    $('#modal').hidden = false;
    fetch('/api/seek/caso/' + encodeURIComponent(lig) + '/dados').then(lerJson).then(function (d) {
      if ($('#modal').hidden) { return; }
      $('#modal-corpo').innerHTML = kv([['processo', d.processo || ''], ['modelo', d.modelo || '', 1],
                                        ['julgada em', quando(d.avaliado_em)]])
        + '<div class="reg-bloco"><i>imagens, na ordem em que foram à IA</i>'
        + ((d.fotos || []).length ? d.fotos.map(function (f, i) {
            return '<div class="reg-coment"><i>' + (i + 1) + '.</i>' + esc(f) + '</div>';
          }).join('') : '<div class="reg-coment">nenhuma</div>') + '</div>'
        + '<div class="reg-bloco"><i>texto</i><pre class="ia-dados">' + esc(d.dados || '(vazio)') + '</pre></div>';
    }).catch(function (e) {
      $('#modal-corpo').innerHTML = '<div class="sem-dado">Não carregou: ' + esc(e.message) + '</div>';
    });
  }

  function cartaoRegistro(r, ctx) {
    var selos = [];
    if (r.descartado_em) {
      selos.push(['cinza', 'vínculo descartado' + (r.descartado_motivo ? ': ' + r.descartado_motivo : '')]);
    }
    if (ctx.rem[r.poi_id] !== undefined) { selos.push(['nao', 'tirado pela checagem']); }
    if (ctx.ader[r.poi_id]) { selos.push(['ok', 'aderente para a IA']); }
    else if (ctx.nao[r.poi_id]) { selos.push(['alerta', 'não combina para a IA']); }
    var por = ctx.rem[r.poi_id] ? ['checagem', ctx.rem[r.poi_id]]
            : ctx.ader[r.poi_id] && ctx.ader[r.poi_id].por ? ['IA', ctx.ader[r.poi_id].por]
            : ctx.nao[r.poi_id] && ctx.nao[r.poi_id].por ? ['IA', ctx.nao[r.poi_id].por] : null;
    var valores = CAMPOS_REG.map(function (x) {
      var v = r[x[0]];
      if (x[0] === 'metros') { v = metrosTxt(v); }
      else if (x[0] === 'avaliacao' && v !== null && v !== undefined) {
        v = String(v).replace('.', ',') + (r.total_avaliacoes ? ' (' + num(r.total_avaliacoes) + ' avaliações)' : '');
      } else if (x[0] === 'confianca' && v !== null && v !== undefined) {
        v = (+v).toFixed(2).replace('.', ',');
      } else { v = valorTxt(v); }
      return [x[1], v, x[0] === 'cnpj' || x[0] === 'cnae' || x[0] === 'telefone'];
    });
    return '<div class="reg' + (r.descartado_em ? ' descartado' : '') + '">'
      + '<div class="reg-cab"><b>' + esc(r.nome || r.razao_social || '(sem nome)') + '</b>'
      + '<span class="poi">POI #' + esc(r.poi_id) + '</span>'
      + selos.map(function (s) { return '<span class="selo ' + s[0] + '">' + esc(s[1]) + '</span>'; }).join('')
      + '</div>'
      + (por ? '<div class="reg-por"><b>' + esc(por[0]) + '</b>' + esc(por[1]) + '</div>' : '')
      + kv(valores) + fichasDoRegistro(r) + comentariosDoRegistro(r) + '</div>';
  }

  /* A FICHA DO CNPJ NO SERASA (15/09/2026): o texto que a IA leu e a miniatura do
   * print, que abre no visor. É a Receita republicada — a mesma fonte do CNPJ. */
  function fichasDoRegistro(r) {
    return (r.fichas_web || []).map(function (f) {
      var mini = f.print && !r.descartado_em
        ? '<button class="reg-mini" data-img="fc' + esc(f.id) + '" title="abrir o print da ficha">'
          + '<img loading="lazy" src="' + esc(window.comToken(f.print)) + '" alt="print da ficha do CNPJ no Serasa"></button>'
        : '';
      return '<div class="reg-bloco"><i>ficha do CNPJ no ' + esc(f.fonte === 'serasa' ? 'Serasa' : f.fonte || 'web')
        + (f.situacao ? ' · ' + esc(f.situacao) : '') + (f.abertura ? ' · aberta em ' + esc(quando(f.abertura)) : '')
        + (f.consultado_em ? ' · consultada em ' + esc(quando(f.consultado_em)) : '') + '</i>'
        + '<div class="reg-ficha' + (mini ? '' : ' sem-mini') + '">' + mini
        + (f.texto_ia ? '<pre class="busca-txt">' + esc(f.texto_ia) + '</pre>' : '<div class="reg-por">sem texto lido</div>')
        + '</div></div>';
    }).join('');
  }

  /* os comentários de cliente que a IA recebeu: os 3 mais recentes, de até 2 anos */
  function comentariosDoRegistro(r) {
    var cs = r.comentarios_recentes || [];
    if (!cs.length) { return ''; }
    return '<div class="reg-bloco"><i>comentários recentes de clientes · até 2 anos · o que a IA leu</i>'
      + cs.map(function (s) {
          var k = String(s).indexOf(': ');
          return '<div class="reg-coment">' + (k > 0 ? '<i>' + esc(String(s).slice(0, k)) + '</i>' + esc(String(s).slice(k + 2))
                                                      : esc(s)) + '</div>';
        }).join('') + '</div>';
  }

  /* A BUSCA WEB, RESUMIDA NO QUE CONFIRMOU (13/09/2026). DuckDuckGo, Yahoo e o
   * Google da reserva buscam pelo endereço; a IA lê os resultados que citam a
   * rua e o número e diz quais CONFIRMAM um registro. Só esses aparecem, e só
   * eles pintam de verde. O resto fica contado numa linha — a página inteira
   * está no carrossel, como evidência. */
  var NOME_MOTOR = {duckduckgo: 'DuckDuckGo', yahoo: 'Yahoo', google: 'Google (reserva)'};

  function linkSeguro(u) {
    var s = String(u || '').trim();
    if (/^https?:\/\//i.test(s)) { return s; }
    if (/^[\w.-]+\.[a-z]{2,}(\/|$)/i.test(s)) { return 'https://' + s; }
    return '';
  }

  /* SERASA SEPARADO DA BUSCA NA WEB (15/09/2026): no julgamento leve a IA diz a fonte
   * de cada confirmação — "Serasa" é a Receita republicada e não acende o verde da
   * busca. Abaixo, as redes sociais que o código achou no endereço com o nome de um
   * registro, e o texto de cada motor com a fonte de cada resultado, como a IA leu. */
  function corpoBusca(d, ctx) {
    var buscas = d.buscas || [], usados = d.usados || [], serasa = d.serasa || [], redes = d.redes_sociais || [];
    if (!buscas.length && !serasa.length && !redes.length) {
      return '<div class="sem-dado">A busca web (DuckDuckGo e Yahoo) ainda não rodou para esta ligação.</div>';
    }
    var h = buscas.length ? '<div class="prova-resumo">' + esc(buscas.map(function (b) {
      return (NOME_MOTOR[b.motor] || b.motor) + ': ' + (b.no_endereco ? b.no_endereco + ' no endereço' : 'nada no endereço');
    }).join(' · ')) + '</div>' : '';
    h += '<div class="grupo-cab">busca na web · o que confirmou</div>';
    if (!buscas.length) {
      h += '<div class="sem-dado">A busca web ainda não rodou para esta ligação.</div>';
    } else if (!d.informado) {
      h += '<div class="sem-dado">Julgada antes de a IA dizer quais resultados usou: está na fila '
        + 'do rejulgamento. Até lá a busca não conta como confirmação.</div>';
    } else if (!usados.length) {
      h += '<div class="sem-dado">Nenhum resultado da busca na web confirmou um registro desta ligação'
        + (d.recusados ? ' — a IA leu ' + d.recusados + ' e não aceitou (outro negócio no endereço ou sem '
                         + 'dado do registro)' : '') + '.</div>';
    } else {
      h += usados.map(function (u) {
        var nome = ctx.nome[u.poi], link = linkSeguro(u.url);
        var motores = (u.motores || []).map(function (m) { return NOME_MOTOR[m] || m; }).join(' e ');
        var trecho = u.trecho ? (u.trecho.length > 160 ? u.trecho.slice(0, 157) + '…' : u.trecho) : '';
        return '<div class="prova ok">'
          + '<div class="prova-cab"><span class="selo ok">confirma</span>'
          + (u.poi ? '<b>#' + esc(u.poi) + (nome ? ' · ' + esc(nome) : '') + '</b>' : '')
          + '<span class="poi">' + esc(motores || u.fonte || '') + '</span></div>'
          + (u.titulo ? '<div class="prova-tit">' + (link
              ? '<a href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">' + esc(u.titulo) + ' ↗</a>'
              : esc(u.titulo)) + '</div>' : '')
          + (!u.titulo && u.fonte ? '<div class="prova-trecho">A IA não cita o resultado: o texto que ela leu, com a '
                                    + 'fonte de cada um, está abaixo.</div>' : '')
          + (trecho ? '<div class="prova-trecho">' + esc(trecho) + '</div>' : '')
          + (u.casa ? '<div class="reg-por"><b>o que casa</b>' + esc(u.casa) + '</div>' : '')
          + '</div>';
      }).join('');
    }
    if (serasa.length) {
      h += '<div class="grupo-cab">Serasa · ficha do CNPJ · é a Receita republicada, não acende a busca</div>'
        + serasa.map(function (s) {
            var nome = ctx.nome[s.poi];
            return '<div class="prova serasa"><div class="prova-cab"><span class="selo ' + (s.confirma ? 'ok' : 'cinza') + '">'
              + (s.confirma ? 'confirma' : 'não confirma') + '</span>'
              + (s.poi ? '<b>#' + esc(s.poi) + (nome ? ' · ' + esc(nome) : '') + '</b>' : '')
              + '<span class="poi">Serasa</span></div>'
              + '<div class="prova-trecho">O texto e o print da ficha estão no registro com o CNPJ.</div></div>';
          }).join('');
    }
    h += '<div class="grupo-cab">redes sociais no endereço · ' + redes.length + '</div>'
      + (redes.length
          ? '<div class="caixa-txt">' + redes.map(linhaRede).join('') + '</div>'
          : '<div class="sem-dado">Nenhum post de rede social no endereço com o nome de um registro. Rede social '
            + 'confirmada pelo código é fonte própria, independente da Receita.</div>');
    var comTexto = buscas.filter(function (b) { return b.texto_anotado; });
    if (comTexto.length) {
      var ab = aberto(S.sel, 'busca:texto', false);
      h += '<button class="mais" data-abre="busca:texto" data-aberto="' + (ab ? '1' : '0') + '">'
        + (ab ? 'esconder o texto que a IA leu' : 'ver o texto que a IA leu, com a fonte de cada resultado · '
               + comTexto.map(function (b) { return NOME_MOTOR[b.motor] || b.motor; }).join(', ')) + '</button>'
        + (ab ? comTexto.map(function (b) {
            return '<div class="caixa-txt"><div class="reg-bloco"><i>' + esc(NOME_MOTOR[b.motor] || b.motor)
              + (b.feito_em ? ' · ' + esc(quando(b.feito_em)) : '') + (b.consulta ? ' · “' + esc(b.consulta) + '”' : '')
              + '</i><pre class="busca-txt">' + esc(b.texto_anotado) + '</pre></div></div>';
          }).join('') : '');
    }
    return h;
  }

  function nomeFoto(q, ctx) {
    return (q.tipo === 'foto publicada' ? 'foto publicada' : 'rua · ' + (VISADAS[q.tipo] || q.tipo))
      + (q.poi ? ' do POI #' + q.poi + (ctx.nome[q.poi] ? ' (' + ctx.nome[q.poi] + ')' : '') : '');
  }

  /* AS FOTOS, PELO QUE A IA IDENTIFICOU NELAS (13/09/2026): ter imagem coletada
   * não é confirmação; a foto confirma quando a IA viu nela o comércio. */
  function corpoFoto(fi, ctx) {
    var d = (fi.fontes && fi.fontes.foto) || {};
    var n = (fi.imagens || []).length;
    if (!n) {
      return '<div class="sem-dado">Nenhuma foto de rua nem foto publicada dos POIs desta ligação.</div>';
    }
    if (!d.informado) {
      return '<div class="sem-dado">Julgada antes de a IA dizer se as fotos mostram o comércio: está na fila '
        + 'do rejulgamento. Até lá as ' + n + ' imagem(ns) coletada(s) não contam como confirmação.</div>';
    }
    var quais = (d.quais || []).map(function (q) { return nomeFoto(q, ctx); });
    return '<div class="prova' + (d.achou ? ' ok' : '') + '">'
      + '<div class="prova-cab"><span class="selo ' + (d.achou ? 'ok' : 'cinza') + '">'
      + (d.achou ? 'identificou o comércio' : 'não identificou') + '</span>'
      + '<span class="poi">a IA viu ' + (d.vistas || []).length + ' de ' + n + ' imagem(ns)</span></div>'
      + (d.o_que_mostram ? '<div class="prova-tit">' + esc(d.o_que_mostram) + '</div>' : '')
      + (d.sinal ? '<div class="reg-por"><b>sinal concreto</b>' + esc(d.sinal) + '</div>' : '')
      + (quais.length ? '<div class="reg-por"><b>' + (quais.length > 1 ? 'nas fotos' : 'na foto') + '</b>'
                        + esc(quais.join(' · ')) + '</div>' : '')
      + '</div>'
      + leituraDaRua(fi, ctx)
      + '<div class="fonte-nota">As imagens estão no carrossel do topo' + (d.achou ? '; a que confirma leva o selo verde' : '')
      + '.</div>';
  }

  /* A LEITURA DA FOTO DE RUA (15/09/2026): cada fachada com a cor do papel que o
   * código deu a ela, e o texto que a IA recebeu. As caixas aparecem sobre a foto
   * ampliada, no visor. */
  function leituraDaRua(fi, ctx) {
    if (!ehAdmin()) { return ''; }
    var rua = (fi.imagens || []).filter(function (x) { return x.fonte === 'foto' && x.leitura; })[0];
    if (!rua) { return ''; }
    var L = rua.leitura, fs = L.fachadas || [];
    return '<div class="grupo-cab">leitura da foto de rua · a fachada da seta é decidida pelo código</div>'
      + (rua.depois_do_julgamento ? '<div class="ia-aviso"><b>foto recapturada depois do julgamento</b>A IA viu a '
                                    + 'captura anterior; esta leitura é da foto atual.</div>' : '')
      + (fs.length ? '<div class="fach-lista">' + fs.map(linhaFachada).join('') + '</div>' : '')
      + (L.desempate && L.desempate.n !== undefined ? '<div class="reg-por caixa-txt"><b>desempate na divisa</b>'
          + esc('fachada ' + (L.desempate.n === null ? 'indefinida' : L.desempate.n) + ' · certeza ' + (L.desempate.certeza || '?')
                + (L.desempate.por ? ' · ' + L.desempate.por : '')) + '</div>' : '')
      + (L.texto_ia ? '<div class="caixa-txt"><div class="reg-bloco"><i>o texto que a IA recebeu desta foto</i>'
                      + '<pre class="busca-txt">' + esc(L.texto_ia) + '</pre></div></div>' : '')
      + '<div class="caixa-txt"><button class="lk" data-img="' + esc(rua.id) + '">ver a foto com as caixas das fachadas</button></div>';
  }

  function linhaFachada(f) {
    var p = PAPEIS[f.papel] || PAPEIS.sem_sinal;
    var ts = (f.textos || []).map(function (t) { return '“' + t.texto + '”' + (t.aluga ? ' (aluga/vende)' : ''); });
    var nomes = (f.nome_em || []).map(function (x) { return '“' + x.texto + '” em ' + x.fonte; });
    return '<div class="fach" style="--fc:' + p.cor + '"><i></i><span><b>' + esc(f.n) + ' · ' + esc(p.rot) + '</b> — '
      + esc(f.descricao || '?') + (f.encoberta ? ' (encoberta)' : '')
      + (ts.length ? ' · textos: ' + esc(ts.join('; ')) : '')
      + ((f.sinais_sem_texto || []).length ? ' · sinais: ' + esc(f.sinais_sem_texto.join('; ')) : '')
      + (nomes.length ? ' · <em>nome em outra fonte: ' + esc(nomes.join('; ')) + '</em>' : '')
      + '</span></div>';
  }

  function noFonte(c, fi, f, ctx) {
    var d = (fi.fontes && fi.fontes[f.id]) || {achou: false};
    var regs = d.registros || [];
    var vivos = regs.filter(function (r) { return !r.descartado_em; });
    var nAder = vivos.filter(function (r) { return ctx.ader[r.poi_id]; }).length;
    var chave = 'f:' + f.id, ab = aberto(c.ligacao, chave, !!d.achou);
    var sit;
    if (f.id === 'busca') {
      var leve = (d.usados || []).some(function (u) { return u.fonte; });
      sit = d.achou ? 'confirmou · ' + (d.usados || []).length + (leve ? ' registro(s)' : ' resultado(s)')
          : (d.buscas || []).length && !d.informado ? 'a IA ainda não informou'
          : (d.serasa || []).some(function (s) { return s.confirma; }) ? 'não confirmou · só o Serasa'
          : 'não confirmou';
    } else if (f.id === 'foto') {
      sit = d.achou ? 'identificou o comércio'
          : (fi.imagens || []).length && !d.informado ? 'a IA ainda não informou' : 'não identificou';
    } else {
      sit = d.achou ? 'achou · ' + vivos.length + ' registro(s)' : (regs.length ? 'só vínculos descartados' : 'não achou');
    }
    var cab = '<button class="no-fonte' + (d.achou ? '' : ' mudo') + '" data-abre="' + chave
      + '" data-aberto="' + (ab ? '1' : '0') + '">' + seta(ab)
      + '<span class="nm">' + esc(rotuloFonte(f)) + '</span>'
      + '<span class="sit ' + (d.achou ? 'ok' : 'mudo') + '">' + esc(sit) + '</span>'
      + (nAder ? '<span class="curado">' + nAder + ' aderente(s) para a IA</span>' : '')
      + '<span class="cresce"></span>'
      + '<span class="chave mono">' + esc(outroNome(f)) + '</span></button>';
    if (!ab) { return cab; }
    var corpo;
    if (f.id === 'busca') { corpo = corpoBusca(d, ctx); }
    else if (f.id === 'foto') { corpo = corpoFoto(fi, ctx); }
    else if (regs.length) {
      corpo = regs.map(function (r) { return cartaoRegistro(r, ctx); }).join('');
    } else {
      corpo = '<div class="sem-dado">A fonte não tem registro vinculado a esta ligação. Não achar '
        + 'não é negativa: é ausência de dado.</div>';
    }
    return cab + '<div class="no-corpo">' + corpo + '</div>';
  }

  function ramoGrupo(c, fi, g, ctx) {
    var fs = FONTES.filter(function (f) { return f.grupo === g.id; });
    var achou = fs.filter(function (f) { return achouNa(c, fi, f.id); });
    var mostrar = S.mudos ? fs : achou;
    var chave = 'g:' + g.id, ab = aberto(c.ligacao, chave, achou.length > 0);
    var prova = !!PROVA[g.id];
    var cab = '<button class="no-crit' + (achou.length ? ' tem' : '') + '" data-abre="' + chave
      + '" data-aberto="' + (ab ? '1' : '0') + '" style="--cc:' + g.cor + '">' + seta(ab)
      + '<span class="nm">' + esc(g.rot) + '</span>'
      + '<span class="res">' + (prova ? (achou.length ? 'confirmou' : 'não confirmou')
          : achou.length ? achou.length + ' de ' + fs.length + ' fonte(s) acharam' : 'nenhuma fonte achou') + '</span>'
      + '<span class="cresce"></span><span class="regra">' + esc(g.diz) + '</span></button>';
    if (!ab) { return '<section class="ramo n-' + g.id + '">' + cab + '</section>'; }
    var dentro = mostrar.length
      ? mostrar.map(function (f) { return noFonte(c, fi, f, ctx); }).join('')
      : '<div class="sem-dado">' + (prova ? 'Não confirmou. Use “mostrar quem não achou” para ver o que foi lido.'
          : 'Nenhuma fonte deste grupo achou registro. Use “mostrar quem não achou” para ver quais eram.')
        + '</div>';
    return '<section class="ramo n-' + g.id + '">' + cab + '<div class="ramo-corpo">' + dentro
      + '</div></section>';
  }

  function ramoOS(c) {
    var chave = 'c:os', ab = aberto(c.ligacao, chave, false);
    var cab = '<button class="no-crit os" data-abre="' + chave + '" data-aberto="' + (ab ? '1' : '0')
      + '" style="--cc:#5B6274">' + seta(ab) + '<span class="nm">Ordens de serviço</span>'
      + '<span class="res">sem dado</span><span class="cresce"></span>'
      + '<span class="regra">o que a operação já sabia sobre este imóvel</span></button>';
    if (!ab) { return '<section class="ramo n-os">' + cab + '</section>'; }
    return '<section class="ramo n-os">' + cab + '<div class="ramo-corpo"><div class="no-corpo">'
      + '<div class="sem-dado">Sem dado: o Comercial Radar ainda não recebe as ordens de serviço da '
      + 'concessionária. Isto não quer dizer que não houve OS — quer dizer que não sabemos.</div>'
      + '</div></div></section>';
  }

  function ramoImpacto(c) {
    var chave = 'c:impacto', ab = aberto(c.ligacao, chave, false);
    var cab = '<button class="no-crit impacto" data-abre="' + chave + '" data-aberto="' + (ab ? '1' : '0')
      + '" style="--cc:#0E8C9E">' + seta(ab) + '<span class="nm">Impacto financeiro</span>'
      + '<span class="res">sem dado</span><span class="cresce"></span>'
      + '<span class="regra">o que a retificação faria com a conta</span></button>';
    if (!ab) { return '<section class="ramo n-impacto">' + cab + '</section>'; }
    return '<section class="ramo n-impacto">' + cab + '<div class="ramo-corpo"><div class="no-corpo">'
      + '<div class="sem-dado">Sem dado: o Comercial Radar não recebe consumo nem valor faturado. '
      + 'Sem os dois, qualquer número aqui seria estimativa nossa — e estimativa não vira valor a '
      + 'cobrar.</div></div></div></section>';
  }

  function pintaArvore() {
    var c = caso(S.sel), alvo = $('#arvore');
    if (!c) {
      alvo.innerHTML = '<div class="vazio-g">' + (S.casos.length ? 'Escolha uma ligação na fila à esquerda.'
        : S.erro ? 'A fila não carregou: ' + esc(S.erro) : 'Carregando a fila…') + '</div>';
      return;
    }
    var fi = S.fichas[c.ligacao];
    if (!fi) {
      alvo.innerHTML = '<div class="vazio-g">carregando a ficha da ligação ' + esc(c.ligacao) + '…</div>';
      return;
    }
    if (fi.erro) {
      alvo.innerHTML = '<div class="vazio-g">A ficha não carregou: ' + esc(fi.erro)
        + '<br><br><button class="bt p" data-refaz="' + esc(c.ligacao) + '">tentar de novo</button></div>';
      return;
    }
    var ctx = contexto(fi);
    alvo.innerHTML = secaoImagens(c, fi) + ramoIA(c, fi, ctx) + ramoBase(c, fi)
      + GRUPOS.slice(1).map(function (g) { return ramoGrupo(c, fi, g, ctx); }).join('')
      + ramoOS(c) + ramoImpacto(c);
    alvo.setAttribute('data-ligacao', c.ligacao);
    var mini = alvo.querySelector('.terr-mini');
    if (mini) { window.setTimeout(function () { desenhaTerritorio(mini, fi, true); }, 20); }
    ajustaRolo();
  }

  /* ===================================================================
   * 6a · TERRITÓRIO, RUA E IMAGENS
   * =================================================================== */
  function anguloDe(id) {
    var h = 0, i, s = String(id);
    for (i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) % 3600; }
    return (h / 3600) * Math.PI * 2;
  }

  function pontosDoTerritorio(fi) {
    var pts = [];
    if (!fi || !fi.fontes) { return pts; }
    FONTES_DADO.forEach(function (f) {
      var d = fi.fontes[f.id];
      ((d && d.registros) || []).forEach(function (r) {
        if (r.descartado_em || r.metros === null || r.metros === undefined) { return; }
        pts.push({id: 'p' + r.poi_id, rot: f.curto + ' #' + r.poi_id, nome: r.nome || '', m: +r.metros,
                  cor: corFonte(f)});
      });
    });
    pts.sort(function (a, b) { return a.m - b.m; });
    return pts.slice(0, 40);
  }

  function desenhaTerritorio(cv, fi, mini) {
    if (!cv || !cv.getContext) { return; }
    var ctx = cv.getContext('2d');
    var dpr = Math.min(window.devicePixelRatio || 1, 3);
    var L = cv.clientWidth || 300, A = cv.clientHeight || 200;
    cv.width = Math.round(L * dpr); cv.height = Math.round(A * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, L, A);
    var pts = pontosDoTerritorio(fi);
    var maior = 120;
    pts.forEach(function (p) { if (p.m > maior) { maior = p.m; } });
    var raio = Math.min(L, A) * 0.40, cx = L / 2, cy = A / 2, escala = raio / (maior * 1.12);
    ctx.fillStyle = '#F7F9FC'; ctx.fillRect(0, 0, L, A);
    [[30, '30 m'], [100, '100 m']].forEach(function (par) {
      var r = par[0] * escala;
      if (r < 4) { return; }
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.strokeStyle = '#CFD6E0'; ctx.setLineDash([4, 5]); ctx.lineWidth = 1;
      ctx.stroke(); ctx.setLineDash([]);
      if (!mini) {
        ctx.fillStyle = '#98A2B3'; ctx.font = '9px ui-monospace, Menlo, monospace';
        ctx.textAlign = 'left'; ctx.fillText(par[1], cx + r + 4, cy - 3);
      }
    });
    var rotula = !mini && pts.length <= 12;
    pts.forEach(function (p) {
      var a = anguloDe(p.id), r = Math.min(p.m * escala, raio * 1.06);
      var x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y);
      ctx.strokeStyle = p.m <= 100 ? 'rgba(11,46,89,.22)' : 'rgba(214,69,69,.30)';
      ctx.lineWidth = 1; ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, mini ? 3.5 : 5, 0, Math.PI * 2);
      ctx.fillStyle = p.cor; ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
      if (rotula) {
        ctx.fillStyle = '#2A2E35'; ctx.font = '10px -apple-system, Segoe UI, sans-serif';
        ctx.textAlign = x < cx ? 'right' : 'left';
        ctx.fillText(p.rot + '  ' + metrosTxt(p.m), x + (x < cx ? -9 : 9), y + 3);
      }
    });
    ctx.beginPath(); ctx.arc(cx, cy, mini ? 5 : 7, 0, Math.PI * 2);
    ctx.fillStyle = '#F28C28'; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
    if (!mini) {
      ctx.fillStyle = '#0B2E59'; ctx.font = '600 11px -apple-system, Segoe UI, sans-serif';
      ctx.textAlign = 'center'; ctx.fillText('ligação ' + S.sel, cx, cy - 14);
      if (!pts.length) {
        ctx.fillStyle = '#98A2B3'; ctx.font = '11px -apple-system, Segoe UI, sans-serif';
        ctx.fillText('nenhum registro com distância medida', cx, cy + 34);
      }
    }
  }

  function abreTerritorio() {
    var fi = ficha();
    if (!fi) { return; }
    var pts = pontosDoTerritorio(fi);
    $('#modal-tit').textContent = 'Território · ligação ' + S.sel;
    $('#modal-corpo').innerHTML = '<div class="terr-grande"><canvas id="terr-cv"></canvas></div>'
      + '<p class="img-legenda"><i>o que este mapa mostra</i>'
      + 'O hidrômetro da ligação no centro e cada registro vinculado na distância medida até ele. '
      + 'Os anéis de 30 m e 100 m são só régua de leitura.</p>'
      + '<div class="terr-lista">' + (pts.length
          ? pts.map(function (p) {
              return '<div class="tl' + (p.m > 100 ? ' fora' : '') + '">'
                + '<i style="background:' + p.cor + '"></i>'
                + '<span>' + esc(p.rot) + (p.nome ? ' · ' + esc(p.nome) : '') + '</span>'
                + '<b class="mono">' + metrosTxt(p.m) + '</b>'
                + '<em>' + (p.m > 100 ? 'além de 100 m' : 'até 100 m') + '</em></div>';
            }).join('')
          : '<div class="sem-dado">Nenhum registro com distância medida.</div>')
      + '</div>';
    $('#modal-pe').innerHTML = '<button class="bt p" data-fecha="1">fechar</button>';
    $('#modal').hidden = false;
    window.setTimeout(function () { desenhaTerritorio($('#terr-cv'), fi, false); }, 30);
  }

  function abreRua() {
    var k = coordenada(ficha());
    $('#modal-tit').textContent = 'Vista de rua · ligação ' + S.sel;
    if (!k) {
      $('#modal-corpo').innerHTML = '<div class="sem-dado">Esta ligação não tem coordenada no '
        + 'cadastro — sem ponto, não há vista de rua.</div>';
    } else {
      $('#modal-corpo').innerHTML = '<div class="rua-aviso">A vista de rua vem de um provedor '
        + 'externo. Carregar significa <b>enviar a coordenada desta ligação para ele</b> — por isso '
        + 'nada foi carregado até aqui.</div>'
        + '<div class="rua-tela" id="rua-tela"><button class="bt pri" data-rua="'
        + esc(k.lat + ',' + k.lon) + '">carregar vista de rua</button></div>'
        + '<p class="img-meta mono">' + esc(k.lat.toFixed(6) + ', ' + k.lon.toFixed(6)) + ' · '
        + esc(k.origem) + '</p>';
    }
    $('#modal-pe').innerHTML = '<button class="bt p" data-fecha="1">fechar</button>';
    $('#modal').hidden = false;
  }

  function carregaRua(par) {
    var tela = $('#rua-tela');
    if (!tela) { return; }
    var src = 'https://maps.google.com/maps?q=&layer=c&cbll=' + encodeURIComponent(par)
      + '&cbp=11,0,0,0,0&output=svembed';
    tela.innerHTML = '<iframe src="' + src + '" loading="lazy" referrerpolicy="no-referrer"'
      + ' allowfullscreen title="vista de rua"></iframe>';
  }

  /* as imagens da ficha, com a legenda dizendo de onde vêm e de quem são. O
   * SELO VERDE É SÓ DA QUE CONFIRMA (13/09/2026): a foto em que a IA viu o
   * comércio e o print da busca de onde saiu um resultado que confirmou. A
   * coleta, sozinha, fica com selo neutro. */
  function imagensDaFicha(fi, ctx) {
    var pf = (fi.fontes && fi.fontes.foto) || {}, pb = (fi.fontes && fi.fontes.busca) || {};
    var confirma = {}, vista = {};
    /* a chave casa a foto do hidrômetro, que não é de POI: {poi: null, tipo: "sv_hidrometro"} */
    function chave(poi, tipo) { return (poi === null || poi === undefined ? '-' : poi) + ':' + tipo; }
    (pf.quais || []).forEach(function (q) { confirma[chave(q.poi, q.tipo)] = 1; });
    (pf.vistas || []).forEach(function (q) { vista[chave(q.poi, q.tipo)] = 1; });
    var lista = (fi.imagens || []).map(function (x) {
      var nome = ctx.nome[x.poi_id] ? ' (' + ctx.nome[x.poi_id] + ')' : '';
      if (x.fonte === 'serasa') {
        /* O PRINT DA FICHA DO CNPJ NO SERASA: a IA leu o texto dela, e não a imagem */
        var cnpj = String(x.cnpj || '').replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5');
        return {id: x.id, url: x.url, fonte: fonte('receita'), rot: 'ficha Serasa', selo: 'ficha Serasa', sel: 'neutro',
                legenda: 'Print da ficha do CNPJ ' + cnpj + ' no Serasa' + (x.situacao ? ' (' + x.situacao + ')' : '')
                  + ', do registro #' + x.poi_id + nome + '. A IA recebeu o texto desta ficha; é a Receita republicada.',
                quando: x.quando ? quando(x.quando) : 'sem data'};
      }
      if (x.fonte === 'maps') {
        /* A PUBLICADA QUE A IA VIU vem marcada pelo servidor, com o mesmo filtro do
         * julgamento: só a foto do próprio lugar. Foto de "lugares também pesquisados"
         * nunca leva selo. */
        var okM = !!x.vista_ia && x.do_lugar !== false && !!confirma[chave(x.poi_id, 'foto publicada')];
        var viuM = !!x.vista_ia && !okM;
        var rotM = 'foto publicada';
        return {
          id: x.id, url: x.url, fonte: fonte('maps'), rot: rotM,
          selo: okM ? 'confirma · ' + rotM : viuM ? 'vista pela IA · ' + rotM : x.do_lugar === false ? 'fora do lugar?' : rotM,
          sel: okM ? 'ok' : 'neutro',
          legenda: 'Foto publicada no Google Maps do POI #' + x.poi_id + nome + '.'
            + (x.do_lugar === false ? ' Coleta antiga, fora da capa: pode ser de “lugares também pesquisados” e não '
                                      + 'prova o lugar.' : '')
            + (okM ? ' A IA identificou o comércio nesta foto' + (pf.o_que_mostram ? ': ' + pf.o_que_mostram : '.')
                   : viuM ? ' A IA viu esta foto no julgamento.' : ''),
          quando: x.quando || 'sem data'
        };
      }
      var hid = x.tipo === 'sv_hidrometro';
      var ch = chave(hid ? null : x.poi_id, x.tipo);
      var ok = x.vista_ia === undefined ? !!confirma[ch] : !!x.vista_ia && !!confirma[ch];
      var viu = !ok && (x.vista_ia === undefined ? !!vista[ch] : !!x.vista_ia);
      var rot = 'rua · ' + (VISADAS[x.tipo] || x.tipo);
      /* A LEGENDA DA FOTO DE RUA DO JULGAMENTO LEVE (15/09/2026): de quando é, onde a
       * seta aponta, a distância da câmera e a planta no canto */
      var legenda = 'Street View de ' + (x.mes_ano || x.quando || 'data desconhecida')
        + ' · seta ' + (hid ? 'no hidrômetro desta instalação (nenhum registro com pin do Maps a até 60 m)'
                            : 'no pin do Maps do POI #' + x.poi_id + nome)
        + (x.distancia_m !== null && x.distancia_m !== undefined ? ' · câmera a ' + x.distancia_m + ' m' : '')
        + ' · planta vista de cima no canto.'
        + (x.depois_do_julgamento ? ' Recapturada depois do julgamento: a IA viu a captura anterior.' : '')
        + (ok ? ' A IA identificou o comércio nesta foto' + (pf.o_que_mostram ? ': ' + pf.o_que_mostram : '.')
              : viu ? ' A IA viu esta foto no julgamento.' : '')
        + (x.leitura && x.leitura.vale !== null && x.leitura.vale !== undefined
           ? ' Pela leitura, sinal que vale para esta instalação: ' + (x.leitura.vale ? 'sim.' : 'não.') : '');
      return {
        id: x.id, url: x.url, fonte: fonte('foto'), rot: rot,
        selo: ok ? 'confirma · ' + rot : viu ? 'vista pela IA · ' + rot : rot, sel: ok ? 'ok' : 'neutro',
        legenda: legenda, leitura: x.leitura || null,
        quando: x.quando || 'sem data'
      };
    });
    var bs = pb.buscas || [];
    var motorUsado = {};
    (pb.usados || []).forEach(function (u) { (u.motores || []).forEach(function (m) { motorUsado[m] = (motorUsado[m] || 0) + 1; }); });
    /* no julgamento leve a IA não diz o motor: com a busca na web confirmada, o
     * verde vai para os prints dos motores que trouxeram resultado no endereço */
    var webLeve = !!pb.achou && (pb.usados || []).some(function (u) { return u.fonte; });
    bs.slice().reverse().forEach(function (b) {
      if (!b.print) { return; }
      var nm = NOME_MOTOR[b.motor] || b.motor, usou = motorUsado[b.motor] || (webLeve && b.no_endereco ? 1 : 0);
      lista.unshift({id: 'busca' + b.id, url: b.print, fonte: fonte('busca'),
                     rot: 'print · ' + nm, selo: (usou ? 'confirma · ' : '') + 'print · ' + nm,
                     sel: usou ? 'ok' : 'neutro',
                     legenda: 'A página que ' + nm + ' mostrou para a busca pelo endereço: '
                       + (b.consulta || '') + '. ' + (b.no_endereco ? b.no_endereco + ' resultado(s) no endereço'
                                                                    : 'Nenhum resultado no endereço')
                       + (usou ? (webLeve && !motorUsado[b.motor] ? '; a busca na web confirmou registro(s) para a IA.'
                                                                   : '; ' + usou + ' confirmou(aram) um registro para a IA.')
                               : '.'),
                     quando: quando(b.feito_em)});
    });
    return lista;
  }

  function secaoImagens(c, fi) {
    var ctx = contexto(fi);
    var imgs = imagensDaFicha(fi, ctx);
    S.imgs = imgs;
    var ab = S.tira;
    var pts = pontosDoTerritorio(fi);
    var k = coordenada(fi);
    var cab = '<button class="img-cab" data-abre="sec:imagens" data-aberto="' + (ab ? '1' : '0') + '">'
      + seta(ab) + '<span class="nm">Território, rua e imagens</span>'
      + '<span class="res">' + pts.length + ' registro(s) no mapa · ' + imgs.length + ' imagem(ns)</span>'
      + '<span class="cresce"></span>'
      + '<span class="lgpd">a vista de rua só carrega no clique · a legenda diz de onde a imagem vem</span>'
      + '</button>';
    if (!ab) { return '<section id="imagens">' + cab + '</section>'; }
    var cartoes = [
      '<figure class="img-cart terr" data-terr="1" tabindex="0" role="button">'
      + '<div class="img-tela"><canvas class="terr-mini"></canvas><span class="img-sel neutro">local</span></div>'
      + '<figcaption><b>TERRITÓRIO</b><span>O hidrômetro no centro e cada registro na distância '
      + 'medida até ele.</span><i class="mono">' + pts.length + ' ponto(s) · desenhado aqui</i>'
      + '</figcaption></figure>',
      '<figure class="img-cart rua" data-rua-abre="1" tabindex="0" role="button">'
      + '<div class="img-tela rua-cap"><span class="rua-icone">◉</span><span class="img-sel '
      + (k ? 'neutro' : 'nao') + '">' + (k ? 'sob clique' : 'sem coord') + '</span></div>'
      + '<figcaption><b>VISTA DE RUA</b><span>' + (k
          ? 'Acervo do provedor no ponto do hidrômetro. Carrega só quando alguém pede.'
          : 'Sem coordenada no cadastro: não há vista de rua.')
      + '</span><i class="mono">provedor externo</i></figcaption></figure>'
    ].concat(imgs.map(function (x) {
      return '<figure class="img-cart" data-img="' + esc(x.id) + '" tabindex="0" role="button">'
        + '<div class="img-tela"><img loading="lazy" src="' + esc(window.comToken(x.url)) + '" alt="'
        + esc(x.legenda) + '"><span class="img-sel ' + x.sel + '">' + esc(x.selo) + '</span></div>'
        + '<figcaption><b>' + esc(rotuloFonte(x.fonte)) + '</b><span>' + esc(x.legenda) + '</span>'
        + '<i class="mono">' + esc(x.quando) + '</i></figcaption></figure>';
    }));
    return '<section id="imagens">' + cab + '<div class="img-rolo">'
      + '<button class="rola esq" data-rola="-1" aria-label="rolar para a esquerda">‹</button>'
      + '<div class="img-tira" id="img-tira">' + cartoes.join('') + '</div>'
      + '<button class="rola dir" data-rola="1" aria-label="rolar para a direita">›</button>'
      + '</div></section>';
  }

  function ajustaRolo() {
    var tira = $('#img-tira');
    if (!tira) { return; }
    var rolo = tira.parentNode;
    var passa = tira.scrollWidth > tira.clientWidth + 4;
    rolo.classList.toggle('tem-rolo', passa);
    if (!passa) { return; }
    rolo.querySelector('.rola.esq').disabled = tira.scrollLeft <= 2;
    rolo.querySelector('.rola.dir').disabled = tira.scrollLeft + tira.clientWidth >= tira.scrollWidth - 2;
  }

  function abreImagem(id) {
    for (var i = 0; i < S.imgs.length; i++) { if (S.imgs[i].id === id) { abreVisor(i); return; } }
  }

  /* ===================================================================
   * 6b · O VISOR — toda imagem do carrossel em tela quase cheia, com zoom
   * (dono do produto, 13/09/2026). Roda do mouse aproxima no ponto do
   * cursor, arrastar move, clique duplo alterna entre ajustar e 2,5×; as
   * setas passam de imagem. O print da busca é alto: abre pela largura,
   * do topo, para dar para ler.
   * =================================================================== */
  var VIS = {i: -1, z: 1, x: 0, y: 0, fit: 1, arrasto: null, caixas: true};

  function visorAberto() { var v = document.getElementById('visor'); return !!(v && !v.hidden); }

  function visorEl() {
    var v = document.getElementById('visor');
    if (v) { return v; }
    v = document.createElement('div');
    v.id = 'visor';
    v.hidden = true;
    v.setAttribute('role', 'dialog');
    v.setAttribute('aria-modal', 'true');
    v.setAttribute('aria-label', 'imagem ampliada');
    v.innerHTML = '<div class="vis-topo"><strong id="vis-tit"></strong><span class="vis-quando" id="vis-quando"></span>'
      + '<span class="cresce"></span>'
      + '<button class="vis-bt txt" data-vis="caixas" id="vis-caixas-bt" hidden aria-pressed="true"'
      + ' title="ligar ou desligar as caixas das fachadas (tecla C)">caixas</button>'
      + '<button class="vis-bt" data-vis="menos" title="afastar (tecla −)" aria-label="afastar">−</button>'
      + '<button class="vis-bt num" data-vis="ajustar" id="vis-zoom" title="ajustar à tela (tecla 0)">100%</button>'
      + '<button class="vis-bt" data-vis="mais" title="aproximar (tecla +)" aria-label="aproximar">+</button>'
      + '<button class="vis-bt num" data-vis="real" title="tamanho real (tecla 1)">1:1</button>'
      + '<button class="vis-bt" data-vis="fecha" title="fechar (Esc)" aria-label="fechar">✕</button></div>'
      + '<div class="vis-palco" id="vis-palco"><img id="vis-img" alt="" draggable="false">'
      + '<svg id="vis-caixas" class="vis-caixas" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"></svg>'
      + '<button class="vis-nav esq" data-vis="ant" aria-label="imagem anterior">‹</button>'
      + '<button class="vis-nav dir" data-vis="prox" aria-label="próxima imagem">›</button></div>'
      + '<p class="vis-legenda" id="vis-legenda"></p>'
      + '<div class="vis-chave" id="vis-chave" hidden></div>';
    document.body.appendChild(v);
    var palco = v.querySelector('#vis-palco'), img = v.querySelector('#vis-img');
    img.addEventListener('load', visorAjusta);
    palco.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      var r = palco.getBoundingClientRect();
      visorZoom(VIS.z * (ev.deltaY < 0 ? 1.18 : 1 / 1.18), ev.clientX - r.left, ev.clientY - r.top);
    }, {passive: false});
    palco.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0 || ev.target.closest('.vis-nav')) { return; }
      VIS.arrasto = {x: ev.clientX - VIS.x, y: ev.clientY - VIS.y};
      palco.setPointerCapture(ev.pointerId);
      palco.classList.add('arrasta');
    });
    palco.addEventListener('pointermove', function (ev) {
      if (!VIS.arrasto) { return; }
      VIS.x = ev.clientX - VIS.arrasto.x;
      VIS.y = ev.clientY - VIS.arrasto.y;
      visorPinta();
    });
    function solta() { VIS.arrasto = null; palco.classList.remove('arrasta'); }
    palco.addEventListener('pointerup', solta);
    palco.addEventListener('pointercancel', solta);
    palco.addEventListener('dblclick', function (ev) {
      if (ev.target.closest('.vis-nav')) { return; }
      var r = palco.getBoundingClientRect();
      if (VIS.z > VIS.fit * 1.05) { visorAjusta(); } else { visorZoom(VIS.fit * 2.5, ev.clientX - r.left, ev.clientY - r.top); }
    });
    return v;
  }

  function visorAjusta() {
    var palco = $('#vis-palco'), img = $('#vis-img');
    if (!palco || !img || !img.naturalWidth) { return; }
    var W = palco.clientWidth, H = palco.clientHeight, w = img.naturalWidth, h = img.naturalHeight;
    var alta = h / w > 2 * (H / W);
    VIS.fit = alta ? Math.min(W / w, 1.5) : Math.min(W / w, H / h);
    VIS.z = VIS.fit;
    VIS.x = (W - w * VIS.z) / 2;
    VIS.y = alta ? 0 : (H - h * VIS.z) / 2;
    visorCaixas();
    visorPinta();
  }

  /* AS CAIXAS DAS FACHADAS SOBRE A FOTO DE RUA (15/09/2026), em SVG no tamanho
   * natural da imagem e com a mesma transformação dela: acompanham o zoom e o
   * arrasto. A cor é o papel que o código deu à fachada (`PAPEIS`). */
  function visorCaixas() {
    var svg = $('#vis-caixas'), img = $('#vis-img'), bt = $('#vis-caixas-bt'), chave = $('#vis-chave');
    var x = S.imgs[VIS.i], L = x && x.leitura, fs = (L && L.fachadas) || [];
    bt.hidden = !fs.length;
    bt.classList.toggle('on', VIS.caixas);
    bt.setAttribute('aria-pressed', VIS.caixas ? 'true' : 'false');
    chave.hidden = !fs.length;
    if (!fs.length || !img.naturalWidth) { svg.innerHTML = ''; svg.style.display = 'none'; return; }
    var w = img.naturalWidth, h = img.naturalHeight, fz = Math.max(14, Math.round(w / 42));
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.setAttribute('width', w);
    svg.setAttribute('height', h);
    svg.innerHTML = fs.map(function (f) {
      var c = f.caixa || [0, 0, 0, 0], p = PAPEIS[f.papel] || PAPEIS.sem_sinal;
      var x1 = c[0] * w / 1000, y1 = c[1] * h / 1000;
      var bw = Math.max(1, (c[2] - c[0]) * w / 1000), bh = Math.max(1, (c[3] - c[1]) * h / 1000);
      return '<g style="--fc:' + p.cor + '"><title>' + esc(f.n + ' · ' + p.rot + ' · ' + (f.descricao || '')) + '</title>'
        + '<rect class="cx" x="' + x1.toFixed(1) + '" y="' + y1.toFixed(1) + '" width="' + bw.toFixed(1)
        + '" height="' + bh.toFixed(1) + '" vector-effect="non-scaling-stroke"></rect>'
        + '<rect class="cx-rot" x="' + x1.toFixed(1) + '" y="' + y1.toFixed(1) + '" width="' + (fz * 1.7).toFixed(1)
        + '" height="' + (fz * 1.45).toFixed(1) + '"></rect>'
        + '<text x="' + (x1 + fz * 0.85).toFixed(1) + '" y="' + (y1 + fz * 1.08).toFixed(1) + '" font-size="' + fz
        + '" text-anchor="middle">' + esc(f.n) + '</text></g>';
    }).join('');
    svg.style.display = VIS.caixas ? '' : 'none';
    var usados = {};
    fs.forEach(function (f) { usados[f.papel] = 1; });
    chave.innerHTML = '<div class="vis-cores">' + Object.keys(PAPEIS).filter(function (k) { return usados[k]; })
        .map(function (k) {
          return '<span style="--fc:' + PAPEIS[k].cor + '"><i></i>' + esc(PAPEIS[k].rot) + '</span>';
        }).join('') + '</div>'
      + fs.map(linhaFachada).join('');
  }

  function visorZoom(z, cx, cy) {
    var palco = $('#vis-palco');
    if (!palco) { return; }
    z = Math.max(VIS.fit * 0.5, Math.min(z, Math.max(8, VIS.fit * 8)));
    if (cx === undefined) { cx = palco.clientWidth / 2; cy = palco.clientHeight / 2; }
    VIS.x = cx - (cx - VIS.x) * (z / VIS.z);
    VIS.y = cy - (cy - VIS.y) * (z / VIS.z);
    VIS.z = z;
    visorPinta();
  }

  function visorPinta() {
    var img = $('#vis-img'), svg = $('#vis-caixas');
    img.style.transform = 'translate(' + VIS.x + 'px,' + VIS.y + 'px) scale(' + VIS.z + ')';
    img.style.visibility = 'visible';
    if (svg) { svg.style.transform = img.style.transform; }
    $('#vis-zoom').textContent = Math.round(VIS.z * 100) + '%';
  }

  function abreVisor(i) {
    if (i < 0 || i >= S.imgs.length) { return; }
    var v = visorEl(), x = S.imgs[i], img = $('#vis-img');
    VIS.i = i;
    $('#vis-tit').textContent = rotuloFonte(x.fonte) + ' · ' + x.selo;
    $('#vis-quando').textContent = (x.quando || '') + ' · ligação ' + S.sel + ' · ' + (i + 1) + ' de ' + S.imgs.length;
    $('#vis-legenda').textContent = x.legenda;
    /* as caixas da foto anterior saem antes de a nova carregar */
    $('#vis-caixas').innerHTML = '';
    $('#vis-caixas').style.display = 'none';
    $('#vis-caixas-bt').hidden = !(x.leitura && (x.leitura.fachadas || []).length);
    $('#vis-chave').hidden = true;
    img.style.visibility = 'hidden';
    img.alt = x.legenda;
    v.hidden = false;
    img.src = window.comToken(x.url);
    if (img.complete && img.naturalWidth) { visorAjusta(); }
    v.querySelector('.vis-nav.esq').disabled = i === 0;
    v.querySelector('.vis-nav.dir').disabled = i === S.imgs.length - 1;
    v.querySelector('[data-vis="fecha"]').focus();
  }

  function fechaVisor() {
    var v = document.getElementById('visor');
    if (!v || v.hidden) { return; }
    v.hidden = true;
    var cart = document.querySelector('[data-img="' + (S.imgs[VIS.i] || {}).id + '"]');
    if (cart) { cart.focus(); }
  }

  function acaoVisor(a) {
    if (a === 'fecha') { fechaVisor(); }
    else if (a === 'mais') { visorZoom(VIS.z * 1.35); }
    else if (a === 'menos') { visorZoom(VIS.z / 1.35); }
    else if (a === 'ajustar') { visorAjusta(); }
    else if (a === 'real') { visorZoom(1); }
    else if (a === 'ant') { abreVisor(VIS.i - 1); }
    else if (a === 'prox') { abreVisor(VIS.i + 1); }
    else if (a === 'caixas') { VIS.caixas = !VIS.caixas; visorCaixas(); }
  }

  function copiar(txt, rot) {
    function ok() { torrada((rot || 'valor') + ' copiado: ' + txt); }
    function falha() { torrada('não deu para copiar', true); }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(txt).then(ok).catch(falha);
      return;
    }
    var ta = document.createElement('textarea');
    ta.value = txt;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { if (document.execCommand('copy')) { ok(); } else { falha(); } } catch (e) { falha(); }
    document.body.removeChild(ta);
  }

  /* ===================================================================
   * 7 · VEREDITO DA IA E DECISÃO OFICIAL
   * =================================================================== */
  function decisaoAtual(c, fi) {
    if (fi && fi.decisao) { return fi.decisao; }
    if (c.decisao) { return {acao: c.decisao, em: c.decidido_em, quem: c.decidido_por}; }
    return null;
  }

  function pintaVeredito() {
    var c = caso(S.sel);
    if (!c) { $('#veredito').innerHTML = ''; $('#decisao').innerHTML = ''; return; }
    var fi = ficha(), ia = fi && fi.ia;
    var V = veredito(c.ia);
    var texto = ia ? (ia.motivo || ia.justificativa || 'A IA não deixou motivo.')
                   : (S.fichas[S.sel] && S.fichas[S.sel].erro ? 'a ficha não carregou' : 'carregando o parecer…');
    var fontesAchou = FONTES_DADO.filter(function (f) { return c.f[f.id]; });
    var motivos = fontesAchou.length
      ? fontesAchou.map(function (f) {
          return '<span class="pm" style="--mc:' + corFonte(f) + '"><i></i><b>' + esc(rotuloFonte(f))
            + '</b><em>' + achouTxt(f) + '</em></span>';
        }).join('')
      : '<span class="pm vazio">nenhuma fonte achou registro</span>';
    var ressalvas = [];
    var ch = ia && ia.checagem;
    if (ch && ch.veredito_ia && ch.veredito_ia !== ia.veredito) {
      ressalvas.push('a IA disse <b>' + esc(veredito(ch.veredito_ia).rot) + '</b>; a checagem do código mudou');
    }
    var dec = decisaoAtual(c, fi);
    if (dec && ((dec.acao === 'aprovar') !== (c.ia === 'aprovado'))) {
      ressalvas.push('a decisão oficial (<b>' + esc(ACOES[dec.acao].rot) + '</b>) diverge da IA');
    }
    var baixa = (ia && ia.prioridade === 'baixa') || (!ia && c.prioridade === 'baixa');
    $('#veredito').innerHTML = '<div class="par ' + V.tom + '">'
      + '<div class="par-cab"><i>veredito da IA · uma das fontes</i><b>' + esc(V.rot) + '</b>'
      + (baixa ? '<span class="par-prio" title="' + esc(TXT_PRIORIDADE) + '">prioridade baixa</span>' : '')
      + '<span class="par-chip">' + (c.avaliado_em ? 'julgada em ' + esc(quando(c.avaliado_em))
                                                  : 'sem data de julgamento') + '</span></div>'
      + '<p class="par-texto" data-expande="1" tabindex="0" title="clique para ler o motivo inteiro">'
      + esc(texto) + '</p>'
      + '<div class="par-motivos">' + motivos
      + ressalvas.map(function (r) { return '<span class="pr">⚠ ' + r + '</span>'; }).join('')
      + '</div></div>';

    var sug = c.ia === 'aprovado' ? 'aprovar' : c.ia === 'reprovado' ? 'rejeitar' : null;
    var pode = podeDecidir();
    var trava = travaDeOutro(c.ligacao);
    var livre = pode && !trava;
    var porque = !pode ? 'decidir exige nível editor ou acima'
      : trava ? 'em análise por ' + (trava.quem_nome || 'outro usuário') + ' — a decisão fica bloqueada até liberar' : '';
    var botoes = Object.keys(ACOES).map(function (a) {
      return '<button class="bt ' + (a === sug ? 'pri' : 'p') + (dec && dec.acao === a ? ' escolhido' : '')
        + '" data-decide="' + a + '"' + (livre ? '' : ' disabled')
        + ' title="' + esc(livre ? ACOES[a].diz : porque) + '">'
        + esc(ACOES[a].rot) + (a === sug ? '<span class="sug">IA</span>' : '') + '</button>';
    }).join('');
    /* o chamado não altera a decisão e não depende da trava (14/09/2026) */
    var chamado = '<button class="bt p chamado" data-chamado="1" title="Abrir chamado para esta ligação">'
      + 'abrir chamado<span class="longo"> para esta ligação</span></button><i class="sep" aria-hidden="true"></i>';
    var hist = fi && fi.decisoes ? fi.decisoes.length : 0;
    /* A LINHA DO TEMPO (16/09/2026): decisões + comentários; o texto mostrado é o mais recente, seja da decisão
     * ou de uma edição posterior, e o link aparece em QUALQUER status */
    var lt = linhaDoTempo(fi);
    var comTexto = lt.filter(function (x) { return x.texto; });
    var ultimoTexto = comTexto.length ? comTexto[comTexto.length - 1].texto : '';
    $('#decisao').innerHTML = (trava
        ? '<div class="dec-estado travado"><b>em análise por ' + esc(trava.quem_nome || 'outro usuário') + '</b>'
          + '<span>a decisão fica bloqueada até a pessoa sair desta ligação</span></div>'
        : '<div class="dec-estado' + (dec ? ' feito' : '') + '">'
          + (dec
              ? '<b>' + esc(ACOES[dec.acao] ? ACOES[dec.acao].rot : dec.acao) + '</b><span>'
                + esc(dec.quem || '') + (dec.em ? ' · ' + esc(quando(dec.em)) : '')
                + (ultimoTexto ? ' · ' + esc(ultimoTexto) : '') + '</span>'
              : '<b>sem decisão oficial</b><span>a IA diz <b>' + esc(V.rot) + '</b></span>')
          + '</div>')
      + '<div class="dec-curadoria">'
      + (lt.length ? '<button class="lk" data-historico="1">linha do tempo · ' + lt.length
                     + (comTexto.length ? ' · ver os comentários' : '') + '</button> · '
         : (podeDecidir() ? '<button class="lk" data-historico="1">comentar</button> · ' : ''))
      + 'a decisão oficial não altera o veredito da IA nem o das fontes</div>'
      + '<div class="cresce"></div><div class="acoes">' + chamado + botoes + '</div>';
  }

  /* TODA DECISÃO ABRE O DIÁLOGO (14/09/2026): o comentário é opcional em aprovar,
   * campo e revisar, e continua obrigatório para rejeitar */
  function decide(acao) {
    var c = caso(S.sel);
    if (!c) { return; }
    if (!podeDecidir()) { torrada('decidir exige nível editor ou acima', true); return; }
    var t = travaDeOutro(c.ligacao);
    if (t) { torrada('em análise por ' + (t.quem_nome || 'outro usuário') + ': a decisão fica bloqueada', true); return; }
    abreDecisao([c.ligacao], acao);
  }

  function descreveFiltros() {
    var f = [];
    if (S.fIA !== 'todos') { f.push('IA: ' + veredito(S.fIA).rot); }
    if (S.fQual !== 'todos') { f.push('cadastro: ' + S.fQual.replace(/_/g, ' ').toLowerCase()); }
    if (S.fDec !== 'todos') {
      f.push('decisão: ' + (DECISOES.filter(function (d) { return d.id === S.fDec; })[0] || {}).rot);
    }
    if (S.fSeg.length) {
      f.push('ramo: ' + S.fSeg.map(function (s) {
        return (SEGMENTOS.filter(function (o) { return o.id === s; })[0] || {rot: s}).rot; }).join(' ou '));
    }
    if (S.fVis.length) {
      f.push('imagem: ' + S.fVis.map(function (s) {
        return (VISUAL.filter(function (o) { return o.id === s; })[0] || {rot: s}).rot; }).join(' ou '));
    }
    Object.keys(S.fFonte).forEach(function (k) {
      f.push(fonte(k).curto + ': ' + (S.fFonte[k] === 'sim' ? 'achou' : 'não achou'));
    });
    if (S.termo) { f.push('busca: “' + S.termo + '”'); }
    return f;
  }

  function abreDecisao(ligs, acao) {
    S.pendente = {ligs: ligs, acao: acao || null};
    var lote = ligs.length > 1;
    var filtros = descreveFiltros();
    $('#modal-tit').textContent = lote ? 'Decisão oficial em lote' : 'Decisão oficial · ligação ' + ligs[0];
    $('#modal-corpo').innerHTML = (lote
        ? '<div class="lote-resumo"><b>' + num(ligs.length) + '</b> ligações do recorte atual recebem '
          + 'a mesma decisão.' + (filtros.length
            ? '<ul>' + filtros.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>'
            : ' Nenhum recorte está ativo: é a fila inteira.') + '</div>'
        : '')
      + '<div class="acao-opcoes">' + Object.keys(ACOES).map(function (a) {
          return '<button class="acao-op' + (S.pendente.acao === a ? ' on' : '') + '" data-escolhe="' + a
            + '" style="--ac:' + ACOES[a].cor + '"><b>' + esc(ACOES[a].rot) + '</b><span>'
            + esc(ACOES[a].diz) + '</span></button>';
        }).join('') + '</div>'
      + '<label class="obs-rot" for="dec-motivo" id="dec-motivo-rot">comentário</label>'
      + '<textarea id="dec-motivo" rows="3" placeholder="comentário sobre a decisão — obrigatório para rejeitar"></textarea>'
      + '<p class="obs-ajuda">A decisão oficial é gravada à parte: o veredito da IA e o das fontes '
      + 'continuam como estão. Fica registrado quem decidiu, quando e o comentário'
      + (lote ? ', e as ' + num(ligs.length) + ' linhas ficam marcadas como um mesmo lote.' : '.')
      + ' <b>Ctrl+Enter</b> grava.</p>';
    pintaPeDecisao();
    $('#modal').hidden = false;
    window.setTimeout(function () { $('#dec-motivo').focus(); }, 40);
  }

  function pintaPeDecisao() {
    var p = S.pendente;
    if (!p) { return; }
    var rot = $('#dec-motivo-rot');
    if (rot) { rot.textContent = p.acao === 'rejeitar' ? 'motivo · obrigatório' : 'comentário · opcional'; }
    $('#modal-pe').innerHTML = '<button class="bt p" data-fecha="1">cancelar</button>'
      + '<span class="cresce"></span>'
      + '<button class="bt pri' + (p.acao === 'rejeitar' ? ' ruim' : '') + '" data-grava="1"'
      + (p.acao ? '' : ' disabled') + '>'
      + (p.acao ? esc(ACOES[p.acao].rot) : 'escolha a decisão')
      + (p.ligs.length > 1 ? ' · ' + num(p.ligs.length) + ' ligações' : '') + '</button>';
  }

  function confirmaDecisao() {
    var p = S.pendente;
    if (!p || !p.acao) { return; }
    var motivo = ($('#dec-motivo') && $('#dec-motivo').value || '').trim();
    if (p.acao === 'rejeitar' && !motivo) { torrada('rejeitar exige o motivo escrito', true); return; }
    var bt = document.querySelector('[data-grava]');
    if (bt) { bt.disabled = true; bt.textContent = 'gravando…'; }
    grava(p.ligs, p.acao, motivo).then(function (ok) {
      if (ok) { $('#modal').hidden = true; S.pendente = null; }
      else if (bt) { pintaPeDecisao(); }
    });
  }

  /* grava, e só mexe na tela com a resposta do servidor na mão */
  function grava(ligs, acao, motivo) {
    var antes = indiceNaFila(S.sel);
    return fetch('/api/seek/decidir', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ligacoes: ligs, acao: acao, motivo: motivo || null})
    }).then(lerJson).then(function (b) {
      var agora = b.em || agoraIso();
      var quem = (window.EU && (window.EU.nome || window.EU.email)) || '';
      var fora = {};
      (b.sem_veredito || []).forEach(function (l) { fora[l] = 1; });
      (b.travadas || []).forEach(function (t) { fora[t.ligacao] = 1; });
      /* no lote, as travadas que não couberam na lista da resposta: nenhuma é marcada
       * localmente — a fila é buscada de novo */
      var incerto = (b.n_travadas || 0) > (b.travadas || []).length;
      ligs.forEach(function (l) {
        if (fora[l] || incerto) { return; }
        var c = S.porLig[l];
        if (c) { c.decisao = acao; c.decidido_em = agora; c.decidido_por = quem; }
        var fi = S.fichas[l];
        if (fi && !fi.erro) {
          /* `local`: o aviso da mesma decisão, quando chegar pelo /ws, só confirma */
          var d = {acao: acao, motivo: motivo || null, quem: quem, em: agora, lote: b.lote, local: true};
          fi.decisoes = [d].concat(fi.decisoes || []);
          fi.decisao = d;
        }
      });
      if (incerto) { recarregaFilaDepois(); }
      var tudo = b.gravadas >= b.pedidas;
      var txt = ligs.length === 1 ? (tudo ? 'decisão gravada: ' + ACOES[acao].rot : 'o servidor não gravou a decisão')
        : num(b.gravadas) + ' de ' + num(b.pedidas) + ' decisões gravadas';
      if (b.n_travadas) {
        txt += ' · ' + num(b.n_travadas) + ' ficaram de fora: em análise por outras pessoas';
      }
      torrada(txt, !tudo);
      pintaFila();
      /* a ligação saiu do recorte (ex.: "sem decisão oficial"): a próxima livre assume */
      if (S.sel && indiceNaFila(S.sel) < 0 && S.vistos.length) {
        var i = Math.min(Math.max(antes, 0), S.vistos.length - 1);
        var j = livreDesde(i, 1);
        if (j < 0) { j = livreDesde(i, -1); }
        if (j >= 0) { seleciona(S.vistos[j].ligacao, true); } else { pintaVeredito(); pintaLado(); }
      } else {
        pintaVeredito(); pintaLado();
      }
      return true;
    }).catch(function (e) {
      torrada('não gravou: ' + e.message, true);
      if (e.status === 409) { carregaTravas(); }
      return false;
    });
  }

  /* ===================================================================
   * 7a · CHAMADO PARA A LIGAÇÃO (14/09/2026)
   *
   * A tela só conversa com o contrato: GET /api/seek/chamado/empresas e
   * POST /api/seek/chamado. A integração com o sistema de chamados é do
   * servidor; se as rotas ainda não existem (404), o diálogo diz e a tela segue.
   * =================================================================== */
  function erroChamado(e) {
    if (e && e.status === 404) {
      return 'o serviço de chamados ainda não está disponível neste ambiente (HTTP 404)';
    }
    return (e && e.message) || 'falha de rede';
  }

  function abreChamado() {
    var c = caso(S.sel);
    if (!c) { return; }
    S.pendente = null;
    S.chamado = {ligacao: c.ligacao, empresas: null, enviando: false, feito: null};
    $('#modal-tit').textContent = 'Abrir chamado · ligação ' + c.ligacao;
    $('#modal-corpo').innerHTML = '<div class="ch-caso"><b class="mono">' + esc(c.ligacao) + '</b><span>'
      + esc(c.endereco + (c.bairro ? ' · ' + c.bairro : '') + (c.cidade ? ' · ' + c.cidade : '')) + '</span></div>'
      + '<label class="obs-rot" for="ch-empresa">empresa que vai atender · obrigatório</label>'
      + '<select id="ch-empresa" class="fl-sel ch-sel" disabled><option value="">carregando as empresas…</option></select>'
      + '<label class="obs-rot" for="ch-texto">o problema · obrigatório</label>'
      + '<textarea id="ch-texto" rows="5" maxlength="4000" '
      + 'placeholder="o que precisa ser verificado ou corrigido nesta ligação"></textarea>'
      + '<p class="obs-ajuda" id="ch-msg">O chamado vai com o número da ligação. A decisão oficial não muda.</p>';
    pintaPeChamado();
    $('#modal').hidden = false;
    fetch('/api/seek/chamado/empresas').then(lerJson).then(function (d) {
      if (!S.chamado || S.chamado.ligacao !== c.ligacao) { return; }
      var lista = (d && d.empresas) || [];
      S.chamado.empresas = lista;
      var sel = $('#ch-empresa');
      if (!sel) { return; }
      sel.innerHTML = '<option value="">' + (lista.length ? 'escolha a empresa' : 'nenhuma empresa atende chamados')
        + '</option>' + lista.map(function (x) {
          return '<option value="' + esc(x.id) + '">' + esc(x.nome) + '</option>';
        }).join('');
      sel.disabled = !lista.length;
      if (lista.length === 1) { sel.value = lista[0].id; }
      pintaPeChamado();
      window.setTimeout(function () { var t = $('#ch-texto'); if (t) { t.focus(); } }, 30);
    }).catch(function (e) {
      if (!S.chamado || S.chamado.ligacao !== c.ligacao) { return; }
      var sel = $('#ch-empresa');
      if (sel) { sel.innerHTML = '<option value="">empresas indisponíveis</option>'; }
      avisoChamado('Não deu para listar as empresas: ' + erroChamado(e), true);
      pintaPeChamado();
    });
  }

  function avisoChamado(txt, ruim) {
    var m = $('#ch-msg');
    if (!m) { return; }
    m.textContent = txt;
    m.className = 'obs-ajuda' + (ruim ? ' ch-erro' : '');
  }

  function pintaPeChamado() {
    var ch = S.chamado;
    if (!ch) { return; }
    if (ch.feito) {
      $('#modal-pe').innerHTML = '<span class="cresce"></span><button class="bt pri" data-fecha="1">fechar</button>';
      return;
    }
    var emp = $('#ch-empresa'), txt = $('#ch-texto');
    var pronto = !!(emp && emp.value && txt && txt.value.trim()) && !ch.enviando;
    $('#modal-pe').innerHTML = '<button class="bt p" data-fecha="1">cancelar</button><span class="cresce"></span>'
      + '<button class="bt pri" data-chamado-envia="1"' + (pronto ? '' : ' disabled') + '>'
      + (ch.enviando ? 'enviando…' : 'enviar chamado') + '</button>';
  }

  function enviaChamado() {
    var ch = S.chamado;
    if (!ch || ch.enviando || ch.feito) { return; }
    var emp = ($('#ch-empresa') || {}).value, texto = (($('#ch-texto') || {}).value || '').trim();
    if (!emp) { avisoChamado('Escolha a empresa que vai atender.', true); return; }
    if (!texto) { avisoChamado('Descreva o problema.', true); return; }
    ch.enviando = true;
    pintaPeChamado();
    avisoChamado('Enviando…');
    fetch('/api/seek/chamado', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ligacao: ch.ligacao, id_empresa_atendente: emp, texto: texto})
    }).then(lerJson).then(function (b) {
      if (S.chamado !== ch) { return; }
      ch.enviando = false;
      ch.feito = b;
      var link = linkSeguro(b.url);
      var resp = b.responsavel && b.responsavel.nome;
      $('#modal-corpo').innerHTML = '<div class="ch-ok"><b>Chamado #' + esc(b.numero) + ' aberto</b>'
        + '<span>' + (resp ? 'encaminhado a ' + esc(resp) : 'ainda sem responsável definido') + '</span>'
        + (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">abrir o chamado ↗</a>' : '')
        + '</div>';
      pintaPeChamado();
      torrada('Chamado #' + b.numero + ' aberto' + (resp ? ', encaminhado a ' + resp : ''));
    }).catch(function (e) {
      if (S.chamado !== ch) { return; }
      ch.enviando = false;
      avisoChamado('O chamado não foi aberto: ' + erroChamado(e), true);
      pintaPeChamado();
    });
  }

  /* A LINHA DO TEMPO DO CASO (dono do produto, 16/09/2026): as decisões e os comentários, do mais antigo ao
   * mais novo. EDITAR NÃO APAGA: a edição entra como item novo que aponta para o que revisou, e o original fica
   * com o status que tinha. Só quem escreveu vê o "editar". */
  var VERBO = {aprovar: 'aprovou', rejeitar: 'rejeitou', campo: 'mandou a campo', revisar: 'deixou em revisão'};

  function linhaDoTempo(fi) {
    var itens = [];
    if (!fi || fi.erro) { return itens; }
    (fi.decisoes || []).forEach(function (d) {
      itens.push({tipo: 'decisao', id: d.id, em: d.em, quem: d.quem, quem_id: d.quem_id, acao: d.acao,
                  texto: d.motivo || '', lote: d.lote});
    });
    (fi.comentarios || []).forEach(function (k) {
      itens.push({tipo: 'comentario', id: k.id, em: k.em, quem: k.quem, quem_id: k.quem_id, texto: k.texto,
                  edita_decisao: k.edita_decisao, edita_comentario: k.edita_comentario});
    });
    itens.sort(function (a, b) { return a.em < b.em ? -1 : a.em > b.em ? 1 : (a.tipo < b.tipo ? -1 : 1); });
    return itens;
  }

  function abreHistorico() {
    var fi = ficha();
    if (!fi) { return; }
    var itens = linhaDoTempo(fi);
    var porId = {};
    itens.forEach(function (x) { porId[x.tipo + ':' + x.id] = x; });
    var eu = window.EU && window.EU.id ? String(window.EU.id) : null;
    $('#modal-tit').textContent = 'Linha do tempo · ligação ' + S.sel;
    var corpo = itens.map(function (x) {
      var meu = eu && x.quem_id && String(x.quem_id) === eu;
      var cor = x.tipo === 'decisao' && ACOES[x.acao] ? (ACOES[x.acao].cor || 'var(--azul)') : 'var(--linha2)';
      var cab;
      if (x.tipo === 'decisao') {
        cab = '<b>' + esc(x.quem || 'alguém') + '</b> ' + esc(VERBO[x.acao] || x.acao)
          + (x.lote ? ' <span class="mono lt-lote">em lote ' + esc(String(x.lote).slice(0, 8)) + '</span>' : '');
      } else {
        var alvo = x.edita_decisao ? porId['decisao:' + x.edita_decisao] : x.edita_comentario ? porId['comentario:' + x.edita_comentario] : null;
        cab = '<b>' + esc(x.quem || 'alguém') + '</b> '
          + (alvo ? 'editou o comentário de ' + esc(quando(alvo.em)) : 'comentou');
      }
      return '<div class="lt-item" data-lt="' + x.tipo + ':' + x.id + '">'
        + '<i class="lt-ponto" style="--lc:' + cor + '"></i>'
        + '<div class="lt-conteudo"><div class="lt-cab"><span>' + cab + '</span><time>' + esc(quando(x.em)) + '</time></div>'
        + (x.texto ? '<div class="lt-texto">' + esc(x.texto) + '</div>' : '')
        + (meu && x.texto ? '<button class="lk lt-editar" data-editar-lt="' + x.tipo + ':' + x.id + '">editar</button>' : '')
        + '<div class="lt-editor" data-editor-lt="' + x.tipo + ':' + x.id + '" hidden>'
        + '<textarea rows="3">' + esc(x.texto || '') + '</textarea>'
        + '<div class="lt-botoes"><button class="bt p" data-cancela-lt="1">cancelar</button>'
        + '<button class="bt pri" data-salva-lt="' + x.tipo + ':' + x.id + '">salvar como nova versão</button></div>'
        + '<small>a versão anterior continua na linha do tempo, com o status que tinha</small></div>'
        + '</div></div>';
    }).join('') || '<div class="sem-dado">Nenhuma decisão nem comentário registrado.</div>';
    var novo = podeDecidir()
      ? '<div class="lt-novo"><textarea id="lt-novo-txt" rows="3" placeholder="adicionar um comentário…"></textarea>'
        + '<div class="lt-botoes"><button class="bt pri" data-comenta-lt="1">comentar</button></div></div>'
      : '';
    $('#modal-corpo').innerHTML = '<div class="lt">' + corpo + '</div>' + novo;
    $('#modal-pe').innerHTML = '<button class="bt p" data-fecha="1">fechar</button>';
    $('#modal').hidden = false;
  }

  /* grava o comentário (novo ou edição) e redesenha a ficha e a linha do tempo */
  function gravaComentario(texto, edita) {
    var lig = S.sel;
    texto = (texto || '').trim();
    if (!texto) { torrada('o comentário está vazio', true); return; }
    var corpo = {ligacao: lig, texto: texto};
    if (edita && edita.tipo === 'decisao') { corpo.edita_decisao = edita.id; }
    if (edita && edita.tipo === 'comentario') { corpo.edita_comentario = edita.id; }
    fetch('/api/seek/comentar', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)
    }).then(lerJson).then(function () {
      torrada(edita ? 'nova versão do comentário gravada' : 'comentário gravado');
      delete S.fichas[lig];
      return carregaFicha(lig);
    }).then(function () {
      if (S.sel === lig) { abreHistorico(); }
    }).catch(function (e) {
      torrada('o comentário não foi gravado: ' + e.message, true);
    });
  }

  /* ===================================================================
   * 8 · O VOLUME — as dez fontes desta ligação
   * =================================================================== */
  var CER = {motor: null, laco: 0, olho: null};
  var MOV_REDUZIDO = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var PONTEIRO = {x: 0, y: 0};

  function coberturaDoCaso() {
    var c = caso(S.sel), m = {};
    FONTES.forEach(function (f) {
      if (!c) { m[f.id] = null; return; }
      if (f.id === 'ia') {
        m.ia = c.ia === 'aprovado' ? 1 : c.ia === 'revisao_humana' ? 0.55 : c.ia ? 0.2 : null;
        return;
      }
      m[f.id] = c.f[f.id] ? 1 : null;   /* não achou fica anônima, como camada sem registro */
    });
    return m;
  }

  function paraCerebro() {
    if (CER.laco) { window.cancelAnimationFrame(CER.laco); CER.laco = 0; }
    if (CER.olho) { CER.olho.disconnect(); CER.olho = null; }
    if (CER.motor) { CER.motor.destroi(); CER.motor = null; }
  }

  function dizArea(id) {
    var el = $('#cer-leg'), c = caso(S.sel), tela = $('#lado .lado-tela');
    if (!el || !tela) { return; }
    var f = id ? fonte(id) : null;
    if (!f || !c) { el.hidden = true; return; }
    var fi = ficha(), estado, classe;
    if (f.id === 'ia') {
      estado = 'veredito: ' + veredito(c.ia).rot;
      classe = c.ia === 'aprovado' ? 'ok' : 'nada';
    } else if (c.f[f.id]) {
      var d = fi && fi.fontes && fi.fontes[f.id];
      var n = d && d.registros ? d.registros.filter(function (r) { return !r.descartado_em; }).length : null;
      estado = f.id === 'busca' ? 'a IA confirmou um registro pela busca'
             : f.id === 'foto' ? 'a IA identificou o comércio nas fotos'
             : 'achou' + (n ? ' · ' + n + ' registro(s)' : '');
      classe = 'ok';
    } else {
      estado = ehProva(f) ? 'não confirmou' : 'não achou registro para esta ligação';
      classe = 'mudo';
    }
    el.innerHTML = '<b><i style="background:' + corFonte(f) + '"></i>' + esc(rotuloFonte(f)) + '</b>'
      + '<span>' + esc(grupo(f.grupo).rot) + ' · <span class="mono">' + esc(outroNome(f)) + '</span></span>'
      + '<em class="' + classe + '">' + esc(estado) + '</em>';
    el.hidden = false;
    var cx = tela.clientWidth, cy = tela.clientHeight, L = el.offsetWidth, A = el.offsetHeight;
    var x = Math.min(Math.max(10, PONTEIRO.x - L / 2), Math.max(10, cx - L - 10));
    var acima = PONTEIRO.y > A + 26;
    var y = acima ? PONTEIRO.y - A - 14 : Math.min(PONTEIRO.y + 16, cy - A - 8);
    el.style.left = x + 'px';
    el.style.top = y + 'px';
    el.className = 'cer-leg' + (acima ? ' acima' : ' abaixo');
    el.style.setProperty('--seta', Math.min(Math.max(14, PONTEIRO.x - x), L - 14) + 'px');
  }

  function ligaCerebro() {
    var cv = document.getElementById('cer-cv');
    paraCerebro();
    if (!cv || !window.MotorCerebro) { return; }
    var meu = window.MotorCerebro.cria(cv, {
      areas: FONTES.map(function (f) {
        return {id: f.id, nome: f.curto, cor: corFonte(f), centro: f.centro};
      }),
      velocidade: 0.4,
      rotulos: true,
      aoPassarArea: dizArea,
      aoClicarArea: function (id) {
        var f = fonte(id), c = caso(S.sel);
        if (!f || !c) { return; }
        if (!S.abertos[S.sel]) { S.abertos[S.sel] = {}; }
        S.abertos[S.sel]['g:' + f.grupo] = true;
        S.abertos[S.sel]['f:' + f.id] = true;
        if (f.id !== 'ia' && !c.f[f.id]) { S.mudos = true; sincronizaMudos(); }
        pintaArvore();
        var no = document.querySelector('[data-abre="' + (f.id === 'ia' ? 'g:ia' : 'f:' + f.id) + '"]');
        if (no) {
          no.scrollIntoView({block: 'center'});
          no.classList.add('piscou');
          window.setTimeout(function () { no.classList.remove('piscou'); }, 1200);
        }
      }
    });
    CER.motor = meu;
    cv.addEventListener('mousemove', function (ev) {
      var r = cv.getBoundingClientRect();
      PONTEIRO.x = ev.clientX - r.left;
      PONTEIRO.y = ev.clientY - r.top;
    });
    cv.addEventListener('mouseleave', function () { dizArea(null); });
    meu.cobertura(coberturaDoCaso());
    meu.redimensiona();
    if (window.ResizeObserver) {
      CER.olho = new window.ResizeObserver(function () {
        if (!CER.motor || cv.clientWidth < 8 || cv.clientHeight < 8) { return; }
        CER.motor.redimensiona();
        if (MOV_REDUZIDO) { CER.motor.quadro(0); }
      });
      CER.olho.observe(cv);
    }
    if (MOV_REDUZIDO) { meu.quadro(0); return; }
    function quadro(t) {
      if (CER.motor !== meu) { return; }
      meu.quadro(t);
      CER.laco = window.requestAnimationFrame(quadro);
    }
    CER.laco = window.requestAnimationFrame(quadro);
  }

  function pintaLado() {
    var c = caso(S.sel);
    if (!c) { $('#criterios').innerHTML = ''; $('#lado-pe').innerHTML = ''; $('#tese').hidden = true; return; }
    var fi = ficha(), b = fi && fi.base;
    var tese = $('#tese');
    tese.innerHTML = '<i>qualificação no cadastro</i>' + esc((c.qualificacao || '—').replace(/_/g, ' '))
      + (b && b.qualificacao_motivo ? ' — ' + esc(b.qualificacao_motivo) : '');
    tese.hidden = false;
    var V = veredito(c.ia);
    $('#criterios').innerHTML = '<div class="cri' + (c.ia === 'aprovado' ? ' bate' : '') + '" title="'
      + esc(grupo('ia').diz) + '"><i style="background:' + grupo('ia').cor + '"></i><span>IA</span>'
      + '<b style="color:' + V.cor + '">' + esc(V.rot) + '</b></div>'
      + GRUPOS.slice(1).map(function (g) {
          var fs = FONTES.filter(function (f) { return f.grupo === g.id; });
          var n = fs.filter(function (f) { return c.f[f.id]; }).length;
          return '<div class="cri' + (n ? ' bate' : '') + '" title="' + esc(g.diz) + '">'
            + '<i style="background:' + g.cor + '"></i><span>' + esc(g.rot) + '</span>'
            + '<b>' + (PROVA[g.id] ? (n ? 'confirmou' : 'não') : n + '/' + fs.length) + '</b></div>';
        }).join('');
    var n = FONTES_DADO.filter(function (f) { return c.f[f.id]; }).length;
    $('#lado-pe').textContent = n + ' de ' + FONTES_DADO.length + ' fontes acharam registro · IA: ' + V.rot;
  }

  /* ===================================================================
   * 9 · EVENTOS E PARTIDA
   * =================================================================== */
  function sincronizaMudos() {
    var bt = $('#arv-mudos');
    bt.classList.toggle('on', S.mudos);
    bt.textContent = S.mudos ? 'esconder quem não achou' : 'mostrar quem não achou';
  }

  function abreTudo(v) {
    if (!S.sel) { return; }
    var m = {'g:ia': v, 'c:base': v, 'c:os': v, 'c:impacto': v};
    GRUPOS.forEach(function (g) { m['g:' + g.id] = v; });
    FONTES.forEach(function (f) { m['f:' + f.id] = v; });
    S.abertos[S.sel] = m;
    pintaArvore();
  }

  function digitando(ev) {
    var t = ev.target;
    return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT'
                 || t.isContentEditable);
  }

  function liga() {
    $('#chip-sair').addEventListener('click', function () {
      liberaAba(true);
      ['cr_token', 'cr_refresh', 'cr_expira'].forEach(function (k) {
        try { window.sessionStorage.removeItem(k); } catch (e) { /* aba sem armazenamento */ }
      });
      window.EU = null;
      window.location.reload();
    });

    $('#chip-rotulo').addEventListener('click', function () {
      S.rotuloCliente = !S.rotuloCliente;
      $('#rotulo-txt').textContent = S.rotuloCliente ? 'rótulo do cliente' : 'nome interno';
      $('#chip-rotulo').className = 'chip' + (S.rotuloCliente ? '' : ' demo');
      pintaFila(); pintaCaso();
    });

    var espera = 0;
    $('#f-busca').addEventListener('input', function () {
      S.busca = this.value;
      window.clearTimeout(espera);
      espera = window.setTimeout(refiltra, 140);
    });
    $('#f-lista').addEventListener('scroll', agendaLista, {passive: true});
    $('#f-lote').addEventListener('click', function () {
      if (!S.vistos.length) { return; }
      if (S.vistos.length > MAX_LOTE) {
        torrada('no máximo ' + num(MAX_LOTE) + ' ligações por vez — refine o recorte', true);
        return;
      }
      abreDecisao(S.vistos.map(function (c) { return c.ligacao; }), null);
    });
    $('#f-limpa').addEventListener('click', function () { S.fFonte = {}; refiltra(); });
    $('#f-limpa-seg').addEventListener('click', function () { S.fSeg = []; refiltra(); });
    $('#f-limpa-vis').addEventListener('click', function () { S.fVis = []; refiltra(); });

    document.addEventListener('change', function (ev) {
      var id = ev.target.id;
      if (id === 'f-sel-ia') { S.fIA = ev.target.value; refiltra(); }
      if (id === 'f-sel-qual') { S.fQual = ev.target.value; refiltra(); }
      if (id === 'f-sel-dec') { S.fDec = ev.target.value; refiltra(); }
      if (id === 'ch-empresa') { pintaPeChamado(); }
    });
    document.addEventListener('input', function (ev) {
      if (ev.target.id === 'ch-texto') { pintaPeChamado(); }
    });

    /* A TRAVA: sinal a cada 30 s, lista de novo a cada 2 min e ao voltar para a
     * aba, e soltar ao sair */
    window.setInterval(function () { if (S.sel) { pedeTrava(S.sel); } }, 30000);
    window.setInterval(function () { if (window.EU) { carregaTravas(); } }, 120000);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && window.EU) {
        carregaTravas();
        if (S.sel) { pedeTrava(S.sel); }
        if (!WS.sock) { ligaTempoReal(); }
      }
    });
    window.addEventListener('pagehide', function () { liberaAba(true); });

    $('#arv-tudo').addEventListener('click', function () { abreTudo(true); });
    $('#arv-nada').addEventListener('click', function () { abreTudo(false); });
    $('#arv-mudos').addEventListener('click', function () {
      S.mudos = !S.mudos; sincronizaMudos(); pintaArvore();
    });

    document.addEventListener('keydown', function (ev) {
      if (visorAberto()) {
        var ac = {Escape: 'fecha', ArrowLeft: 'ant', ArrowRight: 'prox', '+': 'mais', '=': 'mais',
                  '-': 'menos', '0': 'ajustar', '1': 'real', c: 'caixas', C: 'caixas'}[ev.key];
        if (ac) { ev.preventDefault(); acaoVisor(ac); }
        return;
      }
      if (ev.key === 'Escape' && !$('#modal').hidden) {
        $('#modal').querySelector('.modal-caixa').classList.remove('largo');
        $('#modal').hidden = true; S.pendente = null; S.chamado = null; return;
      }
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey) && !$('#modal').hidden) {
        if (ev.target.id === 'dec-motivo') { ev.preventDefault(); confirmaDecisao(); return; }
        if (ev.target.id === 'ch-texto') { ev.preventDefault(); enviaChamado(); return; }
      }
      if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest && ev.target.closest('[data-copia]')
          && !digitando(ev)) {
        ev.preventDefault();
        var cpk = ev.target.closest('[data-copia]');
        copiar(cpk.getAttribute('data-copia'), cpk.getAttribute('data-copia-rot'));
        return;
      }
      if (!$('#modal').hidden || digitando(ev)) { return; }
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        /* pula as que estão em análise por outra pessoa */
        var passo = ev.key === 'ArrowDown' ? 1 : -1;
        var i = livreDesde(indiceNaFila(S.sel) + passo, passo);
        if (i >= 0) { ev.preventDefault(); seleciona(S.vistos[i].ligacao, true); }
        return;
      }
      if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest && ev.target.closest('[data-img]')) {
        ev.preventDefault();
        abreImagem(ev.target.closest('[data-img]').getAttribute('data-img'));
      }
    });

    document.addEventListener('click', function (ev) {
      var t = ev.target.closest ? ev.target : null;
      if (!t) { return; }

      var vb = t.closest('[data-vis]');
      if (vb) { acaoVisor(vb.getAttribute('data-vis')); return; }
      if (visorAberto()) { return; }

      var fs = t.closest('[data-fseg]'), fv = t.closest('[data-fvis]');
      if (fs || fv) {
        var lista = fs ? S.fSeg : S.fVis, id2 = (fs || fv).getAttribute(fs ? 'data-fseg' : 'data-fvis');
        var i2 = lista.indexOf(id2);
        if (i2 >= 0) { lista.splice(i2, 1); } else { lista.push(id2); }
        refiltra();
        return;
      }
      var ff = t.closest('[data-ff]');
      if (ff) {
        var id = ff.getAttribute('data-ff'), e = S.fFonte[id];
        if (!e) { S.fFonte[id] = 'sim'; } else if (e === 'sim') { S.fFonte[id] = 'nao'; } else { delete S.fFonte[id]; }
        refiltra();
        return;
      }
      var k = t.closest('[data-caso]');
      if (k) { seleciona(k.getAttribute('data-caso'), false); return; }

      if (t.closest('[data-recarrega]')) {
        carregaFila().then(function () {
          pintaFila();
          if (S.sel && caso(S.sel)) { pintaCaso(); } else if (S.vistos.length) { seleciona(S.vistos[0].ligacao, true); }
        });
        return;
      }
      var rf = t.closest('[data-refaz]');
      if (rf) { delete S.fichas[rf.getAttribute('data-refaz')]; seleciona(rf.getAttribute('data-refaz')); return; }

      var rl = t.closest('[data-rola]');
      if (rl) {
        var tira = $('#img-tira');
        if (tira) {
          tira.scrollBy({left: (+rl.getAttribute('data-rola')) * 210, behavior: 'smooth'});
          window.setTimeout(ajustaRolo, 400);
        }
        return;
      }
      if (t.closest('[data-terr]')) { abreTerritorio(); return; }
      if (t.closest('[data-rua-abre]')) { abreRua(); return; }
      var ru = t.closest('[data-rua]');
      if (ru) { carregaRua(ru.getAttribute('data-rua')); return; }
      var im = t.closest('[data-img]');
      if (im) { abreImagem(im.getAttribute('data-img')); return; }
      var ex = t.closest('[data-expande]');
      if (ex) { ex.classList.toggle('aberto'); return; }

      var es = t.closest('[data-escolhe]');
      if (es && S.pendente) {
        S.pendente.acao = es.getAttribute('data-escolhe');
        Array.prototype.forEach.call(document.querySelectorAll('.acao-op'), function (b) {
          b.classList.toggle('on', b.getAttribute('data-escolhe') === S.pendente.acao);
        });
        pintaPeDecisao();
        return;
      }
      if (t.closest('[data-grava]')) { confirmaDecisao(); return; }
      var ed = t.closest('[data-editar-lt]');
      if (ed) {
        var chave = ed.getAttribute('data-editar-lt');
        var caixa = document.querySelector('[data-editor-lt="' + chave + '"]');
        if (caixa) { caixa.hidden = false; ed.hidden = true; var ta = caixa.querySelector('textarea'); if (ta) { ta.focus(); } }
        return;
      }
      if (t.closest('[data-cancela-lt]')) {
        var cx = t.closest('.lt-editor');
        if (cx) {
          cx.hidden = true;
          var bt = document.querySelector('[data-editar-lt="' + cx.getAttribute('data-editor-lt') + '"]');
          if (bt) { bt.hidden = false; }
        }
        return;
      }
      var sv = t.closest('[data-salva-lt]');
      if (sv) {
        var par = sv.getAttribute('data-salva-lt').split(':');
        var txt = sv.closest('.lt-editor').querySelector('textarea').value;
        gravaComentario(txt, {tipo: par[0], id: Number(par[1])});
        return;
      }
      if (t.closest('[data-comenta-lt]')) {
        var nv = document.getElementById('lt-novo-txt');
        gravaComentario(nv ? nv.value : '', null);
        return;
      }
      if (t.closest('[data-historico]')) { abreHistorico(); return; }
      var di = t.closest('[data-dados-ia]');
      if (di) { abreDadosIA(di.getAttribute('data-dados-ia')); return; }
      if (t.closest('[data-chamado-envia]')) { enviaChamado(); return; }
      if (t.closest('[data-chamado]')) { abreChamado(); return; }

      if (t.closest('[data-fecha]') || t.closest('#modal-x') || t.closest('.modal-fundo')) {
        $('#modal').hidden = true;
        $('#modal').querySelector('.modal-caixa').classList.remove('largo');
        S.pendente = null;
        S.chamado = null;
        return;
      }

      var ab = t.closest('[data-abre]');
      if (ab) {
        var chave = ab.getAttribute('data-abre');
        var estava = ab.getAttribute('data-aberto') === '1';
        if (chave === 'sec:imagens') { S.tira = !estava; pintaArvore(); return; }
        if (!S.abertos[S.sel]) { S.abertos[S.sel] = {}; }
        S.abertos[S.sel][chave] = !estava;
        pintaArvore();
        return;
      }

      var cp = t.closest('[data-copia]');
      if (cp) { copiar(cp.getAttribute('data-copia'), cp.getAttribute('data-copia-rot')); return; }

      var dc = t.closest('[data-decide]');
      if (dc && !dc.disabled) { decide(dc.getAttribute('data-decide')); }
    });

    window.addEventListener('resize', function () {
      if (visorAberto()) { visorAjusta(); }
      if (CER.motor && !CER.olho) { CER.motor.redimensiona(); }
      ajustaRolo();
      pintaLista();
    });
  }

  function mostraTela(eu) {
    $('#quem-nome').textContent = eu.nome || eu.email || '—';
    $('#quem-sub').textContent = (eu.nivel || '—') + (eu.empresa ? ' · ' + eu.empresa : '');
    /* a gestão das aprovações é de administrador para cima; o servidor confere de novo */
    $('#chip-gestao').hidden = ['admin', 'root'].indexOf(eu.nivel) < 0;
    /* o nível chega depois da ficha em tela: redesenha para aplicar o que é só de administrador */
    if (S.sel) { pintaArvore(); }
    pintaFila(); pintaArvore();
    ligaCerebro();
    ligaTempoReal();
    Promise.all([carregaFila(), carregaTravas()]).then(function () {
      pintaFila();
      var pedida = (window.location.hash || '').replace('#', '');
      var alvo = caso(pedida) ? pedida : (S.vistos[0] && S.vistos[0].ligacao);
      /* a pedida (ou a primeira) em análise por outra pessoa: abre a próxima livre */
      if (alvo && travaDeOutro(alvo)) {
        if (pedida === alvo) { seleciona(alvo, true); }
        var i = livreDesde(Math.max(indiceNaFila(alvo), 0), 1);
        alvo = i >= 0 ? S.vistos[i].ligacao : null;
      }
      if (alvo) { seleciona(alvo, true); } else { pintaCaso(); }
    });
  }

  function inicia() {
    liga();
    var foi = false;
    function vai(eu) {
      if (foi || !eu) { return; }
      foi = true;
      mostraTela(eu);
    }
    /* o sessao.js avisa quando o crachá chegou; sem sessão ele mesmo abre o
     * login, e depois de entrar recarrega a página */
    document.addEventListener('cr:sessao', function (ev) { vai(ev.detail); });
    if (window.EU) { vai(window.EU); }
    pintaFila(); pintaArvore();
  }

  return {inicia: inicia, FONTES: FONTES, GRUPOS: GRUPOS,
          estado: function () { return S; },
          cerebro: function () { return CER.motor; }};
}());

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', SEEK.inicia);
} else { SEEK.inicia(); }
