import { useEffect, useRef } from "react";

const PHRASE = [
  "BEFORE YOU BUY", "ASK ANYTHING", "LIFETIME UPDATES INCLUDED",
  "TWO EDITIONS ONE ARCHITECTURE", "FOR NEXT.JS AND ASTRO",
  "NO SUBSCRIPTION NO SEATS", "PRIVATE REPO ACCESS FOR GOOD",
  "CLONE IT RENAME IT SHIP", "USE IT ON EVERY CLIENT PROJECT",
  "STILL UNSURE JUST EMAIL", "",
].join(" \u00b7 ");
const GLYPHS = " \u00b7.ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/+*#@";
const ROW_HEIGHT = 14;
const ATLAS_HEIGHT = 64;
const ATLAS_WIDTH = 35;
const ATLAS_COLUMNS = 4;

function hash(value: number) {
  const number = Math.sin(value * 12.9898) * 43758.5453;
  return number - Math.floor(number);
}

function cellHash(column: number, row: number) {
  const number = Math.sin(column * 127.1 + row * 311.7) * 43758.5453;
  return number - Math.floor(number);
}

export function FAQGlyphBackdrop() {
  const mountRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    const canvas = canvasRef.current;
    const section = mount?.closest("section");
    const context = canvas?.getContext("2d", { alpha: false });
    if (!mount || !canvas || !section || !context) return;

    const atlas = document.createElement("canvas");
    const atlasContext = atlas.getContext("2d");
    if (!atlasContext) return;
    atlas.width = ATLAS_COLUMNS * ATLAS_WIDTH;
    atlas.height = Math.ceil(GLYPHS.length / ATLAS_COLUMNS) * ATLAS_HEIGHT;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const abort = new AbortController();
    let ready = false;
    let inView = false;
    let raf = 0;
    let simulationTime = 0;
    let lastTime = 0;
    let lastPaint = 0;
    let frame = 0;
    let width = 0;
    let rows = 0;
    let columns = 0;
    let cellWidth = 0;
    let dpr = 1;
    let previousRowOffset = -1;
    let drawn = new Int16Array();

    const draw = (force = false) => {
      if (!ready || !inView || document.hidden) return;
      const travel = Math.max(0, -section.getBoundingClientRect().top);
      const rowOffset = Math.floor(travel / ROW_HEIGHT);
      canvas.style.transform = `translateY(${rowOffset * ROW_HEIGHT - travel}px)`;
      const repaint = force || rowOffset !== previousRowOffset;
      previousRowOffset = rowOffset;
      context.globalAlpha = 1;
      if (repaint) {
        context.fillStyle = "#232323";
        context.fillRect(0, 0, width, rows * ROW_HEIGHT);
      }
      for (let row = 0; row < rows; row += 1) {
        const fieldRow = row + rowOffset;
        const phraseOffset = Math.floor(hash(fieldRow + 0.5) * PHRASE.length);
        for (let column = 0; column < columns; column += 1) {
          const index = row * columns + column;
          const threshold = cellHash(column, fieldRow);
          const twinkle = (Math.sin(simulationTime * 0.18 + threshold * Math.PI * 2) + 1) / 2 >= 0.985;
          const glyph = twinkle
            ? 1 + Math.floor(hash(threshold * 17.13 + Math.floor(simulationTime * 2.5) * 1.7) * (GLYPHS.length - 1))
            : GLYPHS.indexOf(PHRASE[(column + phraseOffset) % PHRASE.length]);
          if (!repaint && drawn[index] === glyph) continue;
          drawn[index] = glyph;
          const left = column * cellWidth;
          context.globalAlpha = 1;
          context.fillStyle = "#232323";
          context.fillRect(left, row * ROW_HEIGHT, cellWidth, ROW_HEIGHT);
          context.globalAlpha = Math.pow(0.01, 0.6);
          context.drawImage(
            atlas,
            (glyph % ATLAS_COLUMNS) * ATLAS_WIDTH,
            Math.floor(glyph / ATLAS_COLUMNS) * ATLAS_HEIGHT,
            ATLAS_WIDTH, ATLAS_HEIGHT,
            left, row * ROW_HEIGHT, cellWidth, ROW_HEIGHT,
          );
        }
      }
      context.globalAlpha = 1;
      canvas.dataset.frame = String(++frame);
    };

    const resize = () => {
      const bounds = mount.getBoundingClientRect();
      width = Math.max(1, bounds.width);
      rows = Math.ceil(bounds.height / ROW_HEIGHT) + 1;
      columns = Math.max(8, Math.round(width / (ROW_HEIGHT * 0.55)));
      cellWidth = width / columns;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(rows * ROW_HEIGHT * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${rows * ROW_HEIGHT}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawn = new Int16Array(rows * columns).fill(-1);
      draw(true);
    };

    const tick = (time: number) => {
      simulationTime += Math.min(0.05, (time - lastTime) / 1000);
      lastTime = time;
      if (time - lastPaint >= 100) {
        lastPaint = time;
        draw();
      }
      raf = requestAnimationFrame(tick);
    };

    const sync = () => {
      cancelAnimationFrame(raf);
      raf = 0;
      canvas.dataset.animationState = motion.matches ? "reduced-motion" : "paused";
      if (!ready || !inView || document.hidden) return;
      draw();
      if (!motion.matches) {
        canvas.dataset.animationState = "running";
        lastTime = performance.now();
        raf = requestAnimationFrame(tick);
      }
    };

    const intersection = new IntersectionObserver(([entry]) => {
      inView = Boolean(entry?.isIntersecting);
      sync();
    });
    intersection.observe(section);
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    const onScroll = () => draw();
    window.addEventListener("scroll", onScroll, { passive: true, signal: abort.signal });
    document.addEventListener("visibilitychange", sync, { signal: abort.signal });
    motion.addEventListener("change", sync, { signal: abort.signal });
    document.fonts.load('500 47.36px "Geist Mono"').catch(() => []).then(() => {
      if (abort.signal.aborted) return;
      atlasContext.font = '500 47.36px "Geist Mono", monospace';
      atlasContext.fillStyle = "#ffffff";
      atlasContext.textAlign = "center";
      atlasContext.textBaseline = "middle";
      for (let i = 0; i < GLYPHS.length; i += 1) {
        atlasContext.fillText(
          GLYPHS[i],
          (i % ATLAS_COLUMNS + 0.5) * ATLAS_WIDTH,
          (Math.floor(i / ATLAS_COLUMNS) + 0.58) * ATLAS_HEIGHT,
        );
      }
      ready = true;
      resize();
      sync();
    });

    return () => {
      abort.abort();
      cancelAnimationFrame(raf);
      intersection.disconnect();
      observer.disconnect();
      canvas.width = 0;
      canvas.height = 0;
    };
  }, []);

  return (
    <div className="reference-faq-glyphs" aria-hidden="true">
      <div className="reference-faq-glyph-viewport" ref={mountRef}>
        <canvas ref={canvasRef} data-frame="0" data-animation-state="paused" />
      </div>
      <div className="reference-faq-glyph-shade" />
    </div>
  );
}
