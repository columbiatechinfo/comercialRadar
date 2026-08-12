#!/usr/bin/env node
/**
 * painel.mjs — gera docs/painel.html a partir de docs/estado.json
 *
 * Sem dependências. O JSON é embutido no HTML, então o arquivo abre
 * com duplo clique, sem servidor e sem fetch bloqueado por CORS.
 *
 *   node scripts/painel.mjs                      # docs/estado.json -> docs/painel.html
 *   node scripts/painel.mjs --estado a.json --out b.html
 *   node scripts/painel.mjs --init --projeto meuapp --perfil saas-multi-cliente
 *   node scripts/painel.mjs --check              # valida sem gerar (uso no CI)
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const SCHEMA = 1;
const DIAS_ATE_ENVELHECER = 30;

const PERFIS = ['script-interno', 'app-single-tenant', 'saas-multi-cliente'];
const STATUS_REQ = ['ok', 'parcial', 'pendente', 'na'];

/* ────────────────────────────── argumentos ────────────────────────────── */

function parseArgs(argv) {
  const a = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const t = argv[i];
    if (t.startsWith('--')) {
      const k = t.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith('--')) a[k] = true;
      else { a[k] = next; i++; }
    } else a._.push(t);
  }
  return a;
}

/* ────────────────────────────── utilidades ────────────────────────────── */

