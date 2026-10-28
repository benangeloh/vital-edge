import { useCallback, useEffect, useId, useRef, useState } from "react";

export interface RowMenuItem {
  label: string;
  onSelect: () => void;
  /** Aksi yang mencabut atau menghapus sesuatu: diberi warna dan dipisah dari yang lain. */
  danger?: boolean;
}

/** Perkiraan tinggi menu, hanya untuk memutuskan buka ke atas atau ke bawah. */
const ESTIMATED_MENU_HEIGHT = 176;
const MENU_GAP = 4;

interface Placement {
  top?: number;
  bottom?: number;
  right: number;
}

/** Menu aksi satu baris tabel, dibuka dari tombol titik tiga.
 *
 *  Posisinya `fixed` dari koordinat pemicunya, bukan `absolute` di dalam sel:
 *  pembungkus tabel bisa menggulir, dan menu absolut di baris terbawah akan
 *  terpotong atau memaksa tabel ikut menggulir. */
export function RowMenu({ label, items }: { label: string; items: RowMenuItem[] }) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<Placement>({ right: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const close = useCallback((restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  const measure = useCallback((): Placement | null => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const right = window.innerWidth - rect.right;
    const flipUp = rect.bottom + ESTIMATED_MENU_HEIGHT > window.innerHeight;
    return flipUp
      ? { bottom: window.innerHeight - rect.top + MENU_GAP, right }
      : { top: rect.bottom + MENU_GAP, right };
  }, []);

  const toggle = () => {
    if (open) return close(false);
    const next = measure();
    if (!next) return;
    setPlacement(next);
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    menuRef.current
      ?.querySelector<HTMLElement>('[role="menuitem"]')
      ?.focus({ preventScroll: true });

    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close(false);
    };
    // Menu `fixed` tidak ikut bergeser saat halaman menggulir, jadi posisinya
    // dihitung ulang. Menutupnya justru keliru: menggulir baris ke area terlihat
    // saat tombol ditekan juga memicu scroll, dan menu langsung menghilang.
    const follow = () => {
      const next = measure();
      if (next) setPlacement(next);
    };
    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("resize", follow);
    window.addEventListener("scroll", follow, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("resize", follow);
      window.removeEventListener("scroll", follow, true);
    };
  }, [open, close, measure]);

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const entries = Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
    );
    const at = entries.indexOf(document.activeElement as HTMLElement);
    const focusAt = (i: number) => entries[(i + entries.length) % entries.length]?.focus();

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        focusAt(at + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        focusAt(at - 1);
        break;
      case "Home":
        event.preventDefault();
        focusAt(0);
        break;
      case "End":
        event.preventDefault();
        focusAt(entries.length - 1);
        break;
      case "Escape":
        event.preventDefault();
        close(true);
        break;
      case "Tab":
        close(false);
        break;
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="rowmenu__trigger"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={toggle}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <circle cx="3" cy="8" r="1.4" fill="currentColor" />
          <circle cx="8" cy="8" r="1.4" fill="currentColor" />
          <circle cx="13" cy="8" r="1.4" fill="currentColor" />
        </svg>
      </button>

      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          className="rowmenu"
          style={{ position: "fixed", ...placement }}
          onKeyDown={onMenuKeyDown}
        >
          {items.map((item, i) => (
            <div key={item.label} role="none">
              {item.danger && !items[i - 1]?.danger && (
                <div role="separator" className="rowmenu__sep" />
              )}
              <button
                type="button"
                role="menuitem"
                className={item.danger ? "rowmenu__item rowmenu__item--danger" : "rowmenu__item"}
                onClick={() => {
                  close(false);
                  item.onSelect();
                }}
              >
                {item.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
