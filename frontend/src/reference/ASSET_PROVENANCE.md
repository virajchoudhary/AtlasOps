# Ring Renderer Provenance

Inspected the public reference at `https://www.contentarchitecture.dev/` on 2026-10-15. At the inspected desktop viewport, the hero uses a full-viewport canvas whose visible typography field occupies the right half; the left-side content remains readable above the light page background.

Publicly served code inspected:

- `/_next/static/immutable/chunks/1x3h3770j5f1t.js` contains the ring scene and GLSL shaders. The bundle creates an OGL `Renderer` with `webgl: 2`, uses a 2x DPR cap, supplies instanced glyph attributes to the shader, and samples an 8-column font atlas. Its shader uniforms include 30-ring arrays for charge, gather, offsets, and arrival timing.
- `/_next/static/immutable/chunks/43o6e-rfict9z.js` is the lazily loaded renderer dependency chunk containing OGL geometry, program, texture, mesh, and VAO implementations.
- `/_next/static/immutable/chunks/0pie699i1gfhn.js` references the ring chunks above for lazy loading.
- `/_next/static/immutable/chunks/0cubl1eesne7h.js` contains the site's deferred-visibility wrapper: a 400px preload margin, `requestIdleCallback` with a 2-second timeout, and a 200ms timer fallback.

The ring renderer also gates RAF rendering with `IntersectionObserver` and document visibility, and responds to `ResizeObserver`. Its observed ring parameters are retained in the independent geometry generator: radii 0.06-1.45, alternating speeds from approximately 0.035 to 0.006 radians/second, and 14-30px nominal glyph sizes. The saved 1440x900 and 1920x1080 captures show the ring field occupying a viewport-height panel on the right, with the outer circle cropped by the panel edges; the 390x844 capture keeps that oversized treatment in the stacked mobile layout. The renderer therefore scales the outer radius to half the actual canvas mount height rather than fitting the circle inside its width. The independent implementation uses one shared 8-column, 64px-cell atlas per text value and uploads it once as a WebGL texture. The WebGL upload flips the top-origin Canvas2D atlas; the vertex shader maps atlas rows top-down while preserving each cell's local glyph orientation. A browser fixture checks all initial `THE CONTENT ARCHITECTURE` tile identities and compares an asymmetric alpha sentinel plus a Geist Mono `F` mask against a separate Canvas2D rendering. It uses the browser WebGL2 API directly; no OGL dependency was added. The fixture verifies local atlas orientation, not pixel-perfect equivalence with every glyph in the live site.

Reused public asset:

- `resources/GeistMono-Variable.woff2`, copied unchanged from `https://www.contentarchitecture.dev/_next/static/immutable/media/GeistMono_Variable.p.0b02cdm7g7y6h.woff2`.
  SHA-256: `fba8f577f38a2bbcbe818efa6348dd58f36303a10b8737c42fefad275be563ab` (verified against the live public URL on 2026-10-15).
  The required SIL Open Font License 1.1 notice is retained in `resources/OFL.txt`; its public upstream source is `https://raw.githubusercontent.com/vercel/geist-font/main/OFL.txt`.
- The current public stylesheet also serves `Geist_Variable-s.p.2-i4fw5gbtlb7.woff2`; this lane reuses only the Geist Mono asset for the ring atlas.

The integration is independently written. The pointer ripple/hover treatment is a compact reconstruction, not a byte-for-byte copy of the source bundle. This does not claim pixel-perfect fidelity.