const esc = (v) =>
  String(v ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

/** Permite `code` inline nos textos do estado.json, escapando o resto. */
function rich(v) {
  return esc(v).replace(/`([^`]+)`/g, '<code>$1</code>');
}

function parseData(v) {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dataCurta(v) {
  const d = parseData(v);
  if (!d) return '—';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function dataHora(v) {
  const d = parseData(v);
  if (!d) return '—';
  return d.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** "há 12 minutos", "há 3 dias" — sempre relativo ao momento da geração. */
function idade(v, agora) {
  const d = parseData(v);
  if (!d) return { texto: 'nunca', dias: Infinity };
  const ms = agora - d;
  const min = Math.floor(ms / 60000);
  const horas = Math.floor(min / 60);
  const dias = Math.floor(horas / 24);
  if (min < 1) return { texto: 'agora', dias: 0 };
  if (min < 60) return { texto: `há ${min} ${min === 1 ? 'minuto' : 'minutos'}`, dias: 0 };
  if (horas < 24) return { texto: `há ${horas} ${horas === 1 ? 'hora' : 'horas'}`, dias: 0 };
  return { texto: `há ${dias} ${dias === 1 ? 'dia' : 'dias'}`, dias };
}

/* ────────────────────────────── validação ────────────────────────────── */

function validar(e) {
  const erros = [];
  if (!e || typeof e !== 'object') return ['estado.json não é um objeto'];
  if (e.schema !== SCHEMA) erros.push(`schema esperado ${SCHEMA}, encontrado ${e.schema ?? 'ausente'}`);
  if (!e.projeto) erros.push('campo "projeto" ausente');
  if (!PERFIS.includes(e.perfil)) erros.push(`perfil inválido: ${e.perfil} (esperado ${PERFIS.join(' | ')})`);
  if (!e.atualizado_em) erros.push('campo "atualizado_em" ausente');
  else if (!parseData(e.atualizado_em)) erros.push(`"atualizado_em" não é data ISO válida: ${e.atualizado_em}`);

  for (const r of e.requisitos ?? []) {
    if (!STATUS_REQ.includes(r.status)) {
      erros.push(`requisito "${r.nome ?? r.id}": status inválido "${r.status}"`);
    }
  }
  const s = e.seguranca;
  if (s && typeof s.nota === 'number' && (s.nota < 0 || s.nota > 100)) {
    erros.push(`seguranca.nota fora de 0–100: ${s.nota}`);
  }
  return erros;
}

/* ────────────────────────────── esqueleto ────────────────────────────── */

const REQUISITOS_BASE = [
  ['prd', 'PRD'], ['uml', 'UML'], ['repo', 'Repo + docs padrão'],
  ['rbac', 'Matriz RBAC'], ['rls', 'tenant_id + RLS'], ['segredos', 'Segredos'],
  ['modulos', 'Módulos + flags'], ['erro', 'Botão de erro'], ['testes', 'Testes'],
  ['backup', 'Backup'], ['borda', 'WAF / TLS / rate limit'], ['lgpd', 'LGPD'],
];

/** Requisitos que não se aplicam a cada perfil — espelha a matriz do desenho. */
const NAO_SE_APLICA = {
  'script-interno': ['uml', 'rbac', 'rls', 'modulos', 'erro', 'borda', 'lgpd'],
  'app-single-tenant': ['modulos', 'lgpd'],
  'saas-multi-cliente': ['lgpd'],
};

const SKILLS = ['/infra', '/projeto-novo', '/segredos', '/modelo-acesso',
  '/servicos-geo-ia', '/observabilidade', '/testes', '/seguranca', '/lgpd'];

function esqueleto(projeto, perfil) {
  const na = NAO_SE_APLICA[perfil] ?? [];
  return {
    schema: SCHEMA,
    projeto,
    perfil,
    versao: '0.1.0',
    ambiente: 'wsl-dev',
    atualizado_em: new Date().toISOString(),
    atualizado_por: '/projeto-novo',
    commit: null,
    seguranca: { nota: null, corte: 70, historico: [], categorias: [], criticos: [], recomendados: [] },
    requisitos: REQUISITOS_BASE.map(([id, nome]) => ({
      id, nome,
      status: na.includes(id) ? 'na' : 'pendente',
      detalhe: na.includes(id) ? 'Não se aplica a este perfil' : 'Ainda não iniciado',
    })),
    skills: SKILLS.map((nome) => ({ nome, ultima_execucao: null, resultado: 'nunca', saidas: null })),
    operacional: {},
    custo: { moeda: 'BRL', mensal_estimado: null, itens: [] },
    dados_por_cliente: [],
    servicos_externos: [],
    timeline: [],
  };
}

/* ────────────────────────────── seções HTML ────────────────────────────── */

function secNota(e) {
  const s = e.seguranca ?? {};
  if (s.nota === null || s.nota === undefined) {
    return `<div class="score vazio">
      <div class="lbl">Nota de segurança</div>
      <div class="n na">—</div>
      <div class="verd">A skill <code>/seguranca</code> ainda não foi executada</div>
    </div>`;
  }
  const corte = s.corte ?? 70;
  const cls = s.nota >= corte ? 'good' : s.nota >= corte - 15 ? 'warn' : 'bad';
  const hist = (s.historico ?? []).slice(-8);
  const max = Math.max(100, ...hist.map((h) => h.nota ?? 0));
  const spark = hist.length > 1
    ? `<div class="sparkwrap"><div class="spark">${hist.map((h, i) =>
        `<div class="${i === hist.length - 1 ? 'cur ' + cls : ''}" style="height:${Math.max(4, (h.nota / max) * 100)}%"
          title="${esc(dataCurta(h.data))}: ${esc(h.nota)}"><span>${esc(dataCurta(h.data).slice(0, 5))}</span></div>`).join('')}</div></div>`
    : '';

  const abertos = (s.criticos ?? []).filter((c) => c.status !== 'resolvido').length;
  let nota;
  if (abertos > 0) {
    nota = `Bloqueio não é a nota — são ${abertos} ${abertos === 1 ? 'crítico aberto' : 'críticos abertos'}.`;
  } else if (s.nota >= corte) {
    nota = 'Acima do corte e sem críticos abertos. Liberado.';
  } else {
    nota = `Faltam ${corte - s.nota} pontos para o corte de ${corte}.`;
  }

  const cats = (s.categorias ?? []).length
    ? `<div class="cats">${s.categorias.map((c) => {
        const pct = c.peso ? Math.round((c.obtido / c.peso) * 100) : 0;
        const k = pct >= 80 ? 'good' : pct >= 50 ? 'warn' : 'bad';
        return `<div class="cat"><div class="cn">${esc(c.nome)}</div>
          <div class="cb"><i class="${k}" style="width:${pct}%"></i></div>
          <div class="cv">${esc(c.obtido)}<em>/${esc(c.peso)}</em></div></div>`;
      }).join('')}</div>`
    : '';

  return `<div class="score">
    <div class="lbl">Nota de segurança</div>
    <div class="n ${cls}">${esc(s.nota)}<small>/100</small></div>
    <div class="verd ${cls}">${s.nota >= corte ? 'Acima do corte' : 'Abaixo do corte'} de deploy (${esc(corte)})</div>
    ${spark}
    ${cats}
    <div class="rodape">${rich(nota)}</div>
  </div>`;
}

function secCriticos(e) {
  const cr = e.seguranca?.criticos ?? [];
  if (!cr.length) {
    return `<div class="crit vazio">
      <h2>Críticos</h2>
      <div class="nada">Nenhum crítico registrado. ${e.seguranca?.nota == null
        ? 'Rode <code>/seguranca</code> para auditar.' : 'Superfície limpa.'}</div>
    </div>`;
  }
  const ordem = [...cr].sort((a, b) => (a.status === 'resolvido') - (b.status === 'resolvido'));
  return `<div class="crit">
    <h2>Críticos — travam deploy em produção</h2>
    ${ordem.map((c) => {
      const ok = c.status === 'resolvido';
      return `<div class="critrow${ok ? ' feito' : ''}">
        <span class="x${ok ? ' ok' : ''}">${ok ? '✓' : '✕'}</span>
        <div>
          <div class="t">${esc(c.titulo)}${ok ? ' — resolvido' : ''}</div>
          <div class="d">${rich(c.detalhe)}${ok && c.resolvido_em ? ` <em>(${esc(dataCurta(c.resolvido_em))})</em>` : ''}</div>
        </div>
        ${!ok && c.correcao === 'automatica' ? '<span class="fix">correção automática</span>' : ''}
      </div>`;
    }).join('')}
  </div>`;
}

function secRequisitos(e) {
  const rs = e.requisitos ?? [];
  if (!rs.length) return '';
  const map = { ok: ['ok', '●'], parcial: ['pa', '◐'], pendente: ['pe', '○'], na: ['na', '—'] };
  return `<h2 class="sec">Requisitos — perfil ${esc(e.perfil.replace(/-/g, ' '))}</h2>
  <div class="reqs">${rs.map((r) => {
    const [cls, ic] = map[r.status] ?? ['pe', '○'];
    return `<div class="req ${cls}"><span class="ic">${ic}</span><div>
      <div class="nm">${esc(r.nome)}</div>
      <div class="dt">${rich(r.detalhe ?? '')}</div></div></div>`;
  }).join('')}</div>`;
}

function secSkills(e) {
  const sk = e.skills ?? [];
  if (!sk.length) return '';
  const pill = { ok: 'p-ok', parcial: 'p-wa', reprovado: 'p-no', nunca: 'p-ne' };
  return `<h2 class="sec">Skills</h2>
  <div class="tbl"><table>
    <thead><tr><th>Skill</th><th>Última execução</th><th>Resultado</th><th>O que produziu</th></tr></thead>
    <tbody>${sk.map((s) => `<tr>
      <td class="sk">${esc(s.nome)}</td>
      <td>${s.ultima_execucao ? esc(dataHora(s.ultima_execucao)) : '—'}</td>
      <td><span class="pill ${pill[s.resultado] ?? 'p-ne'}">${esc(s.resultado)}</span></td>
      <td>${rich(s.saidas ?? '—')}</td></tr>`).join('')}</tbody>
  </table></div>`;
}

function secOperacional(e, agora) {
  const o = e.operacional ?? {};
  const cards = [];
  const add = (k, v, s, cls) => cards.push({ k, v, s, cls });

  if (o.ultimo_backup !== undefined) {
    const i = idade(o.ultimo_backup, agora);
    add('Último backup', i.texto, o.ultimo_backup ? 'pgBackRest → R2' : 'nenhum registrado',
      i.dias === Infinity ? 'bad' : i.dias > 2 ? 'warn' : 'good');
  }
  if (o.restauracao_validada !== undefined) {
    const i = idade(o.restauracao_validada, agora);
    add('Restauração validada', i.texto,
      i.dias === Infinity ? 'crítico aberto' : 'em máquina limpa',
      i.dias === Infinity || i.dias > 40 ? 'bad' : i.dias > 30 ? 'warn' : 'good');
  }
  if (o.cobertura_testes != null) {
    const m = o.meta_cobertura ?? 70;
    add('Cobertura de teste', `${o.cobertura_testes}%`, `meta ${m}%`,
      o.cobertura_testes >= m ? 'good' : o.cobertura_testes >= m * 0.6 ? 'warn' : 'bad');
  }
  if (o.tabelas_sem_rls != null) {
    add('Tabelas sem RLS', String(o.tabelas_sem_rls),
      o.tabelas_negocio ? `de ${o.tabelas_negocio} tabelas de negócio` : '',
      o.tabelas_sem_rls === 0 ? 'good' : 'bad');
  }
  if (o.deps_desatualizadas != null) {
    add('Deps desatualizadas', String(o.deps_desatualizadas),
      o.deps_com_cve ? `${o.deps_com_cve} com CVE conhecido` : 'nenhuma com CVE',
      o.deps_com_cve ? 'bad' : o.deps_desatualizadas > 5 ? 'warn' : 'good');
  }
  if (o.indice_tenant) {
    add('Índice tenant_id', `${o.indice_tenant.ok}/${o.indice_tenant.total}`,
      'tabelas com policy',
      o.indice_tenant.ok === o.indice_tenant.total ? 'good' : 'warn');
  }
  if (!cards.length) return '';
  return `<h2 class="sec">Saúde operacional</h2>
  <div class="ops">${cards.map((c) =>
    `<div class="op ${c.cls}"><div class="k">${esc(c.k)}</div>
     <div class="v">${esc(c.v)}</div><div class="s">${esc(c.s)}</div></div>`).join('')}</div>`;
}

function secCustoDados(e) {
  const c = e.custo ?? {};
  const d = e.dados_por_cliente ?? [];
  if (c.mensal_estimado == null && !d.length) return '';
  const moeda = c.moeda ?? 'BRL';
  const fmt = (n) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: moeda }).format(n);
  const fmtN = (n) => new Intl.NumberFormat('pt-BR').format(n);

  const custo = c.mensal_estimado != null ? `<div class="painelbox">
    <div class="k">Custo de infra — mensal estimado</div>
    <div class="big">${esc(fmt(c.mensal_estimado))}</div>
    ${(c.itens ?? []).length ? `<div class="itens">${c.itens.map((i) =>
      `<div class="item"><span>${esc(i.nome)}</span><b>${esc(fmt(i.valor))}</b></div>`).join('')}</div>` : ''}
  </div>` : '';

  const totalLinhas = d.reduce((s, x) => s + (x.linhas ?? 0), 0);
  const totalMb = d.reduce((s, x) => s + (x.armazenamento_mb ?? 0), 0);
  const maxMb = Math.max(1, ...d.map((x) => x.armazenamento_mb ?? 0));
  const dados = d.length ? `<div class="painelbox">
    <div class="k">Volume por cliente — ${esc(d.length)} ${d.length === 1 ? 'tenant' : 'tenants'}</div>
    <div class="big">${esc(fmtN(totalLinhas))}<em> linhas · ${esc(fmtN(Math.round(totalMb)))} MB</em></div>
    <div class="itens">${d.map((x) => `<div class="bar">
      <span class="bn">${esc(x.tenant)}</span>
      <span class="bb"><i style="width:${Math.round(((x.armazenamento_mb ?? 0) / maxMb) * 100)}%"></i></span>
      <b>${esc(fmtN(Math.round(x.armazenamento_mb ?? 0)))} MB</b></div>`).join('')}</div>
  </div>` : '';

  return `<h2 class="sec">Custo e volume</h2><div class="cd">${custo}${dados}</div>`;
}

function secServicos(e, agora) {
  const sv = e.servicos_externos ?? [];
  if (!sv.length) return '';
  return `<h2 class="sec">Serviços externos</h2>
  <div class="tbl"><table>
    <thead><tr><th>Serviço</th><th>Endpoint</th><th>Estado</th><th>Idade do dado</th></tr></thead>
    <tbody>${sv.map((s) => {
      const i = idade(s.dado_em, agora);
      const velho = i.dias > 60;
      const p = s.status === 'ok' ? 'p-ok' : s.status === 'fora' ? 'p-no' : 'p-ne';
      return `<tr>
        <td class="sk">${esc(s.nome)}</td>
        <td><code>${esc(s.endpoint ?? '—')}</code></td>
        <td><span class="pill ${p}">${esc(s.status ?? 'desconhecido')}</span></td>
        <td class="${velho ? 'velho' : ''}">${esc(i.texto)}${velho ? ' ⚠' : ''}</td>
      </tr>`;
    }).join('')}</tbody>
  </table></div>
  <p class="hint">Dado de OSM com mais de 60 dias produz rota errada e geocodificação furada — sem o serviço dar nenhum sinal de erro.</p>`;
}

function secTimeline(e) {
  const tl = e.timeline ?? [];
  if (!tl.length) return '';
  const nivel = { ok: 'g', aviso: 'a', erro: 'r' };
  const ordenada = [...tl].sort((a, b) => (parseData(b.data) ?? 0) - (parseData(a.data) ?? 0));
  return `<h2 class="sec">Linha do tempo</h2>
  <div class="tl">${ordenada.map((v) => `<div class="ev ${nivel[v.nivel] ?? ''}">
    <div class="when">${esc(dataHora(v.data))}</div>
    <div class="what">${v.skill ? `<b>${esc(v.skill)}</b> ` : ''}${rich(v.evento)}</div>
    ${v.detalhe ? `<div class="det">${rich(v.detalhe)}</div>` : ''}
  </div>`).join('')}</div>`;
}

/* ────────────────────────────── documento ────────────────────────────── */

function render(e, agora) {
  const i = idade(e.atualizado_em, agora);
  const velho = i.dias >= DIAS_ATE_ENVELHECER;
  const amb = e.ambiente === 'servidor-prod'
    ? '<span class="tag prod">servidor · produção</span>'
    : '<span class="tag dev">WSL · desenvolvimento</span>';

  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(e.projeto)} — painel do projeto</title>
<style>${CSS}</style>
</head>
<body${velho ? ' class="envelhecido"' : ''}>
<header>
  <div class="wrap">
    <div class="htop">
      <h1>${esc(e.projeto)}</h1>
      <span class="tag">${esc(e.perfil.replace(/-/g, ' '))}</span>
      ${amb}
      ${e.versao ? `<span class="tag">v${esc(e.versao)}</span>` : ''}
    </div>
    <div class="freshness">Atualizado <b>${esc(i.texto)}</b>${e.atualizado_por
      ? ` — por <code>${esc(e.atualizado_por)}</code>` : ''}${e.commit
      ? ` · commit <code>${esc(e.commit)}</code>` : ''}</div>
    ${velho ? `<div class="stale">⚠ Este painel não é atualizado ${esc(i.texto)}. Os dados abaixo podem não refletir o estado atual do projeto — rode as skills novamente antes de confiar neles.</div>` : ''}
  </div>
</header>
<div class="wrap">
  <div class="top">${secNota(e)}${secCriticos(e)}</div>
  ${secRequisitos(e)}
  ${secSkills(e)}
  ${secOperacional(e, agora)}
  ${secCustoDados(e)}
  ${secServicos(e, agora)}
  ${secTimeline(e)}
  <footer>
    Gerado por <code>scripts/painel.mjs</code> a partir de <code>docs/estado.json</code> ·
    ${esc(dataHora(agora.toISOString()))} · dados embutidos, abre sem servidor
  </footer>
</div>
<script type="application/json" id="estado">${JSON.stringify(e).replace(/</g, '\\u003c')}</script>
</body>
</html>`;
}

