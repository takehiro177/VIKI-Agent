import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { DailyTable, peakAnchor, UsageColumns, type UsageColumnsProps } from "./UsageColumns";

afterEach(() => {
  cleanup();
});

const DATES = ["2026-11-19", "2026-11-20", "2026-11-21"];
const VALUES: UsageColumnsProps["values"] = [
  { tokens: 100, calls: 2 },
  { tokens: 400, calls: 5 },
  { tokens: 200, calls: 3 },
];

function renderChart(values: UsageColumnsProps["values"] = VALUES) {
  render(
    <UsageColumns title="Daily tokens" dates={DATES} values={values} max={400} height={160} />,
  );
}

describe("UsageColumns", () => {
  test("renders one column per date, each its own hit area", () => {
    renderChart();
    expect(document.querySelectorAll(".bot-usage-columns-slot")).toHaveLength(DATES.length);
  });

  test("bar heights are proportional to max (scaled to the headroom-reduced plot), and a zero day draws no bar", () => {
    renderChart([
      { tokens: 100, calls: 1 },
      { tokens: 0, calls: 0 },
      { tokens: 400, calls: 1 },
    ]);
    // height=160 minus the 20px peak-label headroom (F2) leaves a 140px plot: 100/400 and
    // 400/400 of that is 35px and 140px.
    const bars = document.querySelectorAll(".bot-usage-columns-bar");
    expect(bars).toHaveLength(2);
    expect((bars[0] as HTMLElement).style.height).toBe("35px");
    expect((bars[1] as HTMLElement).style.height).toBe("140px");
  });

  test("labels only the peak column, with its compact count and its anchor (F11)", () => {
    renderChart();
    const peaks = document.querySelectorAll(".bot-usage-columns-peak");
    expect(peaks).toHaveLength(1);
    expect(peaks[0]?.textContent).toBe("400");
    // index 1 of 3 days is the middle third: centred.
    expect(peaks[0]?.getAttribute("data-anchor")).toBe("center");
    const slots = document.querySelectorAll(".bot-usage-columns-slot");
    expect(slots[1]?.contains(peaks[0] as Node)).toBe(true);
  });

  test("shows the first, middle and last date on the x axis", () => {
    renderChart();
    const labels = Array.from(document.querySelectorAll(".bot-usage-columns-xlabels span")).map(
      (node) => node.textContent,
    );
    expect(labels).toEqual(["Nov 19", "Nov 20", "Nov 21"]);
  });

  test("hover shows the tooltip and dims the other columns; leaving the plot clears it", () => {
    renderChart();
    const slots = document.querySelectorAll(".bot-usage-columns-slot");
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("");

    fireEvent.mouseEnter(slots[1] as HTMLElement);
    expect(status.textContent).toBe("Nov 20 · 400 tokens · 5 calls");
    expect((slots[0] as HTMLElement).style.opacity).toBe("0.45");
    expect((slots[1] as HTMLElement).style.opacity).toBe("1");

    fireEvent.mouseLeave(screen.getByRole("group"));
    expect(status.textContent).toBe("");
    expect((slots[0] as HTMLElement).style.opacity).toBe("1");
  });

  test("focus defaults to the last day; Left/Right/Home/End move it; Escape clears it", () => {
    renderChart();
    const group = screen.getByRole("group");
    const status = screen.getByRole("status");

    fireEvent.focus(group);
    expect(status.textContent).toBe("Nov 21 · 200 tokens · 3 calls");

    fireEvent.keyDown(group, { key: "ArrowLeft" });
    expect(status.textContent).toBe("Nov 20 · 400 tokens · 5 calls");

    fireEvent.keyDown(group, { key: "Home" });
    expect(status.textContent).toBe("Nov 19 · 100 tokens · 2 calls");

    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(status.textContent).toBe("Nov 20 · 400 tokens · 5 calls");

    fireEvent.keyDown(group, { key: "End" });
    expect(status.textContent).toBe("Nov 21 · 200 tokens · 3 calls");

    fireEvent.keyDown(group, { key: "Escape" });
    expect(status.textContent).toBe("");
  });

  test("a key that belongs to the IME moves nothing", () => {
    renderChart();
    const group = screen.getByRole("group");
    const status = screen.getByRole("status");
    fireEvent.focus(group);
    const before = status.textContent;
    fireEvent.keyDown(group, { key: "ArrowLeft", isComposing: true });
    expect(status.textContent).toBe(before);
  });

  test("the group's accessible name carries the title and the date range", () => {
    renderChart();
    expect(screen.getByRole("group").getAttribute("aria-label")).toBe(
      "Daily tokens · Nov 19–Nov 21",
    );
  });

  test("all-zero values show the empty state instead of a plot", () => {
    renderChart([
      { tokens: 0, calls: 0 },
      { tokens: 0, calls: 0 },
      { tokens: 0, calls: 0 },
    ]);
    expect(screen.getByText("No token use in this period.").textContent).toBe(
      "No token use in this period.",
    );
    expect(document.querySelector(".bot-usage-columns-slot")).toBeNull();
    expect(screen.queryByRole("group")).toBeNull();
  });
});

