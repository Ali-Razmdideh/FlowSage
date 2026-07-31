// figma-plugin/src/ui/main.tsx
import { createRoot } from "react-dom/client";

function App() {
  return <div style={{ fontFamily: "sans-serif", padding: 12 }}>FlowSage plugin loading…</div>;
}

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(<App />);
}
