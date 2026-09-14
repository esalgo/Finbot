import { computed, effect, inject, Service, signal } from '@angular/core';

import { ChatMessage, StoredChat, StreamStatus } from './chat.models';
import { ChatApiError, ChatService } from './chat.service';

/** Versioned: when ChatMessage changes shape, bump it instead of parsing stale data. */
export const STORAGE_KEY = 'finbot.chat.v1';

const GENERIC_ERROR = 'No pude procesar tu mensaje en este momento. Intenta de nuevo en unos segundos.';
const NETWORK_ERROR = 'No hay conexión con el servidor. Revisa tu conexión e intenta de nuevo.';

function newThreadId(): string {
  return crypto.randomUUID();
}

function assistantMessage(partial: Partial<ChatMessage>): ChatMessage {
  return {
    role: 'assistant',
    content: '',
    toolsUsed: [],
    cached: false,
    audio: null,
    image: null,
    hadImage: false,
    error: false,
    ...partial,
  };
}

export function readStoredChat(storage: Storage): StoredChat | null {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredChat;
    if (typeof parsed?.threadId !== 'string' || !Array.isArray(parsed.messages)) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** What goes to localStorage: no audio, no image payloads, no error bubbles. */
export function toStoredChat(threadId: string, messages: ChatMessage[]): StoredChat {
  return {
    threadId,
    messages: messages
      .filter((m) => !m.error)
      .map((m) => ({ ...m, audio: null, image: null })),
  };
}

@Service()
export class ChatStore {
  private readonly api = inject(ChatService);
  private readonly storage: Storage = localStorage;

  /** Replaced on every change, never mutated: with OnPush a push() would not repaint. */
  readonly messages = signal<ChatMessage[]>([]);
  readonly threadId = signal<string>(newThreadId());
  readonly sending = signal(false);
  /** Latest progress event of the turn in flight; null before the first one arrives. */
  readonly status = signal<StreamStatus | null>(null);
  /** Tools already started in the turn in flight, in call order. */
  readonly toolsInFlight = signal<string[]>([]);
  readonly isEmpty = computed(() => this.messages().length === 0);

  constructor() {
    const stored = readStoredChat(this.storage);
    if (stored) {
      // The thread id travels with the history: without it the screen shows the old
      // conversation while the backend starts a new thread that remembers nothing.
      this.threadId.set(stored.threadId);
      this.messages.set(stored.messages);
    }
    effect(() => this.save(toStoredChat(this.threadId(), this.messages())));
  }

  async send(text: string, image: string | null): Promise<void> {
    const message = text.trim();
    if ((!message && !image) || this.sending()) return;

    const userMessage: ChatMessage = {
      role: 'user',
      content: message,
      toolsUsed: [],
      cached: false,
      audio: null,
      image,
      hadImage: image !== null,
      error: false,
    };
    this.messages.update((list) => [...list, userMessage]);
    this.sending.set(true);
    this.status.set(null);
    this.toolsInFlight.set([]);

    try {
      const data = await this.api.stream(
        { message, thread_id: this.threadId(), ...(image ? { image } : {}) },
        (status) => {
          this.status.set(status);
          if (status.stage === 'tool' && status.tool && !this.toolsInFlight().includes(status.tool)) {
            this.toolsInFlight.update((tools) => [...tools, status.tool!]);
          }
        },
      );
      this.messages.update((list) => [
        ...list,
        assistantMessage({
          content: data.response,
          toolsUsed: data.tools_used ?? [],
          cached: data.cached,
          audio: data.audio,
        }),
      ]);
    } catch (err) {
      this.messages.update((list) => [...list, assistantMessage({ content: errorText(err), error: true })]);
    } finally {
      this.sending.set(false);
      this.status.set(null);
      this.toolsInFlight.set([]);
    }
  }

  newConversation(): void {
    this.messages.set([]);
    this.threadId.set(newThreadId());
  }

  private save(payload: StoredChat): void {
    try {
      this.storage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // QuotaExceededError: setItem throws, and an uncaught throw would break sending,
      // not just saving. Drop the stored copy and keep the in-memory conversation.
      try {
        this.storage.removeItem(STORAGE_KEY);
      } catch {
        /* storage unavailable (private mode, disabled): nothing to clean */
      }
    }
  }
}

export function errorText(err: unknown): string {
  if (err instanceof ChatApiError) {
    if (err.status === 0) return NETWORK_ERROR;
    // 422 and 429 carry a user-facing message from the backend (429 is bilingual).
    if (err.detail) return err.detail;
  }
  return GENERIC_ERROR;
}
