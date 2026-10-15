export type Edition = "next" | "astro";

export type Feature = {
  title: string;
  description: string;
};

export type CostTask = {
  label: string;
  hours: string;
};

export type CostDay = {
  day: string;
  total: string;
  tasks: CostTask[];
};

export type ShowcaseProject = {
  name: string;
  year: string;
  href: string;
  image: string;
  alt: string;
  credits: string;
  awards?: string;
};

export type Testimonial = {
  quote: string;
  name: string;
  role: string;
  avatar: string;
};

export type FAQItem = {
  question: string;
  answer: string;
};

export type AboutSection = {
  id: string;
  number: string;
  title: string;
  shortTitle: string;
  paragraphs: readonly string[];
};

export const ABOUT_SECTIONS: readonly AboutSection[] = [
  {
    id: "why-this-exists",
    number: "001",
    title: "Why this exists",
    shortTitle: "Why this exists",
    paragraphs: [
      "Every Sanity project I shipped, the first week looked identical. Spin up Next, or Astro. Wire the Studio. Rewrite the page builder. Rebuild the SEO layer. Re-do the webhook revalidation. Re-style the same contact form for the fourth time.",
      "By the time the actual creative work started, 3 days of the budget were gone and the client had not seen a single pixel that mattered.",
      "Extracting it started small. One project. Then two. Then ten. Every time something broke in production, a Sanity migration that nuked a dataset, a CDN cache that served stale OG images for three weeks, a webhook that fired twice and corrupted a sitemap, the fix went back into the boilerplate.",
      "For a long time I called this the cost of headless. I had rebuilt the same foundation so many times I could do it half-asleep, and I mistook that for being good at my job instead of what it was, doing the same work twice.",
      "At some point that stopped being a reason to keep it to myself.",
    ],
  },
  {
    id: "why-i-keep-shipping-it",
    number: "002",
    title: "Why I keep shipping it",
    shortTitle: "Why I keep shipping it",
    paragraphs: [
      "I use this on every project. I am the heaviest user. The bug I find on a Friday client engagement is the patch you get on Monday.",
      "I am one person, not a team. That is a feature. The architecture is consistent because one mind held it from the first schema file to the last revalidation hook. Nobody overrode the opinion. Nobody added a field because a stakeholder asked nicely.",
      "There is no distance between me and this. The decisions in the repo are the ones I make on paid work, in the same week, under the same deadline. When you open the fetch layer or the page builder, you are reading how I actually ship, not a demo cleaned up for sale. That is the whole relationship: you get the thing I rely on, maintained by the person who relies on it most.",
      "I am not trying to turn this into a SaaS. There is no dashboard, no seat-based pricing, no telemetry. The roadmap is not fixed in stone either, it grows out of real client work: when a project turns up something worth having, it becomes part of the product. You buy the repo, you own the repo. I maintain it because I use it too.",
      "Maintaining this in public is a forcing function for my own work. With paying users on both repos, I cannot let the schema rot, skip a Next.js or Astro major, or sit on a breaking change in a Sanity plugin. The same discipline is why an agent is useful on it: the calls are already made and written down, so it builds inside them instead of guessing. Your projects stay current because mine have to.",
    ],
  },
  {
    id: "who-am-i",
    number: "003",
    title: "Who am I",
    shortTitle: "Who am I",
    paragraphs: [],
  },
];

export type RepoFile = {
  kind: "file";
  name: string;
  content: string;
};

export type RepoFolder = {
  kind: "folder";
  name: string;
  children: RepoEntry[];
};

export type RepoEntry = RepoFile | RepoFolder;

