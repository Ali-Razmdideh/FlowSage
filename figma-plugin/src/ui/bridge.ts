// figma-plugin/src/ui/bridge.ts
let counter = 0;
const pending = new Map<string, (payload: unknown) => void>();

window.addEventListener("message", (event: MessageEvent) => {
  const message = (event.data as { pluginMessage?: { id: string; payload: unknown } })
    .pluginMessage;
  if (!message || !pending.has(message.id)) return;
  const resolve = pending.get(message.id);
  pending.delete(message.id);
  resolve?.(message.payload);
});

export function callPlugin<T>(type: string, payload?: unknown): Promise<T> {
  const id = `${type}-${counter++}`;
  return new Promise<T>((resolve) => {
    pending.set(id, resolve as (payload: unknown) => void);
    window.parent.postMessage({ pluginMessage: { id, type, payload } }, "*");
  });
}
