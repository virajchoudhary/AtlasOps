import { useEffect, useRef } from "react";
import geistMonoUrl from "./resources/GeistMono-Variable.woff2?url";
import {
  ATLAS_COLUMNS,
  ATLAS_UPLOAD_FLIP_Y,
  INSTANCE_ANGLE,
  INSTANCE_GLYPH,
  INSTANCE_RADIUS,
  INSTANCE_RING,
  INSTANCE_SIZE,
  INSTANCE_SPEED,
  INSTANCE_STRIDE,
  RING_COUNT,
  RING_MAX_RADIUS,
  createGlyphAtlas,
  createRingModel,
} from "./ring-canvas-model";
import { ringFragmentShader, ringVertexShader } from "./ring-canvas-shaders";

const FONT_FAMILY = "RingCanvasGeistMono";
const MAX_DPR = 2;
const RIPPLE_COUNT = 16;

type IdleDeadlineLike = {
  readonly didTimeout: boolean;
  timeRemaining(): number;
};

type IdleWindow = Window & {
  requestIdleCallback?: (
    callback: (deadline: IdleDeadlineLike) => void,
    options?: { timeout: number },
  ) => number;
  cancelIdleCallback?: (handle: number) => void;
};

type RingUniforms = {
  time: WebGLUniformLocation | null;
  resolution: WebGLUniformLocation | null;
  atlasGrid: WebGLUniformLocation | null;
  radiusScale: WebGLUniformLocation | null;
  glyphScale: WebGLUniformLocation | null;
  dotIndex: WebGLUniformLocation | null;
  pointer: WebGLUniformLocation | null;
  pointerInfluence: WebGLUniformLocation | null;
  pointerRadius: WebGLUniformLocation | null;
  rippleStarts: WebGLUniformLocation | null;
  rippleStrengths: WebGLUniformLocation | null;
  atlas: WebGLUniformLocation | null;
};

type RingRenderer = {
  gl: WebGL2RenderingContext;
  program: WebGLProgram;
  vertexArray: WebGLVertexArrayObject;
  quadBuffer: WebGLBuffer;
  instanceBuffer: WebGLBuffer;
  texture: WebGLTexture;
  uniforms: RingUniforms;
  instanceCount: number;
};

type Point = {
  x: number;
  y: number;
};

function getRadiusScale(height: number): number {
  return height * 0.5 / RING_MAX_RADIUS;
}

export type RingCanvasProps = {
  text: string;
  className?: string;
  ariaLabel?: string;
};

let typefaceLoad: Promise<void> | null = null;

function loadTypeface(): Promise<void> {
  if (document.fonts.check(`16px "${FONT_FAMILY}"`)) return Promise.resolve();
  if (typefaceLoad) return typefaceLoad;

  typefaceLoad = new FontFace(
    FONT_FAMILY,
    `url("${geistMonoUrl}")`,
    { weight: "100 900" },
  ).load()
    .then((face) => {
      document.fonts.add(face);
    })
    .catch(() => {
      typefaceLoad = null;
    });

  return typefaceLoad;
}

function scheduleIdle(callback: () => void): () => void {
  const idleWindow: IdleWindow = window;

  if (idleWindow.requestIdleCallback) {
    const handle = idleWindow.requestIdleCallback(() => callback(), { timeout: 2000 });
    return () => idleWindow.cancelIdleCallback?.(handle);
  }

  const timeout = window.setTimeout(callback, 200);
  return () => window.clearTimeout(timeout);
}

function compileShader(
  gl: WebGL2RenderingContext,
  type: number,
  source: string,
): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;

  gl.shaderSource(shader, source);
  gl.compileShader(shader);

  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }

  return shader;
}

