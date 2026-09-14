import { afterRenderEffect, Component, computed, ElementRef, inject, signal, viewChild } from '@angular/core';

import { ChatMessage } from './chat/chat.models';
import { ChatStore } from './chat/chat.store';
import { MAX_IMAGE_BYTES, prepareImage } from './chat/image-prep';
import { renderMarkdown } from './chat/markdown';
import { originsOf, statusText, toolLabel } from './chat/tool-labels';

const ACCEPTED_IMAGE_TYPES = 'image/jpeg,image/png,image/gif,image/webp';

export interface Suggestion {
  icon: string;
  title: string;
  hint: string;
  prompt: string;
}

export const SUGGESTIONS: Suggestion[] = [
  { icon: '💱', title: 'Dólar hoy', hint: 'Tasa de cambio USD/COP', prompt: '¿A cuánto está el dólar hoy?' },
  { icon: '📈', title: 'Simular inversión', hint: 'Interés compuesto', prompt: '¿Cuánto tendría si invierto 5.000.000 al 9% anual durante 4 años?' },
  { icon: '📄', title: 'Seguro de depósitos', hint: 'Base de conocimiento', prompt: '¿Qué cubre el seguro de depósitos de Fogafín?' },
  { icon: '₿', title: 'Bitcoin en pesos', hint: 'Mercado cripto', prompt: '¿A cuánto está el bitcoin en pesos?' },
  { icon: '📊', title: 'Acción de Ecopetrol', hint: 'Bolsa de valores', prompt: '¿En cuánto cerró la acción de Ecopetrol?' },
  { icon: '🔑', title: 'Llaves Bre-B', hint: 'Pagos inmediatos', prompt: '¿Qué es una llave de Bre-B?' },
];

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
})
export class App {
  protected readonly store = inject(ChatStore);
  protected readonly draft = signal('');
  protected readonly attachment = signal<string | null>(null);
  protected readonly attachmentError = signal<string | null>(null);
  protected readonly preparingImage = signal(false);
  protected readonly acceptedImageTypes = ACCEPTED_IMAGE_TYPES;
  protected readonly suggestions = SUGGESTIONS;
  protected readonly toolLabel = toolLabel;
  protected readonly originsOf = originsOf;
  protected readonly statusLine = computed(() => statusText(this.store.status()));
  protected readonly canSend = computed(
    () =>
      !this.store.sending() &&
      !this.preparingImage() &&
      (this.draft().trim().length > 0 || this.attachment() !== null),
  );

  private readonly scroller = viewChild<ElementRef<HTMLElement>>('scroller');
  private readonly fileInput = viewChild<ElementRef<HTMLInputElement>>('fileInput');

  constructor() {
    // Keep the newest message and the progress card in view after each render.
    afterRenderEffect(() => {
      this.store.messages();
      this.store.status();
      this.store.sending();
      const el = this.scroller()?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  protected html(message: ChatMessage): string {
    return renderMarkdown(message.content);
  }

  protected async submit(event?: Event): Promise<void> {
    event?.preventDefault();
    if (!this.canSend()) return;
    const text = this.draft();
    const image = this.attachment();
    this.draft.set('');
    this.clearAttachment();
    await this.store.send(text, image);
  }

  protected async ask(suggestion: Suggestion): Promise<void> {
    if (this.store.sending()) return;
    await this.store.send(suggestion.prompt, null);
  }

  protected onKeydown(event: KeyboardEvent): void {
    // Enter sends, Shift+Enter inserts a line break.
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      void this.submit(event);
    }
  }

  protected pickImage(): void {
    this.fileInput()?.nativeElement.click();
  }

  protected async onFileSelected(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    this.attachmentError.set(null);
    if (!file) return;
    // Early feedback only: the backend validates format and size again.
    if (file.size > MAX_IMAGE_BYTES) {
      this.attachmentError.set('La imagen supera el tamaño máximo de 5 MB.');
      return;
    }
    this.preparingImage.set(true);
    try {
      // Small images are upscaled so the vision model gets enough detail to read figures.
      this.attachment.set(await prepareImage(file));
    } catch {
      this.attachmentError.set('No se pudo leer la imagen.');
    } finally {
      this.preparingImage.set(false);
    }
  }

  protected clearAttachment(): void {
    this.attachment.set(null);
    this.attachmentError.set(null);
  }

  protected newConversation(): void {
    this.clearAttachment();
    this.draft.set('');
    this.store.newConversation();
  }
}
