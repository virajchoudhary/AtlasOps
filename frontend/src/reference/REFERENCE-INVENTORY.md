# Content Architecture Reference Inventory

Observed on 2026-10-15 at `https://www.contentarchitecture.dev/`. Inspection covered
the publicly served page only. The site owner authorized public design and asset
reproduction; no sign-up, checkout, authentication, or private repository flow was
submitted or accessed.

## Scope and Integration

- This lane owns the homepage, shared navigation, the Learn More and Watch Reel
  overlays, repository explorer, project reel previews, testimonial carousel, FAQ,
  newsletter, local public asset catalog, and reference capture tooling.
- The ring renderer belongs to a separate lane. `ReferencePage` currently accepts
  `heroArtwork: ReactNode`; the route integration should pass the renderer as that
  prop once its owner supplies the final export contract.
- The secondary public routes (`/blog`, `/roadmap`, `/legal/privacy-policy`, and
  `/legal/terms-of-service`) belong to a separate implementation lane. Root owns
  combining those routes with `/reference`; this lane does not edit `App.tsx`.
- The operational AtlasOps route and API are outside this reference surface.

## Page Sequence

1. Split hero: off-white editorial copy on the left and dark rotating typographic
   artwork on the right. Copy: “The stack agents don't reinvent.” Primary links
   are Get Access and Watch Reel; a Scroll to Dive In cue points into the page.
2. “The page builder alone costs you days. Every single time.” A 24-hour,
   three-day cost breakdown.
3. “Every decision already made. So you can skip to the actual work.” Nine
   numbered feature blocks.
4. “This is the actual repo.” Interactive Next.js/Astro file explorer, README
   editor, search, and terminal panel.
5. “The work that gets remembered.” Eleven project previews and a three-step
   testimonial carousel.
6. “Two editions. One architecture. Lifetime updates.” Edition switch, €549
   one-time price, benefits, and edition-specific CTA.
7. “Before you buy.” Seventeen single-open FAQ entries; the first is open by
   default.
8. Pale footer CTA, inline newsletter form, resource/legal links, attribution,
   and year.

The site alternates light `#f1eee7` sections with dark `#232323` sections. Body
text is `#232323`; the body uses Geist Sans and small labels/navigation use Geist
Mono. A live desktop observation at CSS viewport `1904x881` measured the hero
heading at `78.77px / 78.77px`, weight `500`; the root coordinator separately
observed the narrower `974px` state at `40px / 40px`, weight `500`. A later live
check at `1440x900` measured the heading at `43.0769px / 43.0769px`, weight `500`,
with a `510.66px` text box at `(80px, 335.06px)`. The centered navigation measured
`515.5x76px` at `(454.75px, 16px)`. Do not treat the browser-window screenshot
size as a CSS viewport measurement.

At the live desktop observation, the centered floating navigation occupied
approximately `521x76px` at `(691,16)` and contained Home, Features, The repo,
Showcase, Pricing, FAQ, Blog, plus the “Now available with Astro” ticker. The
main content geometry measured:

| Section | Top | Height | Surface |
| --- | ---: | ---: | --- |
| Features | 4416px | 3400px | Dark |
| The repo | 7816px | 1570px | `#f1eee7` around dark editor |
| Showcase | 9386px | 5878px | `#232323` |
| Pricing | 15264px | 1005px | `#f1eee7` |
| FAQ | 16269px | 2173px | `#232323` |

The actual CSS viewport recorded by the dedicated Chrome tab was `1904x881` at
DPR 1; the scrollbar accounts for the difference between `innerWidth` and
`documentElement.clientWidth` in one capture. The supplied audit folder also
already contains initial-state captures at `1920x1080`, `1440x900`, `1280x800`,
`768x1024`, and `390x844`, plus a delayed-newsletter mobile state. Those captures
are inspection evidence, not implementation assets.

## Verified Interactions

- Header links use native anchors for `#features`, `#the-repo`, `#showcase`,
  `#pricing`, and `#faq`; the mobile header collapses to Menu plus the Astro
  ticker.
- The hero's Watch Reel button opens a native `<dialog aria-label="Watch reel">`.
  The dialog is black/full-screen with a top-right Close control, shows a short
  “CALIBRATING” state, then plays a public 27-second Mux reel. The live public
  page uses a `<mux-player>` custom element with controls and tracking disabled.
- Learn More opens a native drawer, not an anchor jump. It has a section navigator
  and the three anchored sections listed above. “Who Am I” includes a portrait,
  an embedded muted loop video, and public social/contact links.
- The bottom-right overview control expands page links; Learn More is a separate
  action.
- The repo explorer switches between Next.js and Astro. Its terminal is visible
  in the captured initial state. Ctrl+J toggles the terminal; Ctrl+K opens Search
  Files. Search filters the file list. The public terminal is not a command
  executor.