/* ────────────────────────────── estilo ────────────────────────────── */

const CSS = `
:root{--bg:#0a0a0b;--card:#141416;--line:#26262a;--tx:#e8e8ea;--tx2:#a1a1aa;--tx3:#6b6b73;
--red:#e11d2e;--green:#22c55e;--amber:#f59e0b;--blue:#60a5fa;--gray:#3f3f46;
--mono:ui-monospace,"Cascadia Code","SF Mono",Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.6 -apple-system,"Segoe UI",Roboto,sans-serif;padding:0 0 60px}
body.envelhecido{filter:saturate(.45)}
.wrap{max-width:1240px;margin:0 auto;padding:0 20px}
code{font:12.5px var(--mono);color:#f0a8b0;background:#1e1216;padding:1px 5px;border-radius:4px}
header{border-bottom:1px solid var(--line);padding:28px 0 24px;margin-bottom:28px;background:linear-gradient(160deg,#160406 0%,#0a0a0b 70%)}
.htop{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px}
h1{font-size:26px;margin:0;letter-spacing:-.02em;font-weight:800}
.tag{font:600 11px/1 var(--mono);padding:5px 9px;border-radius:5px;background:#1e1e22;border:1px solid var(--line);color:var(--tx2)}
.tag.dev{background:#0d1a2b;border-color:#1e3a5f;color:var(--blue)}
.tag.prod{background:#1a0709;border-color:#4a1119;color:#f0a8b0}
.freshness{margin-top:12px;font-size:13px;color:var(--tx3)}.freshness b{color:var(--green)}
.stale{background:#1a1207;border:1px solid #7c4a05;color:#f0c674;padding:10px 14px;border-radius:8px;margin-top:12px;font-size:13.5px}
.top{display:grid;grid-template-columns:300px 1fr;gap:18px;margin-bottom:6px}
@media(max-width:860px){.top{grid-template-columns:1fr}}
.score,.crit{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px}
.crit{border-color:#4a1119}.crit.vazio{border-color:var(--line)}
.lbl,.crit h2{font:700 10px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--tx3);margin:0}
.crit h2{color:var(--red);margin-bottom:14px}.crit.vazio h2{color:var(--tx3)}
.nada{color:var(--tx3);font-size:13.5px}
.score .n{font:800 60px/1 sans-serif;letter-spacing:-.04em;margin:12px 0 2px}
.score .n small{font-size:21px;color:var(--tx3);font-weight:700}
.score .n.good{color:var(--green)}.score .n.warn{color:var(--amber)}.score .n.bad{color:var(--red)}.score .n.na{color:var(--tx3)}
.verd{font-size:13.5px;font-weight:600;margin-bottom:16px;color:var(--tx2)}
.verd.good{color:var(--green)}.verd.warn{color:var(--amber)}.verd.bad{color:var(--red)}
.sparkwrap{margin-bottom:26px}
.spark{display:flex;align-items:flex-end;gap:5px;height:44px}
.spark div{flex:1;background:#2c2c31;border-radius:3px 3px 0 0;position:relative}
.spark div.cur.good{background:var(--green)}.spark div.cur.warn{background:var(--amber)}.spark div.cur.bad{background:var(--red)}
.spark span{position:absolute;bottom:-17px;left:0;right:0;text-align:center;font:9.5px var(--mono);color:var(--tx3)}
.cats{border-top:1px solid var(--line);padding-top:14px;margin-bottom:14px}
.cat{display:grid;grid-template-columns:1fr 54px 44px;gap:8px;align-items:center;margin-bottom:7px}
.cn{font-size:12px;color:var(--tx2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cb{height:5px;background:#26262a;border-radius:3px;overflow:hidden}
.cb i{display:block;height:100%;border-radius:3px}
.cb i.good{background:var(--green)}.cb i.warn{background:var(--amber)}.cb i.bad{background:var(--red)}
.cv{font:600 11px var(--mono);color:var(--tx2);text-align:right}.cv em{color:var(--tx3);font-style:normal}
.rodape{font-size:12.5px;color:var(--tx3);border-top:1px solid var(--line);padding-top:14px}
.critrow{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid #1c1c1f;align-items:flex-start}
.critrow:last-child{border:0;padding-bottom:0}.critrow.feito{opacity:.5}
.critrow .x{color:var(--red);font:700 13px var(--mono);flex-shrink:0;margin-top:1px}
.critrow .x.ok{color:var(--green)}
.critrow .t{font-weight:600;font-size:14px}
.critrow .d{color:var(--tx2);font-size:13px;margin-top:2px}
.critrow .d em{color:var(--tx3)}
.critrow .fix{margin-left:auto;flex-shrink:0;font:600 11px var(--mono);color:var(--green);border:1px solid #14532d;background:#08170f;padding:4px 8px;border-radius:5px;white-space:nowrap}
h2.sec{font:700 11px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--tx3);margin:34px 0 16px;border-top:1px solid var(--line);padding-top:22px}
.reqs{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:11px}
.req{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px;display:flex;gap:11px;align-items:flex-start}
.req .ic{font:700 12px var(--mono);flex-shrink:0;width:15px;text-align:center;margin-top:2px}
.req.ok{border-left:3px solid var(--green)}.req.ok .ic{color:var(--green)}
.req.pa{border-left:3px solid var(--amber)}.req.pa .ic{color:var(--amber)}
.req.pe{border-left:3px solid var(--red)}.req.pe .ic{color:var(--red)}
.req.na{border-left:3px solid var(--gray);opacity:.45}.req.na .ic{color:var(--gray)}
.req .nm{font-weight:600;font-size:14px}
.req .dt{color:var(--tx3);font-size:12.5px;margin-top:2px;line-height:1.45}
.tbl{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:560px}
th{text-align:left;font:700 10px/1 var(--mono);letter-spacing:.11em;text-transform:uppercase;color:var(--tx3);padding:0 12px 11px;border-bottom:1px solid var(--line)}
td{padding:11px 12px;border-bottom:1px solid #1a1a1d;color:var(--tx2);vertical-align:top}
td.sk{font:600 13px var(--mono);color:#f0a8b0;white-space:nowrap}
td.velho{color:var(--amber)}
.pill{font:600 11px var(--mono);padding:3px 8px;border-radius:20px;white-space:nowrap}
.p-ok{background:#08170f;color:var(--green);border:1px solid #14532d}
.p-wa{background:#1a1207;color:var(--amber);border:1px solid #78350f}
.p-no{background:#1a0709;color:var(--red);border:1px solid #4a1119}
.p-ne{background:#18181b;color:var(--tx3);border:1px solid var(--line)}
.hint{color:var(--tx3);font-size:12.5px;margin-top:12px}
.ops{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.op{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.op .k{font:700 10px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--tx3)}
.op .v{font-size:23px;font-weight:800;margin:9px 0 3px;letter-spacing:-.02em}
.op .s{font-size:12.5px;color:var(--tx3)}
.op.bad .v{color:var(--red)}.op.warn .v{color:var(--amber)}.op.good .v{color:var(--green)}
.cd{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.painelbox{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px}
.painelbox .k{font:700 10px/1 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--tx3)}
.painelbox .big{font-size:28px;font-weight:800;margin:10px 0 14px;letter-spacing:-.02em}
.painelbox .big em{font-size:14px;font-weight:600;color:var(--tx3);font-style:normal}
.itens{border-top:1px solid var(--line);padding-top:12px}
.item{display:flex;justify-content:space-between;font-size:13px;color:var(--tx2);padding:4px 0}
.item b{color:var(--tx);font-weight:600}
.bar{display:grid;grid-template-columns:1fr 90px 80px;gap:10px;align-items:center;padding:4px 0;font-size:12.5px}
.bn{color:var(--tx2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bb{height:6px;background:#26262a;border-radius:3px;overflow:hidden}
.bb i{display:block;height:100%;background:var(--blue);border-radius:3px}
.bar b{text-align:right;color:var(--tx);font:600 12px var(--mono)}
.tl{position:relative;padding-left:26px}
.tl::before{content:"";position:absolute;left:6px;top:6px;bottom:6px;width:2px;background:var(--line)}
.ev{position:relative;padding:0 0 20px}
.ev::before{content:"";position:absolute;left:-24px;top:6px;width:9px;height:9px;border-radius:50%;background:var(--card);border:2px solid var(--gray)}
.ev.g::before{border-color:var(--green)}.ev.a::before{border-color:var(--amber)}.ev.r::before{border-color:var(--red)}
.ev .when{font:11px var(--mono);color:var(--tx3)}
.ev .what{font-size:14px;margin-top:2px}
.ev .what b{font-family:var(--mono);font-size:13px;color:#f0a8b0;font-weight:600}
.ev .det{color:var(--tx3);font-size:12.5px;margin-top:2px}
footer{margin-top:44px;border-top:1px solid var(--line);padding-top:18px;color:var(--tx3);font-size:12.5px}
`;