export const FEATURE_CARDS: readonly Feature[] = [
  {
    title: "001 / Agent-native",
    description:
      "AGENTS.md and a dozen scoped skills load the conventions before the first prompt, so Claude Code or Cursor builds inside the decisions instead of proposing a new architecture per run. Two MCP servers ship in the repo: one reads the running app, one drives a real Chrome. The agent checks its own work.",
  },
  {
    title: "002 / Agent-ready in production",
    description:
      "The shipped site stays legible to agents: an editable llms.txt drafted from your content, and a token-light Markdown twin of every page on the same URL. A feature you can put in your own proposals.",
  },
  {
    title: "003 / Schema as a system",
    description:
      "Document roles, factory functions, singletons. Every schema looks the same, so every editor knows where to go. You never model the structure from scratch again.",
  },
  {
    title: "004 / The hard fields, already built",
    description:
      "The three fields nobody gets right the first time. A link field that handles every kind of link. A media field that returns image, Mux, Rive, and Lottie in one shape, dimensions included, so layout never shifts. A page builder with guardrails. Typed, composed everywhere.",
  },
  {
    title: "005 / Fetch layer, solved",
    description:
      "CDN bypassed in production, Data Cache doing the work, webhooks invalidating on publish, draft mode wired in. Stale content after publish stops being a midnight problem.",
  },
  {
    title: "006 / A Studio editors actually use",
    description:
      "Every document type where editors expect it. Pages own their routes, singletons stay locked, no hunting. Clients stop emailing to ask where their homepage lives.",
  },
  {
    title: "007 / SEO, done not deferred",
    description:
      "Per-page metadata from schema, sitemap driven by Sanity, OpenGraph with auto-cropped images, robots.txt included. Nothing bolted on the week before launch.",
  },
  {
    title: "008 / Production-ready from day one",
    description:
      "Basic auth, spam-protected forms, redirects managed in Sanity, analytics, view transitions. The plumbing you reconfigure every project, already wired.",
  },
  {
    title: "009 / Wired up, not just cloned",
    description:
      "An interactive setup script provisions the Sanity project, mints tokens, wires CORS and the revalidation webhook, and writes your .env. Migration scripts back up production and move content between environments. The first run is handled, not documented.",
  },
];

export const COST_DAYS: readonly CostDay[] = [
  {
    day: "Day 01",
    total: "~8 hrs",
    tasks: [
      { label: "Page builder schema + preview", hours: "~5" },
      { label: "Stale content after publish", hours: "~3" },
    ],
  },
  {
    day: "Day 02",
    total: "~8 hrs",
    tasks: [
      { label: "Draft mode + live preview", hours: "~4" },
      { label: "Studio editors can actually use", hours: "~3" },
      { label: "Contact form + spam guard", hours: "~1" },
    ],
  },
  {
    day: "Day 03",
    total: "~8 hrs",
    tasks: [
      { label: "SEO, OG images, sitemaps", hours: "~2" },
      { label: "The same 12 components, again", hours: "~2" },
      { label: "Redirects, analytics, Mux", hours: "~2" },
      { label: "Biome, Prettier, git hooks", hours: "~1" },
      { label: "Basic auth for staging", hours: "~1" },
    ],
  },
];

