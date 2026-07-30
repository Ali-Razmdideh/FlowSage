import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LandingPage } from "./LandingPage";

describe("LandingPage", () => {
  it("renders all three pillar headings", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /predictive engine/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /observational engine/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /calibration loop/i })).toBeInTheDocument();
  });

  it("renders all three pricing tiers with correct limits and links each to /login", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/1,000 events\/mo/i)).toBeInTheDocument();
    expect(screen.getByText(/\$49/)).toBeInTheDocument();
    expect(screen.getByText(/50,000 events\/mo/i)).toBeInTheDocument();
    expect(screen.getByText(/\$199/)).toBeInTheDocument();
    expect(screen.getByText(/500,000 events\/mo/i)).toBeInTheDocument();

    const loginLinks = screen.getAllByRole("link", { name: /log in/i });
    expect(loginLinks.length).toBeGreaterThanOrEqual(4);
    for (const link of loginLinks) {
      expect(link).toHaveAttribute("href", "/login");
    }
  });

  it("renders a footer with the current year", () => {
    render(
      <MemoryRouter>
        <LandingPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(new RegExp(`© ${new Date().getFullYear()} FlowSage`))).toBeInTheDocument();
  });
});