describe("peakAnchor", () => {
  test("index 0 is always in the first third: start", () => {
    expect(peakAnchor(0, 7)).toBe("start");
    expect(peakAnchor(0, 30)).toBe("start");
    expect(peakAnchor(0, 90)).toBe("start");
  });

  test("the last index is always in the last third: end", () => {
    expect(peakAnchor(6, 7)).toBe("end");
    expect(peakAnchor(29, 30)).toBe("end");
    expect(peakAnchor(89, 90)).toBe("end");
  });

  test("7 days: thirds at 7/3 (2.33) and 14/3 (4.67), so indices split 3/2/2", () => {
    expect([0, 1, 2, 3, 4, 5, 6].map((i) => peakAnchor(i, 7))).toEqual([
      "start",
      "start",
      "start",
      "center",
      "center",
      "end",
      "end",
    ]);
  });

  test("30 days: clean thirds of 10", () => {
    expect(peakAnchor(9, 30)).toBe("start");
    expect(peakAnchor(10, 30)).toBe("center");
    expect(peakAnchor(19, 30)).toBe("center");
    expect(peakAnchor(20, 30)).toBe("end");
  });

  test("90 days: clean thirds of 30", () => {
    expect(peakAnchor(29, 90)).toBe("start");
    expect(peakAnchor(30, 90)).toBe("center");
    expect(peakAnchor(59, 90)).toBe("center");
    expect(peakAnchor(60, 90)).toBe("end");
  });
});

describe("DailyTable", () => {
  test("rows run newest date first, with scope headers and right-aligned counts, in a labelled scroll region", () => {
    render(
      <DailyTable
        dates={DATES}
        series={[
          { label: "All Bots", values: [100, 400, 200] },
          { label: "Research Bot", values: [40, 100, 50] },
        ]}
      />,
    );

    // F13: the table sits in its own focusable, named scroll region.
    const region = screen.getByRole("region", { name: "Daily tokens" });
    expect(region.tabIndex).toBe(0);
    expect(region.querySelector("table")).toBeTruthy();

    expect(screen.getAllByRole("rowheader").map((node) => node.textContent)).toEqual([
      "Nov 21",
      "Nov 20",
      "Nov 19",
    ]);
    expect(screen.getAllByRole("columnheader").map((node) => node.textContent)).toEqual([
      "Date",
      "All Bots",
      "Research Bot",
    ]);

    const newestRow = screen.getByRole("rowheader", { name: "Nov 21" }).closest("tr");
    const cells = newestRow ? Array.from(newestRow.querySelectorAll("td")) : [];
    expect(cells.map((cell) => cell.textContent)).toEqual(["200", "50"]);
    expect(cells.every((cell) => cell.classList.contains("bot-usage-columns-num"))).toBe(true);
  });
});
