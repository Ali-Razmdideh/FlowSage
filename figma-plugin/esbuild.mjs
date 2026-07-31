// figma-plugin/esbuild.mjs
import { build } from "esbuild";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";

mkdirSync("dist", { recursive: true });

await build({
  entryPoints: ["src/code.ts"],
  bundle: true,
  outfile: "dist/code.js",
  target: "es2017",
  format: "iife",
});

await build({
  entryPoints: ["src/ui/main.tsx"],
  bundle: true,
  outfile: "dist/ui.js",
  target: "es2017",
  format: "iife",
  jsx: "automatic",
});

const template = readFileSync("ui-template.html", "utf-8");
const uiScript = readFileSync("dist/ui.js", "utf-8");
const html = template.replace("/* __UI_SCRIPT__ */", uiScript);
writeFileSync("dist/ui.html", html);

console.log("Built dist/code.js and dist/ui.html");