export const SHOWCASE_PROJECTS: readonly ShowcaseProject[] = [
  {
    name: "Good Fella",
    year: "2026",
    href: "https://good-fella.com/",
    image: "/reference/assets/good-fella.avif",
    alt: "Good Fella website built on The Content Architecture",
    credits:
      "Studio: Good Fella. Awards: Awwwards Site of the Day, FWA of the Day, CSSDA Website of the Day.",
    awards: "3 awards / 2026",
  },
  {
    name: "House of Honey",
    year: "2026",
    href: "https://www.houseofhoney.com/",
    image: "/reference/assets/house-of-honey.avif",
    alt: "House of Honey website built on The Content Architecture",
    credits:
      "Design: Eva Landaluce. Development: Edoardo Lunardi. Awards: Awwwards Site of the Day, Awwwards Developer Award, Awwwards Honorable Mention, FWA of the Day, CSSDA Website of the Day, CSSDA Best UI, CSSDA Best UX, CSSDA Best Innovation, Muzli Picks.",
    awards: "9 awards / 2026",
  },
  {
    name: "Aspen Search",
    year: "2026",
    href: "https://www.aspensearch.com/",
    image: "/reference/assets/aspen-search.avif",
    alt: "Aspen Search website hero section about recruiting for software and AI/ML roles",
    credits:
      "Agency: Code Resolution. Design: Louis Saville. Development: Edoardo Lunardi. Awards: Awwwards Site of the Day, Awwwards Developer Award, Awwwards Honorable Mention, FWA of the Day, CSSDA Website of the Day, CSSDA Best UI, CSSDA Best UX, CSSDA Best Innovation.",
    awards: "8 awards / 2026",
  },
  {
    name: "Anuc Home",
    year: "2025",
    href: "https://www.anuchome.com/",
    image: "/reference/assets/anuc-home.avif",
    alt: "Anuc Home website built on The Content Architecture",
    credits:
      "Design: Eva Landaluce. Development: Edoardo Lunardi. Awards: Awwwards Site of the Day, Awwwards Developer Award, Awwwards Honorable Mention, FWA of the Day.",
    awards: "4 awards / 2025",
  },
  {
    name: "Edoardo Lunardi",
    year: "2025",
    href: "https://www.edoardolunardi.dev/",
    image: "/reference/assets/edoardo-lunardi.avif",
    alt: "Edoardo Lunardi portfolio website",
    credits:
      "Awards: Awwwards Site of the Day, Awwwards Developer Award, Awwwards Honorable Mention, FWA of the Day, CSSDA Best UI, CSSDA Best UX, CSSDA Best Innovation, CSSDA Special Kudos, WD Awards Site of the Day.",
    awards: "9 awards / 2025",
  },
  {
    name: "Serve Robotics",
    year: "2026",
    href: "https://www.serverobotics.com/",
    image: "/reference/assets/serve-robotics.avif",
    alt: "Serve Robotics website built on The Content Architecture",
    credits:
      "Agency: wild. Design: Eva Landaluce, Monde Marafane. Development: Edoardo Lunardi. Awards: Awwwards Site of the Day, Awwwards Developer Award, Awwwards Honorable Mention.",
    awards: "3 awards / 2026",
  },
  {
    name: "Prism",
    year: "2026",
    href: "https://prismscience.org/",
    image: "/reference/assets/prism.avif",
    alt: "Prism website homepage with the headline Unlocking Protein Dynamics",
    credits: "Design: Benjamin Kuehn, Louis Saville. Development: Edoardo Lunardi.",
  },
  {
    name: "Muralia",
    year: "2025",
    href: "https://www.muralia.at/",
    image: "/reference/assets/muralia.avif",
    alt: "Muralia website built on The Content Architecture",
    credits:
      "Design: Sebastian Wieser. Development: Edoardo Lunardi. Awards: Awwwards Honorable Mention, CSSDA Special Kudos.",
    awards: "2 awards / 2025",
  },
  {
    name: "blink",
    year: "2026",
    href: "https://www.blink.trade/",
    image: "/reference/assets/blink.avif",
    alt: "blink website built on The Content Architecture",
    credits: "Design: Sebastian Wieser. Development: Edoardo Lunardi.",
  },
  {
    name: "Creative Lives in Progress",
    year: "2025",
    href: "https://creativelivesinprogress.com/",
    image: "/reference/assets/creative-lives.avif",
    alt: "Creative Lives in Progress website built on The Content Architecture",
    credits:
      "Agency: wild. Design: Monde Marafane, Anna Mitterhauser. Development: Edoardo Lunardi, Alex Pagnotta.",
  },
  {
    name: "The Content Architecture",
    year: "2026",
    href: "https://www.contentarchitecture.dev/",
    image: "/reference/assets/content-architecture.avif",
    alt: "The Content Architecture homepage",
    credits: "Design: Edoardo Lunardi. Development: Edoardo Lunardi.",
  },
];

export const TESTIMONIALS: readonly Testimonial[] = [
  {
    quote:
      "We shipped the Good Fella site on an early version and it saved us tons of time. Six months in, we're still building pages and sections in an afternoon without fighting the setup.",
    name: "Julian Fella",
    role: "Co-Founder, Good Fella",
    avatar: "/reference/assets/avatar-julian.jpg",
  },
  {
    quote:
      "Edo and I ran a client project on this together. The plumbing was already handled, so the week we'd normally lose to setup went into the creative work the client actually remembers.",
    name: "Elliott Mangham",
    role: "Founder & Frontend Engineer",
    avatar: "/reference/assets/avatar-elliott.png",
  },
  {
    quote:
      "I opened the fetch layer and found the revalidation problem I'd burned two days on last project, already solved and committed. That one folder paid for the whole thing, and the rest is six years of decisions I'd have made the slow way.",
    name: "Malik Kotb",
    role: "Web Designer & Engineer",
    avatar: "/reference/assets/avatar-malik.png",
  },
];

