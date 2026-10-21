export const PAGE_SIZE = 25;

export interface PagerProps {
  page: number;
  total: number;
  pageSize?: number;
  onPage: (page: number) => void;
}

/** Penomoran halaman daftar. Tidak muncul bila semuanya muat di satu halaman. */
export function Pager({ page, total, pageSize = PAGE_SIZE, onPage }: PagerProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  const current = Math.min(page, pages - 1);

  return (
    <nav className="pager" aria-label="Halaman daftar">
      <p className="pager__info tabular">
        Menampilkan {current * pageSize + 1} sampai {Math.min((current + 1) * pageSize, total)} dari{" "}
        {total}
      </p>
      <div className="pager__btns">
        <button
          type="button"
          className="btn btn--outline"
          disabled={current === 0}
          onClick={() => onPage(current - 1)}
        >
          Sebelumnya
        </button>
        <span className="pager__page tabular">
          Halaman {current + 1} dari {pages}
        </span>
        <button
          type="button"
          className="btn btn--outline"
          disabled={current >= pages - 1}
          onClick={() => onPage(current + 1)}
        >
          Berikutnya
        </button>
      </div>
    </nav>
  );
}
