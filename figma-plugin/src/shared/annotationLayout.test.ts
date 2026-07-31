import { describe, expect, it } from "vitest";
import { computeCardPosition, CARD_GAP, CARD_HEIGHT, CARD_VERTICAL_GAP } from "./annotationLayout";

describe("computeCardPosition", () => {
  it("places the first card to the right of the source frame", () => {
    const position = computeCardPosition({ x: 100, y: 200, width: 400, height: 800 }, 0);
    expect(position).toEqual({ x: 100 + 400 + CARD_GAP, y: 200 });
  });

  it("stacks subsequent cards vertically below the first", () => {
    const position = computeCardPosition({ x: 100, y: 200, width: 400, height: 800 }, 2);
    expect(position).toEqual({
      x: 100 + 400 + CARD_GAP,
      y: 200 + 2 * (CARD_HEIGHT + CARD_VERTICAL_GAP),
    });
  });
});
