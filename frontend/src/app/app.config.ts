import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';

// No HttpClient: ChatService reads the SSE stream with fetch.
export const appConfig: ApplicationConfig = {
  providers: [provideBrowserGlobalErrorListeners()],
};
