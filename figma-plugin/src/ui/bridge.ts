// figma-plugin/src/ui/bridge.ts
let counter = 0;
const pending = new Map<string, { resolve: (payload: unknown) => void; reject: (error: Error) => void }>();

window.addEventListener("message", (event: MessageEvent) => {
  const message = (
    event.data as { pluginMessage?: { id: string; payload: unknown; error?: string } }
  ).pluginMessage;
  if (!message || !pending.has(message.id)) return;
  const handlers = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) {
    handlers?.reject(new Error(message.error));
  } else {
    handlers?.resolve(message.payload);
  }
});

export function callPlugin<T>(type: string, payload?: unknown): Promise<T> {
  const id = `${type}-${counter++}`;
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { resolve: resolve as (payload: unknown) => void, reject });
    window.parent.postMessage({ pluginMessage: { id, type, payload } }, "*");
  });
}
