import { describe, expect, it } from "vitest";
import { base64ToUint8Array, uint8ArrayToBase64 } from "./binary";

describe("binary encoding", () => {
  it("round-trips arbitrary bytes through base64", () => {
    const original = new Uint8Array([0, 1, 2, 137, 80, 78, 71, 255, 254, 13, 10]);
    const encoded = uint8ArrayToBase64(original);
    const decoded = base64ToUint8Array(encoded);
    expect(Array.from(decoded)).toEqual(Array.from(original));
  });

  it("handles an empty array", () => {
    expect(uint8ArrayToBase64(new Uint8Array([]))).toBe("");
    expect(Array.from(base64ToUint8Array(""))).toEqual([]);
  });
});
