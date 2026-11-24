import { describe, expect, it } from "vitest";
import { runScenarios } from "./scenarios";

describe("gates scenarios", () => {
  for (const result of runScenarios()) {
    it(result.name, () => {
      expect(result.ok, result.detail).toBe(true);
    });
  }
});
