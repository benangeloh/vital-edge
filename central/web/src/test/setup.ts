import "@testing-library/jest-dom/vitest";

// uPlot menggambar ke canvas, yang tidak ada di jsdom. Chart diuji lewat
// ringkasan teksnya (yang justru bagian yang penting untuk aksesibilitas),
// bukan lewat piksel — untuk itu ada uji visual di browser sungguhan.
if (!HTMLCanvasElement.prototype.getContext) {
  HTMLCanvasElement.prototype.getContext = (() => null) as never;
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as never;
}
