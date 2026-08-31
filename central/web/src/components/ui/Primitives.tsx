import type { ReactNode } from "react";

export function Section({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <header className="section__head">
        <div>
          <h2 className="section__title">{title}</h2>
          {description && <p className="section__desc">{description}</p>}
        </div>
        {actions && <div className="section__actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function Panel({ children, flush = false }: { children: ReactNode; flush?: boolean }) {
  return <div className={flush ? "panel panel--flush" : "panel"}>{children}</div>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <dt className="field__label">{label}</dt>
      <dd className="field__value">{children}</dd>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {hint && <p className="empty__hint">{hint}</p>}
    </div>
  );
}

export function Loading({ label = "Memuat" }: { label?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      {label}…
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Terjadi kesalahan";
  return (
    <div className="error-state" role="alert">
      <p className="error-state__msg">{message}</p>
      {onRetry && (
        <button type="button" className="btn btn--ghost" onClick={onRetry}>
          Coba lagi
        </button>
      )}
    </div>
  );
}
