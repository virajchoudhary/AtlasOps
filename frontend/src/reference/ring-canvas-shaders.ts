export const ringVertexShader = `#version 300 es
precision highp float;

in vec2 aPosition;
in vec2 aUv;
in float aRadius;
in float aTheta0;
in float aSpeed;
in float aSize;
in float aGlyphIndex;
in float aRingIndex;

uniform float uTime;
uniform vec2 uResolution;
uniform vec2 uAtlasGrid;
uniform float uRadiusScale;
uniform float uGlyphScale;
uniform float uDotIndex;
uniform vec2 uPointer;
uniform float uPointerInfluence;
uniform float uPointerRadius;
uniform float uRippleStarts[16];
uniform float uRippleStrengths[16];

out vec2 vAtlasUv;
out float vAlpha;

void main() {
  float rippleInfluence = 0.0;
  for (int index = 0; index < 16; index++) {
    float start = uRippleStarts[index];
    if (start < 0.0) continue;

    float elapsed = uTime - start;
    if (elapsed < 0.0 || elapsed >= 1.8) continue;

    float progress = elapsed / 1.8;
    float waveRadius = smoothstep(0.0, 1.0, progress) * 1.6;
    float wave = 1.0 - smoothstep(0.0, 0.425, abs(aRadius - waveRadius));
    float fade = smoothstep(0.0, 0.22, progress) * (1.0 - smoothstep(0.78, 1.0, progress));
    rippleInfluence = max(rippleInfluence, wave * fade * uRippleStrengths[index]);
  }

  float theta = aTheta0 + uTime * aSpeed;
  float cosine = cos(theta);
  float sine = sin(theta);
  vec2 ringPosition = vec2(cosine, sine) * aRadius;
  float mouseDistance = length(ringPosition - uPointer);
  float hover = (1.0 - smoothstep(0.0, uPointerRadius, mouseDistance)) * uPointerInfluence;
  float seed = aTheta0 * 7.13 + aRadius * 13.97;
  float threshold = fract(sin(seed * 12.9898) * 43758.5453);
  float isDot = step(threshold, max(hover * 2.5, rippleInfluence));
  float glyphIndex = mix(aGlyphIndex, uDotIndex, isDot);
  float glyphSize = mix(aSize, 5.0, isDot) * (1.0 + rippleInfluence * 0.5);
  vec2 tangentPosition = vec2(
    -aPosition.x * sine - aPosition.y * cosine,
    aPosition.x * cosine - aPosition.y * sine
  ) * glyphSize * uGlyphScale;

  vec2 pixel = uResolution * 0.5
    + ringPosition * uRadiusScale
    + tangentPosition;
  vec2 clipPosition = vec2(
    pixel.x / uResolution.x * 2.0 - 1.0,
    1.0 - pixel.y / uResolution.y * 2.0
  );
  gl_Position = vec4(clipPosition, 0.0, 1.0);

  float column = mod(glyphIndex, uAtlasGrid.x);
  float row = floor(glyphIndex / uAtlasGrid.x);
  vAtlasUv = vec2(
    (column + aUv.x) / uAtlasGrid.x,
    (row + aUv.y) / uAtlasGrid.y
  );
  vAlpha = mix(0.56, 0.92, clamp(aRingIndex / 29.0, 0.0, 1.0))
    * (1.0 - hover * 0.15);
}`;

export const ringFragmentShader = `#version 300 es
precision mediump float;

uniform sampler2D tAtlas;

in vec2 vAtlasUv;
in float vAlpha;

out vec4 fragColor;

void main() {
  vec4 sampled = texture(tAtlas, vAtlasUv);
  float alpha = sampled.a * vAlpha;
  if (alpha < 0.015) discard;
  fragColor = vec4(vec3(1.0), alpha);
}`;
