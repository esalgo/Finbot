/** Wire contract with the backend (POST /chat). */
export interface ChatRequest {
  message: string;
  thread_id: string;
  /** Data URI or bare base64; validated by magic bytes on the backend. */
  image?: string;
}

export interface ChatResponse {
  response: string;
  tools_used: string[];
  cached: boolean;
  audio: string | null;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  toolsUsed: string[];
  cached: boolean;
  /** Kept in the contract; voice is not implemented, so it is always null. Never persisted. */
  audio: string | null;
  /** Data URI of an attached image, in memory only. Never persisted. */
  image: string | null;
  /** True when the message had an image, survives a reload even though the image does not. */
  hadImage: boolean;
  /** Error bubbles (422/429/502/network) are shown but never persisted. */
  error: boolean;
}

/** Progress event sent by POST /chat/stream while the graph runs. Never persisted. */
export interface StreamStatus {
  stage: 'thinking' | 'vision' | 'tool' | 'writing';
  tool?: string;
}

export interface StoredChat {
  threadId: string;
  messages: ChatMessage[];
}
