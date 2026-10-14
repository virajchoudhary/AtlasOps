import {
  ATLAS_UPLOAD_FLIP_Y,
  ATLAS_CELL_SIZE,
  ATLAS_COLUMNS,
  createRingModel,
} from "../ring-canvas-model";
import { ringVertexShader } from "../ring-canvas-shaders";

const phrase = "THE CONTENT ARCHITECTURE";
const output = document.querySelector<HTMLOutputElement>("#glyph-atlas-identity");
if (!output) throw new Error("Glyph atlas identity output is missing.");

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

function buildProgram(gl: WebGL2RenderingContext): WebGLProgram {
  const fragmentSource = `#version 300 es
precision highp float;

uniform sampler2D tAtlas;

in vec2 vAtlasUv;
out vec4 fragColor;

void main() {
  fragColor = vec4(texture(tAtlas, vAtlasUv).rgb, 1.0);
}`;
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
  const canvas = document.createElement("canvas");
  canvas.width = ATLAS_CELL_SIZE;
  canvas.height = ATLAS_CELL_SIZE;
  const gl = canvas.getContext("webgl2", {
    alpha: false,
    antialias: false,
    preserveDrawingBuffer: true,
  });
  if (!gl) throw new Error("WebGL2 is unavailable for the atlas identity fixture.");

  const program = buildProgram(gl);
  const vertexArray = gl.createVertexArray();
  const quadBuffer = gl.createBuffer();
  const instanceBuffer = gl.createBuffer();
  const texture = gl.createTexture();
  if (!vertexArray || !quadBuffer || !instanceBuffer || !texture) {
    throw new Error("WebGL could not allocate the atlas identity fixture.");
  }

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
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

  gl.uniform1f(gl.getUniformLocation(program, "uTime"), 0);
  gl.uniform2f(gl.getUniformLocation(program, "uResolution"), ATLAS_CELL_SIZE, ATLAS_CELL_SIZE);
  gl.uniform2f(gl.getUniformLocation(program, "uAtlasGrid"), ATLAS_COLUMNS, model.atlasRows);
  gl.uniform1f(gl.getUniformLocation(program, "uRadiusScale"), 0);
  gl.uniform1f(gl.getUniformLocation(program, "uGlyphScale"), 1);
  gl.uniform2f(gl.getUniformLocation(program, "uPointer"), 999, 999);
  gl.uniform1f(gl.getUniformLocation(program, "uPointerInfluence"), 0);
  gl.uniform1f(gl.getUniformLocation(program, "uPointerRadius"), 0.35);
  gl.uniform1fv(gl.getUniformLocation(program, "uRippleStarts[0]"), new Float32Array(16).fill(-1));
  gl.uniform1fv(gl.getUniformLocation(program, "uRippleStrengths[0]"), new Float32Array(16));
  gl.uniform1i(gl.getUniformLocation(program, "tAtlas"), 0);
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 1);

  const identities: string[] = [];
  const pixels = new Uint8Array(4);
  for (let index = 0; index < model.glyphs.length; index += 1) {
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array([
      0.001,
      1,
      0,
      ATLAS_CELL_SIZE,
      index,
      0,
    ]));
    gl.uniform1f(gl.getUniformLocation(program, "uDotIndex"), index);
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, 4, 1);
    gl.readPixels(
      ATLAS_CELL_SIZE / 2,
      ATLAS_CELL_SIZE / 2,
      1,
      1,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      pixels,
    );
    const glyph = model.glyphs[index] ?? "";
    identities.push(`${glyph === " " ? "_" : glyph}:${pixels[0] ?? 0}`);
  }

  gl.deleteTexture(texture);
  gl.deleteBuffer(quadBuffer);
  gl.deleteBuffer(instanceBuffer);
  gl.deleteVertexArray(vertexArray);
  gl.deleteProgram(program);

  return identities.join("|");
}

try {
  output.dataset.glyphIdentities = runAtlasIdentityReadback();
} catch (error) {
  output.dataset.error = error instanceof Error ? error.message : "Atlas identity readback failed.";
}
