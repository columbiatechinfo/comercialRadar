/**
 * ingest.ts — Ingestão de POIs no PostgreSQL via Prisma
 *
 * Lê um JSON normalizado (gerado pelos scrapers Python) e grava em:
 *   pois (principal) + images_urls + comentarios + horario_funcionamento (derivadas).
 *
 * Idempotente: se o registro tem place_id e já existe, apaga o antigo (cascade
 * remove as derivadas) e recria — re-ingerir o mesmo arquivo não duplica.
 *
 * Formato esperado de cada registro (array no JSON):
 *   {
 *     fonte, sessao, nome, categoria, endereco, telefone, website, avaliacao,
 *     total_avaliacoes, plus_code, status_horario, lat_origem, lng_origem,
 *     maps_lat, maps_lng, maps_url, place_id, status, distancia_m, similaridade,
 *     match_valido, ocr_texto,
 *     fotos: ["url", ...],
 *     comentarios: [{autor, nota, texto, data}, ...],
 *     horarios: [{dia, horario}, ...]   (ou objeto {dia: horario})
 *   }
 *
 * Uso: npx ts-node src/ingest.ts <arquivo.json>
 */
import { PrismaClient } from '@prisma/client';
import * as fs from 'fs';

const prisma = new PrismaClient();

function toFloat(v: any): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'string' ? parseFloat(v.replace(',', '.')) : Number(v);
  return isNaN(n) ? null : n;
}
function toInt(v: any): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = parseInt(String(v).replace(/[^\d-]/g, ''), 10);
  return isNaN(n) ? null : n;
}
function toStr(v: any): string | null {
  if (v === null || v === undefined || v === '') return null;
  return String(v);
}

function normalizaHorarios(h: any): { dia: string; horario: string | null }[] {
  if (!h) return [];
  if (Array.isArray(h)) {
    return h
      .filter((x) => x && (x.dia || x.horario))
      .map((x) => ({ dia: String(x.dia ?? ''), horario: toStr(x.horario) }));
  }
  // objeto {dia: horario}
  return Object.entries(h).map(([dia, horario]) => ({ dia, horario: toStr(horario) }));
}

async function main() {
  const jsonPath = process.argv[2];
  if (!jsonPath) {
    console.error('Uso: npx ts-node src/ingest.ts <arquivo.json>');
    process.exit(1);
  }
  if (!fs.existsSync(jsonPath)) {
    console.error(`Arquivo não encontrado: ${jsonPath}`);
    process.exit(1);
  }

  const registros: any[] = JSON.parse(fs.readFileSync(jsonPath, 'utf-8'));
  let criados = 0, pulados = 0, nFotos = 0, nComent = 0, nHorarios = 0, substituidos = 0;

  for (const r of registros) {
    if (!r.nome) { pulados++; continue; }
    // Não ingere matches não confirmados (divergente / não encontrado)
    if (r.match_valido === false) { pulados++; continue; }
    // Guard geográfico: rejeita coordenada fora do Brasil (evita dispersão global)
    const _la = toFloat(r.maps_lat) ?? toFloat(r.lat_origem);
    const _lo = toFloat(r.maps_lng) ?? toFloat(r.lng_origem);
    if (_la != null && _lo != null && (_la < -34 || _la > 6 || _lo < -74 || _lo > -34)) {
      pulados++; continue;
    }

    if (r.place_id) {
      const del = await prisma.poi.deleteMany({ where: { placeId: r.place_id } });
      if (del.count > 0) substituidos += del.count;
    }

    const imagens = (r.fotos || [])
      .filter(Boolean)
      .map((url: string, i: number) => ({ url: String(url), ordem: i }));
    const comentarios = (r.comentarios || []).map((c: any) => ({
      autor: toStr(c.autor),
      nota: toFloat(c.nota),
      texto: toStr(c.texto),
      data: toStr(c.data),
    }));
    const horarios = normalizaHorarios(r.horarios);

    await prisma.poi.create({
      data: {
        fonte: r.fonte ?? 'desconhecido',
        sessao: toStr(r.sessao),
        nome: String(r.nome),
        categoria: toStr(r.categoria),
        endereco: toStr(r.endereco),
        telefone: toStr(r.telefone),
        website: toStr(r.website),
        avaliacao: toStr(r.avaliacao),
        totalAvaliacoes: toInt(r.total_avaliacoes),
        plusCode: toStr(r.plus_code),
        statusHorario: toStr(r.status_horario),
        latOrigem: toFloat(r.lat_origem ?? r.lat),
        lngOrigem: toFloat(r.lng_origem ?? r.lng),
        mapsLat: toFloat(r.maps_lat),
        mapsLng: toFloat(r.maps_lng),
        mapsUrl: toStr(r.maps_url),
        placeId: toStr(r.place_id),
        status: toStr(r.status),
        distanciaM: toFloat(r.distancia_m),
        similaridade: toFloat(r.similaridade),
        matchValido: r.match_valido ?? null,
        ocrTexto: toStr(r.ocr_texto),
        nomeOriginal: toStr(r.nome_planilha),
        enderecoOriginal: toStr(r.endereco_planilha),
        precoMedio: toStr(r.preco_medio),
        fonteDado: toStr(r.fonte_dado),
        iaResposta: toStr(r.ia_resposta),
        cnpj: toStr(r.cnpj),
        razaoSocial: toStr(r.razao_social),
        nomeFantasia: toStr(r.nome_fantasia),
        naturezaJuridica: toStr(r.natureza_juridica),
        cnae: toStr(r.cnae),
        situacaoCadastral: toStr(r.situacao_cadastral),
        socios: toStr(r.socios),
        instagram: toStr(r.instagram),
        email: toStr(r.email),
        resumoAvaliacoes: toStr(r.resumo_avaliacoes),
        streetviewPath: toStr(r.streetview_path),
        fontesWeb: toStr(r.fontes_web),
        enderecoFonte: toStr(r.endereco_fonte),
        imagens: { create: imagens },
        comentarios: { create: comentarios },
        horarios: { create: horarios },
      },
    });

    criados++;
    nFotos += imagens.length;
    nComent += comentarios.length;
    nHorarios += horarios.length;
  }

  console.log('\n══════════════════════════════════════════');
  console.log('  Ingestão concluída (Prisma → PostgreSQL)');
  console.log('══════════════════════════════════════════');
  console.log(`  POIs gravados        : ${criados}`);
  console.log(`  (substituídos p/ id) : ${substituidos}`);
  console.log(`  Pulados (sem nome)   : ${pulados}`);
  console.log(`  Fotos                : ${nFotos}`);
  console.log(`  Comentários          : ${nComent}`);
  console.log(`  Linhas de horário    : ${nHorarios}`);
  console.log('══════════════════════════════════════════\n');
}

main()
  .catch((e) => { console.error('Erro na ingestão:', e); process.exit(1); })
  .finally(async () => { await prisma.$disconnect(); });
