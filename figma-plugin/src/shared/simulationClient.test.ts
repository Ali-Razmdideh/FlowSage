import { describe, expect, it } from "vitest";
import { buildScreenFilename, groupIssuesByFrameIndex, parseScreenIndex } from "./simulationClient";
import type { FrictionIssue } from "./types";

describe("buildScreenFilename", () => {
  it("zero-pads a 1-based index to 3 digits", () => {
    expect(buildScreenFilename(0)).toBe("001.png");
    expect(buildScreenFilename(9)).toBe("010.png");
    expect(buildScreenFilename(998)).toBe("999.png");
  });
});

describe("parseScreenIndex", () => {
  it("parses a zero-padded screen string back to a 0-based index", () => {
    expect(parseScreenIndex("001")).toBe(0);
    expect(parseScreenIndex("010")).toBe(9);
  });

  it("throws on a non-numeric screen string", () => {
    expect(() => parseScreenIndex("checkout")).toThrow();
  });
});

describe("groupIssuesByFrameIndex", () => {
  it("groups issues by their 0-based source frame index, preserving order", () => {
    const issues: FrictionIssue[] = [
      { id: "a", screen: "002", severity: "high", title: "t1" } as FrictionIssue,
      { id: "b", screen: "001", severity: "low", title: "t2" } as FrictionIssue,
      { id: "c", screen: "002", severity: "critical", title: "t3" } as FrictionIssue,
    ];

    const grouped = groupIssuesByFrameIndex(issues);

    expect(grouped.get(0)?.map((i) => i.id)).toEqual(["b"]);
    expect(grouped.get(1)?.map((i) => i.id)).toEqual(["a", "c"]);
  });
});
