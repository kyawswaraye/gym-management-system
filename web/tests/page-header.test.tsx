import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "@/components/layout/page-header";

describe("PageHeader", () => {
  it("renders the shared page-flow header and heading id", () => {
    const { container } = render(
      <PageHeader
        description="Search current members, or open Archived to find members who were archived."
        eyebrow="Member directory"
        title="Members"
        titleId="members-title"
      />,
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "Members" }),
    ).toHaveAttribute("id", "members-title");
    expect(screen.getByText("Member directory")).toBeInTheDocument();
    expect(
      screen.getByText(/open Archived to find members who were archived/),
    ).toBeInTheDocument();
    expect(container.querySelector("header")?.className ?? "").not.toMatch(
      /bg-slate-900|rounded-2xl/,
    );
  });

  it("renders optional actions and supporting header content", () => {
    render(
      <PageHeader
        actions={<span>Live overview</span>}
        description="Monitor member activity."
        eyebrow="Business overview"
        title="Owner dashboard"
        titleId="dashboard-title"
      >
        <span>Financial totals</span>
      </PageHeader>,
    );

    expect(screen.getByText("Live overview")).toBeInTheDocument();
    expect(screen.getByText("Financial totals")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "Owner dashboard" }),
    ).toHaveAttribute("id", "dashboard-title");
  });
});
