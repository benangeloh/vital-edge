import { describe, expect, it } from "vitest";
import { metricLabel } from "./metrics";

describe("metricLabel", () => {
  it.each([
    ["rpm", "Putaran mesin"],
    ["temperature", "Suhu"],
    ["pressure", "Tekanan"],
    ["fuel_level", "Level bahan bakar"],
    ["fuel_flow", "Aliran bahan bakar"],
    ["engine_load", "Beban mesin"],
    ["voltage", "Tegangan"],
    ["position", "Posisi GPS"],
    ["raw", "Data mentah panel"],
  ])("%s menjadi %s", (metric, label) => {
    expect(metricLabel(metric)).toBe(label);
  });

  it("besaran yang belum dikenal tetap terbaca, bukan kode mentah", () => {
    expect(metricLabel("water_depth")).toBe("Water depth");
  });
});
