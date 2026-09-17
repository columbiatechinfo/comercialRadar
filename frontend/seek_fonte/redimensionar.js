/* redimensionar.js — AS COLUNAS DA SEEK MUDAM DE LARGURA (dono do produto, 16/09/2026)
 *
 * Um puxador entre as colunas: arrastar muda a largura (instalações, filtros e cérebro; a árvore fica com o resto),
 * duplo clique volta ao padrão, e com o foco nele as setas mexem de 16 em 16 px. A largura vai para a variável CSS da
 * coluna no <html> — a mesma que o CSS já usa —, e fica guardada neste navegador. Sem o localStorage (janela privada),
 * funciona igual e só não lembra. O cérebro redesenha sozinho (ResizeObserver); a lista ouve o `resize` do fim. */
(function () {
  'use strict';
  var CHAVE = 'seek.colunas';
  var MIN_ARVORE = 320;
  var COLUNAS = [
    {id: 'fila', variavel: '--fila', min: 180, max: 560, lado: 1, rotulo: 'largura da lista de instalações'},
    {id: 'filtros', variavel: '--filtros', min: 160, max: 480, lado: 1, rotulo: 'largura da coluna de filtros'},
    {id: 'lado', variavel: '--lado', min: 200, max: 640, lado: -1, rotulo: 'largura do painel do cérebro'}
  ];
  var raiz = document.documentElement;

  function lerGuardado() {
    try { return JSON.parse(window.localStorage.getItem(CHAVE) || '{}') || {}; } catch (e) { return {}; }
  }
  function guardar(v) {
    try { window.localStorage.setItem(CHAVE, JSON.stringify(v)); } catch (e) { /* sem armazenamento: só não lembra */ }
  }
  var guardado = lerGuardado();

  function largura(el) { return el ? el.getBoundingClientRect().width : 0; }

  function aplicar(col, px, persistir) {
    var arvore = document.getElementById('arvore-caixa');
    var el = document.getElementById(col.id);
    // a árvore nunca fica mais estreita que MIN_ARVORE: o que a coluna ganha sai dela
    var folga = largura(arvore) + largura(el) - MIN_ARVORE;
    var v = Math.round(Math.max(col.min, Math.min(col.max, folga, px)));
    raiz.style.setProperty(col.variavel, v + 'px');
    if (persistir) { guardado[col.id] = v; guardar(guardado); }
    return v;
  }

  function padrao(col) {
    raiz.style.removeProperty(col.variavel);
    delete guardado[col.id];
    guardar(guardado);
    window.dispatchEvent(new Event('resize'));
  }

  function puxador(col) {
    var el = document.getElementById(col.id);
    if (!el) { return; }
    var p = document.createElement('div');
    p.className = 'col-puxador';
    p.setAttribute('role', 'separator');
    p.setAttribute('aria-orientation', 'vertical');
    p.setAttribute('aria-label', col.rotulo);
    p.setAttribute('tabindex', '0');
    p.title = 'arraste para mudar a largura · duplo clique volta ao padrão';
    p.dataset.coluna = col.id;
    // à direita da coluna que cresce para a direita; à esquerda do painel do cérebro
    el.parentNode.insertBefore(p, col.lado > 0 ? el.nextSibling : el);

    var x0 = 0, w0 = 0, quadro = 0, ultimo = 0;
    p.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0) { return; }
      ev.preventDefault();
      x0 = ev.clientX; w0 = largura(el); ultimo = w0;
      p.setPointerCapture(ev.pointerId);
      p.classList.add('ativo');
      document.body.classList.add('arrastando-coluna');
    });
    p.addEventListener('pointermove', function (ev) {
      if (!p.hasPointerCapture(ev.pointerId)) { return; }
      var alvo = w0 + (ev.clientX - x0) * col.lado;
      if (quadro) { return; }
      quadro = window.requestAnimationFrame(function () { quadro = 0; ultimo = aplicar(col, alvo, false); });
    });
    function soltar(ev) {
      if (!p.hasPointerCapture(ev.pointerId)) { return; }
      p.releasePointerCapture(ev.pointerId);
      p.classList.remove('ativo');
      document.body.classList.remove('arrastando-coluna');
      aplicar(col, largura(el) || ultimo, true);
      window.dispatchEvent(new Event('resize'));
    }
    p.addEventListener('pointerup', soltar);
    p.addEventListener('pointercancel', soltar);
    p.addEventListener('dblclick', function () { padrao(col); });
    p.addEventListener('keydown', function (ev) {
      var passo = ev.key === 'ArrowRight' ? 16 : ev.key === 'ArrowLeft' ? -16 : 0;
      if (!passo) { return; }
      ev.preventDefault();
      aplicar(col, largura(el) + passo * col.lado, true);
      window.dispatchEvent(new Event('resize'));
    });
  }

  function iniciar() {
    COLUNAS.forEach(function (col) {
      if (guardado[col.id]) { raiz.style.setProperty(col.variavel, guardado[col.id] + 'px'); }
      puxador(col);
    });
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', iniciar); } else { iniciar(); }
})();
