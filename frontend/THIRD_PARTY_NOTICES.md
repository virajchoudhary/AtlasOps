# Third-Party Notices

## shadcn/ui

The Button, Badge, Input, and Accordion primitives in `src/components/ui/` are
source-adapted from the official shadcn/ui Radix base, inspected at commit
`0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13`:

- [button.tsx](https://github.com/shadcn-ui/ui/blob/0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13/apps/v4/registry/bases/radix/ui/button.tsx)
- [badge.tsx](https://github.com/shadcn-ui/ui/blob/0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13/apps/v4/registry/bases/radix/ui/badge.tsx)
- [accordion.tsx](https://github.com/shadcn-ui/ui/blob/0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13/apps/v4/registry/bases/radix/ui/accordion.tsx)
- [input.tsx](https://github.com/shadcn-ui/ui/blob/0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13/apps/v4/registry/bases/radix/ui/input.tsx)
- [Repository license](https://github.com/shadcn-ui/ui/blob/0e3abd65a97707f4a9cc3ed07bf5006e1cb67b13/LICENSE.md)

The upstream repository identifies its license as MIT. The adaptations retain
the composable native-element pattern, focus states, and Radix accordion
semantics while using AtlasOps-specific tokens and a smaller local API.

```text
MIT License

Copyright (c) 2023 shadcn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Runtime Libraries

The app imports, rather than copies, these libraries. The package lock records
the resolved versions; their full license files are included in installed
packages:

- [Radix UI Accordion](https://github.com/radix-ui/primitives) - MIT.
- [Lucide React](https://github.com/lucide-icons/lucide) - ISC; the upstream
  license also includes MIT attribution for icons derived from Feather.
- [Motion](https://github.com/motiondivision/motion/tree/55eb6bbd5f861785592992b6b8bed2cf81fe910) - MIT.
- React and React DOM - MIT.
- React Router - MIT.
- Tailwind CSS and its Vite plugin - MIT.
- Vite, Vitest, and the React Vite plugin - MIT.
- Testing Library packages - MIT.
- TypeScript - Apache-2.0.
- jsdom - MIT.

## Geist fonts

`public/assets/fonts/GeistSans-VF.woff2` and
`public/assets/fonts/GeistMono-VF.woff2` are
unchanged variable fonts observed on the publicly accessible
`https://www.contentarchitecture.dev/` site, which the project lead authorized
for visual reproduction. Their SHA-256 digests are
`a369fcf5628ea2aa4e1b9e2ec6a5b3624e365bda588e1f0f2f12b564f728fbb8` and
`fba8f577f38a2bbcbe818efa6348dd58f36303a10b8737c42fefad275be563ab`,
respectively. The fonts are from The Geist Project and are licensed under the
SIL Open Font License 1.1; a copy is included at `public/assets/fonts/OFL.txt`.
The public source font URLs are:

- `https://www.contentarchitecture.dev/_next/static/immutable/media/Geist_Variable-s.p.2-i4fw5gbtlb7.woff2`
- `https://www.contentarchitecture.dev/_next/static/immutable/media/GeistMono_Variable.p.0b02cdm7g7y6h.woff2`

The reference site's public mark was reviewed but is not embedded in the
console; AtlasOps keeps its own identity.
