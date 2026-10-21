// @vitest-environment node
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import {
  buildInstallScript,
  installScriptFileName,
  isReachableFromShip,
  type InstallScriptInput,
} from "./installScript";

const base: InstallScriptInput = {
  shipName: "KM Sinar Jaya",
  slug: "SHIP-049",
  centralUrl: "https://fleetview.spil.example",
  clientId: "ship-049",
  secret: "NesSm_6q3GlDZt19SKRR9DTtFJUIZY34PVoRkQKvmjM",
  panelHost: "192.168.100.100",
};

function snippet(script: string, name: string): string {
  const match = new RegExp(`<<'${name}'[^\\n]*\\n([\\s\\S]*?)\\n${name}\\n`).exec(script);
  if (!match) throw new Error(`blok ${name} tidak ditemukan`);
  return match[1]!;
}

function bashSyntaxOk(script: string): { ok: boolean; stderr: string } {
  const run = spawnSync("bash", ["-n"], { input: script, encoding: "utf8" });
  return { ok: run.status === 0, stderr: run.stderr };
}

describe("buildInstallScript", () => {
  it("dimulai dengan shebang dan berhenti pada kesalahan pertama", () => {
    const script = buildInstallScript(base);
    expect(script.startsWith("#!/usr/bin/env bash\n")).toBe(true);
    expect(script).toContain("set -euo pipefail");
    expect(script).not.toMatch(/set -[a-z]*x/);
  });

  it("memuat kredensial kapal ini, masing-masing dikutip", () => {
    const script = buildInstallScript(base);
    expect(script).toContain("CENTRAL_URL='https://fleetview.spil.example'");
    expect(script).toContain("CLIENT_ID='ship-049'");
    expect(script).toContain(`SECRET='${base.secret}'`);
  });

  it("rahasia hanya muncul satu kali, tidak pernah dicetak atau dilewatkan sebagai argumen", () => {
    const script = buildInstallScript(base);
    expect(script.split(base.secret)).toHaveLength(2);
  });

  it("karakter kutip di dalam nilai tidak membuka celah penyuntikan perintah", () => {
    const script = buildInstallScript({
      ...base,
      secret: "a'b$(touch /tmp/x)`id`",
      clientId: "x'; rm -rf / #",
    });
    expect(script).toContain(`SECRET='a'\\''b$(touch /tmp/x)\`id\`'`);
    expect(bashSyntaxOk(script).ok).toBe(true);
  });

  it("nama kapal di komentar tidak bisa menyisipkan baris perintah", () => {
    const script = buildInstallScript({ ...base, shipName: "KM Jahat\nrm -rf /\n" });
    expect(script).not.toMatch(/^rm -rf \/$/m);
  });

  it("alamat panel mengubah adapter ke lp_a104", () => {
    const script = buildInstallScript(base);
    expect(script).toContain("PANEL_HOST='192.168.100.100'");
    expect(script).toContain('collector["adapter"] = "lp_a104"');
  });

  it("tanpa alamat panel menyebut dengan jelas bahwa datanya simulasi", () => {
    const script = buildInstallScript({ ...base, panelHost: "" });
    expect(script).toContain("PANEL_HOST=''");
    expect(script).toMatch(/BUKAN data asli/);
  });

  it("lolos pemeriksaan sintaks bash", () => {
    const run = bashSyntaxOk(buildInstallScript(base));
    expect(run.stderr).toBe("");
    expect(run.ok).toBe(true);
  });

  it("jalur kode bisa diganti saat dijalankan, untuk kapal tanpa internet", () => {
    const script = buildInstallScript(base);
    expect(script).toContain("FLEETVIEW_EDGE_SRC");
    expect(script).toContain("FLEETVIEW_EDGE_REPO_URL");
  });

  it("menghapus dirinya setelah berhasil", () => {
    expect(buildInstallScript(base)).toMatch(/rm -f -- "\$\{BASH_SOURCE\[0\]\}"/);
  });
});

describe("installScriptFileName", () => {
  it("memuat kode kapal dan aman untuk nama berkas", () => {
    expect(installScriptFileName("SHIP-049")).toBe("pasang-fleetview-SHIP-049.sh");
    expect(installScriptFileName("../a b")).not.toMatch(/[/\\ ]/);
  });
});

describe("isReachableFromShip", () => {
  it.each([
    ["https://fleetview.spil.example", true],
    ["http://10.20.0.5:8000", true],
    ["http://localhost:5173", false],
    ["http://127.0.0.1:8000", false],
    ["http://[::1]:8000", false],
    ["bukan url", false],
  ])("%s -> %s", (url, expected) => {
    expect(isReachableFromShip(url)).toBe(expected);
  });
});

