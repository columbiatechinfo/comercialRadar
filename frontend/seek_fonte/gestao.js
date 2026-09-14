/* ==== 99-gestao.js ==== */
/* 99-gestao.js — a gestão das aprovações da SEEK (14/09/2026)
 *
 * Pedido do dono do produto, para administrador ou acima: quem decide o quê, com
 * que taxa de aprovação, em quanto tempo e quando foi a última atividade; descer
 * até as ligações de cada pessoa e ao histórico de cada uma; e, numa seção à
 * parte, exportar em XLSX ou CSV — cada exportação fica registrada.
 *
 * O administrador vê a própria empresa; o suporte (root) escolhe uma ou vê todas.
 * O servidor confere o nível em toda rota (403): esconder aqui é cortesia.
 */
var GESTAO = (function () {
  'use strict';

  var ACOES = {
    aprovar:  {rot: 'aprovadas', um: 'aprovada', cor: '#14603D'},
    campo:    {rot: 'campo', um: 'mandada a campo', cor: '#006DFF'},
    revisar:  {rot: 'revisão', um: 'em revisão', cor: '#C96F16'},
    rejeitar: {rot: 'rejeitadas', um: 'rejeitada', cor: '#D64545'}
  };
  var ORDEM = ['aprovar', 'campo', 'revisar', 'rejeitar'];
  var VEREDITOS = {aprovado: 'aprovado', revisao_humana: 'revisão humana', reprovado: 'reprovado'};
  var NIVEIS = {user: 'usuário', editor: 'editor', supervisor: 'supervisor', admin: 'administrador', root: 'suporte'};

  var G = {
    resumo: null, ordem: 'decisoes_total', desc: true,
    pessoa: undefined, linhas: [], cursor: null, carregandoLig: false, filtroAcao: '', soVigentes: false,
    colunas: [], exportando: false, pedido: 0
  };

  function $(s) { return document.querySelector(s); }
  function esc(t) {
    return String(t === null || t === undefined ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function num(n) { return (+n || 0).toLocaleString('pt-BR'); }
  function pct(x) { return x === null || x === undefined ? '—' : (x * 100).toFixed(1).replace('.', ',') + '%'; }
  function dur(s) {
    if (s === null || s === undefined) { return '—'; }
    s = Math.round(+s);
    if (s < 60) { return s + ' s'; }
    if (s < 3600) { return Math.floor(s / 60) + ' min ' + (s % 60 < 10 ? '0' : '') + (s % 60) + ' s'; }
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h + ' h ' + (m < 10 ? '0' : '') + m + ' min';
  }
  function dois(n) { return (n < 10 ? '0' : '') + n; }
  function quando(iso) {
    if (!iso) { return '—'; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return String(iso); }
    return dois(d.getDate()) + '/' + dois(d.getMonth() + 1) + '/' + String(d.getFullYear()).slice(2)
      + ' ' + dois(d.getHours()) + ':' + dois(d.getMinutes());
  }
  function diaIso(d) { return d.getFullYear() + '-' + dois(d.getMonth() + 1) + '-' + dois(d.getDate()); }
  function diaTxt(iso) { var p = String(iso || '').split('-'); return p.length === 3 ? p[2] + '/' + p[1] + '/' + p[0] : '—'; }
  function torrada(msg, ruim) {
    var d = document.createElement('div');
    d.className = 't' + (ruim ? ' ruim' : '');
    d.textContent = msg;
    $('#torradas').appendChild(d);
    window.setTimeout(function () { d.classList.add('vai'); }, ruim ? 6000 : 3400);
    window.setTimeout(function () { if (d.parentNode) { d.parentNode.removeChild(d); } }, ruim ? 6500 : 3900);
  }
  function erroDe(r, b) {
    return (b && (b.erro || (typeof b.detail === 'string' ? b.detail : ''))) || ('HTTP ' + r.status);
  }
  function lerJson(r) {
    return r.json().catch(function () { return {}; }).then(function (b) {
      if (!r.ok) { var e = new Error(erroDe(r, b)); e.status = r.status; throw e; }
      return b;
    });
  }
  function qs(o) {
    return Object.keys(o).filter(function (k) { return o[k] !== '' && o[k] !== null && o[k] !== undefined; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&');
  }
  /* a SEEK mora na raiz da aplicação, que pode estar sob um prefixo público (/seek) */
  function linkSeek(lig) {
    var base = window.location.pathname.replace(/gestao\/?$/, '');
    return base + '#' + encodeURIComponent(lig);
  }
  function chipAcao(a) {
    var A = ACOES[a];
    return A ? '<span class="dchip" style="--dc:' + A.cor + '">' + esc(A.um) + '</span>' : esc(a || '—');
  }
  function recorte() {
    return {de: $('#g-de').value, ate: $('#g-ate').value, cidade: $('#g-cidade').value,
            empresa: $('#g-empresa').value};
  }

  /* ===================================================================
   * 1 · PESSOAS
   * =================================================================== */
  function carregaResumo() {
    var pedido = ++G.pedido;
    $('#g-aplica').disabled = true;
    $('#g-aplica').textContent = 'carregando…';
    return fetch('/api/seek/gestao/resumo?' + qs(recorte())).then(lerJson).then(function (d) {
      if (pedido !== G.pedido) { return; }
      G.resumo = d;
      $('#g-aviso').hidden = true;
      pintaEmpresas(d);
      pintaCidades(d);
      pintaResumo();
      pintaFiltrosExportacao();
      if (G.pessoa !== undefined) { abrePessoa(G.pessoa, true); }
    }).catch(function (e) {
      if (pedido !== G.pedido) { return; }
      $('#g-aviso').hidden = false;
      $('#g-aviso').textContent = 'O resumo não carregou: ' + e.message;
    }).then(function () {
      $('#g-aplica').disabled = false;
      $('#g-aplica').textContent = 'atualizar';
    });
  }

  function pintaEmpresas(d) {
    var root = window.EU && window.EU.nivel === 'root';
    $('#g-campo-empresa').hidden = !root;
    if (!root) { return; }
    var sel = $('#g-empresa'), atual = d.empresa || '';
    sel.innerHTML = '<option value="">todas as empresas</option>' + (d.empresas || []).map(function (e) {
      return '<option value="' + esc(e.id) + '">' + esc(e.nome) + '</option>';
    }).join('');
    sel.value = atual;
  }

  function pintaCidades(d) {
    ['#g-cidade', '#x-cidade'].forEach(function (id) {
      var sel = $(id), atual = sel.value;
      var lista = (d.cidades || []).slice();
      if (atual && lista.indexOf(atual) < 0) { lista.unshift(atual); }
      sel.innerHTML = '<option value="">todas</option>' + lista.map(function (c) {
        return '<option value="' + esc(c) + '">' + esc(c) + '</option>';
      }).join('');
      sel.value = atual;
    });
  }

  function kpi(rot, valor, sub, dica) {
    return '<div class="g-kpi"' + (dica ? ' title="' + esc(dica) + '"' : '') + '><i>' + esc(rot) + '</i><b>'
      + esc(valor) + '</b><span>' + esc(sub || '') + '</span></div>';
  }

  function pintaResumo() {
    var d = G.resumo, t = d.totais;
    var nomeEmp = d.empresa ? ((d.empresas || []).filter(function (e) { return e.id === d.empresa; })[0] || {}).nome : null;
    $('#g-resumo-sub').textContent = diaTxt(d.periodo.de) + ' a ' + diaTxt(d.periodo.ate)
      + (d.cidade ? ' · ' + d.cidade : '') + (window.EU && window.EU.nivel === 'root'
        ? ' · ' + (nomeEmp || 'todas as empresas') : '')
      + ' · vigente é a decisão mais recente de cada ligação · tempo de avaliação vai da abertura do caso à decisão';
    $('#g-kpis').innerHTML =
      kpi('decisões no período', num(t.decisoes_total), num(t.em_lote) + ' em lote',
          'toda decisão gravada no período, inclusive as que depois mudaram')
      + kpi('ligações decididas', num(t.vigentes_total),
            ORDEM.map(function (a) { return num(t.vigentes[a]) + ' ' + ACOES[a].rot; }).join(' · '),
            'decisão vigente de cada ligação no período, por status')
      + kpi('taxa de aprovação', pct(t.taxa_aprovacao), 'aprovadas ÷ ligações decididas')
      + kpi('tempo de avaliação', dur(t.tempo_mediano_s), 'mediana · média ' + dur(t.tempo_medio_s)
            + ' · ' + num(t.medidas) + ' medidas', 'da abertura do caso à decisão; decisão em lote não entra')
      + kpi('casos abertos', num(t.aberturas), num(t.ligacoes_abertas) + ' ligações diferentes')
      + kpi('pessoas ativas', num(d.ativos), 'última atividade ' + quando(t.ultima_atividade));
    pintaPessoas();
  }

  var COLS_PESSOA = [
    {k: 'nome', rot: 'pessoa'},
    {k: 'v_aprovar', rot: 'aprovadas', acao: 'aprovar', n: 1, grupo: 1},
    {k: 'v_campo', rot: 'campo', acao: 'campo', n: 1},
    {k: 'v_revisar', rot: 'revisão', acao: 'revisar', n: 1},
    {k: 'v_rejeitar', rot: 'rejeitadas', acao: 'rejeitar', n: 1},
    {k: 'vigentes_total', rot: 'vigentes', n: 1},
    {k: 'decisoes_total', rot: 'decisões', n: 1, grupo: 1},
    {k: 'taxa_aprovacao', rot: 'taxa de aprovação', n: 1},
    {k: 'tempo_mediano_s', rot: 'tempo (mediana)', n: 1},
    {k: 'aberturas', rot: 'aberturas', n: 1},
    {k: 'ultima_atividade', rot: 'última atividade', n: 1}
  ];

  function valorPessoa(u, k) {
    if (k.indexOf('v_') === 0) { return u.vigentes[k.slice(2)] || 0; }
    return u[k];
  }

  function pintaPessoas() {
    var lista = (G.resumo.usuarios || []).slice();
    var k = G.ordem, desc = G.desc;
    lista.sort(function (a, b) {
      var x = valorPessoa(a, k), y = valorPessoa(b, k);
      if (k === 'nome') { x = String(x || '').toLowerCase(); y = String(y || '').toLowerCase(); }
      if (x === null || x === undefined) { return 1; }
      if (y === null || y === undefined) { return -1; }
      return (x < y ? -1 : x > y ? 1 : 0) * (desc ? -1 : 1);
    });
    var cab = '<thead><tr>' + COLS_PESSOA.map(function (c) {
      return '<th class="ordena' + (c.n ? ' n' : '') + (c.grupo ? ' grupo' : '') + (G.ordem === c.k ? ' on' : '')
        + '" data-ordem="' + c.k + '" title="ordenar por ' + esc(c.rot) + (c.acao ? ' (decisões vigentes)' : '') + '">'
        + (c.acao ? '<span class="g-pt" style="background:' + ACOES[c.acao].cor + '"></span>' : '')
        + esc(c.rot) + (G.ordem === c.k ? (G.desc ? ' ↓' : ' ↑') : '') + '</th>';
    }).join('') + '<th></th></tr></thead>';
    var corpo = lista.map(function (u) {
      var id = u.id || '';
      var celula = function (v, classe) {
        return '<td class="n' + (classe ? ' ' + classe : '') + (!v ? ' zero' : '') + '">' + num(v) + '</td>';
      };
      return '<tr class="clica' + (G.pessoa === id && G.pessoa !== undefined ? ' on' : '') + '" data-pessoa="' + esc(id) + '">'
        + '<td class="g-pessoa" title="' + esc(u.email || '') + '"><b>' + esc(u.nome) + '</b><span>'
        + esc(NIVEIS[u.nivel] || u.nivel || 'fora da sua empresa') + (u.ativo === false ? ' · desativado' : '')
        + '</span></td>'
        + celula(u.vigentes.aprovar, 'grupo') + celula(u.vigentes.campo) + celula(u.vigentes.revisar)
        + celula(u.vigentes.rejeitar) + celula(u.vigentes_total)
        + '<td class="n grupo' + (!u.decisoes_total ? ' zero' : '') + '" title="' + esc(ORDEM.map(function (a) {
            return num(u.decisoes[a]) + ' ' + ACOES[a].rot; }).join(' · ') + ' · ' + num(u.em_lote) + ' em lote') + '">'
        + num(u.decisoes_total) + (u.em_lote ? '<span class="fraco"> · ' + num(u.em_lote) + ' lote</span>' : '') + '</td>'
        + '<td class="n' + (u.taxa_aprovacao === null ? ' zero' : '') + '">' + pct(u.taxa_aprovacao) + '</td>'
        + '<td class="n' + (u.tempo_mediano_s === null ? ' zero' : '') + '" title="média ' + esc(dur(u.tempo_medio_s))
        + ' · ' + num(u.medidas) + ' decisões medidas">' + dur(u.tempo_mediano_s) + '</td>'
        + '<td class="n' + (!u.aberturas ? ' zero' : '') + '" title="' + num(u.ligacoes_abertas) + ' ligações diferentes">'
        + num(u.aberturas) + '</td>'
        + '<td class="n' + (!u.ultima_atividade ? ' zero' : '') + '">' + quando(u.ultima_atividade) + '</td>'
        + '<td class="g-acoes-lin"><button class="lk" data-pessoa-abre="' + esc(id) + '">ligações →</button></td>'
        + '</tr>';
    }).join('');
    $('#g-pessoas').innerHTML = cab + '<tbody>' + corpo + '</tbody>';
    $('#g-pessoas-vazio').hidden = !!lista.length;
    $('#g-pessoas-vazio').textContent = 'Ninguém decidiu nem abriu caso neste recorte.';
  }

  /* ===================================================================
   * 2 · AS LIGAÇÕES DE UMA PESSOA (ou de todas)
   * =================================================================== */
  function abrePessoa(id, manterRolagem) {
    G.pessoa = id;
    G.linhas = [];
    G.cursor = null;
    var u = id ? (G.resumo.usuarios || []).filter(function (x) { return x.id === id; })[0] : null;
    $('#sec-ligacoes').hidden = false;
    $('#g-lig-tit').textContent = id ? 'Ligações de ' + ((u && u.nome) || 'uma pessoa') : 'Todas as decisões';
    $('#g-lig-sub').textContent = diaTxt(G.resumo.periodo.de) + ' a ' + diaTxt(G.resumo.periodo.ate)
      + (G.resumo.cidade ? ' · ' + G.resumo.cidade : '')
      + ' · da mais recente para trás · clique numa linha para ver o histórico da ligação';
    pintaStatus();
    pintaPessoas();
    carregaLigacoes(false);
    if (!manterRolagem) { $('#sec-ligacoes').scrollIntoView({behavior: 'smooth', block: 'start'}); }
  }

  function pintaStatus() {
    $('#g-lig-status').innerHTML = [''].concat(ORDEM).map(function (a) {
      return '<button class="bt p' + (G.filtroAcao === a ? ' on' : '') + '" data-status="' + a + '">'
        + (a ? '<span class="g-pt" style="background:' + ACOES[a].cor + '"></span>' + esc(ACOES[a].rot) : 'todas')
        + '</button>';
    }).join('');
    $('#g-lig-vigentes').checked = G.soVigentes;
  }

  function carregaLigacoes(mais) {
    if (G.carregandoLig) { return; }
    G.carregandoLig = true;
    var r = recorte();
    var alvo = G.pessoa;
    $('#g-lig-pe').innerHTML = '<span>carregando…</span>';
    fetch('/api/seek/gestao/ligacoes?' + qs({quem: alvo || '', de: r.de, ate: r.ate, cidade: r.cidade,
      empresa: r.empresa, acao: G.filtroAcao, vigentes: G.soVigentes ? 1 : '', limite: 200,
      cursor: mais ? G.cursor : ''})).then(lerJson).then(function (d) {
      if (alvo !== G.pessoa) { return; }
      G.linhas = (mais ? G.linhas : []).concat(d.linhas || []);
      G.cursor = d.cursor;
      pintaLigacoes();
    }).catch(function (e) {
      $('#g-lig-pe').innerHTML = '<span>A lista não carregou: ' + esc(e.message) + '</span>';
    }).then(function () { G.carregandoLig = false; });
  }

  function pintaLigacoes() {
    var todas = !G.pessoa;
    var cab = '<thead><tr><th>ligação</th><th>endereço</th><th>decisão</th><th>comentário</th>'
      + (todas ? '<th>quem</th>' : '') + '<th class="n">quando</th><th class="n">tempo</th><th>status hoje</th><th></th></tr></thead>';
    var corpo = G.linhas.map(function (l) {
      var hoje = l.vigente ? '<span class="fraco">vigente</span>'
        : '<span class="g-subst">substituída: ' + esc((ACOES[l.acao_vigente] || {}).um || l.acao_vigente || '—')
          + (l.vigente_por ? ' por ' + esc(l.vigente_por) : '') + '</span>';
      return '<tr class="clica" data-hist="' + esc(l.ligacao) + '" data-hist-emp="' + esc(l.id_empresa) + '">'
        + '<td class="mono">' + esc(l.ligacao) + '</td>'
        + '<td>' + esc(l.endereco || '—') + '<span class="fraco">' + (l.bairro ? ' · ' + esc(l.bairro) : '')
        + (l.cidade ? ' · ' + esc(l.cidade) : '') + '</span></td>'
        + '<td>' + chipAcao(l.acao) + (l.lote ? '<span class="fraco"> · lote</span>' : '') + '</td>'
        + '<td class="g-com' + (l.motivo ? '' : ' vazio-c') + '">' + esc(l.motivo || 'sem comentário') + '</td>'
        + (todas ? '<td>' + esc(l.quem_nome || '—') + '</td>' : '')
        + '<td class="n">' + quando(l.em) + '</td>'
        + '<td class="n' + (l.tempo_s === null ? ' zero' : '') + '">' + dur(l.tempo_s) + '</td>'
        + '<td>' + hoje + '</td>'
        + '<td class="g-acoes-lin"><button class="lk" data-hist-abre="' + esc(l.ligacao) + '" data-hist-emp="'
        + esc(l.id_empresa) + '">histórico</button><a class="lk" href="' + esc(linkSeek(l.ligacao))
        + '" target="_blank" rel="noopener" title="abrir a ficha na SEEK">SEEK ↗</a></td>'
        + '</tr>';
    }).join('');
    $('#g-ligacoes').innerHTML = cab + '<tbody>' + corpo + '</tbody>';
    $('#g-lig-vazio').hidden = !!G.linhas.length;
    $('#g-lig-vazio').textContent = 'Nenhuma decisão neste recorte.';
    $('#g-lig-pe').innerHTML = '<span>' + num(G.linhas.length) + ' decisão(ões)</span>'
      + (G.cursor ? '<button class="lk" data-mais="1">carregar mais</button>' : '');
  }

  function abreHistorico(lig, emp) {
    $('#modal-tit').textContent = 'Histórico · ligação ' + lig;
    $('#modal-corpo').innerHTML = '<div class="vazio">carregando…</div>';
    $('#modal-pe').innerHTML = '<a class="bt p" href="' + esc(linkSeek(lig)) + '" target="_blank" rel="noopener">'
      + 'abrir a ficha na SEEK ↗</a><span class="cresce"></span><button class="bt pri" data-fecha="1">fechar</button>';
    $('#modal .modal-caixa').classList.add('largo');
    $('#modal').hidden = false;
    var empresa = window.EU && window.EU.nivel === 'root' ? (emp || $('#g-empresa').value) : '';
    fetch('/api/seek/gestao/ligacao/' + encodeURIComponent(lig) + '?' + qs({empresa: empresa}))
      .then(lerJson).then(function (d) {
        var b = d.base || {};
        var ficha = '<div class="g-ficha">'
          + [['ligação', d.ligacao], ['endereço', b.endereco], ['bairro', b.bairro], ['cidade', b.cidade],
             ['cadastro', (b.qualificacao || '').replace(/_/g, ' ')]]
            .filter(function (p) { return p[1]; })
            .map(function (p) { return '<div><i>' + esc(p[0]) + '</i><b>' + esc(p[1]) + '</b></div>'; }).join('')
          + '</div>';
        var ia = d.ia ? '<div class="g-ia"><b>IA: ' + esc(VEREDITOS[d.ia.veredito] || d.ia.veredito || '—') + '</b>'
          + (d.ia.avaliado_em ? ' · julgada em ' + esc(quando(d.ia.avaliado_em)) : '')
          + (d.ia.motivo ? ' — ' + esc(d.ia.motivo) : '') + '</div>' : '';
        var trava = d.trava ? '<div class="g-trava">Em análise agora por <b>' + esc(d.trava.quem_nome || '—')
          + '</b>, desde ' + esc(quando(d.trava.desde)) + '.</div>' : '';
        var decisoes = (d.decisoes || []).map(function (x) {
          return '<div class="hist-lin"><i>' + esc(quando(x.em)) + '</i><div>'
            + '<div class="g-hl">' + chipAcao(x.acao) + '<b>' + esc(x.quem_nome || '—') + '</b>'
            + (x.tempo_s !== null ? '<em class="g-tempo">avaliou em ' + esc(dur(x.tempo_s)) + '</em>' : '')
            + (x.lote ? '<em class="g-tempo">lote ' + esc(String(x.lote).slice(0, 8)) + '</em>' : '') + '</div>'
            + '<p class="g-hl-com' + (x.motivo ? '' : ' vazio-c') + '">' + esc(x.motivo || 'sem comentário') + '</p>'
            + '</div></div>';
        }).join('') || '<div class="sem-dado">Nenhuma decisão registrada.</div>';
        var aberturas = (d.aberturas || []).slice(0, 30).map(function (x) {
          return '<div class="hist-lin"><i>' + esc(quando(x.aberta_em)) + '</i><div>' + esc(x.quem_nome || '—') + '</div></div>';
        }).join('');
        $('#modal-corpo').innerHTML = ficha + ia + trava
          + '<div class="g-hist-tit">mudanças de status · da mais recente para trás · ' + num((d.decisoes || []).length) + '</div>'
          + decisoes
          + (aberturas ? '<div class="g-hist-tit">quem abriu o caso · ' + num((d.aberturas || []).length) + '</div>' + aberturas : '');
      }).catch(function (e) {
        $('#modal-corpo').innerHTML = '<div class="sem-dado">O histórico não carregou: ' + esc(e.message) + '</div>';
      });
  }

  /* ===================================================================
   * 3 · EXPORTAÇÃO
   * =================================================================== */
  function pintaFiltrosExportacao() {
    var sel = $('#x-quem'), atual = sel.value;
    sel.innerHTML = '<option value="">todos</option>' + (G.resumo.usuarios || []).filter(function (u) { return u.id; })
      .map(function (u) { return '<option value="' + esc(u.id) + '">' + esc(u.nome) + '</option>'; }).join('');
    sel.value = atual;
    if (!$('#x-de').value) { $('#x-de').value = $('#g-de').value; }
    if (!$('#x-ate').value) { $('#x-ate').value = $('#g-ate').value; }
    var root = window.EU && window.EU.nivel === 'root';
    var emp = $('#g-empresa').value;
    var nome = emp ? ((G.resumo.empresas || []).filter(function (e) { return e.id === emp; })[0] || {}).nome : null;
    $('#x-empresa-nota').textContent = root ? 'Empresa: ' + (nome || 'todas as empresas')
      + ' (a do recorte no topo da página).' : '';
  }

  function pintaColunas(lista) {
    G.colunas = lista;
    $('#x-colunas').innerHTML = lista.map(function (c) {
      return '<label class="g-check"><input type="checkbox" data-coluna="' + esc(c.id) + '"'
        + (c.padrao ? ' checked' : '') + '> ' + esc(c.nome) + '</label>';
    }).join('');
  }
  function marcaColunas(modo) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-coluna]'), function (el) {
      var c = G.colunas.filter(function (x) { return x.id === el.getAttribute('data-coluna'); })[0] || {};
      el.checked = modo === 'todas' ? true : modo === 'nenhuma' ? false : !!c.padrao;
    });
  }

  function carregaExportacoes() {
    var r = recorte();
    return fetch('/api/seek/gestao/exportacoes?' + qs({empresa: r.empresa})).then(lerJson).then(function (d) {
      if (!G.colunas.length) { pintaColunas(d.colunas || []); }
      var nomeCol = {};
      (d.colunas || G.colunas).forEach(function (c) { nomeCol[c.id] = c.nome; });
      var pessoas = {};
      ((G.resumo && G.resumo.usuarios) || []).forEach(function (u) { if (u.id) { pessoas[u.id] = u.nome; } });
      var linhas = (d.exportacoes || []).map(function (x) {
        var f = x.filtros || {};
        var filtros = [diaTxt(f.de) + ' a ' + diaTxt(f.ate),
          f.quem ? 'usuário: ' + (pessoas[f.quem] || String(f.quem).slice(0, 8)) : 'todos os usuários',
          (f.acoes && f.acoes.length) ? 'status: ' + f.acoes.map(function (a) { return (ACOES[a] || {}).rot || a; }).join(', ') : 'todos os status',
          f.cidade ? 'cidade: ' + f.cidade : null, f.vigentes ? 'só vigentes' : null].filter(Boolean).join(' · ');
        var cols = (x.colunas || []).map(function (c) { return nomeCol[c] || c; });
        return '<tr><td class="n">' + quando(x.em) + '</td><td>' + esc(x.quem_nome || '—') + '</td>'
          + (window.EU && window.EU.nivel === 'root' ? '<td>' + esc(x.empresa || '—') + '</td>' : '')
          + '<td class="mono">' + esc(String(x.formato).toUpperCase()) + '</td><td class="n">' + num(x.linhas) + '</td>'
          + '<td class="g-com">' + esc(filtros) + '</td>'
          + '<td title="' + esc(cols.join(', ')) + '">' + num(cols.length) + ' coluna(s)<span class="fraco"> · '
          + esc(cols.slice(0, 3).join(', ') + (cols.length > 3 ? '…' : '')) + '</span></td>'
          + '<td class="mono">' + esc(x.ip || '—') + '</td></tr>';
      }).join('');
      $('#x-lista').innerHTML = '<thead><tr><th class="n">quando</th><th>quem</th>'
        + (window.EU && window.EU.nivel === 'root' ? '<th>empresa</th>' : '')
        + '<th>formato</th><th class="n">linhas</th><th>filtros</th><th>colunas</th><th>IP</th></tr></thead><tbody>'
        + linhas + '</tbody>';
      $('#x-lista-vazio').hidden = !!(d.exportacoes || []).length;
      $('#x-lista-vazio').textContent = 'Nenhuma exportação registrada ainda.';
    }).catch(function (e) {
      $('#x-lista-vazio').hidden = false;
      $('#x-lista-vazio').textContent = 'A lista de exportações não carregou: ' + e.message;
    });
  }

  function exporta() {
    if (G.exportando) { return; }
    var colunas = Array.prototype.filter.call(document.querySelectorAll('[data-coluna]'), function (el) { return el.checked; })
      .map(function (el) { return el.getAttribute('data-coluna'); });
    if (!colunas.length) { torrada('escolha ao menos uma coluna', true); return; }
    var formato = (document.querySelector('input[name="x-formato"]:checked') || {}).value || 'xlsx';
    var corpo = {
      formato: formato, de: $('#x-de').value || null, ate: $('#x-ate').value || null,
      empresa: $('#g-empresa').value || null, quem: $('#x-quem').value || null,
      acoes: Array.prototype.filter.call(document.querySelectorAll('[data-x-acao]'), function (el) { return el.checked; })
        .map(function (el) { return el.getAttribute('data-x-acao'); }),
      cidade: $('#x-cidade').value || null, vigentes: $('#x-vigentes').checked, colunas: colunas
    };
    var bt = $('#x-exporta');
    G.exportando = true;
    bt.disabled = true;
    bt.textContent = 'gerando o arquivo…';
    fetch('/api/seek/gestao/exportar', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(corpo)
    }).then(function (r) {
      if (!r.ok) { return lerJson(r); }
      var linhas = r.headers.get('X-Exportacao-Linhas');
      var nome = (/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') || '') || [])[1]
        || 'seek_decisoes.' + formato;
      return r.blob().then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = nome;
        document.body.appendChild(a);
        a.click();
        window.setTimeout(function () { URL.revokeObjectURL(url); a.parentNode.removeChild(a); }, 2000);
        torrada('Exportação registrada: ' + num(linhas) + ' linha(s) em ' + formato.toUpperCase());
        carregaExportacoes();
      });
    }).catch(function (e) {
      torrada('A exportação não saiu: ' + e.message, true);
    }).then(function () {
      G.exportando = false;
      bt.disabled = false;
      bt.textContent = 'exportar';
    });
  }

  /* ===================================================================
   * 4 · EVENTOS E PARTIDA
   * =================================================================== */
  function periodo(dias) {
    var hoje = new Date(), ini = new Date();
    ini.setDate(hoje.getDate() - (dias - 1));
    $('#g-de').value = diaIso(ini);
    $('#g-ate').value = diaIso(hoje);
  }

  function liga() {
    $('#chip-sair').addEventListener('click', function () {
      ['cr_token', 'cr_refresh', 'cr_expira'].forEach(function (k) {
        try { window.sessionStorage.removeItem(k); } catch (e) { /* aba sem armazenamento */ }
      });
      window.EU = null;
      window.location.reload();
    });
    $('#g-aplica').addEventListener('click', function () { carregaResumo(); carregaExportacoes(); });
    $('#g-empresa').addEventListener('change', function () {
      G.pessoa = undefined;
      $('#sec-ligacoes').hidden = true;
      $('#x-quem').value = '';
      carregaResumo();
      carregaExportacoes();
    });
    $('#g-cidade').addEventListener('change', carregaResumo);
    ['#g-de', '#g-ate'].forEach(function (id) {
      $(id).addEventListener('change', function () {
        $('#x-de').value = $('#g-de').value;
        $('#x-ate').value = $('#g-ate').value;
        carregaResumo();
      });
    });
    $('#g-todas').addEventListener('click', function () { abrePessoa(null); });
    $('#g-lig-fecha').addEventListener('click', function () {
      G.pessoa = undefined;
      $('#sec-ligacoes').hidden = true;
      pintaPessoas();
    });
    $('#g-lig-vigentes').addEventListener('change', function () {
      G.soVigentes = this.checked;
      carregaLigacoes(false);
    });
    $('#x-exporta').addEventListener('click', exporta);
    $('#x-recarrega').addEventListener('click', carregaExportacoes);
    $('#x-col-padrao').addEventListener('click', function () { marcaColunas('padrao'); });
    $('#x-col-todas').addEventListener('click', function () { marcaColunas('todas'); });
    $('#x-col-nenhuma').addEventListener('click', function () { marcaColunas('nenhuma'); });

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !$('#modal').hidden) { $('#modal').hidden = true; }
    });
    document.addEventListener('click', function (ev) {
      var t = ev.target.closest ? ev.target : null;
      if (!t) { return; }
      var dd = t.closest('[data-dias]');
      if (dd) {
        periodo(+dd.getAttribute('data-dias'));
        $('#x-de').value = $('#g-de').value;
        $('#x-ate').value = $('#g-ate').value;
        carregaResumo();
        return;
      }
      var or = t.closest('[data-ordem]');
      if (or) {
        var k = or.getAttribute('data-ordem');
        if (G.ordem === k) { G.desc = !G.desc; } else { G.ordem = k; G.desc = k !== 'nome'; }
        pintaPessoas();
        return;
      }
      if (t.closest('a[href]') && !t.closest('[data-fecha]')) { return; }
      var ha = t.closest('[data-hist-abre]') || t.closest('[data-hist]');
      if (ha) {
        abreHistorico(ha.getAttribute('data-hist-abre') || ha.getAttribute('data-hist'), ha.getAttribute('data-hist-emp'));
        return;
      }
      var pa = t.closest('[data-pessoa-abre]') || t.closest('[data-pessoa]');
      if (pa) {
        var id = pa.getAttribute('data-pessoa-abre');
        if (id === null) { id = pa.getAttribute('data-pessoa'); }
        abrePessoa(id || null);
        return;
      }
      var st = t.closest('[data-status]');
      if (st) {
        G.filtroAcao = st.getAttribute('data-status');
        pintaStatus();
        carregaLigacoes(false);
        return;
      }
      if (t.closest('[data-mais]')) { carregaLigacoes(true); return; }
      if (t.closest('[data-fecha]') || t.closest('#modal-x') || t.closest('.modal-fundo')) {
        $('#modal').hidden = true;
      }
    });
  }

  function mostra(eu) {
    $('#quem-nome').textContent = eu.nome || eu.email || '—';
    $('#quem-sub').textContent = (NIVEIS[eu.nivel] || eu.nivel || '—') + (eu.empresa ? ' · ' + eu.empresa : '');
    if (['admin', 'root'].indexOf(eu.nivel) < 0) {
      $('#g-aviso').hidden = false;
      $('#g-aviso').textContent = 'A gestão das aprovações é para administrador ou acima. '
        + 'Volte para a SEEK pelo botão no topo.';
      Array.prototype.forEach.call(document.querySelectorAll('#gestao section, #g-barra'), function (s) { s.hidden = true; });
      return;
    }
    $('#x-acoes').innerHTML = ORDEM.map(function (a) {
      return '<label class="g-check"><input type="checkbox" data-x-acao="' + a + '"> <span class="g-pt" style="background:'
        + ACOES[a].cor + '"></span>' + esc(ACOES[a].rot) + '</label>';
    }).join('');
    periodo(30);
    $('#x-de').value = $('#g-de').value;
    $('#x-ate').value = $('#g-ate').value;
    carregaResumo().then(carregaExportacoes);
  }

  function inicia() {
    liga();
    var foi = false;
    function vai(eu) {
      if (foi || !eu) { return; }
      foi = true;
      mostra(eu);
    }
    document.addEventListener('cr:sessao', function (ev) { vai(ev.detail); });
    if (window.EU) { vai(window.EU); }
  }

  return {inicia: inicia, estado: function () { return G; }};
}());

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', GESTAO.inicia);
} else { GESTAO.inicia(); }
