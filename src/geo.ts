import axios from 'axios';
import { BoundingBox, NominatimResult } from './types';

export async function searchBoundsByName(query: string): Promise<{ bbox: BoundingBox; displayName: string }[]> {
  const url = 'https://nominatim.openstreetmap.org/search';
  const response = await axios.get<NominatimResult[]>(url, {
    params: { q: query, format: 'json', limit: 5, addressdetails: 1 },
    headers: {
      'User-Agent': 'comercialRadar/1.0 (captura-mapas)',
      'Accept-Language': 'pt-BR,pt;q=0.9',
    },
  });
  if (!response.data || response.data.length === 0) {
    throw new Error(`Nenhum resultado encontrado para: "${query}"`);
  }
  return response.data.map((result) => {
    const [south, north, west, east] = result.boundingbox.map(Number);
    return { bbox: { north, south, east, west }, displayName: result.display_name };
  });
}

export function estimateTileCount(bbox: BoundingBox, zoomLevel: number, overlapPercent = 10): number {
  const { rows, cols } = calculateGrid(bbox, zoomLevel, overlapPercent);
  return rows * cols;
}

export function calculateGrid(
  bbox: BoundingBox,
  zoomLevel: number,
  overlapPercent = 10
): { rows: number; cols: number; latStep: number; lngStep: number } {
  const W = 3840;
  const H = 2160;

  // Graus cobertos por 1 tile no zoom dado
  const lngPerTile = (W * 360) / (256 * Math.pow(2, zoomLevel));
  const centerLat = (bbox.north + bbox.south) / 2;
  const mercatorFactor = Math.cos((centerLat * Math.PI) / 180);
  const latPerTile = lngPerTile * (H / W) / mercatorFactor;

  const bboxW = bbox.east - bbox.west;
  const bboxH = bbox.north - bbox.south;

  // Só adiciona tile extra se o excedente for > 5% do tile
  // (evita tile desnecessário por causa de overlap ou imprecisão)
  const threshold = 0.05;
  const cols = bboxW <= lngPerTile * (1 + threshold)
    ? Math.max(1, Math.ceil(bboxW / lngPerTile))
    : Math.ceil(bboxW / (lngPerTile * (1 - overlapPercent / 100)));

  const rows = bboxH <= latPerTile * (1 + threshold)
    ? Math.max(1, Math.ceil(bboxH / latPerTile))
    : Math.ceil(bboxH / (latPerTile * (1 - overlapPercent / 100)));

  const lngStep = bboxW / Math.max(cols, 1);
  const latStep = bboxH / Math.max(rows, 1);

  return { rows, cols, latStep, lngStep };
}
