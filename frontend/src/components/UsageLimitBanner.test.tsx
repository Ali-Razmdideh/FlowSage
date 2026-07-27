import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { UsageLimitBanner } from "./UsageLimitBanner";

describe("UsageLimitBanner", () => {
  it("renders nothing when message is null", () => {
    const { container } = render(
      <MemoryRouter>
        <UsageLimitBanner message={null} />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the message and an upgrade link when present", () => {
    render(
      <MemoryRouter>
        <UsageLimitBanner message="Free plan limit reached for runs (5/5). Upgrade to continue." />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Free plan limit reached for runs/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upgrade/i })).toHaveAttribute("href", "/settings/billing");
  });
});