/* ────────────────────────────── main ────────────────────────────── */

function main() {
  const a = parseArgs(process.argv.slice(2));
  const caminhoEstado = resolve(a.estado || 'docs/estado.json');

  if (a.init) {
    if (!a.projeto) { console.error('erro: --init exige --projeto <nome>'); process.exit(2); }
    const perfil = a.perfil || 'saas-multi-cliente';
    if (!PERFIS.includes(perfil)) {
      console.error(`erro: perfil inválido "${perfil}". Use: ${PERFIS.join(' | ')}`); process.exit(2);
    }
    if (existsSync(caminhoEstado) && !a.force) {
      console.error(`erro: ${caminhoEstado} já existe. Use --force para sobrescrever.`); process.exit(2);
    }
    mkdirSync(dirname(caminhoEstado), { recursive: true });
    writeFileSync(caminhoEstado, JSON.stringify(esqueleto(a.projeto, perfil), null, 2) + '\n', 'utf8');
    console.log(`estado.json criado: ${caminhoEstado}`);
    if (a.check) return;
  }

  if (!existsSync(caminhoEstado)) {
    console.error(`erro: ${caminhoEstado} não encontrado. Rode com --init --projeto <nome>.`);
    process.exit(2);
  }

  let estado;
  try {
    estado = JSON.parse(readFileSync(caminhoEstado, 'utf8'));
  } catch (err) {
    console.error(`erro: ${caminhoEstado} não é JSON válido — ${err.message}`);
    process.exit(2);
  }

  const erros = validar(estado);
  if (erros.length) {
    console.error(`estado.json inválido (${erros.length}):`);
    for (const e of erros) console.error(`  · ${e}`);
    process.exit(1);
  }

  if (a.check) { console.log(`ok: ${caminhoEstado} válido`); return; }

  const saida = resolve(a.out || 'docs/painel.html');
  mkdirSync(dirname(saida), { recursive: true });
  writeFileSync(saida, render(estado, new Date()), 'utf8');

  const nota = estado.seguranca?.nota;
  const abertos = (estado.seguranca?.criticos ?? []).filter((c) => c.status !== 'resolvido').length;
  console.log(`painel gerado: ${saida}`);
  console.log(`  projeto ${estado.projeto} · nota ${nota ?? '—'} · ${abertos} crítico(s) aberto(s)`);
}

main();
