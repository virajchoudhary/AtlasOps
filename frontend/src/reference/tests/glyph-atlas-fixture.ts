import geistMonoUrl from "../resources/GeistMono-Variable.woff2?url";
import {
  ATLAS_UPLOAD_FLIP_Y,
  ATLAS_CELL_SIZE,
  ATLAS_COLUMNS,
  createGlyphAtlas,
  createRingModel,
} from "../ring-canvas-model";
import { ringFragmentShader, ringVertexShader } from "../ring-canvas-shaders";

const phrase = "THE CONTENT ARCHITECTURE";
const fontFamily = "RingCanvasGeistMono";
const output = document.querySelector<HTMLOutputElement>("#glyph-atlas-identity");
const orientationOutput = document.querySelector<HTMLOutputElement>("#glyph-atlas-orientation");
if (!output) throw new Error("Glyph atlas identity output is missing.");
if (!orientationOutput) throw new Error("Glyph atlas orientation output is missing.");

function compileShader(
  gl: WebGL2RenderingContext,
  type: number,
  source: string,
): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("WebGL could not allocate a shader.");

  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(shader) ?? "Unknown shader compilation error.";
    gl.deleteShader(shader);
    throw new Error(message);
  }

  return shader;
}

function buildProgram(
  gl: WebGL2RenderingContext,
  fragmentSource = `#version 300 es
precision highp float;

uniform sampler2D tAtlas;

in vec2 vAtlasUv;
out vec4 fragColor;

void main() {
  fragColor = vec4(texture(tAtlas, vAtlasUv).rgb, 1.0);
}`,
): WebGLProgram {
  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, ringVertexShader);
  const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = gl.createProgram();
  if (!program) throw new Error("WebGL could not allocate a program.");

  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);
  gl.deleteShader(vertexShader);
  gl.deleteShader(fragmentShader);

  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const message = gl.getProgramInfoLog(program) ?? "Unknown program link error.";
    gl.deleteProgram(program);
    throw new Error(message);
  }

  return program;
}

function createAtlasRenderer(
  atlas: HTMLCanvasElement,
  atlasRows: number,
  fragmentSource?: string,
  linearFiltering = false,
) {
  const canvas = document.createElement("canvas");
  canvas.width = ATLAS_CELL_SIZE;
  canvas.height = ATLAS_CELL_SIZE;
  const gl = canvas.getContext("webgl2", {
    alpha: true,
    antialias: false,
    premultipliedAlpha: false,
    preserveDrawingBuffer: true,
  });
  if (!gl) throw new Error("WebGL2 is unavailable for the glyph orientation fixture.");

  const program = buildProgram(gl, fragmentSource);
  const vertexArray = gl.createVertexArray();
  const quadBuffer = gl.createBuffer();
  const instanceBuffer = gl.createBuffer();
  const texture = gl.createTexture();
  if (!vertexArray || !quadBuffer || !instanceBuffer || !texture) {
    throw new Error("WebGL could not allocate the glyph orientation fixture.");
  }

  gl.useProgram(program);
  gl.bindVertexArray(vertexArray);
  gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -0.5, -0.5, 0, 1,
    0.5, -0.5, 1, 1,
    -0.5, 0.5, 0, 0,
    0.5, 0.5, 1, 0,
  ]), gl.STATIC_DRAW);
  bindAttribute(gl, gl.getAttribLocation(program, "aPosition"), 2, 16, 0);
  bindAttribute(gl, gl.getAttribLocation(program, "aUv"), 2, 16, 8);

  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(6), gl.DYNAMIC_DRAW);
  const stride = 6 * Float32Array.BYTES_PER_ELEMENT;
  const instanceAttributes = [
    { name: "aRadius", offset: 0 },
    { name: "aTheta0", offset: 1 },
    { name: "aSpeed", offset: 2 },
    { name: "aSize", offset: 3 },
    { name: "aGlyphIndex", offset: 4 },
    { name: "aRingIndex", offset: 5 },
  ];
  for (const attribute of instanceAttributes) {
    bindAttribute(
      gl,
      gl.getAttribLocation(program, attribute.name),
      1,
      stride,
      attribute.offset * Float32Array.BYTES_PER_ELEMENT,
      1,
    );
  }

  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, ATLAS_UPLOAD_FLIP_Y ? 1 : 0);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, atlas);
  const filter = linearFiltering ? gl.LINEAR : gl.NEAREST;
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  const uniform = (name: string) => gl.getUniformLocation(program, name);
  gl.uniform1f(uniform("uTime"), 0);
  gl.uniform2f(gl.getUniformLocation(program, "uResolution"), canvas.width, canvas.height);
  gl.uniform2f(uniform("uAtlasGrid"), ATLAS_COLUMNS, atlasRows);
  gl.uniform1f(uniform("uRadiusScale"), 0);
  gl.uniform1f(uniform("uGlyphScale"), 1);
  gl.uniform2f(uniform("uPointer"), 999, 999);
  gl.uniform1f(uniform("uPointerInfluence"), 0);
  gl.uniform1f(uniform("uPointerRadius"), 0.35);
  gl.uniform1fv(uniform("uRippleStarts[0]"), new Float32Array(16).fill(-1));
  gl.uniform1fv(uniform("uRippleStrengths[0]"), new Float32Array(16));
  gl.uniform1i(uniform("tAtlas"), 0);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 0);
  gl.enable(gl.BLEND);
  gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

  return {
    draw(glyphIndex: number, theta: number): Uint8Array {
      gl.useProgram(program);
      gl.bindVertexArray(vertexArray);
      gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array([
        0.001,
        theta,
        0,
        ATLAS_CELL_SIZE,
        glyphIndex,
        29,
      ]));
      gl.uniform1f(uniform("uDotIndex"), glyphIndex);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, 1);

      const pixels = new Uint8Array(canvas.width * canvas.height * 4);
      gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      return pixels;
    },
    dispose(): void {
      gl.deleteTexture(texture);
      gl.deleteBuffer(quadBuffer);
      gl.deleteBuffer(instanceBuffer);
      gl.deleteVertexArray(vertexArray);
      gl.deleteProgram(program);
    },
  };
}