export const FAQ_ITEMS: readonly FAQItem[] = [
  {
    question: "What stack is this built on?",
    answer:
      "Two editions, one architecture. The Next.js edition runs Next.js 16 with the App Router and React Compiler; the Astro edition runs Astro 7. Both share Sanity v6, TypeScript in strict mode, Tailwind 4, and Biome for lint and format. Deploys on Vercel out of the box; the Next.js edition also runs on Cloudflare via OpenNext.",
  },
  {
    question: "Does it work with Claude Code and Cursor?",
    answer:
      "It is built for it. AGENTS.md plus a dozen scoped skills mean any agentic tool ingests the conventions and boundaries before you write a prompt. Ask an agent to build this from scratch and you get a different architecture every run. Here the decisions are already made, so the agent works inside them instead of inventing new ones. You still read and write the real code yourself; the agent works inside the architecture, it does not write the app for you.\n\nIt also ships two preconfigured MCP servers. One reads the running Next.js dev server: compilation errors, routes, docs that match the installed version. The other drives a real Chrome: screenshots across viewports, performance traces, screencasts of transitions it can review frame by frame. The agent doesn't just know the conventions, it can look at the app it's changing.",
  },
  {
    question: "Is the shipped site agent-ready too?",
    answer:
      "Yes. Every site built on this ships with an editable llms.txt, drafted from your content with Sanity Agent Actions from the Site document, and a token-light Markdown version of every page and article, served on the same URL to any agent that sends Accept: text/markdown. You generate it per page in Studio, review it, and it is served verbatim. Your client's site is readable by assistants and agentic crawlers on day one, a line item you can put in your own proposal.",
  },
  {
    question: "Am I locked into this exact stack?",
    answer:
      "No. The opinion lives in the architecture, and the tools sit on top of it. Tailwind, Biome, Mux, Rive, Lottie, these are the defaults I reach for on most projects, wired in cleanly so they come out just as cleanly. Don't want Tailwind? Pull it. Prefer ESLint over Biome? Swap it. No Mux, Rive, or Lottie in this project? Drop them. What you are really buying is the patterns underneath, how content is modeled, fetched, and composed. The libraries are just what I ship with on 90% of my projects.\n\nThe Sanity layer is decoupled by design too. Every import inside the sanity/ folder is relative or an external package, nothing reaches into the app code, so you can lift the whole Studio, schema, and field primitives into another project. The content layer doesn't hold you hostage to the front end.",
  },
  {
    question: "Can I buy both editions?",
    answer:
      "Yes, and you should not pay twice for the same architecture. Both editions model content the same way, so the second repo is mostly a re-read of patterns you already know. If you own one and want the other, whether that is today or a year from now, you pay a reduced price for it.\n\nThere is no automatic checkout for it. Email me from the address you bought with and I will send you a discount code for the second edition.",
  },
  {
    question: "Do I need to know Sanity?",
    answer:
      "Some, yes. This is a real codebase, not a no-code template. You should be comfortable in a Sanity schema file and a Next.js or Astro project. If you are, you will feel at home in minutes. If you have never opened a schema, the article series is the best place to start before deciding.",
  },
  {
    question: "Is this for me if I don't code?",
    answer:
      "No, and I'd rather tell you here than take your money. This is a real Next.js or Astro and Sanity codebase, not a no-code tool: no visual page builder, no drag-and-drop editor. You clone the repo and write real code on top of it. If you don't work in Next.js or Astro with Sanity, it isn't for you.",
  },
  {
    question: "Can I use this for client work?",
    answer:
      "Yes. Unlimited projects, commercial use, no attribution required. Use it on every client site you ship. The one thing you cannot do is resell the architecture itself as a competing product.",
  },
  {
    question: "How is this different from other boilerplates?",
    answer:
      "Most boilerplates give you a pile of features. This gives you decisions. Every hard call, document modeling, the fetch layer, revalidation, the link and media fields, was made once over six years and committed. It is opinionated on purpose, and it is the architecture I ship my own client work on, not a side project cleaned up for sale.",
  },
  {
    question: "Why not just use a free Sanity starter?",
    answer:
      "A free starter gets you a clean install and the easy parts. What it leaves you is the work that actually costs the days: a page builder with guardrails, the fetch layer and revalidation, the link and media fields, a Studio structure your editors don't email you about. Those decisions are still yours to make on every project. Here they're already made, over six years of real client work, and committed. You're not paying for code you could scaffold in an afternoon. You're paying to skip the part nobody quotes for.",
  },
  {
    question: "Will it break on Next.js or Astro updates?",
    answer:
      "This is my daily driver, so I keep it current. Next.js and Astro majors, Sanity migrations, breaking plugin changes, I handle them and push the update. Your license includes every update for as long as I maintain it, which is for as long as I am using it myself.",
  },
  {
    question: "What do I actually get, and for how long?",
    answer:
      "The full repo on day one, a perpetual license, and lifetime updates included, not sold as a separate tier. One payment, no subscription. You own it forever.",
  },
  {
    question: "Do I need a GitHub account?",
    answer:
      "Yes. The repo is delivered as private GitHub access: you enter your username at checkout, and access is granted to that exact account, including every future update. No account yet? Creating one takes a minute, then come back and buy.",
  },
  {
    question: "Do you offer support?",
    answer:
      "Buyers get a private GitHub Discussions space, threaded and searchable, where I answer questions directly. For anything bigger, I am reachable by email. What you will not find is a Discord to get lost in or a support queue that routes you to a bot.",
  },
  {
    question: "What if I find a bug?",
    answer:
      "Tell me, and it gets fixed in the codebase, usually fast. A bug you hit is a bug my own client projects will hit too, so fixing it is in my interest as much as yours. The patch ships to everyone.",
  },
  {
    question: "Can I get a refund?",
    answer:
      "Because you get the full source on purchase, sales are final, the same way every serious code product works. Once the repo is cloned, it cannot be un-cloned. So I have put everything you need to decide up front: read the article series for the full reasoning, browse the real repo above, and look at the sites already shipped on it. If something is unclear before you buy, email me and I will answer honestly, sometimes that means telling you it is not the right fit.",
  },
  {
    question: "What is this not?",
    answer:
      "It is a set of architectural decisions, made once over six years and committed, that gets you to the real work faster. You write real code on top of it. There is no no-code editor, no UI kit or component library to theme, no auth-billing-dashboard SaaS scaffolding, and no course wrapped around it, though the article series explains the reasoning behind it. You buy it once and own it.",
  },
];

