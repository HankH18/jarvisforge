import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders the Get started heading", () => {
  render(<App />);
  expect(
    screen.getByRole("heading", { name: /get started/i }),
  ).toBeInTheDocument();
});