function createRenderer(
  canvas: HTMLCanvasElement,
  atlas: HTMLCanvasElement,
  atlasRows: number,
  dotIndex: number,
  instances: Float32Array,
  instanceCount: number,
): RingRenderer | null {
  const gl = canvas.getContext("webgl2", {
    alpha: true,
    antialias: true,
    premultipliedAlpha: false,
    preserveDrawingBuffer: false,
  });
  if (!gl) return null;
  const releaseContext = () => {
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return null;
  };

  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, ringVertexShader);
  const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, ringFragmentShader);
  if (!vertexShader || !fragmentShader) {
    if (vertexShader) gl.deleteShader(vertexShader);
    if (fragmentShader) gl.deleteShader(fragmentShader);
    return releaseContext();
  }

  const program = gl.createProgram();
  if (!program) {
    gl.deleteShader(vertexShader);
    gl.deleteShader(fragmentShader);
    return releaseContext();
  }

  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);
  gl.deleteShader(vertexShader);
  gl.deleteShader(fragmentShader);

  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    gl.deleteProgram(program);
    return releaseContext();
  }

  const positionLocation = gl.getAttribLocation(program, "aPosition");
  const uvLocation = gl.getAttribLocation(program, "aUv");
  const radiusLocation = gl.getAttribLocation(program, "aRadius");
  const angleLocation = gl.getAttribLocation(program, "aTheta0");
  const speedLocation = gl.getAttribLocation(program, "aSpeed");
  const sizeLocation = gl.getAttribLocation(program, "aSize");
  const glyphLocation = gl.getAttribLocation(program, "aGlyphIndex");
  const ringLocation = gl.getAttribLocation(program, "aRingIndex");

  if ([positionLocation, uvLocation, radiusLocation, angleLocation, speedLocation, sizeLocation, glyphLocation, ringLocation]
    .some((location) => location < 0)) {
    gl.deleteProgram(program);
    return releaseContext();
  }

  const vertexArray = gl.createVertexArray();
  const quadBuffer = gl.createBuffer();
  const instanceBuffer = gl.createBuffer();
  const texture = gl.createTexture();
  if (!vertexArray || !quadBuffer || !instanceBuffer || !texture) {
    if (vertexArray) gl.deleteVertexArray(vertexArray);
    if (quadBuffer) gl.deleteBuffer(quadBuffer);
    if (instanceBuffer) gl.deleteBuffer(instanceBuffer);
    if (texture) gl.deleteTexture(texture);
    gl.deleteProgram(program);
    return releaseContext();
  }

  const uniforms: RingUniforms = {
    time: gl.getUniformLocation(program, "uTime"),
    resolution: gl.getUniformLocation(program, "uResolution"),
    atlasGrid: gl.getUniformLocation(program, "uAtlasGrid"),
    radiusScale: gl.getUniformLocation(program, "uRadiusScale"),
    glyphScale: gl.getUniformLocation(program, "uGlyphScale"),
    dotIndex: gl.getUniformLocation(program, "uDotIndex"),
    pointer: gl.getUniformLocation(program, "uPointer"),
    pointerInfluence: gl.getUniformLocation(program, "uPointerInfluence"),
    pointerRadius: gl.getUniformLocation(program, "uPointerRadius"),
    rippleStarts: gl.getUniformLocation(program, "uRippleStarts[0]"),
    rippleStrengths: gl.getUniformLocation(program, "uRippleStrengths[0]"),
    atlas: gl.getUniformLocation(program, "tAtlas"),
  };

  const quad = new Float32Array([
    -0.5, -0.5, 0, 1,
    0.5, -0.5, 1, 1,
    -0.5, 0.5, 0, 0,
    0.5, 0.5, 1, 0,
  ]);

  gl.bindVertexArray(vertexArray);
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(positionLocation);
  gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 4 * Float32Array.BYTES_PER_ELEMENT, 0);
  gl.enableVertexAttribArray(uvLocation);
  gl.vertexAttribPointer(uvLocation, 2, gl.FLOAT, false, 4 * Float32Array.BYTES_PER_ELEMENT, 2 * Float32Array.BYTES_PER_ELEMENT);

  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, instances, gl.STATIC_DRAW);
  const stride = INSTANCE_STRIDE * Float32Array.BYTES_PER_ELEMENT;
  const instanceAttributes = [
    { location: radiusLocation, offset: INSTANCE_RADIUS },
    { location: angleLocation, offset: INSTANCE_ANGLE },
    { location: speedLocation, offset: INSTANCE_SPEED },
    { location: sizeLocation, offset: INSTANCE_SIZE },
    { location: glyphLocation, offset: INSTANCE_GLYPH },
    { location: ringLocation, offset: INSTANCE_RING },
  ];

  for (const attribute of instanceAttributes) {
    gl.enableVertexAttribArray(attribute.location);
    gl.vertexAttribPointer(
      attribute.location,
      1,
      gl.FLOAT,
      false,
      stride,
      attribute.offset * Float32Array.BYTES_PER_ELEMENT,
    );
    gl.vertexAttribDivisor(attribute.location, 1);
  }

  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, ATLAS_UPLOAD_FLIP_Y ? 1 : 0);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, atlas);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  gl.useProgram(program);
  gl.uniform2f(uniforms.atlasGrid, ATLAS_COLUMNS, atlasRows);
  gl.uniform1f(uniforms.dotIndex, dotIndex);
  gl.uniform1i(uniforms.atlas, 0);
  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);

  return {
    gl,
    program,
    vertexArray,
    quadBuffer,
    instanceBuffer,
    texture,
    uniforms,
    instanceCount,
  };
}

