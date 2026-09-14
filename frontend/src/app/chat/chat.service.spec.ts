import { ChatApiError, ChatService, parseEvent } from './chat.service';
import { StreamStatus } from './chat.models';
import { originsOf, statusText } from './tool-labels';

function sseResponse(chunks: string[], init: ResponseInit = { status: 200 }): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      chunks.forEach((c) => controller.enqueue(enc.encode(c)));
      controller.close();
    },
  });
  return new Response(stream, { ...init, headers: { 'Content-Type': 'text/event-stream' } });
}

describe('ChatService.stream', () => {
  afterEach(() => vi.restoreAllMocks());

  it('reports status events and resolves with the final contract, even across chunk splits', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      sseResponse([
        'event: status\ndata: {"stage": "thinking"}\n\nevent: sta',
        'tus\ndata: {"stage": "tool", "tool": "search_docs"}\n\n',
        'event: final\ndata: {"response": "Según Fogafín…", "tools_used": ["search_docs"], "cached": false, "audio": null}\n\n',
      ]),
    );
    const seen: StreamStatus[] = [];
    const res = await new ChatService().stream({ message: 'hola', thread_id: 't' }, (s) => seen.push(s));
    expect(seen).toEqual([{ stage: 'thinking' }, { stage: 'tool', tool: 'search_docs' }]);
    expect(res.tools_used).toEqual(['search_docs']);
  });

  it('turns 422/429 JSON errors into ChatApiError with the backend detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'El mensaje no puede estar vacío.' }), { status: 422 }),
    );
    await expect(new ChatService().stream({ message: ' ', thread_id: 't' }, () => {})).rejects.toMatchObject({
      status: 422,
      detail: 'El mensaje no puede estar vacío.',
    });
  });

  it('turns an error event and a stream without final into ChatApiError', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(sseResponse(['event: error\ndata: {"status": 502, "detail": "fallo"}\n\n']))
      .mockResolvedValueOnce(sseResponse(['event: status\ndata: {"stage": "thinking"}\n\n']));
    const svc = new ChatService();
    await expect(svc.stream({ message: 'a', thread_id: 't' }, () => {})).rejects.toBeInstanceOf(ChatApiError);
    await expect(svc.stream({ message: 'a', thread_id: 't' }, () => {})).rejects.toMatchObject({ status: 502 });
  });

  it('maps a network failure to status 0', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(new ChatService().stream({ message: 'a', thread_id: 't' }, () => {})).rejects.toMatchObject({ status: 0 });
  });

  it('ignores malformed events', () => {
    expect(parseEvent('event: status\ndata: {nope')).toBeNull();
    expect(parseEvent(': comment only')).toBeNull();
  });
});

describe('origin and progress labels', () => {
  it('derives origins only from tools_used and cached', () => {
    expect(originsOf({ toolsUsed: [], cached: false }).map((o) => o.kind)).toEqual(['model']);
    expect(originsOf({ toolsUsed: [], cached: true }).map((o) => o.kind)).toEqual(['cache']);
    expect(originsOf({ toolsUsed: ['calculate_interest', 'get_crypto_price'], cached: false }).map((o) => o.kind))
      .toEqual(['market', 'calculation']);
    expect(originsOf({ toolsUsed: ['imagen'], cached: false })[0].label).toBe('Análisis de imagen');
    expect(originsOf({ toolsUsed: ['new_tool'], cached: false })[0].kind).not.toBe('model');
  });

  it('words every stage for the user', () => {
    expect(statusText(null)).toContain('Enviando');
    expect(statusText({ stage: 'vision' })).toContain('imagen');
    expect(statusText({ stage: 'tool', tool: 'get_stock_quote' })).toContain('bolsa');
    expect(statusText({ stage: 'writing' })).toContain('Redactando');
  });
});
