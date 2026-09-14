import { TestBed } from '@angular/core/testing';

import { App } from './app';
import { ChatMessage, ChatRequest, ChatResponse, StreamStatus } from './chat/chat.models';
import { ChatApiError, ChatService } from './chat/chat.service';
import { ChatStore, STORAGE_KEY } from './chat/chat.store';

function message(partial: Partial<ChatMessage>): ChatMessage {
  return {
    role: 'assistant', content: 'hola', toolsUsed: [], cached: false, audio: null,
    image: null, hadImage: false, error: false, ...partial,
  };
}

/** Test double for the streaming service: each call is answered by the next script. */
class FakeChatService {
  calls: ChatRequest[] = [];
  scripts: Array<(onStatus: (s: StreamStatus) => void) => Promise<ChatResponse>> = [];
  stream(body: ChatRequest, onStatus: (s: StreamStatus) => void): Promise<ChatResponse> {
    this.calls.push(body);
    return this.scripts.shift()!(onStatus);
  }
}

const ok = (partial: Partial<ChatResponse> = {}): ChatResponse => ({
  response: 'ok', tools_used: [], cached: false, audio: null, ...partial,
});

describe('App', () => {
  let fake: FakeChatService;

  beforeEach(async () => {
    localStorage.clear();
    fake = new FakeChatService();
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [{ provide: ChatService, useValue: fake }],
    }).compileComponents();
  });

  it('renders the header and clickable suggestions on an empty chat', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h1')?.textContent).toContain('FinBot');
    expect(el.querySelectorAll('.suggestion').length).toBeGreaterThan(3);
  });

  it('shows the origin banner: model knowledge when no tools were used', async () => {
    TestBed.inject(ChatStore).messages.set([message({ content: 'directa', toolsUsed: [] })]);
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const card = (fixture.nativeElement as HTMLElement).querySelector('.card')!;
    expect(card.querySelector('.origin')?.classList).toContain('origin-model');
    expect(card.querySelector('.origin')?.textContent).toContain('Conocimiento general del modelo');
    expect(card.querySelector('.badge')).toBeNull(); // [] paints no tool badge
  });

  it('shows knowledge + market origins and one badge per tool', async () => {
    TestBed.inject(ChatStore).messages.set([message({ toolsUsed: ['search_docs', 'get_usd_rate'] })]);
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const banner = (fixture.nativeElement as HTMLElement).querySelector('.origin')!;
    expect(banner.textContent).toContain('Base de conocimiento');
    expect(banner.textContent).toContain('Datos de mercado en vivo');
    const badges = banner.querySelectorAll('.badge');
    expect(badges.length).toBe(2);
    expect(badges[1].textContent).toContain('Tasa USD/COP');
  });

  it('shows the cache origin with its own style', async () => {
    TestBed.inject(ChatStore).messages.set([message({ cached: true })]);
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const banner = (fixture.nativeElement as HTMLElement).querySelector('.origin')!;
    expect(banner.classList).toContain('origin-cache');
    expect(banner.textContent).toContain('caché');
  });

  it('shows real progress while the stream runs, then the answer', async () => {
    const store = TestBed.inject(ChatStore);
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    let release!: () => void;
    fake.scripts.push(async (onStatus) => {
      onStatus({ stage: 'thinking' });
      onStatus({ stage: 'tool', tool: 'get_crypto_price' });
      await new Promise<void>((r) => (release = r));
      return ok({ response: '1 BTC = 242 millones COP', tools_used: ['get_crypto_price'] });
    });

    const pending = store.send('¿bitcoin en pesos?', null);
    await fixture.whenStable();
    expect(el.querySelector('.progress-card')?.textContent).toContain('Consultando el mercado cripto…');
    expect(el.querySelector('.progress-steps')?.textContent).toContain('Precio cripto');

    release();
    await pending;
    await fixture.whenStable();
    expect(el.querySelector('.progress-card')).toBeNull();
    expect(el.querySelector('.origin')?.textContent).toContain('Datos de mercado en vivo');
  });

  it('appends immutably and persists the thread id with badges', async () => {
    const store = TestBed.inject(ChatStore);
    const before = store.messages();
    fake.scripts.push(async () => ok({ response: '1 USD = 3.085 COP', tools_used: ['get_usd_rate'] }));

    await store.send('¿A cuánto está el dólar?', null);

    expect(fake.calls[0]).toEqual({ message: '¿A cuánto está el dólar?', thread_id: store.threadId() });
    expect(store.messages()).not.toBe(before);
    expect(before.length).toBe(0);
    TestBed.tick();
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored.threadId).toBe(store.threadId());
    expect(stored.messages[1].toolsUsed).toEqual(['get_usd_rate']);
  });

  it('never persists audio, image payloads or error bubbles', async () => {
    const store = TestBed.inject(ChatStore);
    fake.scripts.push(async () => ok({ tools_used: ['imagen'], audio: 'data:audio/mpeg;base64,BBBB' }));
    fake.scripts.push(async () => { throw new ChatApiError(429, 'El servicio alcanzó su capacidad…'); });

    await store.send('analiza', 'data:image/png;base64,AAAA');
    await store.send('otra', null);

    expect(store.messages().at(-1)!.error).toBe(true);
    expect(store.messages().at(-1)!.content).toContain('capacidad');
    TestBed.tick();
    const raw = localStorage.getItem(STORAGE_KEY)!;
    expect(raw).not.toContain('AAAA');
    expect(raw).not.toContain('BBBB');
    const stored = JSON.parse(raw);
    expect(stored.messages.some((m: ChatMessage) => m.error)).toBe(false);
    expect(stored.messages[0].hadImage).toBe(true);
  });

  it('restores the conversation and its thread id on reload', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      threadId: 'thread-from-before',
      messages: [message({ role: 'user', content: 'hola' }), message({ toolsUsed: ['search_docs'] })],
    }));
    const store = TestBed.inject(ChatStore);
    expect(store.threadId()).toBe('thread-from-before');
    expect(store.messages()[1].toolsUsed).toEqual(['search_docs']);
  });

  it('keeps working when setItem throws (quota exceeded)', async () => {
    const store = TestBed.inject(ChatStore);
    fake.scripts.push(async () => ok());
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });
    try {
      await store.send('hola', null);
      expect(() => TestBed.tick()).not.toThrow();
      expect(store.messages().length).toBe(2);
    } finally {
      setItem.mockRestore();
    }
  });
});
