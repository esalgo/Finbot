import { Service } from '@angular/core';

import { ChatRequest, ChatResponse, StreamStatus } from './chat.models';

export class ChatApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string | null,
  ) {
    super(detail ?? `HTTP ${status}`);
  }
}

/**
 * Imperative POST to /chat/stream (never httpResource: sending is a user action,
 * and a resource would re-fire whenever a signal in the request changes).
 *
 * Uses fetch instead of HttpClient: the progress events arrive as Server-Sent
 * Events on a POST body, which needs a readable stream. The final event carries
 * exactly the /chat contract.
 */
@Service()
export class ChatService {
  async stream(body: ChatRequest, onStatus: (status: StreamStatus) => void): Promise<ChatResponse> {
    let res: Response;
    try {
      res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(body),
      });
    } catch {
      throw new ChatApiError(0, null);
    }

    if (!res.ok || !res.body) {
      // 422 and 429 arrive as plain JSON errors before the stream opens.
      const detail = await res.json().then((d) => (typeof d?.detail === 'string' ? d.detail : null)).catch(() => null);
      throw new ChatApiError(res.status, detail);
    }

    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let boundary: number;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const event = parseEvent(block);
        if (!event) continue;
        if (event.name === 'status') onStatus(event.data as StreamStatus);
        else if (event.name === 'final') return event.data as ChatResponse;
        else if (event.name === 'error') {
          const err = event.data as { status?: number; detail?: string };
          throw new ChatApiError(err.status ?? 502, err.detail ?? null);
        }
      }
    }
    // The connection closed without a final event (proxy timeout, server restart).
    throw new ChatApiError(502, null);
  }
}

export function parseEvent(block: string): { name: string; data: unknown } | null {
  let name = 'message';
  const data: string[] = [];
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  try {
    return { name, data: JSON.parse(data.join('\n')) };
  } catch {
    return null;
  }
}
