import { describe, expect, it } from "vitest";
import { nextShipCode } from "./shipCode";

describe("nextShipCode", () => {
  it("armada kosong dimulai dari SHIP-001", () => {
    expect(nextShipCode([])).toBe("SHIP-001");
  });

  it("melanjutkan dari nomor terbesar, bukan dari jumlah kapal", () => {
    expect(nextShipCode(["SHIP-001", "SHIP-003"])).toBe("SHIP-004");
    expect(nextShipCode(["SHIP-001", "SHIP-002", "SHIP-048"])).toBe("SHIP-049");
  });

  it("kode berbentuk lain diabaikan dan huruf kecil tetap dihitung", () => {
    expect(nextShipCode(["SHIP-PI-TRY01", "ship-5", "KM-DGT"])).toBe("SHIP-006");
  });

  it("tetap rapi saat melewati tiga digit", () => {
    expect(nextShipCode(["SHIP-999"])).toBe("SHIP-1000");
  });
});
