// figma-plugin/src/ui/bridge.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { callPlugin } from "./bridge";

describe("callPlugin", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts a message to the parent and resolves when a matching reply arrives", async () => {
    const postMessageSpy = vi.spyOn(window.parent, "postMessage").mockImplementation(() => {});

    const resultPromise = callPlugin<{ ok: boolean }>("get-settings");

    expect(postMessageSpy).toHaveBeenCalledTimes(1);
    const sentMessage = postMessageSpy.mock.calls[0][0] as {
      pluginMessage: { id: string; type: string };
    };
    expect(sentMessage.pluginMessage.type).toBe("get-settings");

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { pluginMessage: { id: sentMessage.pluginMessage.id, payload: { ok: true } } },
      }),
    );

    await expect(resultPromise).resolves.toEqual({ ok: true });
  });

  it("ignores messages with an unrecognized id", async () => {
    vi.spyOn(window.parent, "postMessage").mockImplementation(() => {});
    const resultPromise = callPlugin<{ ok: boolean }>("get-settings");

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { pluginMessage: { id: "some-other-id", payload: { ok: false } } },
      }),
    );

    let resolved = false;
    resultPromise.then(() => {
      resolved = true;
    });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(resolved).toBe(false);
  });
});
