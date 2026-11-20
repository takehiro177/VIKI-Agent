/**
 * fenceScanner: the shared CommonMark fence tracker splitPlanDetails and splitCardParts scan
 * their lines through. Each test feeds a short text's lines through one scanner, in order, and
 * checks the step() result for every line — true for a fence delimiter or fenced content, false
 * for ordinary text outside any fence.
 */
import { expect, test } from "vitest";
import { fenceScanner } from "./markdownFences";

function steps(text: string): boolean[] {
  const fence = fenceScanner();
  return text.split("\n").map((line) => fence.step(line));
}

test("same-type nesting by length: a shorter ``` run inside a ```` fence cannot close it", () => {
  const text = "````\n```\ncontent\n````\nafter";
  expect(steps(text)).toEqual([true, true, true, true, false]);
});

test("cross-type nesting: a ~~~ run inside a ``` fence cannot close it", () => {
  const text = "```\n~~~\ncontent\n```\nafter";
  expect(steps(text)).toEqual([true, true, true, true, false]);
});

test("cross-type nesting: a ``` run inside a ~~~ fence cannot close it", () => {
  const text = "~~~\n```\ncontent\n~~~\nafter";
  expect(steps(text)).toEqual([true, true, true, true, false]);
});

test("a closer with trailing text is not a closer", () => {
  const text = "```\n``` extra text\n```\nafter";
  expect(steps(text)).toEqual([true, true, true, false]);
});

test("a backtick opener whose info string has a backtick is not an opener", () => {
  const text = "``` `code`\nafter";
  expect(steps(text)).toEqual([false, false]);
});

test("4-space indentation is not a fence", () => {
  const text = "    ```\nafter";
  expect(steps(text)).toEqual([false, false]);
});

test("an unclosed fence runs to the end of the text", () => {
  const text = "```\nline one\nline two";
  expect(steps(text)).toEqual([true, true, true]);
});
