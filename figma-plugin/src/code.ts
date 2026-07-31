// figma-plugin/src/code.ts
figma.showUI(__html__, { width: 340, height: 480 });

figma.ui.onmessage = (msg: { id: string; type: string; payload?: unknown }) => {
  console.log("received message", msg.type);
};