function getVisualAlpha(pixels: Uint8Array, x: number, y: number): number {
  const readbackY = ATLAS_CELL_SIZE - 1 - y;
  return pixels[(readbackY * ATLAS_CELL_SIZE + x) * 4 + 3] ?? 0;
}

function countMaskMismatches(reference: ImageData, pixels: Uint8Array, mirrored: boolean): number {
  let mismatches = 0;
  for (let y = 0; y < ATLAS_CELL_SIZE; y += 1) {
    const referenceY = mirrored ? ATLAS_CELL_SIZE - 1 - y : y;
    const readbackY = ATLAS_CELL_SIZE - 1 - y;
    for (let x = 0; x < ATLAS_CELL_SIZE; x += 1) {
      const referenceAlpha = reference.data[(referenceY * ATLAS_CELL_SIZE + x) * 4 + 3] ?? 0;
      const actualAlpha = pixels[(readbackY * ATLAS_CELL_SIZE + x) * 4 + 3] ?? 0;
      const expectedVisible = referenceAlpha >= 128;
      const actualVisible = actualAlpha >= 118;
      if (expectedVisible !== actualVisible) mismatches += 1;
    }
  }
  return mismatches;
}

async function runGlyphOrientationReadback(result: HTMLOutputElement): Promise<void> {
  const face = await new FontFace(
    fontFamily,
    `url("${geistMonoUrl}")`,
    { weight: "100 900" },
  ).load();
  document.fonts.add(face);
  await document.fonts.load(`500 47.36px "${fontFamily}"`);

  const model = createRingModel("F");
  const glyphAtlas = createGlyphAtlas(model, fontFamily);
  if (!glyphAtlas) throw new Error("Canvas2D could not create the production glyph atlas.");
  const atlasContext = glyphAtlas.getContext("2d");
  if (!atlasContext) throw new Error("Canvas2D is unavailable for the sentinel atlas.");
  atlasContext.clearRect(
    ATLAS_CELL_SIZE,
    0,
    ATLAS_CELL_SIZE,
    ATLAS_CELL_SIZE,
  );
  atlasContext.fillStyle = "rgba(255, 255, 255, 1)";
  atlasContext.fillRect(ATLAS_CELL_SIZE, 0, ATLAS_CELL_SIZE, ATLAS_CELL_SIZE / 2);
  atlasContext.fillStyle = "rgba(255, 255, 255, 0.25)";
  atlasContext.fillRect(
    ATLAS_CELL_SIZE,
    ATLAS_CELL_SIZE / 2,
    ATLAS_CELL_SIZE,
    ATLAS_CELL_SIZE / 2,
  );

  const referenceCanvas = document.createElement("canvas");
  referenceCanvas.width = ATLAS_CELL_SIZE;
  referenceCanvas.height = ATLAS_CELL_SIZE;
  const referenceContext = referenceCanvas.getContext("2d");
  if (!referenceContext) throw new Error("Canvas2D is unavailable for the font reference.");
  referenceContext.textAlign = "center";
  referenceContext.textBaseline = "middle";
  referenceContext.fillStyle = "#ffffff";
  referenceContext.font = `500 47.36px "${fontFamily}", ui-monospace, monospace`;
  referenceContext.fillText("F", ATLAS_CELL_SIZE / 2, ATLAS_CELL_SIZE * 0.55);
  const reference = referenceContext.getImageData(
    0,
    0,
    ATLAS_CELL_SIZE,
    ATLAS_CELL_SIZE,
  );

  const renderer = createAtlasRenderer(glyphAtlas, model.atlasRows, ringFragmentShader, true);
  const sentinelPixels = renderer.draw(1, -Math.PI / 2);
  const glyphPixels = renderer.draw(0, -Math.PI / 2);
  renderer.dispose();
  result.dataset.sentinelTopAlpha = String(
    getVisualAlpha(sentinelPixels, ATLAS_CELL_SIZE / 2, ATLAS_CELL_SIZE / 4),
  );
  result.dataset.sentinelBottomAlpha = String(
    getVisualAlpha(sentinelPixels, ATLAS_CELL_SIZE / 2, ATLAS_CELL_SIZE * 3 / 4),
  );
  result.dataset.glyphPixels = String(
    reference.data.filter((_, index) => index % 4 === 3 && reference.data[index] !== 0).length,
  );
  result.dataset.glyphDirectMismatches = String(
    countMaskMismatches(reference, glyphPixels, false),
  );
  result.dataset.glyphMirroredMismatches = String(
    countMaskMismatches(reference, glyphPixels, true),
  );
  result.dataset.orientationState = "ready";
}