- Project previews reveal their source images over canvas artwork. The testimonial
  carousel has three steps (Julian Fella, Elliott Mangham, Malik Kotb), with
  bounded Previous/Next buttons and an `NN / 03` counter.
- Pricing changes between Next.js and Astro using the edition selector; both
  retain the €549 price and update edition-specific text/CTA.
- FAQ is single-open. The earlier 16-entry capture predates the current
  seventeenth entry, “What is this not?” Entry one starts expanded. The new answer
  describes the offer as an architectural foundation for writing real code, not a
  no-code editor, UI kit, auth/billing/dashboard SaaS, or course.
- The newsletter dialog title is “Not buying today? Stay close.” The public page
  data specifies a 12-second delay. It includes an email field and Subscribe
  button. This local reproduction must never transmit the typed address.
- All checkout, signup, and third-party destinations remain visible but are never
  automatically submitted.
- The site includes scroll-linked reveals, moving typographic seams, a persistent
  Learn More/minimap control, and reduced-motion behavior.

## Public Sources and Asset Catalog

The page loads Geist Sans and Geist Mono as variable WOFF2 files from:

- `https://www.contentarchitecture.dev/_next/static/immutable/media/Geist_Variable-s.p.2-i4fw5gbtlb7.woff2`
- `https://www.contentarchitecture.dev/_next/static/immutable/media/GeistMono_Variable.p.0b02cdm7g7y6h.woff2`

Stylesheets observed:

- `https://www.contentarchitecture.dev/_next/static/immutable/chunks/3_7k096ht-by4.css`
- `https://www.contentarchitecture.dev/_next/static/immutable/chunks/41dd7myy6mu18.css`
- `https://www.contentarchitecture.dev/_next/static/immutable/chunks/3k9-onrok7n76.css`

Showcase image origins observed in the page DOM (all served from Sanity's public
CDN and negotiated as AVIF by `auto=format`):

| Project | Public Sanity image stem | Local asset name |
| --- | --- | --- |
| Good Fella | `be3c66bd045bc48cd8cdbd8a42f4488b340547f7-3840x2160.jpg` | `good-fella.avif` |
| House of Honey | `dd1aa647aa9b19c7eb7b72f3649e4425a2c9dd44-3840x2160.jpg` | `house-of-honey.avif` |
| Aspen Search | `d79913ca416f63f31c617b2c3936befb77a3d6ac-3840x2160.jpg` | `aspen-search.avif` |
| Anuc Home | `ec80e52f1fe72f2d882ec6a1d067090a79cc4288-3840x2160.jpg` | `anuc-home.avif` |
| Edoardo Lunardi | `4f7bcbe2f921c92cdd73ab3e44633209d089071a-3840x2160.jpg` | `edoardo-lunardi.avif` |
| Serve Robotics | `b7825b5fd0f02439d431345d7c86f0a19ee9f27b-3840x2160.jpg` | `serve-robotics.avif` |
| Prism | `dc1b336904b1f9bf83a5e7acb04d07304be55c53-3840x2160.png` | `prism.avif` |
| Muralia | `2d6f54ea28f4d35e3099be43fe7cedde3c2aebff-3840x2160.jpg` | `muralia.avif` |
| blink | `2ce1bfb9315017055ecb2efb9daaeda9ae53a02b-3840x2160.jpg` | `blink.avif` |
| Creative Lives in Progress | `619d1accb39f837a13c75efe4e0af4a11977669a-3840x2160.jpg` | `creative-lives.avif` |
| The Content Architecture | `3d4d11a7eca4cee6d7d9955e3009804699923b19-3840x2160.png` | `content-architecture.avif` |

Testimonial portraits use the public Sanity CDN: Julian
`ffbe610b30c6bdd31d413a5e16821db3f489b4cd-512x512.jpg`, Elliott
`21e750fd3ee0a6217523a980b741bb38ce2a3336-512x512.png`, and Malik
`d8178d0c978dc78b2848cb4684cd92812a4cd6a6-800x800.jpg`. Five small pricing
avatars are public GitHub profile images for Good Fella Studio, Elliott Mangham,
Studio Boldest, Malik Kotb, and Minh Chanh.

The header uses the public square inline SVG (`viewBox="0 0 400 400"`) observed
in the page DOM. The footer uses the separately observed horizontal wordmark
SVG (`f46430fbdd804bde.svg`). Both local assets and their SHA-256 values are
recorded in `ASSET_CATALOG.md`. The profile portrait in Learn More is served
from the public Sanity CDN.

The homepage exposes a fixed hero canvas and several other canvas surfaces. The
hero canvas observed at CSS viewport `1904x881` measured `1904x881` at DPR 1.
This lane has not independently verified that canvas's OGL/shader/font-atlas
implementation; the ring-renderer owner is responsible for that technical
finding. The visible reel uses the public Mux playback asset and the repository
content contains no credentials or private source.
