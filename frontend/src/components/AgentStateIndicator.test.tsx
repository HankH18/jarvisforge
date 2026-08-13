import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentStateIndicator } from "./AgentStateIndicator";

describe("AgentStateIndicator", () => {
  it('shows "Not connected" with data-state="none" when state is null', () => {
    render(<AgentStateIndicator state={null} status="idle" />);
    const el = screen.getByTestId("agent-state");
    expect(el).toHaveAttribute("data-state", "none");
    expect(el).toHaveAttribute("role", "status");
    expect(el).toHaveAttribute("aria-live", "polite");
    expect(el).toHaveTextContent("Not connected");
  });

  it('shows "Not connected" for a null state even while connecting', () => {
    render(<AgentStateIndicator state={null} status="connecting" />);
    const el = screen.getByTestId("agent-state");
    expect(el).toHaveAttribute("data-state", "none");
    expect(el).toHaveTextContent("Not connected");
  });

  it('shows data-state="listening" and the label "Listening"', () => {
    render(<AgentStateIndicator state="listening" status="connected" />);
    const el = screen.getByTestId("agent-state");
    expect(el).toHaveAttribute("data-state", "listening");
    expect(el).toHaveTextContent("Listening");
  });

  it('shows data-state="thinking" and the label "Thinking"', () => {
    render(<AgentStateIndicator state="thinking" status="connected" />);
    const el = screen.getByTestId("agent-state");
    expect(el).toHaveAttribute("data-state", "thinking");
    expect(el).toHaveTextContent("Thinking");
  });

  it('shows data-state="working" and the label "Working"', () => {
    render(<AgentStateIndicator state="working" status="connected" />);
    const el = screen.getByTestId("agent-state");
    expect(el).toHaveAttribute("data-state", "working");
    expect(el).toHaveTextContent("Working");
  });

  it('shows data-state="speaking" and the label "Speaking"', () => {
    render(<AgentStateIndicator state="speaking" status="connected" />);
    const el = screen.getByTestId("agent-state");
    expect(el).toHaveAttribute("data-state", "speaking");
    expect(el).toHaveTextContent("Speaking");
  });
});
