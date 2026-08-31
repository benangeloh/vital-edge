import { useState } from "react";
import { useLogin } from "@/lib/queries";

export function Login({ onAuthenticated }: { onAuthenticated: () => void }) {
  const login = useLogin(onAuthenticated);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <main className="login">
      <form
        className="login__card"
        onSubmit={(e) => {
          e.preventDefault();
          login.mutate({ username, password });
        }}
      >
        <div className="login__brand">
          <span className="sidebar__mark" aria-hidden="true" />
          <span>
            FleetView<span className="login__sub">SPIL</span>
          </span>
        </div>
        <h1 className="login__title">Masuk</h1>

        <label className="login__field">
          <span>Nama pengguna</span>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
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

        <button type="submit" className="btn btn--primary" disabled={login.isPending}>
          {login.isPending ? "Memeriksa…" : "Masuk"}
        </button>
      </form>
    </main>
  );
}
