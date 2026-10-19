import { useState } from "react";
import { useLogin } from "@/lib/queries";

export function Login({ onAuthenticated }: { onAuthenticated: () => void }) {
  const login = useLogin(onAuthenticated);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <main className="login">
      <img src="/login-bg.webp" alt="" className="login__bg" />

      <header className="login__topbar">
        <div className="login__brand-lockup">
          <img src="/spil-logo.webp" alt="" className="login__brand-icon" />
          <div>
            <div className="login__brand-name">
              FleetView<span className="login__sub">SPIL</span>
            </div>
            <p className="login__brand-tagline">Monitoring Data Armada Kapal</p>
          </div>
        </div>
        <div className="login__motto">
          <span className="login__motto-text">
            Pemantauan data telemetry armada real-time
            <br />
            70+ kapal terhubung
          </span>
          <span className="login__motto-rule" aria-hidden="true" />
        </div>
      </header>

      <div className="login__main">
        {/* Satu kartu utuh — headline dan form dulu dua elemen mengambang
            terpisah dengan jarak yang berubah-ubah tergantung lebar layar.
            Digabung jadi satu panel supaya marginnya selalu simetris dan
            konsisten di ukuran layar berapa pun. */}
        <div className="login__panel">
          <div className="login__panel-brand">
            <h1 className="login__headline">
              Pantau Kendalikan
              <br />
              <span className="login__headline-accent">Berlayar aman</span>
            </h1>
            <span className="login__headline-rule" aria-hidden="true" />
            <p className="login__panel-copy">
              Sistem monitoring telemetry armada real-time: offline-first, data
              selalu tersimpan di kapal sebelum tersinkron ke pusat.
            </p>
          </div>

          <form
            className="login__panel-form"
            onSubmit={(e) => {
              e.preventDefault();
              login.mutate({ username, password });
            }}
          >
            <div className="login__card-brand">
              <img src="/spil-logo.webp" alt="" className="login__mark" />
              <span>
                FleetView<span className="login__sub login__sub--dark">SPIL</span>
              </span>
            </div>

            <h2 className="login__title">Masuk</h2>

            <label className="login__field">
              <span>Nama pengguna</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                required
              />
            </label>

            <label className="login__field">
              <span>Kata sandi</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>

            {login.isError && (
              <p className="login__error" role="alert">
                {login.error instanceof Error ? login.error.message : "Gagal masuk"}
              </p>
            )}

            <button type="submit" className="btn btn--primary login__submit" disabled={login.isPending}>
              {login.isPending ? "Memeriksa…" : "Masuk"}
            </button>
          </form>
        </div>
      </div>

      <footer className="login__footer">
        <span className="login__footer-item">
          <span className="login__footer-rule" aria-hidden="true" />
          PT Salam Pacific Indonesia Lines
        </span>
        {/* <span className="login__footer-item login__footer-item--end">
          Pantau seluruh armada
          <span className="login__footer-rule" aria-hidden="true" />
        </span> */}
      </footer>
    </main>
  );
}