function disposeRenderer(renderer: RingRenderer | null): void {
  if (!renderer) return;
  renderer.gl.deleteTexture(renderer.texture);
  renderer.gl.deleteBuffer(renderer.quadBuffer);
  renderer.gl.deleteBuffer(renderer.instanceBuffer);
  renderer.gl.deleteVertexArray(renderer.vertexArray);
  renderer.gl.deleteProgram(renderer.program);
  renderer.gl.getExtension("WEBGL_lose_context")?.loseContext();
}

function drawStaticFallback(
  canvas: HTMLCanvasElement,
  model: ReturnType<typeof createRingModel>,
  width: number,
  height: number,
  pixelRatio: number,
): boolean {
  const context = canvas.getContext("2d");
  if (!context) return false;

  const radiusScale = getRadiusScale(height);
  const glyphScale = Math.min(height, 1080) / 1080;
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillStyle = "#ffffff";

  for (let index = 0; index < model.instanceCount; index += 1) {
    const offset = index * INSTANCE_STRIDE;
    const radius = model.instances[offset + INSTANCE_RADIUS] ?? 0;
    const theta = model.instances[offset + INSTANCE_ANGLE] ?? 0;
    const size = model.instances[offset + INSTANCE_SIZE] ?? 0;
    const glyphIndex = model.instances[offset + INSTANCE_GLYPH] ?? model.dotIndex;
    const ringIndex = model.instances[offset + INSTANCE_RING] ?? 0;
    const glyph = model.glyphs[glyphIndex] ?? ".";
    const x = width / 2 + Math.cos(theta) * radius * radiusScale;
    const y = height / 2 + Math.sin(theta) * radius * radiusScale;
    const opacity = 0.56 + 0.36 * (ringIndex / (RING_COUNT - 1));

    context.save();
    context.translate(x, y);
    context.rotate(theta + Math.PI / 2);
    context.globalAlpha = opacity;
    context.font = `${Math.max(1, size * glyphScale)}px "${FONT_FAMILY}", ui-monospace, monospace`;
    context.fillText(glyph, 0, 0);
    context.restore();
  }

  context.globalAlpha = 1;
  return model.instanceCount > 0;
}

