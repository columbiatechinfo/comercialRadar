/* mapa_ligacoes.js — os hidrômetros da cidade, desenhados na GPU.
 *
 * POR QUE ESTE ARQUIVO EXISTE. Até 08/09/2026 o mapa mostrava POIs, com um
 * marcador DOM por ponto e agrupamento por proximidade. O dono do produto
 * mudou o sujeito: "no mapa os pontos exibidos devem ser as instalações da
 * cidade em questão". Faz sentido — é a instalação que fatura errado; o POI é
 * testemunha sobre ela.
 *
 * E ISSO QUEBRA A TÉCNICA ANTERIOR. Canoas tem 102.131 ligações, todas com
 * coordenada. Marcador DOM morre nessa ordem de grandeza: são 102 mil
 * elementos, cada um com seus ouvintes de evento. Canvas 2D também não resolve
 * — 102 mil `drawImage` por quadro passa longe de tempo real.
 *
 * WebGL resolve porque desenha os cem mil numa chamada só, com a posição e o
 * índice do sprite vindo de buffers que a GPU já tem. O deck.gl faz isso e
 * sincroniza com o Leaflet, que continua cuidando do tile, do desenho de área
 * e do zoom.
 *
 * NÃO USA O EMBRULHO `deck.gl-leaflet`: a versão publicada não responde no
 * unpkg (404, conferido). São vinte linhas sincronizar a vista na mão, e uma
 * dependência a menos é uma coisa a menos para quebrar.
 */
