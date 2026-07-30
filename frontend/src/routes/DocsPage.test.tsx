import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DocsPage } from "./DocsPage";

describe("DocsPage", () => {
  it("renders the quickstart and send-events sections with real field names", () => {
    render(
      <MemoryRouter>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /quickstart/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /send events/i })).toBeInTheDocument();
    expect(screen.getByText("X-API-Key")).toBeInTheDocument();
    expect(screen.getByText("session_id")).toBeInTheDocument();
    expect(screen.getByText(/120\/minute/)).toBeInTheDocument();
  });

  it("renders the webhooks and reference sections with real field names", () => {
    render(
      <MemoryRouter>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /webhooks/i })).toBeInTheDocument();
    expect(screen.getByText("alert.triggered")).toBeInTheDocument();
    expect(screen.getByText("X-FlowSage-Signature")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /full api reference/i })).toHaveAttribute(
      "href",
      "/api/docs",
    );
  });
});
