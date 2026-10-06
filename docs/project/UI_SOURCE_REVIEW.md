# Research Console Source Review

Review date: 6 October 2026. This redesign starts from remote `main`
`6970590b450e1f85828488aef6686c28deea980f`, not PR #185.

## Reusable Code

The coherent implementation uses local shadcn/Radix application primitives,
Lucide icons, and direct Motion transitions. Full copied-source license notices
are in [THIRD_PARTY_NOTICES](../../frontend/THIRD_PARTY_NOTICES.md).

| Source | Inspected source / license | Selection |
|---|---|---|
| shadcn/ui | `0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13`, `apps/v4/registry/bases/radix/ui/`, MIT | Button, Badge, Input, Accordion, adapted to one local token system |
| Motion | `55eb6bbd5f861785592992b6b8bed2cf81fe9103`, MIT | Restrained route, chart and interaction transitions; reduced-motion support |
| Lucide | Official `lucide-react` package, ISC plus Feather MIT attribution | Consistent agent, governance and navigation icons |
| Magic UI | `cdb348cb4c72a9b54b554d8617801e479fbc8714`, MIT; Animated Beam and Dot Pattern reviewed | Not copied; custom architecture rendering avoids a second visual system |
| Motion Primitives | `120f64f6ca60348e251f929e9c81f11ccbe45eda`, MIT; Transition Panel reviewed | Not copied; direct Motion covers the needed transitions |
| Aceternity UI | Official Spotlight/component catalog and licence/terms inspected | Not copied; no blanket MIT assumption, no Pro source or assets |

Official component sources:
[shadcn](https://github.com/shadcn-ui/ui/tree/0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13/apps/v4/registry/bases/radix/ui),
[Motion](https://motion.dev/docs/react),
[Magic UI](https://magicui.design/docs/components/animated-beam),
[Motion Primitives](https://github.com/ibelick/motion-primitives/tree/120f64f6ca60348e251f929e9c81f11ccbe45eda/components/core),
[Aceternity](https://ui.aceternity.com/components/spotlight).

## Inspiration Only

Current public interfaces were browsed: [OpenAI](https://openai.com),
[Apple](https://www.apple.com), [Linear](https://linear.app),
[Vercel](https://vercel.com), [Raycast](https://www.raycast.com),
[Cursor](https://cursor.com), and [Grafana](https://grafana.com).
Linear's embedded issue interface and Grafana's public dashboard informed compact
navigation, identifiers, state hierarchy and focused details. Product-first
whitespace and restrained transitions informed the overall direction.

No proprietary source, font files, logos, imagery, or page composition was copied.
The architecture graphic depicts AtlasOps' own conceptual workflow, not telemetry.
System fonts use the operating system's installed font stack; no font is redistributed.

## Verification Boundary

Component documentation, source, licensing, browser DOM and screenshots were
collected. The current root and Luna runtimes reject image inputs, so captured
screenshots cannot by themselves establish independent pixel-level visual acceptance.
Do not merge on the strength of source, DOM, or unviewed screenshots alone.
