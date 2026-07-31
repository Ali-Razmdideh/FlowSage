import type { FrictionIssue } from "./types";

export function buildScreenFilename(index: number): string {
  return `${String(index + 1).padStart(3, "0")}.png`;
}

export function parseScreenIndex(screen: string): number {
  const parsed = Number.parseInt(screen, 10);
  if (Number.isNaN(parsed)) {
    throw new Error(`Cannot parse screen index from screen name: ${screen}`);
  }
  return parsed - 1;
}

export function groupIssuesByFrameIndex(issues: FrictionIssue[]): Map<number, FrictionIssue[]> {
  const grouped = new Map<number, FrictionIssue[]>();
  for (const issue of issues) {
    const index = parseScreenIndex(issue.screen);
    const existing = grouped.get(index);
    if (existing) {
      existing.push(issue);
    } else {
      grouped.set(index, [issue]);
    }
  }
  return grouped;
}