function bindAttribute(
  gl: WebGL2RenderingContext,
  location: number,
  size: number,
  stride: number,
  offset: number,
  divisor = 0,
): void {
  if (location < 0) return;
  gl.enableVertexAttribArray(location);
  gl.vertexAttribPointer(location, size, gl.FLOAT, false, stride, offset);
  if (divisor > 0) gl.vertexAttribDivisor(location, divisor);
}

function runAtlasIdentityReadback(): string {
  const model = createRingModel(phrase);
  const atlas = document.createElement("canvas");
  atlas.width = ATLAS_COLUMNS * ATLAS_CELL_SIZE;
  atlas.height = model.atlasRows * ATLAS_CELL_SIZE;
  const atlasContext = atlas.getContext("2d");
  if (!atlasContext) throw new Error("Canvas2D is unavailable for the synthetic atlas.");
  for (let row = 0; row < model.atlasRows; row += 1) {
    for (let column = 0; column < ATLAS_COLUMNS; column += 1) {
      const index = row * ATLAS_COLUMNS + column;
      atlasContext.fillStyle = `rgb(${index + 1}, 77, 131)`;
      atlasContext.fillRect(
        column * ATLAS_CELL_SIZE,
        row * ATLAS_CELL_SIZE,
        ATLAS_CELL_SIZE,
        ATLAS_CELL_SIZE,
      );
    }
  }

  const renderer = createAtlasRenderer(atlas, model.atlasRows);
  const identities: string[] = [];
  const centerOffset = (ATLAS_CELL_SIZE * (ATLAS_CELL_SIZE / 2) + ATLAS_CELL_SIZE / 2) * 4;
  for (let index = 0; index < model.glyphs.length; index += 1) {
    const pixels = renderer.draw(index, 1);
    const glyph = model.glyphs[index] ?? "";
    identities.push(`${glyph === " " ? "_" : glyph}:${pixels[centerOffset] ?? 0}`);
  }

  renderer.dispose();
  return identities.join("|");
}

try {
  output.dataset.glyphIdentities = runAtlasIdentityReadback();
} catch (error) {
  output.dataset.error = error instanceof Error ? error.message : "Atlas identity readback failed.";
}

void runGlyphOrientationReadback(orientationOutput).catch((error: unknown) => {
  orientationOutput.dataset.error = error instanceof Error
    ? error.message
    : "Glyph orientation readback failed.";
  orientationOutput.dataset.orientationState = "error";
});