(function (global) {
  "use strict";

  // ── as cores, no padrão do painel ──────────────────────────────────────
  //
  // VERDE É "A EXTRAÇÃO ACHOU ALGUÉM AQUI"; cinza é "não achou". Não é
  // aprovação nem reprovação — isso quem diz é o símbolo de cima.
  const VERDE = "#15803d";
  const CINZA = "#94a3b8";
  const VERMELHO = "#dc2626";
  const AMBAR = "#d97706";
  const DOURADO = "#eab308";

  const LADO = 64;          // o sprite é desenhado grande e reduzido na tela
  const CATEGORIA = { RESIDENCIAL: 0 };   // 0 = casa; o resto é prédio

  /* Casa e prédio em traço simples, no espírito do resto da interface.
   *
   * DESENHADO EM CAMINHO, e não em fonte de ícone: o sprite precisa virar
   * pixel numa textura, e converter fonte para textura daria o mesmo trabalho
   * com uma dependência a mais. */
  function desenharCasa(ctx, cor) {
    ctx.strokeStyle = cor;
    ctx.fillStyle = cor + "22";
    ctx.lineWidth = 4;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(10, 30); ctx.lineTo(32, 12); ctx.lineTo(54, 30);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(16, 28); ctx.lineTo(16, 52); ctx.lineTo(48, 52); ctx.lineTo(48, 28);
    ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.beginPath();                       // a porta, para ler como casa
    ctx.moveTo(28, 52); ctx.lineTo(28, 40); ctx.lineTo(37, 40); ctx.lineTo(37, 52);
    ctx.stroke();
  }

  function desenharPredio(ctx, cor) {
    ctx.strokeStyle = cor;
    ctx.fillStyle = cor + "22";
    ctx.lineWidth = 4;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.rect(16, 12, 32, 40);
    ctx.fill(); ctx.stroke();
    ctx.lineWidth = 3;
    for (let y = 20; y <= 40; y += 10) {   // as janelas, que separam de casa
      ctx.beginPath();
      ctx.moveTo(23, y); ctx.lineTo(29, y);
      ctx.moveTo(35, y); ctx.lineTo(41, y);
      ctx.stroke();
    }
  }

  function desenharEstrela(ctx) {
    ctx.fillStyle = DOURADO;
    ctx.strokeStyle = "#78350f";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    for (let i = 0; i < 10; i++) {
      const r = i % 2 ? 9 : 20;
      const a = (Math.PI / 5) * i - Math.PI / 2;
      const x = 32 + r * Math.cos(a), y = 32 + r * Math.sin(a);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.closePath();
    ctx.fill(); ctx.stroke();
  }

  function desenharX(ctx) {
    ctx.strokeStyle = VERMELHO;
    ctx.lineWidth = 8;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(18, 18); ctx.lineTo(46, 46);
    ctx.moveTo(46, 18); ctx.lineTo(18, 46);
    ctx.stroke();
  }

  function desenharAtencao(ctx) {
    ctx.fillStyle = AMBAR;
    ctx.strokeStyle = "#78350f";
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(32, 12); ctx.lineTo(54, 50); ctx.lineTo(10, 50);
    ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.strokeStyle = "#451a03";
    ctx.lineWidth = 5;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(32, 24); ctx.lineTo(32, 36);
    ctx.moveTo(32, 43); ctx.lineTo(32, 43.5);
    ctx.stroke();
  }

  /* O ATLAS É UM DESENHO SÓ, e não sete imagens.
   *
   * A GPU troca de textura devagar; com um atlas ela liga a textura uma vez e
   * cada ponto diz de qual retângulo dela quer o pixel. É por isso que cem mil
   * marcadores diferentes custam o mesmo que cem mil iguais. */
  function montarAtlas() {
    const partes = [
      ["casa_verde", (c) => desenharCasa(c, VERDE)],
      ["casa_cinza", (c) => desenharCasa(c, CINZA)],
      ["predio_verde", (c) => desenharPredio(c, VERDE)],
      ["predio_cinza", (c) => desenharPredio(c, CINZA)],
      ["estrela", desenharEstrela],
      ["x", desenharX],
      ["atencao", desenharAtencao],
    ];
    const cv = document.createElement("canvas");
    cv.width = LADO * partes.length;
    cv.height = LADO;
    const ctx = cv.getContext("2d");
    const mapping = {};
    partes.forEach(([nome, fn], i) => {
      ctx.save();
      ctx.translate(i * LADO, 0);
      fn(ctx);
      ctx.restore();
      mapping[nome] = { x: i * LADO, y: 0, width: LADO, height: LADO,
                        anchorY: LADO, mask: false };
    });
    return { url: cv.toDataURL("image/png"), mapping };
  }

  // ── a leitura de cada linha ────────────────────────────────────────────
  //
  // A resposta vem posicional para caber em 4,7 MB; estes índices são o
  // contrato com `server.listar_ligacoes`. Mudou lá, muda aqui.
  const I = { LIG: 0, LAT: 1, LNG: 2, CAT: 3, SIT: 4, VINC: 5, VER: 6 };
  const ATIVA = 0;
  const VER_APROVADO = [1, 2], VER_REVISAO = 3, VER_REPROVADO = 4;

  function simboloBase(l) {
    const casa = l[I.CAT] === CATEGORIA.RESIDENCIAL;
    const achou = l[I.VINC] === 1;
    return (casa ? "casa_" : "predio_") + (achou ? "verde" : "cinza");
  }

  function simboloTopo(l) {
    const v = l[I.VER];
    if (VER_APROVADO.indexOf(v) >= 0) return "estrela";
    if (v === VER_REPROVADO) return "x";
    if (v === VER_REVISAO) return "atencao";
    return null;
  }

  /* A LIGAÇÃO QUE NÃO FATURA FICA APAGADA, e não escondida.
   *
   * Contorno tracejado não existe por instância num IconLayer — a alternativa
   * honesta é a opacidade. E esconder seria pior: uma ligação CORTADA com
   * comércio funcionando é achado forte, pode ser ligação clandestina, e
   * sumir com ela apagaria isso da tela. */
  function corDe(l) {
    return l[I.SIT] === ATIVA ? [255, 255, 255, 255] : [255, 255, 255, 110];
  }

  // ── a camada ───────────────────────────────────────────────────────────
  function MapaLigacoes(mapa, aoClicar) {
    this.mapa = mapa;
    this.aoClicar = aoClicar;
    this.linhas = [];
    this.filtro = null;
    this.atlas = montarAtlas();

    // O CANVAS VIVE NUM PANE DO LEAFLET, e não solto sobre o mapa: assim ele
    // herda a ordem de empilhamento, e o desenho de área continua por cima.
    const pane = mapa.createPane("paneLigacoes");
    pane.style.zIndex = 620;
    pane.style.pointerEvents = "none";
    const cv = document.createElement("canvas");
    cv.style.position = "absolute";
    cv.style.left = cv.style.top = "0";
    cv.style.pointerEvents = "auto";
    pane.appendChild(cv);
    this.canvas = cv;

    this.deck = new deck.Deck({
      canvas: cv,
      controller: false,          // quem controla a vista é o Leaflet
      initialViewState: this._vista(),
      layers: [],
      getCursor: () => "pointer",
      onClick: (info) => {
        if (info && info.object) this.aoClicar(info.object[I.LIG]);
      },
    });

    const sincronizar = () => this._sincronizar();
    mapa.on("move zoom moveend zoomend resize viewreset", sincronizar);
    this._sincronizar();
  }

  MapaLigacoes.prototype._vista = function () {
    const c = this.mapa.getCenter();
    const t = this.mapa.getSize();
    this.canvas.width = t.x;
    this.canvas.height = t.y;
    this.canvas.style.width = t.x + "px";
    this.canvas.style.height = t.y + "px";
    // O CANTO DO CANVAS ACOMPANHA O PANE. Sem isto o desenho fica preso onde
    // o mapa estava quando a camada nasceu, e "anda" ao arrastar.
    const canto = this.mapa.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(this.canvas, canto);
    return { longitude: c.lng, latitude: c.lat,
             zoom: this.mapa.getZoom() - 1, pitch: 0, bearing: 0 };
  };

  MapaLigacoes.prototype._sincronizar = function () {
    if (!this.deck) return;
    this.deck.setProps({ viewState: this._vista() });
  };

  MapaLigacoes.prototype.definirDados = function (linhas) {
    this.linhas = linhas || [];
    this.redesenhar();
  };

  MapaLigacoes.prototype.definirFiltro = function (fn) {
    this.filtro = fn;
    this.redesenhar();
  };

  MapaLigacoes.prototype.visiveis = function () {
    return this.filtro ? this.linhas.filter(this.filtro) : this.linhas;
  };

  MapaLigacoes.prototype.redesenhar = function () {
    const dados = this.visiveis();
    const atlas = this.atlas;
    // DUAS CAMADAS, e não um sprite por combinação. Seriam quatro bases vezes
    // quatro estados de veredito — dezesseis desenhos para manter. Com a marca
    // por cima, são sete, e mudar a estrela não obriga a redesenhar as casas.
    const base = new deck.IconLayer({
      id: "ligacoes-base",
      data: dados,
      pickable: true,
      iconAtlas: atlas.url,
      iconMapping: atlas.mapping,
      getIcon: simboloBase,
      getPosition: (l) => [l[I.LNG], l[I.LAT]],
      getSize: 26,
      getColor: corDe,
      sizeUnits: "pixels",
      updateTriggers: { getIcon: dados.length, getColor: dados.length },
    });
    const marcados = dados.filter(simboloTopo);
    const topo = new deck.IconLayer({
      id: "ligacoes-topo",
      data: marcados,
      pickable: false,
      iconAtlas: atlas.url,
      iconMapping: atlas.mapping,
      getIcon: simboloTopo,
      getPosition: (l) => [l[I.LNG], l[I.LAT]],
      getSize: 14,
      // A MARCA FICA ACIMA DO SÍMBOLO, e não em cima dele: o deslocamento em
      // pixels sobe a estrela para fora do telhado, senão ela tapa a casa.
      getPixelOffset: [0, -26],
      sizeUnits: "pixels",
      updateTriggers: { getIcon: marcados.length },
    });
    this.deck.setProps({ layers: [base, topo] });
  };

  MapaLigacoes.prototype.destruir = function () {
    if (this.deck) { this.deck.finalize(); this.deck = null; }
  };

  global.MapaLigacoes = MapaLigacoes;
  global.MapaLigacoes.INDICES = I;
})(window);
