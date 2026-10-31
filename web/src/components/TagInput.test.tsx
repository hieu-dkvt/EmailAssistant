import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { TagInput } from "./TagInput";

function Harness({
  initial = [],
  onSubmit,
  draft,
}: {
  initial?: string[];
  onSubmit?: () => void;
  draft?: { current: string };
}) {
  const [values, setValues] = useState(initial);
  const own = useRef("");
  const draftRef = draft ?? own;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit?.();
      }}
    >
      <TagInput
        id="from"
        label="From"
        hint="Địa chỉ người gửi."
        addLabel="Thêm địa chỉ"
        values={values}
        onChange={setValues}
        draftRef={draftRef}
      />
      <output data-testid="values">{JSON.stringify(values)}</output>
    </form>
  );
}

const values = () => JSON.parse(screen.getByTestId("values").textContent ?? "[]") as string[];

describe("TagInput", () => {
  it("Enter thêm giá trị và không gửi form", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    await userEvent.type(screen.getByLabelText("From"), "a@x.example{Enter}");
    expect(values()).toEqual(["a@x.example"]);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByLabelText("From")).toHaveValue("");
  });

  it("dấu phẩy thêm giá trị", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("From"), "a@x.example,b");
    expect(values()).toEqual(["a@x.example"]);
    expect(screen.getByLabelText("From")).toHaveValue("b");
  });

  it("rời ô thì thêm phần đang gõ", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("From"), "a@x.example");
    await userEvent.tab();
    expect(values()).toEqual(["a@x.example"]);
  });

  it("Backspace ở ô trống bỏ giá trị cuối", async () => {
    render(<Harness initial={["a@x.example", "b@x.example"]} />);
    await userEvent.type(screen.getByLabelText("From"), "{Backspace}");
    expect(values()).toEqual(["a@x.example"]);
  });

  it("dán nhiều giá trị cách nhau bằng dấu phẩy hoặc xuống dòng", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByLabelText("From"));
    await userEvent.paste("a@x.example, b@x.example\nc@x.example");
    expect(values()).toEqual(["a@x.example", "b@x.example", "c@x.example"]);
  });

  it("nút bỏ có aria-label và bỏ đúng giá trị", async () => {
    render(<Harness initial={["a@x.example", "b@x.example"]} />);
    await userEvent.click(screen.getByRole("button", { name: "Bỏ a@x.example" }));
    expect(values()).toEqual(["b@x.example"]);
  });

  it("không thêm trùng (không phân biệt hoa thường)", async () => {
    render(<Harness initial={["a@x.example"]} />);
    await userEvent.type(screen.getByLabelText("From"), "A@x.example{Enter}");
    expect(values()).toEqual(["a@x.example"]);
  });

  it("draftRef giữ phần đang gõ chưa thêm", async () => {
    const draft = { current: "" };
    render(<Harness draft={draft} />);
    await userEvent.type(screen.getByLabelText("From"), "dở");
    expect(draft.current).toBe("dở");
    await userEvent.tab();
    expect(draft.current).toBe("");
  });

  it("Enter khi đang soạn bằng IME không thêm giá trị, Enter thường sau đó thì thêm", async () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    const input = screen.getByLabelText("From");
    await userEvent.type(input, "【サンプルA");
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(values()).toEqual([]);
    expect(input).toHaveValue("【サンプルA");
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(values()).toEqual(["【サンプルA"]);
    expect(input).toHaveValue("");
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
