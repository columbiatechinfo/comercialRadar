/* ==== 99-validacao.js ==== */
/* 99-validacao.js — a validação das cidades e áreas (16/09/2026)
 *
 * Mostra cada validação como uma grade lote × etapa, na ordem das etapas, com o estado de cada tarefa e — ao clicar
 * — quem pegou, quantas tentativas, o placar do log e o erro. Atualiza sozinha a cada 10 s enquanto a aba está à
 * vista. Criar, pausar, retomar e cancelar é para administrador ou acima; o servidor confere (403).
 */
var VALIDACAO = (function () {
  'use strict';

  var ESTADOS = {
    espera: 'espera', fila: 'na fila', rodando: 'rodando', ok: 'feita', erro: 'erro', cancelada: 'cancelada'
  };
  var ROTULOS = {
    fichas: 'Fichas do Maps', storage: 'Fotos', frente: 'Foto de rua', leitura: 'Leitura das placas',
    cnpj: 'Serasa', busca: 'Busca web', conferencia: 'Conferência', julgamento: 'Julgamento'
  };
  var RECURSOS = {google: 'Google · proxy', cnpj: 'navegador', busca: 'navegador', spark: 'IA', local: 'local'};
  var ESTADO_VAL = {
    preparando: 'preparando', rodando: 'rodando', pausada: 'pausada', ok: 'concluída', erro: 'com erro',
    cancelada: 'cancelada'
  };
  var NIVEIS = {user: 'usuário', editor: 'editor', supervisor: 'supervisor', admin: 'administrador', root: 'suporte'};

  var V = {dados: null, admin: false, relogio: null, pedido: 0};

  function $(s) { return document.querySelector(s); }
  function esc(t) {
    return String(t === null || t === undefined ? '' : t)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function num(n) { return (+n || 0).toLocaleString('pt-BR'); }
  function dois(n) { return (n < 10 ? '0' : '') + n; }
  function quando(iso) {
    if (!iso) { return '—'; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return String(iso); }
    return dois(d.getDate()) + '/' + dois(d.getMonth() + 1) + ' ' + dois(d.getHours()) + ':' + dois(d.getMinutes());
  }
  function duracao(a, b) {
    if (!a) { return '—'; }
    var s = Math.max(0, Math.round(((b ? new Date(b) : new Date()) - new Date(a)) / 1000));
    if (s < 60) { return s + ' s'; }
    if (s < 3600) { return Math.floor(s / 60) + ' min'; }
    return Math.floor(s / 3600) + ' h ' + dois(Math.floor((s % 3600) / 60)) + ' min';
  }
  function torrada(msg, ruim) {
    var d = document.createElement('div');
    d.className = 't' + (ruim ? ' ruim' : '');
    d.textContent = msg;
    $('#torradas').appendChild(d);
    window.setTimeout(function () { d.classList.add('vai'); }, ruim ? 6000 : 3400);
    window.setTimeout(function () { if (d.parentNode) { d.parentNode.removeChild(d); } }, ruim ? 6500 : 3900);
  }
  function lerJson(r) {
    return r.json().catch(function () { return {}; }).then(function (b) {
      if (!r.ok) {
        throw new Error((b && (b.erro || (typeof b.detail === 'string' ? b.detail : ''))) || ('HTTP ' + r.status));
      }
      return b;
    });
  }

  /* ---- a lista ---- */
  function carrega() {
    var pedido = ++V.pedido;
    return fetch('/api/validacoes?limite=20').then(lerJson).then(function (d) {
      if (pedido !== V.pedido) { return; }
      V.dados = d;
      $('#v-aviso').hidden = true;
      var dev = d.ambiente === 'desenvolvimento';
      $('#v-amb').hidden = !dev;
      $('#v-amb').textContent = 'desenvolvimento';
      document.body.classList.toggle('em-homolog', dev);
      pinta();
    }).catch(function (e) {
      $('#v-aviso').hidden = false;
      $('#v-aviso').textContent = 'Não consegui ler as validações: ' + e.message;
    });
  }

  function legenda() {
    $('#v-legenda').innerHTML = Object.keys(ESTADOS).map(function (k) {
      return '<span class="v-cel ' + k + '">' + esc(ESTADOS[k]) + '</span>';
    }).join('');
  }

  function pinta() {
    var d = V.dados;
    if (!d.validacoes.length) {
      $('#v-lista').innerHTML = '<p class="v-vazio">Nenhuma validação ainda. '
        + (V.admin ? 'Comece uma acima, ou ao fim de uma extração.' : 'Um administrador pode começar uma.') + '</p>';
      return;
    }
    $('#v-lista').innerHTML = d.validacoes.map(function (v) { return cartao(v, d.etapas); }).join('');
  }

  function cartao(v, etapas) {
    var feitas = v.tarefas ? Math.round(100 * v.tarefas_feitas / v.tarefas) : 0;
    var acoes = '';
    if (V.admin) {
      if (v.estado === 'rodando') { acoes += '<button class="bt p" data-acao="pausar" data-id="' + v.id + '">pausar</button>'; }
      if (v.estado === 'pausada') { acoes += '<button class="bt p" data-acao="retomar" data-id="' + v.id + '">retomar</button>'; }
      if (['rodando', 'pausada', 'preparando'].indexOf(v.estado) >= 0) {
        acoes += '<button class="bt p" data-acao="cancelar" data-id="' + v.id + '">cancelar</button>';
      }
    }
    var ordem = Object.keys(v.ordem || {}).map(function (k) {
      return '<span class="' + (k.indexOf('fora') === 0 || k.indexOf('aptas') === 0 ? 'fora' : '') + '">'
        + esc(k) + ' · ' + num(v.ordem[k]) + '</span>';
    }).join('');
    var cab = '<tr><th></th>' + etapas.map(function (e) {
      return '<th class="' + (e.ia ? 'ia' : '') + '" title="depende de: ' + esc(e.depende.map(function (x) {
        return ROTULOS[x] || x; }).join(', ') || 'nada') + '"><b>' + esc(ROTULOS[e.id] || e.id) + '</b>'
        + esc(RECURSOS[e.recurso] || e.recurso) + '</th>';
    }).join('') + '</tr>';
    var linhas = v.lotes.map(function (l) {
      return '<tr><td class="lote">lote ' + dois(l.lote + 1) + '</td>' + etapas.map(function (e) {
        var t = l.etapas[e.id] || {};
        var txt = ESTADOS[t.estado] || '—';
        if (t.estado === 'rodando') { txt = duracao(t.iniciado_em); }
        if (t.estado === 'ok' && t.tentativas > 1) { txt += ' · ' + t.tentativas + 'x'; }
        return '<td><button class="v-cel ' + esc(t.estado || '') + '" data-v="' + v.id + '" data-l="' + l.lote
          + '" data-e="' + e.id + '" title="' + esc((t.worker ? t.worker + ' · ' : '') + (t.resumo || t.erro || ''))
          + '">' + esc(txt) + '</button></td>';
      }).join('') + '</tr>';
    }).join('');
    var soma = v.lotes.length > 1 ? '<tr class="v-soma"><td></td>' + etapas.map(function (e) {
      var c = v.por_etapa[e.id] || {};
      return '<td>' + num(c.ok || 0) + '/' + num(v.lotes.length) + '</td>';
    }).join('') + '</tr>' : '';
    return '<div class="v-val">'
      + '<div class="v-cab"><div class="v-tit"><strong>' + esc(v.cidade) + (v.area ? ' · ' + esc(v.area) : '')
      + '</strong><span>#' + v.id + ' · ' + num(v.ligacoes) + ' ligações · ' + v.lotes.length + ' lote'
      + (v.lotes.length === 1 ? '' : 's') + ' · começou ' + quando(v.criado_em)
      + (v.terminado_em ? ' · terminou ' + quando(v.terminado_em) : ' · há ' + duracao(v.criado_em))
      + ' · ' + feitas + '% das tarefas</span></div>'
      + '<span class="v-estado ' + esc(v.estado) + '">' + esc(ESTADO_VAL[v.estado] || v.estado) + '</span>'
      + (v.erro ? '<span class="v-estado erro">' + esc(v.erro) + '</span>' : '')
      + '<div class="v-acoes">' + acoes + '</div></div>'
      + '<div class="v-barra"><i style="width:' + feitas + '%"></i></div>'
      + '<div class="v-ordem">' + ordem + '</div>'
      + '<div class="v-grade-caixa"><table class="v-grade"><thead>' + cab + '</thead><tbody>' + linhas + soma
      + '</tbody></table></div></div>';
  }

  /* ---- o detalhe de uma tarefa ---- */
  function detalhe(vid, lote, etapa) {
    var v = (V.dados.validacoes || []).filter(function (x) { return String(x.id) === String(vid); })[0];
    if (!v) { return; }
    var l = v.lotes.filter(function (x) { return String(x.lote) === String(lote); })[0] || {etapas: {}};
    var t = l.etapas[etapa] || {};
    $('#modal-tit').textContent = (ROTULOS[etapa] || etapa) + ' · lote ' + dois(+lote + 1) + ' · ' + v.cidade;
    var linha = function (rot, val, mono) {
      return '<dt>' + esc(rot) + '</dt><dd' + (mono ? ' class="mono"' : '') + '>' + esc(val || '—') + '</dd>';
    };
    $('#modal-corpo').innerHTML = '<dl class="v-det">'
      + linha('estado', ESTADOS[t.estado] || t.estado)
      + linha('máquina', t.worker)
      + linha('tentativas', t.tentativas)
      + linha('começou', quando(t.iniciado_em))
      + linha('terminou', quando(t.terminado_em))
      + linha('duração', t.iniciado_em ? duracao(t.iniciado_em, t.terminado_em) : '')
      + linha('placar', t.resumo, true)
      + (t.erro ? linha('erro', t.erro, true) : '')
      + '</dl>';
    $('#modal-pe').innerHTML = '<button class="bt p" data-fecha>fechar</button>';
    $('#modal').hidden = false;
  }

  function acao(id, qual, bt) {
    if (qual === 'cancelar' && !window.confirm('Cancelar a validação #' + id + '? O que está rodando é derrubado.')) {
      return;
    }
    bt.disabled = true;
    fetch('/api/validacoes/' + id + '/' + qual, {method: 'POST'}).then(lerJson).then(function () {
      torrada({pausar: 'Pausada', retomar: 'Retomada', cancelar: 'Cancelada'}[qual] + ' · validação #' + id);
      return carrega();
    }).catch(function (e) {
      bt.disabled = false;
      torrada('Não foi possível ' + qual + ': ' + e.message, true);
    });
  }

  function criar(ev) {
    ev.preventDefault();
    var bt = $('#v-criar');
    bt.disabled = true;
    bt.textContent = 'criando…';
    fetch('/api/validacoes', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cidade: $('#v-cidade').value.trim(), area: $('#v-area').value.trim() || null,
                            refazer: $('#v-refazer').checked})
    }).then(lerJson).then(function (d) {
      torrada(d.mensagem || 'Validação criada', !d.id);
      return carrega();
    }).catch(function (e) {
      torrada('Não criei: ' + e.message, true);
    }).then(function () {
      bt.disabled = false;
      bt.textContent = 'validar';
    });
  }

  /* ---- eventos e partida ---- */
  function liga() {
    $('#chip-sair').addEventListener('click', function () {
      ['cr_token', 'cr_refresh', 'cr_expira'].forEach(function (k) {
        try { window.sessionStorage.removeItem(k); } catch (e) { /* aba sem armazenamento */ }
      });
      window.EU = null;
      window.location.reload();
    });
    $('#v-atualiza').addEventListener('click', carrega);
    $('#v-form').addEventListener('submit', criar);
    document.addEventListener('click', function (ev) {
      var t = ev.target;
      var cel = t.closest('.v-cel[data-v]');
      if (cel) { detalhe(cel.getAttribute('data-v'), cel.getAttribute('data-l'), cel.getAttribute('data-e')); return; }
      var a = t.closest('[data-acao]');
      if (a) { acao(a.getAttribute('data-id'), a.getAttribute('data-acao'), a); return; }
      if (t.closest('[data-fecha]') || t.closest('#modal-x') || t.closest('.modal-fundo')) { $('#modal').hidden = true; }
    });
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { $('#modal').hidden = true; } });
  }

  function mostra(eu) {
    $('#quem-nome').textContent = eu.nome || eu.email || '—';
    $('#quem-sub').textContent = (NIVEIS[eu.nivel] || eu.nivel || '—') + (eu.empresa ? ' · ' + eu.empresa : '');
    V.admin = ['admin', 'root'].indexOf(eu.nivel) >= 0;
    $('#v-nova').hidden = !V.admin;
    legenda();
    carrega();
    V.relogio = window.setInterval(function () {
      if (!document.hidden && $('#modal').hidden) { carrega(); }
    }, 10000);
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

  return {inicia: inicia, estado: function () { return V; }};
}());

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', VALIDACAO.inicia);
} else { VALIDACAO.inicia(); }
