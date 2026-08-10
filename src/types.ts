export interface BoundingBox {
  north: number;
  south: number;
  east: number;
  west: number;
}

export interface CaptureConfig {
  boundingBox: BoundingBox;
  zoomLevel: number;
  outputDir: string;
  sessionName: string;
  tileOverlapPercent: number;
  viewportWidth: number;
  viewportHeight: number;
  delayBetweenTiles: number; // ms
  // Área REAL de trabalho, [[lat, lng], ...] — a mesma que o painel desenha.
  // A caixa envolvente de um município é muito maior que ele: sem este recorte,
  // a captura paga tiles de mato. Quando ausente, captura a caixa inteira.
  polygon?: { lat: number; lng: number }[];
}

export interface TileCoord {
  row: number;
  col: number;
  lat: number;
  lng: number;
  filename: string;
}

export interface CaptureSession {
  sessionName: string;
  startedAt: string;
  config: CaptureConfig;
  totalTiles: number;
  completedTiles: number;
  failedTiles: TileCoord[];
  status: 'running' | 'completed' | 'failed' | 'paused';
}

export interface NominatimResult {
  place_id: number;
  display_name: string;
  boundingbox: [string, string, string, string]; // [minLat, maxLat, minLng, maxLng]
  lat: string;
  lon: string;
}
