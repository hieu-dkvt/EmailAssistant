import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Highlighted } from "./Highlighted";

describe("Highlighted", () => {
  it("đoạn khớp hiện bằng <mark>, text được escape", () => {
    const segments = [
      { text: "𠮷野家の", hit: false },
      { text: "見積", hit: true },
      { text: " <script>alert(1)</script>", hit: false },
    ];
    const { container } = render(
      <p>
        <Highlighted segments={segments} />
      </p>,
    );
    const marks = container.querySelectorAll("mark");
    expect([...marks].map((m) => m.textContent)).toEqual(["見積"]);
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toBe(segments.map((s) => s.text).join(""));
  });
});
