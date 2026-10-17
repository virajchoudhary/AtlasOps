import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no layout engine; browser tests cover actual resize behavior.
globalThis.ResizeObserver ??= class implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};
window.matchMedia ??= (query) => Object.assign(new EventTarget(), {
  matches: false,
  media: query,
  onchange: null,
  addListener() {},
  removeListener() {},
});
for (const property of ["textTracks", "audioTracks", "videoTracks"]) {
  const original: unknown = Reflect.get(document.createElement("video"), property);
  if (!Array.isArray(original)) continue;
  const lists = new WeakMap<HTMLMediaElement, EventTarget>();
  Object.defineProperty(HTMLMediaElement.prototype, property, {
    configurable: true,
    get(this: HTMLMediaElement) {
      let list = lists.get(this);
      if (!list) {
        list = Object.assign(new EventTarget(), {
          length: 0,
          [Symbol.iterator]: () => [][Symbol.iterator](),
        });
        lists.set(this, list);
      }
      return list;
    },
  });
}

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});
