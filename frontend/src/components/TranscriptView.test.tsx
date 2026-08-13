import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TranscriptView } from "./TranscriptView";
import type { TranscriptLine } from "../hooks/useJarvisSession";

describe("TranscriptView", () => {
  it("renders the empty-transcript hint when there are no lines", () => {
    render(<TranscriptView lines={[]} />);
    expect(screen.getByTestId("transcript")).toBeInTheDocument();
    const hint = screen.getByTestId("transcript-empty");
    expect(hint.textContent?.trim().length).toBeGreaterThan(0);
    expect(screen.queryAllByTestId("transcript-line")).toHaveLength(0);
  });

  it("renders each line with its pinned data hooks and text, in order", () => {
    const lines: TranscriptLine[] = [
      { id: "1", role: "user", text: "What's the weather?", final: true },
      { id: "2", role: "assistant", text: "Let me check", final: false },
    ];
    render(<TranscriptView lines={lines} />);

    expect(screen.queryByTestId("transcript-empty")).not.toBeInTheDocument();
    const rendered = screen.getAllByTestId("transcript-line");
    expect(rendered).toHaveLength(2);

    expect(rendered[0]).toHaveAttribute("data-role", "user");
    expect(rendered[0]).toHaveAttribute("data-final", "true");
    expect(rendered[0]).toHaveTextContent("What's the weather?");

    expect(rendered[1]).toHaveAttribute("data-role", "assistant");
    expect(rendered[1]).toHaveAttribute("data-final", "false");
    expect(rendered[1]).toHaveTextContent("Let me check");
  });
});
