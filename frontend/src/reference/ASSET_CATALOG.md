# Public asset catalog

Captured from the publicly served `https://www.contentarchitecture.dev/` page on
2026-10-15. The site owner authorized reproducing its public design and assets.
No private source, sign-up, checkout, or authenticated destination was accessed.
Image captures were saved as AVIF where the public Sanity CDN negotiated
`auto=format`. SHA-256 values below identify the local copies.

## Fonts

| Local asset | Public source | SHA-256 |
| --- | --- | --- |
| `public/reference/assets/GeistSans-VF.woff2` | `https://www.contentarchitecture.dev/_next/static/immutable/media/Geist_Variable-s.p.2-i4fw5gbtlb7.woff2` | `a369fcf5628ea2aa4e1b9e2ec6a5b3624e365bda588e1f0f2f12b564f728fbb8` |
| `public/reference/assets/GeistMono-VF.woff2` | `https://www.contentarchitecture.dev/_next/static/immutable/media/GeistMono_Variable.p.0b02cdm7g7y6h.woff2` | `fba8f577f38a2bbcbe818efa6348dd58f36303a10b8737c42fefad275be563ab` |

Both font files are from the public Geist family. Its SIL Open Font License is
retained at `frontend/src/reference/resources/OFL.txt`.

## Showcase images

Each source below is the public Sanity CDN object stem observed in the page DOM.
The browser requested an AVIF representation with `auto=format`.

| Local asset | Public object stem | SHA-256 |
| --- | --- | --- |
| `anuc-home.avif` | `ec80e52f1fe72f2d882ec6a1d067090a79cc4288-3840x2160.jpg` | `9c9950fa4881ebc3dec51269352b64e7c47c26db8826e422f23e7a923984151d` |
| `aspen-search.avif` | `d79913ca416f63f31c617b2c3936befb77a3d6ac-3840x2160.jpg` | `ad7f00b1b6d94b49d146c238c4028b71b61bd98d71b67b7dfe754a1ba88cfb54` |
| `blink.avif` | `2ce1bfb9315017055ecb2efb9daaeda9ae53a02b-3840x2160.jpg` | `e140f6676771248a8d3be58ed3d5b8ec3e7c5884138c83e8ab2392a36d72bae6` |
| `content-architecture.avif` | `3d4d11a7eca4cee6d7d9955e3009804699923b19-3840x2160.png` | `d52a93217c84612a9e872a43e06274699d35464e93e9c1710be15a565b19033b` |
| `creative-lives.avif` | `619d1accb39f837a13c75efe4e0af4a11977669a-3840x2160.jpg` | `9ad0a909c40679c3ec287828d7ffd437d52ce68a3c50738c58a1ee07a970268c` |
| `edoardo-lunardi.avif` | `4f7bcbe2f921c92cdd73ab3e44633209d089071a-3840x2160.jpg` | `2881a599d429fea9193915e091255aef64bfc4a4784bfe55f2b1099286d77785` |
| `good-fella.avif` | `be3c66bd045bc48cd8cdbd8a42f4488b340547f7-3840x2160.jpg` | `201452b62c90e711a56ffa107c4e83b3abbf0fe20f4023f8036eb1e9475aa510` |
| `house-of-honey.avif` | `dd1aa647aa9b19c7eb7b72f3649e4425a2c9dd44-3840x2160.jpg` | `7d04ce68e11d8de53022f857054141023b346eb06cb05beeab3db9054c7428d9` |
| `muralia.avif` | `2d6f54ea28f4d35e3099be43fe7cedde3c2aebff-3840x2160.jpg` | `2206e8c5d7dc6308ac90fc3ea56b03901c433875003f89e2bd025fa57c5c78a3` |
| `prism.avif` | `dc1b336904b1f9bf83a5e7acb04d07304be55c53-3840x2160.png` | `e9da085aa4284aa0d673bc5a217e2cd97477f5c751fcf41b765ac52437537411` |
| `serve-robotics.avif` | `b7825b5fd0f02439d431345d7c86f0a19ee9f27b-3840x2160.jpg` | `68fd9ddd0cbc8e8a8b13252f0a1801d76d52f06c654fa5ad5b5f66a1aaa4845f` |

## Marks and portraits

| Local asset | Public source observed | SHA-256 |
| --- | --- | --- |
| `content-architecture-mark.svg` | Inline SVG mark collected from the public page DOM (`f46430fbdd804bde.svg`) | `332e8ee42d63233c514135de4ff089089b2045b8d24f73c16a41275ca487d41c` |
| `content-architecture-symbol.svg` | Square inline SVG (`viewBox="0 0 400 400"`) observed in the public header DOM on 2026-10-15 | `9e927bbe14c53564439b467b049c44e565748dfded4ad629362212061ffdf7a8` |
| `edoardo-portrait.png` | Public profile portrait image served by the Sanity CDN | `d20b76a2d058580e2a9f736a125c924a533defadb8c38d8322e70083113deb10` |
| `avatar-julian.jpg` | Public Sanity object `ffbe610b30c6bdd31d413a5e16821db3f489b4cd-512x512.jpg` | `b0d542dea751395fe48146707b734e4d5fa9ccde44f5cb0e80e56ad8b183eb6f` |
| `avatar-elliott.png` | Public Sanity object `21e750fd3ee0a6217523a980b741bb38ce2a3336-512x512.png` | `e494a320f360c264b1bb42c41c862a55ad8fa980f6ee5dd800f06b09bc300c42` |
| `avatar-malik.png` | Public Sanity object `d8178d0c978dc78b2848cb4684cd92812a4cd6a6-800x800.jpg` | `84e86e6ac9bd10ff3002f34aa0e6c9645f8acc9bed00e7bfdddcd11306d2a352` |
| `avatar-good-fella.png` | Public GitHub profile image for Good Fella Studio, collected from the page DOM | `791b886f0a4c22bcc9149476e890a34dcda436d69cdcd034e3f92d580c42ba55` |
| `avatar-elliott-github.png` | Public GitHub profile image for Elliott Mangham, collected from the page DOM; not referenced by the current homepage component | `cf37aee9d049032eea309a847e3ff2b67cde0fe3ce60d40beeec812360327981` |
| `avatar-studioboldest.png` | Public GitHub profile image for Studio Boldest, collected from the page DOM | `f72ada3b42e477cd654d374afe3002b868572638cefbc2b6ac16990f297fb8d0` |
| `avatar-minh.jpg` | Public GitHub profile image for Minh Chanh, collected from the page DOM | `f4ad507817977521f1c1e79796c772da1163c4e43f085edd99617cf497fee26e` |

## Videos

These local MP4s are remuxed with VLC 3.0.20 from the exact public Mux HLS
playback IDs used by the reference. Each source rendition is capped at 720p.
Byte counts below are the final local MP4 sizes.

| Local asset | Public source and local bytes | SHA-256 |
| --- | --- | --- |
| `content-architecture-reel.mp4` | `https://stream.mux.com/DK00HHX701aluFidRzbGHE5uEqyhWfB00Gsqv02JfSzYQL4.m3u8` (720p; 6,295,486 bytes) | `6f5d768ad646a0f1a39c9ae45a0c8a79ab38c5f0f8e0801fc726933275197663` |
| `edoardo-founder-video.mp4` | `https://stream.mux.com/MTxcyyeb3G2Nms7mL00HNlpdh4UEc7Uk019pYk3zWovbU.m3u8` (720p; 13,037,212 bytes) | `801bd393597143bbb049dd1fae4854f36dfe655cebbeff1ce3afcc887f0308cf` |