/** Blok Python di dalam skrip dijalankan sungguhan terhadap server palsu, karena
 *  di situlah kredensial berpindah dari skrip ke Edge Console di kapal. */
describe("blok setup (PY_SETUP)", () => {
  const code = snippet(buildInstallScript(base), "PY_SETUP");

  async function jalankan(respond: (req: { method: string; body: string }) => {
    status: number;
    location?: string;
  }): Promise<{ status: number | null; stdout: string; seen: string[] }> {
    const seen: string[] = [];
    const server = createServer((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        seen.push(`${req.method} ${req.url} ${body}`);
        if (req.method === "GET") {
          res.writeHead(200).end("ok");
          return;
        }
        const r = respond({ method: req.method ?? "", body });
        res.writeHead(r.status, r.location ? { Location: r.location } : {}).end();
      });
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const { port } = server.address() as AddressInfo;
    try {
      return await new Promise((resolve) => {
        const child = spawn("python3", ["-I", "-"], {
          env: {
            PATH: process.env.PATH ?? "",
            CONSOLE_URL: `http://127.0.0.1:${port}`,
            CENTRAL_URL: base.centralUrl,
            CLIENT_ID: base.clientId,
            SECRET: base.secret,
            PIN: "123456",
          },
        });
        let stdout = "";
        child.stdout.on("data", (d) => (stdout += d));
        child.stderr.on("data", (d) => (stdout += d));
        child.on("close", (status) => resolve({ status, stdout, seen }));
        child.stdin.end(code);
      });
    } finally {
      server.close();
    }
  }

  it("berhasil bila Edge Console mengalihkan ke ?selesai=1, dan mengirim semua isian", async () => {
    const result = await jalankan(() => ({ status: 303, location: "/setup?selesai=1" }));
    expect(result.status).toBe(0);
    const post = result.seen.find((s) => s.startsWith("POST"))!;
    expect(post).toContain("central_url=https%3A%2F%2Ffleetview.spil.example");
    expect(post).toContain("client_id=ship-049");
    expect(post).toContain(`secret=${encodeURIComponent(base.secret)}`);
    expect(post).toContain("pin=123456");
  });

  it("gagal dengan pesan dari Edge Console bila kredensial ditolak", async () => {
    const result = await jalankan(() => ({
      status: 303,
      location: `/setup?${new URLSearchParams({ error: "central menolak kredensial ini" })}`,
    }));
    expect(result.status).toBe(1);
    expect(result.stdout).toContain("central menolak kredensial ini");
  });

  it("gagal dengan jelas bila Edge Console tidak menyala", async () => {
    const run = spawnSync("python3", ["-I", "-"], {
      input: code.replace("range(45)", "range(1)").replace("time.sleep(2)", "time.sleep(0)"),
      env: { PATH: process.env.PATH ?? "", CONSOLE_URL: "http://127.0.0.1:9", PIN: "1" , CENTRAL_URL: "x", CLIENT_ID: "x", SECRET: "x" },
      encoding: "utf8",
    });
    expect(run.status).not.toBe(0);
    expect(`${run.stdout}${run.stderr}`).toMatch(/Edge Console/);
  });
});

describe("blok config (PY_CONFIG)", () => {
  const venvPython = join(process.cwd(), "..", "..", ".venv", "bin", "python");
  const code = snippet(buildInstallScript(base), "PY_CONFIG");

  it.skipIf(!existsSync(venvPython))(
    "mengubah adapter dan alamat panel tanpa menghapus bagian config lain",
    () => {
      const dir = mkdtempSync(join(tmpdir(), "fv-config-"));
      const path = join(dir, "edge.yaml");
      writeFileSync(
        path,
        "collector:\n  adapter: simulator\n  sensors_path: /etc/fleetview/sensors.yaml\nstorage:\n  influx_url: http://127.0.0.1:8086\n",
      );
      const run = spawnSync(venvPython, ["-I", "-"], {
        input: code,
        env: { PATH: process.env.PATH ?? "", EDGE_CONFIG: path, PANEL_HOST: "192.168.100.100" },
        encoding: "utf8",
      });
      expect(run.status, run.stderr).toBe(0);
      const text = readFileSync(path, "utf8");
      expect(text).toContain("adapter: lp_a104");
      expect(text).toContain("lp_a104_host: 192.168.100.100");
      expect(text).toContain("sensors_path: /etc/fleetview/sensors.yaml");
      expect(text).toContain("influx_url: http://127.0.0.1:8086");
    },
  );
});
