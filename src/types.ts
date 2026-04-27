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
