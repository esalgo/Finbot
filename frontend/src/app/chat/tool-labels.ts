/**
 * Technical tool names and stage codes come from the backend; every user-facing
 * label, icon and wording lives here so copy changes never touch Python.
 */
import { ChatMessage, StreamStatus } from './chat.models';

export interface ToolLabel {
  label: string;
  icon: string;
  /** Progress line shown while the tool runs. */
  progress: string;
}

export const TOOL_LABELS: Record<string, ToolLabel> = {
  calculate_interest: { label: 'Interés compuesto', icon: '📈', progress: 'Calculando el rendimiento…' },
  get_usd_rate: { label: 'Tasa USD/COP', icon: '💱', progress: 'Consultando la tasa de cambio…' },
  get_stock_quote: { label: 'Cotización bursátil', icon: '📊', progress: 'Consultando la bolsa de valores…' },
  get_crypto_price: { label: 'Precio cripto', icon: '₿', progress: 'Consultando el mercado cripto…' },
  search_docs: { label: 'Base documental', icon: '📄', progress: 'Consultando la base de conocimiento…' },
  imagen: { label: 'Análisis de imagen', icon: '🖼️', progress: 'Analizando la imagen…' },
};

export function toolLabel(name: string): ToolLabel {
  // An unknown tool still gets a badge: hiding it would misreport what ran.
  return TOOL_LABELS[name] ?? { label: name, icon: '🔧', progress: 'Consultando información…' };
}

export function statusText(status: StreamStatus | null): string {
  if (!status) return 'Enviando tu mensaje…';
  switch (status.stage) {
    case 'thinking':
      return 'Analizando tu pregunta…';
    case 'vision':
      return 'Analizando la imagen…';
    case 'tool':
      return toolLabel(status.tool ?? '').progress;
    case 'writing':
      return 'Redactando la respuesta…';
  }
}

/** Where an answer came from, derived only from tools_used and cached. */
export type OriginKind = 'knowledge' | 'market' | 'calculation' | 'image' | 'cache' | 'model';

export interface Origin {
  kind: OriginKind;
  icon: string;
  label: string;
}

const MARKET_TOOLS = new Set(['get_usd_rate', 'get_stock_quote', 'get_crypto_price']);

export function originsOf(message: Pick<ChatMessage, 'toolsUsed' | 'cached'>): Origin[] {
  if (message.cached) {
    return [{ kind: 'cache', icon: '⚡', label: 'Respuesta frecuente · desde caché' }];
  }
  const tools = message.toolsUsed;
  const origins: Origin[] = [];
  if (tools.includes('search_docs')) origins.push({ kind: 'knowledge', icon: '📄', label: 'Base de conocimiento' });
  if (tools.some((t) => MARKET_TOOLS.has(t))) origins.push({ kind: 'market', icon: '📈', label: 'Datos de mercado en vivo' });
  if (tools.includes('calculate_interest')) origins.push({ kind: 'calculation', icon: '🧮', label: 'Cálculo financiero' });
  if (tools.includes('imagen')) origins.push({ kind: 'image', icon: '🖼️', label: 'Análisis de imagen' });
  // Unknown tools still count as "consulted something": never label them as model knowledge.
  if (!origins.length && tools.length) origins.push({ kind: 'knowledge', icon: '🔧', label: 'Herramientas consultadas' });
  if (!origins.length) origins.push({ kind: 'model', icon: '🤖', label: 'Conocimiento general del modelo' });
  return origins;
}
