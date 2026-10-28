import { useEffect, type ReactNode } from "react";

/** Kerangka dialog. Escape menutupnya selama `dismissable`; dialog yang sedang
 *  menampilkan rahasia mematikannya supaya tidak tertutup tanpa sengaja. */
export function Modal({
  titleId,
  onClose,
  dismissable = true,
  children,
}: {
  titleId: string;
  onClose: () => void;
  dismissable?: boolean;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!dismissable) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dismissable, onClose]);

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        {children}
      </div>
    </div>
  );
}
