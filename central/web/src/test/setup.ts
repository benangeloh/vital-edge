import "@testing-library/jest-dom/vitest";

// Beberapa berkas uji memakai lingkungan node (mis. menjalankan skrip bash
// sungguhan), tempat DOM tidak ada.
const hasDom = typeof window !== "undefined";

// uPlot menggambar ke canvas, yang tidak ada di jsdom. Chart diuji lewat
// ringkasan teksnya (yang justru bagian yang penting untuk aksesibilitas),
// bukan lewat piksel — untuk itu ada uji visual di browser sungguhan.
if (hasDom && !HTMLCanvasElement.prototype.getContext) {
  HTMLCanvasElement.prototype.getContext = (() => null) as never;
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as never;
}