export function RingCanvas({ text, className, ariaLabel }: RingCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const webglCanvasRef = useRef<HTMLCanvasElement>(null);
  const fallbackCanvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    const webglCanvas = webglCanvasRef.current;
    const fallbackCanvas = fallbackCanvasRef.current;
    if (!container || !webglCanvas || !fallbackCanvas) return;

    const abortController = new AbortController();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const rippleStarts = new Float32Array(RIPPLE_COUNT).fill(-1);
    const rippleStrengths = new Float32Array(RIPPLE_COUNT).fill(0);
    const model = createRingModel(text);
    let renderer: RingRenderer | null = null;
    let idleCancel: (() => void) | null = null;
    let loadObserver: IntersectionObserver | null = null;
    let visibilityObserver: IntersectionObserver | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let animationFrame = 0;
    let frameCount = 0;
    let rippleIndex = 0;
    let elapsed = 0;
    let lastFrameTime = 0;
    let pointerTarget = 0;
    let pointerInfluence = 0;
    let pointer: Point = { x: 999, y: 999 };
    let cssWidth = 0;
    let cssHeight = 0;
    let pixelRatio = 1;
    let radiusScale = 0;
    let glyphScale = 1;
    let isNearViewport = false;
    let isInViewport = false;
    let isPageVisible = !document.hidden;
    let initializationRequested = false;
    let didFallback = false;

    container.dataset.renderer = "pending";
    container.dataset.animationState = "pending";
    container.dataset.instanceCount = String(model.instanceCount);

    const updateData = (state: string) => {
      container.dataset.animationState = state;
      container.dataset.frame = String(frameCount);
    };

    const resize = () => {
      const bounds = container.getBoundingClientRect();
      cssWidth = bounds.width;
      cssHeight = bounds.height;
      pixelRatio = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      radiusScale = getRadiusScale(cssHeight);
      glyphScale = Math.min(cssHeight, 1080) / 1080;

      if (cssWidth <= 0 || cssHeight <= 0) {
        idleCancel?.();
        idleCancel = null;
        if (!renderer && !didFallback) initializationRequested = false;
        return;
      }

      const width = Math.max(1, Math.round(cssWidth * pixelRatio));
      const height = Math.max(1, Math.round(cssHeight * pixelRatio));
      webglCanvas.width = width;
      webglCanvas.height = height;
      fallbackCanvas.width = width;
      fallbackCanvas.height = height;
      container.dataset.pixelRatio = String(pixelRatio);

      if (renderer) {
        renderer.gl.viewport(0, 0, width, height);
        drawFrame(elapsed);
      } else if (didFallback) {
        drawStaticFallback(fallbackCanvas, model, cssWidth, cssHeight, pixelRatio);
      }

      if (isNearViewport && !renderer && !didFallback) scheduleInitialization();
    };

    const activateFallback = () => {
      if (didFallback) return;
      didFallback = true;
      if (animationFrame !== 0) {
        window.cancelAnimationFrame(animationFrame);
        animationFrame = 0;
      }

      disposeRenderer(renderer);
      renderer = null;
      webglCanvas.style.display = "none";
      fallbackCanvas.style.display = "block";
      container.dataset.renderer = "2d";
      const rendered = drawStaticFallback(fallbackCanvas, model, cssWidth, cssHeight, pixelRatio);
      updateData(rendered ? "fallback" : "unavailable");
    };

    const drawFrame = (time: number) => {
      if (!renderer) return;
      const { gl, uniforms } = renderer;

      gl.viewport(0, 0, webglCanvas.width, webglCanvas.height);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(renderer.program);
      gl.bindVertexArray(renderer.vertexArray);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, renderer.texture);
      gl.uniform1f(uniforms.time, time);
      gl.uniform2f(uniforms.resolution, webglCanvas.width, webglCanvas.height);
      gl.uniform2f(uniforms.atlasGrid, ATLAS_COLUMNS, model.atlasRows);
      gl.uniform1f(uniforms.radiusScale, radiusScale * pixelRatio);
      gl.uniform1f(uniforms.glyphScale, glyphScale * pixelRatio);
      gl.uniform1f(uniforms.dotIndex, model.dotIndex);
      gl.uniform2f(uniforms.pointer, pointer.x, pointer.y);
      gl.uniform1f(uniforms.pointerInfluence, pointerInfluence);
      gl.uniform1f(uniforms.pointerRadius, 0.35);
      gl.uniform1fv(uniforms.rippleStarts, rippleStarts);
      gl.uniform1fv(uniforms.rippleStrengths, rippleStrengths);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, renderer.instanceCount);

      frameCount += 1;
      container.dataset.frame = String(frameCount);
    };

    const stopAnimation = () => {
      if (animationFrame !== 0) {
        window.cancelAnimationFrame(animationFrame);
        animationFrame = 0;
      }
      lastFrameTime = 0;
    };

    const animate = (now: number) => {
      if (!renderer || !isInViewport || !isPageVisible || reducedMotion.matches) {
        animationFrame = 0;
        return;
      }

      const delta = lastFrameTime === 0 ? 0 : Math.min((now - lastFrameTime) / 1000, 0.06);
      lastFrameTime = now;
      elapsed += delta;
      pointerInfluence += (pointerTarget - pointerInfluence) * (1 - Math.exp(-6 * delta));
      drawFrame(elapsed);
      animationFrame = window.requestAnimationFrame(animate);
    };

    const synchronizeAnimation = () => {
      if (!renderer || didFallback) return;

      if (!isInViewport) {
        stopAnimation();
        updateData("offscreen");
        return;
      }

      if (!isPageVisible) {
        stopAnimation();
        updateData("hidden");
        return;
      }

      if (reducedMotion.matches) {
        stopAnimation();
        drawFrame(elapsed);
        updateData("reduced-motion");
        return;
      }

      updateData("running");
      if (animationFrame === 0) {
        lastFrameTime = 0;
        animationFrame = window.requestAnimationFrame(animate);
      }
    };

    const scheduleInitialization = () => {
      if (initializationRequested || renderer || didFallback) return;
      initializationRequested = true;
      idleCancel = scheduleIdle(() => {
        idleCancel = null;
        void initialize();
      });
    };

    const initialize = async () => {
      if (abortController.signal.aborted) return;
      if (cssWidth <= 0 || cssHeight <= 0) {
        initializationRequested = false;
        return;
      }

      try {
        await loadTypeface();
        if (abortController.signal.aborted) return;
        if (cssWidth <= 0 || cssHeight <= 0) {
          initializationRequested = false;
          return;
        }
        const atlas = createGlyphAtlas(model, FONT_FAMILY);
        if (!atlas) {
          activateFallback();
          return;
        }

        renderer = createRenderer(
          webglCanvas,
          atlas,
          model.atlasRows,
          model.dotIndex,
          model.instances,
          model.instanceCount,
        );
        atlas.width = 0;
        atlas.height = 0;
        if (!renderer) {
          activateFallback();
          return;
        }

        container.dataset.renderer = "webgl2";
        resize();
        drawFrame(elapsed);
        synchronizeAnimation();
      } catch {
        activateFallback();
      }
    };

    const updatePointer = (event: PointerEvent) => {
      if (reducedMotion.matches) return;
      const bounds = container.getBoundingClientRect();
      const cssRadiusScale = getRadiusScale(bounds.height);
      if (cssRadiusScale <= 0) return;

      pointer = {
        x: (event.clientX - bounds.left - bounds.width / 2) / cssRadiusScale,
        y: (bounds.height / 2 - (event.clientY - bounds.top)) / cssRadiusScale,
      };
      pointerTarget = 1;
    };

    const addRipple = (event: PointerEvent) => {
      if (reducedMotion.matches || event.button !== 0) return;
      updatePointer(event);
      rippleStarts[rippleIndex] = elapsed;
      rippleStrengths[rippleIndex] = 1;
      rippleIndex = (rippleIndex + 1) % RIPPLE_COUNT;
    };

    const onVisibilityChange = () => {
      isPageVisible = !document.hidden;
      synchronizeAnimation();
    };

    const onMotionChange = () => {
      if (reducedMotion.matches) {
        pointerTarget = 0;
        pointerInfluence = 0;
      }
      synchronizeAnimation();
    };

    const onContextLost = (event: Event) => {
      event.preventDefault();
      activateFallback();
    };

    resize();
    webglCanvas.addEventListener("webglcontextlost", onContextLost, {
      signal: abortController.signal,
    });
    container.addEventListener("pointermove", updatePointer, {
      signal: abortController.signal,
    });
    container.addEventListener("pointerleave", () => {
      pointerTarget = 0;
    }, { signal: abortController.signal });
    container.addEventListener("pointerdown", addRipple, {
      signal: abortController.signal,
    });
    document.addEventListener("visibilitychange", onVisibilityChange, {
      signal: abortController.signal,
    });
    reducedMotion.addEventListener("change", onMotionChange, {
      signal: abortController.signal,
    });

    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(container);
    } else {
      window.addEventListener("resize", resize, { signal: abortController.signal });
    }

    if (typeof IntersectionObserver !== "undefined") {
      loadObserver = new IntersectionObserver((entries) => {
        isNearViewport = entries.some((entry) => entry.isIntersecting);
        if (isNearViewport) scheduleInitialization();
      }, { rootMargin: "400px" });
      visibilityObserver = new IntersectionObserver((entries) => {
        const entry = entries[entries.length - 1];
        isInViewport = entry?.isIntersecting ?? false;
        synchronizeAnimation();
      }, { threshold: 0 });
      loadObserver.observe(container);
      visibilityObserver.observe(container);
    } else {
      isNearViewport = true;
      isInViewport = true;
      scheduleInitialization();
    }

    return () => {
      abortController.abort();
      idleCancel?.();
      loadObserver?.disconnect();
      visibilityObserver?.disconnect();
      resizeObserver?.disconnect();
      stopAnimation();
      disposeRenderer(renderer);
      container.dataset.animationState = "disposed";
    };
  }, [text]);

  return (
    <div
      key={text}
      ref={containerRef}
      className={className ? `ring-canvas ${className}` : "ring-canvas"}
      role={ariaLabel ? "img" : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      data-ring-count={RING_COUNT}
      data-renderer="pending"
      data-animation-state="pending"
      data-instance-count="0"
      data-frame="0"
      style={{
        position: "relative",
        display: "block",
        width: "100%",
        height: "100%",
        overflow: "hidden",
      }}
    >
      <canvas
        ref={webglCanvasRef}
        className="ring-canvas__webgl"
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          display: "block",
          width: "100%",
          height: "100%",
          pointerEvents: "none",
        }}
      />
      <canvas
        ref={fallbackCanvasRef}
        className="ring-canvas__fallback"
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          display: "none",
          width: "100%",
          height: "100%",
          pointerEvents: "none",
        }}
      />
    </div>
  );
}