const NEXT_REPO: readonly RepoEntry[] = [
  {
    kind: "folder",
    name: ".agents",
    children: [
      { kind: "file", name: "README.md", content: "Agent conventions and scoped skills." },
      { kind: "folder", name: "skills", children: [{ kind: "file", name: "SKILL.md", content: "A scoped skill loaded before the first prompt." }] },
    ],
  },
  {
    kind: "folder",
    name: ".zed",
    children: [{ kind: "file", name: "settings.json", content: "Editor settings and project conventions." }],
  },
  {
    kind: "folder",
    name: "app",
    children: [
      { kind: "folder", name: "(web)", children: [{ kind: "file", name: "page.tsx", content: "The pages model renders the homepage and every page document." }] },
      { kind: "folder", name: "api", children: [{ kind: "file", name: "revalidate/route.ts", content: "The publish webhook revalidates tagged content." }] },
      { kind: "file", name: "favicon.ico", content: "Stable browser and search favicon." },
      { kind: "file", name: "feed.xml", content: "RSS 2.0 feed with article bodies." },
      { kind: "file", name: "llms.txt", content: "Editable content draft for AI assistants." },
      { kind: "file", name: "openapi.json", content: "OpenAPI 3.1 description of public endpoints." },
      { kind: "file", name: "robots.txt", content: "Search and agent crawler directives." },
      { kind: "folder", name: "sanity-studio", children: [{ kind: "file", name: "structure.ts", content: "Studio document structure and singleton locations." }] },
      { kind: "file", name: "not-found.tsx", content: "A real not-found route with a recovery map." },
      { kind: "file", name: "sitemap.ts", content: "CMS-driven sitemap." },
    ],
  },
  {
    kind: "folder",
    name: "components",
    children: [{ kind: "file", name: "button.tsx", content: "Reusable UI primitives and composition patterns." }],
  },
  {
    kind: "folder",
    name: "features",
    children: [
      { kind: "folder", name: "agents", children: [{ kind: "file", name: "llms-txt", content: "Agent-facing content generation." }] },
      { kind: "folder", name: "api", children: [{ kind: "file", name: "openapi", content: "Typed public API descriptions." }] },
      { kind: "folder", name: "auth", children: [{ kind: "file", name: "proxy.ts", content: "Optional HTTP Basic Auth." }] },
      { kind: "folder", name: "blog", children: [{ kind: "file", name: "blog-page.tsx", content: "Knowledge hub and article routes." }] },
      { kind: "folder", name: "draft-mode", children: [{ kind: "file", name: "presentation.tsx", content: "Live preview and draft mode." }] },
      { kind: "folder", name: "fonts", children: [{ kind: "file", name: "font.ts", content: "Local font loading and typography setup." }] },
      { kind: "folder", name: "forms", children: [{ kind: "file", name: "contact-form.tsx", content: "Contact form with spam prevention." }] },
      { kind: "folder", name: "motion", children: [{ kind: "file", name: "transitions.tsx", content: "Scroll, reveal and transition primitives." }] },
      { kind: "folder", name: "page-builder", children: [{ kind: "file", name: "sections.tsx", content: "Reusable page builder sections." }] },
      { kind: "folder", name: "sanity", children: [{ kind: "file", name: "schemas", content: "Typed document schemas and field builders." }] },
      { kind: "folder", name: "style", children: [{ kind: "file", name: "globals.css", content: "Design tokens and global styles." }] },
      { kind: "folder", name: "view-transition", children: [{ kind: "file", name: "transition.tsx", content: "Route transitions." }] },
    ],
  },
  { kind: "file", name: "AGENTS.md", content: "Project architecture and conventions." },
  { kind: "file", name: "README.md", content: "The Content Architecture (Next.js).\n\nA modern Next.js starter with Sanity CMS integration." },
  { kind: "file", name: "sanity.config.ts", content: "Sanity Studio configuration." },
  { kind: "file", name: "package.json", content: "Pinned toolchain and project scripts." },
];

