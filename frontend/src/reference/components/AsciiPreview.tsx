import { useEffect, useRef, useState } from "react";

const GLYPHS = " .:-=+*#%@";

export function AsciiPreview({
  src,
  alt,
}: {
  src: string;
  alt: string;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [imageFailed, setImageFailed] = useState(false);

  useEffect(() => {
    const frame = frameRef.current;
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!frame || !canvas || !context || imageFailed) return;

    const sample = document.createElement("canvas");
    const sampleContext = sample.getContext("2d", { willReadFrequently: true });
    if (!sampleContext) return;

    const image = new Image();
    let inViewport = false;
    let pageVisible = document.visibilityState === "visible";
    let drawQueued = false;

    const draw = () => {
      if (!inViewport || !pageVisible || drawQueued) return;
      const width = frame.clientWidth;
      const height = frame.clientHeight;
      if (!width || !height || !image.complete || !image.naturalWidth) return;
      drawQueued = true;

      requestAnimationFrame(() => {
        drawQueued = false;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const columns = Math.max(8, Math.round(width / 12));
        const rows = Math.max(8, Math.round(height / 17));
        const cellWidth = width / columns;
        const cellHeight = height / rows;
        sample.width = columns;
        sample.height = rows;
        const scale = Math.max(columns / image.naturalWidth, rows / image.naturalHeight);
        const sourceWidth = columns / scale;
        const sourceHeight = rows / scale;
        const sourceX = (image.naturalWidth - sourceWidth) / 2;
        const sourceY = (image.naturalHeight - sourceHeight) / 2;
        sampleContext.drawImage(
          image,
          sourceX,
          sourceY,
          sourceWidth,
          sourceHeight,
          0,
          0,
          columns,
          rows,
        );
        const pixels = sampleContext.getImageData(0, 0, columns, rows).data;

        canvas.width = Math.max(1, Math.round(width * dpr));
        canvas.height = Math.max(1, Math.round(height * dpr));
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        context.fillStyle = "#232323";
        context.fillRect(0, 0, width, height);
        context.font = `${Math.max(7, Math.min(13, cellHeight * 0.72))}px GeistMono, monospace`;
        context.textBaseline = "middle";

        for (let row = 0; row < rows; row += 1) {
          for (let column = 0; column < columns; column += 1) {
            const offset = (row * columns + column) * 4;
            const luminance =
              (pixels[offset] * 0.2126 +
                pixels[offset + 1] * 0.7152 +
                pixels[offset + 2] * 0.0722) /
              255;
            const glyphIndex = Math.min(
              GLYPHS.length - 1,
              Math.floor(luminance * GLYPHS.length),
            );
            const glyph = GLYPHS[glyphIndex];
            if (glyph === " ") continue;
            context.fillStyle = `rgba(255,255,255,${0.12 + luminance * 0.7})`;
            context.fillText(
              glyph,
              column * cellWidth,
              row * cellHeight + cellHeight / 2,
            );
          }
        }
      });
    };

    const resizeObserver = new ResizeObserver(draw);
    resizeObserver.observe(frame);
    const intersectionObserver = new IntersectionObserver(
      ([entry]) => {
        inViewport = Boolean(entry?.isIntersecting);
        if (inViewport) draw();
      },
      { rootMargin: "400px" },
    );
    intersectionObserver.observe(frame);
    const onVisibilityChange = () => {
      pageVisible = document.visibilityState === "visible";
      if (pageVisible) draw();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    image.onload = draw;
    image.onerror = () => setImageFailed(true);
    image.src = src;

    return () => {
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      image.onload = null;
      image.onerror = null;
    };
  }, [imageFailed, src]);

  return (
    <div className="reference-ascii-frame" ref={frameRef}>
      {!imageFailed && (
        <canvas
          ref={canvasRef}
          className="reference-ascii-canvas"
          aria-hidden="true"
        />
      )}
      <img
        className={`reference-project-image ${imageFailed ? "is-fallback" : ""}`}
        src={src}
        alt={alt}
        loading="lazy"
        decoding="async"
        onError={() => setImageFailed(true)}
      />
    </div>
  );
}
