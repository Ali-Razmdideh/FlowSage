// figma-plugin/src/code.ts
import { base64ToUint8Array, uint8ArrayToBase64 } from "./shared/binary";
import { computeCardPosition } from "./shared/annotationLayout";
import { groupIssuesByFrameIndex } from "./shared/simulationClient";
import type { FrictionIssue } from "./shared/types";

figma.showUI(__html__, { width: 360, height: 520 });

const SEVERITY_COLORS: Record<FrictionIssue["severity"], RGB> = {
  low: { r: 0.29, g: 0.58, b: 0.9 },
  medium: { r: 0.95, g: 0.7, b: 0.2 },
  high: { r: 0.92, g: 0.45, b: 0.2 },
  critical: { r: 0.85, g: 0.2, b: 0.2 },
};

interface PluginMessage {
  id: string;
  type: "get-settings" | "save-settings" | "export-selection" | "annotate";
  payload?: unknown;
}

let lastExportedFrames: SceneNode[] = [];

async function handleGetSettings(): Promise<{ baseUrl: string; apiKey: string }> {
  const baseUrl = (await figma.clientStorage.getAsync("baseUrl")) ?? "";
  const apiKey = (await figma.clientStorage.getAsync("apiKey")) ?? "";
  return { baseUrl, apiKey };
}

async function handleSaveSettings(payload: { baseUrl: string; apiKey: string }): Promise<void> {
  await figma.clientStorage.setAsync("baseUrl", payload.baseUrl);
  await figma.clientStorage.setAsync("apiKey", payload.apiKey);
}

async function handleExportSelection(): Promise<{ index: number; bytes: number[] }[]> {
  const exportable = figma.currentPage.selection.filter(
    (node): node is SceneNode & ExportMixin => "exportAsync" in node,
  );
  if (exportable.length === 0) {
    throw new Error("Select at least one frame before running a simulation.");
  }

  lastExportedFrames = exportable;
  const results: { index: number; bytes: number[] }[] = [];
  for (let index = 0; index < exportable.length; index++) {
    const bytes = await exportable[index].exportAsync({ format: "PNG" });
    const base64 = uint8ArrayToBase64(bytes);
    results.push({ index, bytes: Array.from(base64ToUint8Array(base64)) });
  }
  return results;
}

function createAnnotationCard(
  issue: FrictionIssue,
  position: { x: number; y: number },
): FrameNode {
  const card = figma.createFrame();
  card.name = `FlowSage: ${issue.title}`;
  card.layoutMode = "VERTICAL";
  card.itemSpacing = 6;
  card.paddingTop = 12;
  card.paddingBottom = 12;
  card.paddingLeft = 12;
  card.paddingRight = 12;
  card.resize(280, card.height);
  card.x = position.x;
  card.y = position.y;
  card.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
  card.strokes = [{ type: "SOLID", color: SEVERITY_COLORS[issue.severity] }];
  card.strokeWeight = 2;

  const severityPill = figma.createText();
  severityPill.characters = issue.severity.toUpperCase();
  severityPill.fills = [{ type: "SOLID", color: SEVERITY_COLORS[issue.severity] }];
  card.appendChild(severityPill);

  const title = figma.createText();
  title.characters = issue.title;
  card.appendChild(title);

  const heuristic = figma.createText();
  heuristic.characters = issue.heuristic_violated;
  card.appendChild(heuristic);

  const suggestedFix = figma.createText();
  suggestedFix.characters = issue.suggested_fix;
  suggestedFix.textAutoResize = "HEIGHT";
  suggestedFix.resize(256, suggestedFix.height);
  card.appendChild(suggestedFix);

  return card;
}

async function handleAnnotate(payload: { issues: FrictionIssue[] }): Promise<void> {
  await figma.loadFontAsync({ family: "Inter", style: "Regular" });
  const grouped = groupIssuesByFrameIndex(payload.issues);

  for (const [frameIndex, issues] of grouped) {
    const sourceNode = lastExportedFrames[frameIndex];
    if (!sourceNode || !("x" in sourceNode)) continue;
    const bounds = {
      x: sourceNode.x,
      y: sourceNode.y,
      width: "width" in sourceNode ? sourceNode.width : 0,
      height: "height" in sourceNode ? sourceNode.height : 0,
    };

    issues.forEach((issue, stackIndex) => {
      const position = computeCardPosition(bounds, stackIndex);
      createAnnotationCard(issue, position);
    });
  }

  figma.notify(`Annotated ${payload.issues.length} issue(s) on the canvas`);
}

figma.ui.onmessage = async (message: PluginMessage) => {
  try {
    let payload: unknown;
    switch (message.type) {
      case "get-settings":
        payload = await handleGetSettings();
        break;
      case "save-settings":
        await handleSaveSettings(message.payload as { baseUrl: string; apiKey: string });
        break;
      case "export-selection":
        payload = await handleExportSelection();
        break;
      case "annotate":
        await handleAnnotate(message.payload as { issues: FrictionIssue[] });
        break;
    }
    figma.ui.postMessage({ pluginMessage: { id: message.id, payload } });
  } catch (error) {
    figma.ui.postMessage({
      pluginMessage: {
        id: message.id,
        payload: undefined,
        error: error instanceof Error ? error.message : "Unknown plugin error",
      },
    });
  }
};