const ASTRO_REPO: readonly RepoEntry[] = [
  { kind: "folder", name: ".agents", children: [{ kind: "file", name: "README.md", content: "Agent conventions and scoped skills." }] },
  {
    kind: "folder",
    name: "src",
    children: [
      { kind: "folder", name: "pages", children: [{ kind: "file", name: "[...slug].astro", content: "A catch-all page route backed by Sanity." }] },
      { kind: "folder", name: "components", children: [{ kind: "file", name: "page-builder", content: "Composable page builder sections." }] },
      { kind: "folder", name: "features", children: [{ kind: "file", name: "sanity", content: "Sanity schemas, queries and fetch layer." }] },
      { kind: "folder", name: "sanity", children: [{ kind: "file", name: "studio", content: "Sanity Studio integration." }] },
    ],
  },
  { kind: "folder", name: "public", children: [{ kind: "file", name: "robots.txt", content: "Crawler directives." }, { kind: "file", name: "llms.txt", content: "Agent-readable site map." }] },
  { kind: "file", name: "astro.config.mjs", content: "Astro project configuration." },
  { kind: "file", name: "AGENTS.md", content: "Project architecture and conventions." },
  { kind: "file", name: "README.md", content: "The Content Architecture (Astro).\n\nSanity content architecture and page builder." },
  { kind: "file", name: "package.json", content: "Pinned toolchain and project scripts." },
];

export const REPO_TREES: Record<Edition, readonly RepoEntry[]> = {
  next: NEXT_REPO,
  astro: ASTRO_REPO,
};

export const REPO_READMES: Record<Edition, string> = {
  next: [
    "# The Content Architecture (Next.js)",
    "",
    "A modern Next.js starter with Sanity CMS integration: content model, in-app Studio, and product features.",
    "",
    "## Features",
    "",
    "- Next.js App Router with Server Components",
    "- Sanity Studio mounted alongside the site and API routes",
    "- Reusable, self-fetching page builder sections",
    "- Typed schema, fetch layer, preview and revalidation",
    "- Agent Markdown, llms.txt, RSS, OpenAPI and CMS-driven metadata",
    "- Optional Basic Auth, contact forms and spam prevention",
  ].join("\n"),
  astro: [
    "# The Content Architecture (Astro)",
    "",
    "A modern Astro starter with Sanity CMS integration and the same content architecture.",
    "",
    "## Features",
    "",
    "- Astro routes and reusable page builder sections",
    "- Sanity Studio, typed schemas and fetch layer",
    "- Preview, content revalidation and agent-readable Markdown",
    "- SEO metadata, sitemap, RSS and llms.txt",
  ].join("\n"),
};

export const SHOWCASE_INTRO =
  "Real sites, shipped on The Content Architecture. With the plumbing already handled, the effort goes where it shows. The work here has been recognized by";

export const PRICING_BENEFITS = [
  "Unlimited commercial projects",
  "Lifetime updates",
  "Agent-ready: skills, MCP, llms.txt",
  "Direct support from the maintainer",
] as const;

export const EXTERNAL_AWARDS = [
  { label: "Awwwards", href: "https://www.awwwards.com/" },
  { label: "FWA", href: "https://thefwa.com/" },
  { label: "CSSDA", href: "https://www.cssdesignawards.com/" },
] as const;

export const SECONDARY_PAGES = [
  { path: "/blog", title: "Blog" },
  { path: "/roadmap", title: "Roadmap" },
  { path: "/legal/privacy-policy", title: "Privacy policy" },
  { path: "/legal/terms-of-service", title: "Terms of service" },
] as const;
