import { describe, expect, it } from "vitest";
import {
  createRingModel,
  INSTANCE_STRIDE,
  RING_MIN_RADIUS,
  RING_COUNT,
  RING_MAX_RADIUS,
} from "./ring-canvas-model";

describe("createRingModel", () => {
  it("makes stable, independently configurable glyph fields with thirty radial bands", () => {
    const first = createRingModel("AtlasOps rings");
    const second = createRingModel("AtlasOps rings");
    const alternate = createRingModel("Recovery verified");

    expect(first.phrase).toBe("ATLASOPS RINGS");
    expect(first.instances).toEqual(second.instances);
    expect(first.instanceCount).toBeGreaterThan(RING_COUNT * 8);
    expect(first.instances.length).toBe(first.instanceCount * INSTANCE_STRIDE);
    expect(first.instances).not.toEqual(alternate.instances);
    expect(first.glyphs.at(-1)).toBe(".");
    expect(first.atlasRows).toBe(Math.ceil(first.glyphs.length / 8));
  });

  it("keeps ring radii and alternating angular velocities inside the reference envelope", () => {
    const model = createRingModel("Content Architecture");
    const firstRing = model.instances.slice(0, INSTANCE_STRIDE);
    const lastRingOffset = (model.instanceCount - 1) * INSTANCE_STRIDE;

    expect(firstRing[0]).toBeCloseTo(RING_MIN_RADIUS);
    expect(firstRing[2]).toBeGreaterThan(0);
    expect(model.instances[lastRingOffset + 0]).toBeCloseTo(RING_MAX_RADIUS);
    expect(model.instances[lastRingOffset + 2]).toBeLessThan(0);
  });
});
