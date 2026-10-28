import { describe, expect, it } from "vitest";
import { canManageFleet } from "./permissions";

describe("canManageFleet", () => {
  it("admin dan fleet_manager boleh", () => {
    expect(canManageFleet(["admin"])).toBe(true);
    expect(canManageFleet(["fleet_manager"])).toBe(true);
    expect(canManageFleet(["viewer", "admin"])).toBe(true);
  });

  it("pengamat tanpa peran khusus tidak boleh", () => {
    expect(canManageFleet([])).toBe(false);
    expect(canManageFleet(["viewer"])).toBe(false);
  });

  it("selama peran belum dimuat, tombol disembunyikan", () => {
    expect(canManageFleet(undefined)).toBe(false);
  });
});
