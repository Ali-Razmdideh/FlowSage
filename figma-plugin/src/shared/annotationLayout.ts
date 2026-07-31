export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const CARD_WIDTH = 280;
export const CARD_HEIGHT = 160;
export const CARD_GAP = 60;
export const CARD_VERTICAL_GAP = 24;

export function computeCardPosition(source: Bounds, stackIndex: number): { x: number; y: number } {
  return {
    x: source.x + source.width + CARD_GAP,
    y: source.y + stackIndex * (CARD_HEIGHT + CARD_VERTICAL_GAP),
  };
}
