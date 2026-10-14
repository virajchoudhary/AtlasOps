export const RING_COUNT = 30;
export const ATLAS_COLUMNS = 8;
export const ATLAS_CELL_SIZE = 64;
export const MAX_ATLAS_GLYPHS = 128;
export const INSTANCE_STRIDE = 6;

export const INSTANCE_RADIUS = 0;
export const INSTANCE_ANGLE = 1;
export const INSTANCE_SPEED = 2;
export const INSTANCE_SIZE = 3;
export const INSTANCE_GLYPH = 4;
export const INSTANCE_RING = 5;

export interface RingModel {
  readonly phrase: string;
  readonly glyphs: readonly string[];
  readonly dotIndex: number;
  readonly atlasRows: number;
  readonly instanceCount: number;
  readonly instances: Float32Array;
}

function createRandom(seedText: string): () => number {
  let state = 2166136261;

  for (let index = 0; index < seedText.length; index += 1) {
    state = Math.imul(state ^ seedText.charCodeAt(index), 16777619);
  }

  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

function smoothstep(edge0: number, edge1: number, value: number): number {
  if (edge0 === edge1) return value < edge0 ? 0 : 1;
  const t = Math.max(0, Math.min(1, (value - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

function angularDistance(angle: number, center: number): number {
  return Math.abs(Math.atan2(Math.sin(angle - center), Math.cos(angle - center)));
}

function normalizeText(text: string): string {
  return text.normalize("NFKC").replace(/\s+/g, " ").trim().toUpperCase();
}

export function createRingModel(text: string): RingModel {
  const phrase = normalizeText(text);
  const phraseCharacters = Array.from(phrase);
  const uniqueCharacters = [...new Set(phraseCharacters.filter((character) => character !== "."))]
    .slice(0, MAX_ATLAS_GLYPHS - 1);
  const dotIndex = uniqueCharacters.length;
  const glyphs = [...uniqueCharacters, "."];
  const glyphIndices = new Map(glyphs.map((glyph, index) => [glyph, index]));
  const atlasRows = Math.max(1, Math.ceil(glyphs.length / ATLAS_COLUMNS));
  const random = createRandom(phrase);
  const instances: number[] = [];

  for (let ringIndex = 0; ringIndex < RING_COUNT; ringIndex += 1) {
    const progress = ringIndex / (RING_COUNT - 1);
    const radius = 0.06 + 1.39 * progress;
    const speedMagnitude = 0.006 + (1 - progress) * 0.029;
    const speed = (ringIndex % 2 === 0 ? 1 : -1) * speedMagnitude;
    const fontSize = 14 + 16 * progress;
    const characterCount = Math.max(
      8,
      Math.floor((2 * Math.PI * radius) / (0.6 * fontSize * (2 / 1080))),
    );
    const bandCenter = random() < 0.15
      ? random() * Math.PI * 2
      : 0.25 + (random() - 0.5) * Math.PI * 2;
    const bandFraction = random() < 0.1
      ? 0.05 + 0.15 * random()
      : 0.25 + 0.35 * progress + 0.3 * random();
    const bandHalfWidth = Math.min(0.98, bandFraction) * Math.PI;
    const bandSoftness = Math.PI * (0.07 + 0.13 * random());
    const startAngle = random() * Math.PI * 2;
    const angleStep = (Math.PI * 2) / characterCount;
    const outerBand = bandHalfWidth + bandSoftness;
    const innerBand = Math.max(0, bandHalfWidth - bandSoftness);
    let slot = 0;

    while (slot < characterCount) {
      const phraseRun = phraseCharacters.length > 0 ? phraseCharacters : [" "];

      for (const character of phraseRun) {
        if (slot >= characterCount) break;

        const angle = startAngle + slot * angleStep;
        const bandWeight = 1 - smoothstep(
          innerBand,
          outerBand,
          angularDistance(angle, bandCenter),
        );
        const isGlyph = character !== " "
          && glyphIndices.has(character)
          && (bandWeight > 0.7 || (bandWeight >= 0.3 && random() < bandWeight));
        const glyphIndex = isGlyph ? glyphIndices.get(character) ?? dotIndex : dotIndex;
        const size = isGlyph ? fontSize * (0.85 + 0.15 * bandWeight) : 5;

        instances.push(radius, angle, speed, size, glyphIndex, ringIndex);
        slot += 1;
      }

      const gapCount = 1 + Math.floor(random() * 3);
      for (let gap = 0; gap < gapCount && slot < characterCount; gap += 1) {
        const angle = startAngle + slot * angleStep;
        instances.push(radius, angle, speed, 5, dotIndex, ringIndex);
        slot += 1;
      }
    }
  }

  const instanceData = new Float32Array(instances);

  return {
    phrase,
    glyphs,
    dotIndex,
    atlasRows,
    instanceCount: instanceData.length / INSTANCE_STRIDE,
    instances: instanceData,
  };
}

export function createGlyphAtlas(model: RingModel, fontFamily: string): HTMLCanvasElement | null {
  const atlas = document.createElement("canvas");
  atlas.width = ATLAS_COLUMNS * ATLAS_CELL_SIZE;
  atlas.height = model.atlasRows * ATLAS_CELL_SIZE;

  const context = atlas.getContext("2d");
  if (!context) return null;

  context.clearRect(0, 0, atlas.width, atlas.height);
  context.fillStyle = "#ffffff";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.font = `500 47.36px "${fontFamily}", ui-monospace, monospace`;

  for (let index = 0; index < model.glyphs.length; index += 1) {
    const x = ((index % ATLAS_COLUMNS) + 0.5) * ATLAS_CELL_SIZE;
    const y = (Math.floor(index / ATLAS_COLUMNS) + 0.55) * ATLAS_CELL_SIZE;
    const glyph = model.glyphs[index] ?? "";
    if (glyph === ".") {
      context.beginPath();
      context.arc(x, y, 5.76, 0, Math.PI * 2);
      context.fill();
    } else {
      context.fillText(glyph, x, y);
    }
  }

  return atlas;
}
