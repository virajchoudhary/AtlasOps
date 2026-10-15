import { ArrowLeft, ArrowUp, ArrowUpRight } from "lucide-react";
import type { FormEvent, MouseEvent, ReactNode } from "react";
import { useEffect, useState } from "react";
import {
  articleImagesBySourceUrl,
  legalPages,
  publicArticles,
  roadmapLanes,
  type PublicArticle,
  type RoadmapItem,
  type SecondaryBlock,
  type SecondaryTextSegment,
} from "./ReferenceSecondaryContent";
import "./ReferenceSecondaryPages.css";

export type ReferenceSecondaryPagesProps = {
  pathname: string;
  onNavigate: (href: string) => void;
};

const normalizePath = (pathname: string) => {
  const path = pathname.split(/[?#]/, 1)[0] || "/";
  if (path === "/") return path;
  return path.replace(/\/+$/, "");
};

function isLocalPath(href: string) {
  return href.startsWith("/") && !href.startsWith("//");
}

function titleForPath(path: string, article: PublicArticle | undefined) {
  if (article?.slug === "the-content-architecture-four-months-in") {
    return "What Real Projects Changed in a Sanity Starter";
  }
  if (article?.slug === "serve-content-to-ai-agents-llms-txt-markdown-sanity") {
    return "Serve Your Site to AI Agents: llms.txt and Markdown in Sanity";
  }
  if (article?.slug === "stale-sanity-content-nextjs-caching-revalidation") {
    return "Sanity Content Not Updating in Next.js or Astro: The Fix";
  }
  if (path === "/blog" || path === "/blog/articles") {
    return "Content Architecture Blog | Next.js + Sanity Engineering";
  }
  if (path === "/roadmap") return "Roadmap, What's Shipped and What's Next | The Content Architecture";
  if (path === "/legal/privacy-policy") return "Privacy Policy | The Content Architecture";
  if (path === "/legal/terms-of-service") return "Terms of Service & License | The Content Architecture";
  return undefined;
}

function InternalLink({
  href,
  onNavigate,
  children,
  className,
  ariaLabel,
}: {
  href: string;
  onNavigate: (href: string) => void;
  children: ReactNode;
  className?: string;
  ariaLabel?: string;
}) {
  const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!isLocalPath(href) || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    onNavigate(href);
  };

  return (
    <a className={className} href={href} aria-label={ariaLabel} onClick={onClick}>
      {children}
    </a>
  );
}

function TextSegments({ segments, onNavigate }: {
  segments: readonly SecondaryTextSegment[];
  onNavigate: (href: string) => void;
}) {
  return (
    <>
      {segments.map((segment, index) => {
        if (segment.kind === "link") {
          if (isLocalPath(segment.href)) {
            return (
              <InternalLink href={segment.href} onNavigate={onNavigate} key={`${segment.kind}-${index}`}>
                {segment.text}
              </InternalLink>
            );
          }
          return (
            <a href={segment.href} key={`${segment.kind}-${index}`}>
              {segment.text}
            </a>
          );
        }
        if (segment.kind === "code") {
          return <code key={`${segment.kind}-${index}`}>{segment.text}</code>;
        }
        return <span key={`${segment.kind}-${index}`}>{segment.text}</span>;
      })}
    </>
  );
}

function NewsletterPreview({ title, copy, actionLabel }: {
  title: string;
  copy: string;
  actionLabel: string;
}) {
  return (
    <aside className="secondary-newsletter" aria-label={`${title}. Signup is not connected in this local reproduction.`}>
      <p className="secondary-newsletter-title">{title}</p>
      <p className="secondary-newsletter-copy">{copy}</p>
      <div className="secondary-newsletter-preview" aria-hidden="true">
        <span>your@email.com</span>
        <span>{actionLabel}</span>
      </div>
    </aside>
  );
}

function FooterEmailSignup() {
  const [result, setResult] = useState("");

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setResult("This local reproduction does not submit or save email addresses.");
  };

  return (
    <form
      className="reference-email-form secondary-footer-email-form"
      aria-label="Newsletter signup (local preview)"
      onSubmit={onSubmit}
    >
      <label className="reference-sr-only" htmlFor="secondary-footer-email">
        Email
      </label>
      <input
        id="secondary-footer-email"
        type="email"
        name="email"
        autoComplete="email"
        placeholder="you@email.com"
        required
      />
      <button type="submit">
        <span>Stay updated</span>
        <ArrowUpRight size={12} aria-hidden="true" />
      </button>
      <span className="reference-form-status" role="status">{result}</span>
    </form>
  );
}

function SecondaryPattern() {
  const line = "A STARTER IS A SNAPSHOT  ·  EVERY DECISION ALREADY MADE  ·  SKIP TO THE ACTUAL WORK  ·";
  return (
    <div className="secondary-pattern" aria-hidden="true">
      {Array.from({ length: 40 }, (_, index) => (
        <span key={index}>{line}</span>
      ))}
    </div>
  );
}

function SecondaryFooter({ onNavigate }: { onNavigate: (href: string) => void }) {
  return (
    <footer className="reference-footer secondary-footer">
      <div className="reference-footer-main">
        <div className="reference-footer-newsletter">
          <InternalLink
            className="reference-footer-logo"
            href="/reference"
            onNavigate={onNavigate}
            ariaLabel="The Content Architecture home"
          >
            <img
              src="/reference/assets/content-architecture-mark.svg"
              width="36"
              height="36"
              alt=""
            />
          </InternalLink>
          <p>One short email when the repo changes or a discount goes live.</p>
          <FooterEmailSignup />
        </div>
        <nav className="reference-footer-links" aria-label="Footer">
          <div>
            <h3>Resources</h3>
            <InternalLink href="/blog" onNavigate={onNavigate}>
              Blog <ArrowUpRight size={11} aria-hidden="true" />
            </InternalLink>
            <InternalLink href="/roadmap" onNavigate={onNavigate}>
              Roadmap <ArrowUpRight size={11} aria-hidden="true" />
            </InternalLink>
          </div>
          <div>
            <h3>Legal</h3>
            <InternalLink href="/legal/privacy-policy" onNavigate={onNavigate}>
              Privacy policy <ArrowUpRight size={11} aria-hidden="true" />
            </InternalLink>
            <InternalLink href="/legal/terms-of-service" onNavigate={onNavigate}>
              Terms of service <ArrowUpRight size={11} aria-hidden="true" />
            </InternalLink>
          </div>
        </nav>
      </div>
      <div className="reference-footer-bottom">
        <span>© 2026 The Content Architecture</span>
        <a href="https://edoardolunardi.dev/" target="_blank" rel="noreferrer">
          Built by edoardolunardi.dev <ArrowUpRight size={11} aria-hidden="true" />
        </a>
      </div>
    </footer>
  );
}

function PageFrame({ children, className = "", onNavigate }: {
  children: ReactNode;
  className?: string;
  onNavigate: (href: string) => void;
}) {
  return (
    <div className={`reference-secondary-page ${className}`}>
      <SecondaryPattern />
      {children}
      <SecondaryFooter onNavigate={onNavigate} />
    </div>
  );
}

function ArticleCard({ article, index, onNavigate }: {
  article: PublicArticle;
  index: number;
  onNavigate: (href: string) => void;
}) {
  const href = `/blog/${article.slug}`;
  return (
    <InternalLink
      href={href}
      onNavigate={onNavigate}
      className="secondary-article-card"
      ariaLabel={`Entry ${String(index + 1).padStart(3, "0")}: ${article.title}. Read article.`}
    >
      <span className="secondary-article-meta">
        <span>ENTRY {String(index + 1).padStart(3, "0")}</span>
        <span>{article.date} · {article.readTime} READ</span>
      </span>
      <h2>{article.title}</h2>
      <p className="secondary-article-summary">{article.summary}</p>
      <span className="secondary-article-action" aria-hidden="true">
        <span>READ</span>
        <span>ARTICLE <i /></span>
      </span>
    </InternalLink>
  );
}

function BlogIndex({ onNavigate }: { onNavigate: (href: string) => void }) {
  return (
    <PageFrame className="secondary-blog-index" onNavigate={onNavigate}>
      <main className="secondary-container">
        <header className="secondary-index-heading">
          <h1>Knowledge Hub</h1>
          <p>Practical, opinionated writing on content architecture, headless CMS, and shipping real products with Sanity and Next.js.</p>
        </header>
        <section className="secondary-article-list" aria-label="Articles">
          {publicArticles.map((article, index) => (
            <ArticleCard
              article={article}
              index={index}
              onNavigate={onNavigate}
              key={article.slug}
            />
          ))}
        </section>
      </main>
    </PageFrame>
  );
}

function ArticleBlock({ block, onNavigate }: {
  block: SecondaryBlock;
  onNavigate: (href: string) => void;
}) {
  switch (block.type) {
    case "paragraph":
      return <p><TextSegments segments={block.segments} onNavigate={onNavigate} /></p>;
    case "heading":
      if (block.level === 2) {
        return <h2>{block.text}</h2>;
      }
      return <h3>{block.text}</h3>;
    case "code":
      return (
        <pre className="secondary-code-block">
          <code>{block.code}</code>
        </pre>
      );
    case "list":
      return (
        <ul>
          {block.items.map((item, index) => (
            <li key={index}><TextSegments segments={item} onNavigate={onNavigate} /></li>
          ))}
        </ul>
      );
    case "image": {
      const src = articleImagesBySourceUrl.get(block.src) ?? block.src;
      return (
        <figure className="secondary-article-image">
          <img src={src} alt={block.alt} loading="lazy" />
          <figcaption>{block.alt}</figcaption>
        </figure>
      );
    }
    case "callout":
      return <NewsletterPreview title={block.title} copy={block.copy} actionLabel={block.actionLabel} />;
  }
}

function ArticlePage({ article, onNavigate }: {
  article: PublicArticle;
  onNavigate: (href: string) => void;
}) {
  return (
    <PageFrame className="secondary-article-page" onNavigate={onNavigate}>
      <main className="secondary-article-container">
        <InternalLink
          className="secondary-back-link"
          href="/blog"
          onNavigate={onNavigate}
        >
          <ArrowLeft size={14} aria-hidden="true" />
          <span>ALL ARTICLES</span>
        </InternalLink>
        <article>
          <header className="secondary-article-header">
            <h1>{article.title}</h1>
            <p className="secondary-article-dek">{article.summary}</p>
            <dl className="secondary-article-details">
              <div>
                <dt>AUTHOR</dt>
                <dd>EDOARDO LUNARDI</dd>
              </div>
              <div>
                <dt>PUBLISHED</dt>
                <dd>{article.date}</dd>
              </div>
              <div>
                <dt>READING TIME</dt>
                <dd>{article.readTime}</dd>
              </div>
            </dl>
          </header>
          <div className="secondary-article-body">
            {article.blocks.map((block, index) => (
              <ArticleBlock block={block} onNavigate={onNavigate} key={`${block.type}-${index}`} />
            ))}
          </div>
        </article>
      </main>
    </PageFrame>
  );
}

function RoadmapCard({ item, selected, inProgress, onToggleVote }: {
  item: RoadmapItem;
  selected: boolean;
  inProgress: boolean;
  onToggleVote: () => void;
}) {
  return (
    <article className={`secondary-roadmap-card${inProgress ? " secondary-roadmap-card--in-progress" : ""}`}>
      <header>
        <span>{item.number}</span>
        <span>{item.category}</span>
      </header>
      <h3>{item.title}</h3>
      <p>{item.summary}</p>
      <div className="secondary-roadmap-card-footer">
        <button
          className="secondary-roadmap-votes"
          type="button"
          aria-label={`${item.votes} historical votes. Toggle a local vote for ${item.title}.`}
          aria-describedby="secondary-roadmap-vote-note"
          aria-pressed={selected}
          onClick={onToggleVote}
          title="Local-only preview. Selection is not saved; historical totals do not change."
        >
          <ArrowUp size={12} aria-hidden="true" />
          <span>{String(item.votes).padStart(2, "0")}</span>
        </button>
      </div>
    </article>
  );
}

function RoadmapPage({ onNavigate }: { onNavigate: (href: string) => void }) {
  const [localVotePreview, setLocalVotePreview] = useState({
    selected: new Set<string>(),
    status: "",
  });

  const toggleLocalVote = (key: string, item: RoadmapItem) => {
    setLocalVotePreview((current) => {
      const selected = new Set(current.selected);
      const wasSelected = selected.has(key);
      if (wasSelected) {
        selected.delete(key);
      } else {
        selected.add(key);
      }
      return {
        selected,
        status: `Local vote ${wasSelected ? "removed" : "selected"} for ${item.title}. Historical total remains ${item.votes}; nothing was sent or saved.`,
      };
    });
  };

  return (
    <PageFrame className="secondary-roadmap-page" onNavigate={onNavigate}>
      <main className="secondary-container">
        <header className="secondary-roadmap-heading">
          <h1>Roadmap</h1>
          <p>What's shipped, what's in progress, and what's planned next.</p>
        </header>
        <div className="secondary-roadmap-lanes">
          {roadmapLanes.map((lane) => (
            <section className="secondary-roadmap-lane" aria-label={lane.label} key={lane.label}>
              <header className={`secondary-roadmap-lane-heading secondary-roadmap-lane-heading--${lane.label.toLowerCase().replaceAll(" ", "-")}`}>
                <span><i aria-hidden="true" />{lane.label}</span>
                <span>{lane.count}</span>
              </header>
              {lane.items.length ? (
                <div className="secondary-roadmap-cards">
                  {lane.items.map((item) => {
                    const voteKey = `${lane.label}:${item.number}`;
                    return (
                      <RoadmapCard
                        key={voteKey}
                        item={item}
                        selected={localVotePreview.selected.has(voteKey)}
                        inProgress={lane.label === "IN PROGRESS"}
                        onToggleVote={() => toggleLocalVote(voteKey, item)}
                      />
                    );
                  })}
                </div>
              ) : (
                <div className="secondary-roadmap-empty">
                  <span>NOTHING HERE YET</span>
                  <InternalLink href="/reference" onNavigate={onNavigate} className="secondary-roadmap-learn">
                    LEARN MORE <ArrowUpRight size={13} aria-hidden="true" />
                  </InternalLink>
                </div>
              )}
            </section>
          ))}
        </div>
        <p id="secondary-roadmap-vote-note" className="secondary-visually-hidden">
          Vote selections only change this local preview and are not saved or sent. Displayed vote totals are historical.
        </p>
        <p
          className="secondary-visually-hidden"
          role="status"
          aria-label="Roadmap vote feedback"
          aria-live="polite"
          aria-atomic="true"
        >
          {localVotePreview.status}
        </p>
      </main>
    </PageFrame>
  );
}

function LegalPage({ path, onNavigate }: {
  path: string;
  onNavigate: (href: string) => void;
}) {
  const page = legalPages.find((candidate) => candidate.title === (
    path === "/legal/privacy-policy" ? "Privacy Policy" : "Terms of Service"
  ));
  if (!page) return null;

  return (
    <PageFrame className="secondary-legal-page" onNavigate={onNavigate}>
      <main className="secondary-legal-container">
        <header className="secondary-legal-header">
          <h1>{page.title}</h1>
          <p className="secondary-legal-summary">{page.summary}</p>
        </header>
        <article className="secondary-legal-body">
          <p className="secondary-legal-lede">
            <TextSegments segments={page.lede} onNavigate={() => {}} />
          </p>
          {page.blocks.map((block, index) => (
            block.type === "heading" ? (
              <h2 key={`${block.text}-${index}`}>{block.text}</h2>
            ) : (
              <p key={`paragraph-${index}`}>
                <TextSegments segments={block.segments} onNavigate={() => {}} />
              </p>
            )
          ))}
        </article>
      </main>
    </PageFrame>
  );
}

export function ReferenceSecondaryPages({
  pathname,
  onNavigate,
}: ReferenceSecondaryPagesProps) {
  const path = normalizePath(pathname);
  const article = publicArticles.find((candidate) => (
    `/blog/${candidate.slug}` === path
    || `/blog/articles/${candidate.slug}` === path
  ));
  const title = titleForPath(path, article);

  useEffect(() => {
    if (!title) return;
    const previousTitle = document.title;
    document.title = title;
    return () => {
      document.title = previousTitle;
    };
  }, [title]);

  if (path === "/blog" || path === "/blog/articles") {
    return <BlogIndex onNavigate={onNavigate} />;
  }
  if (path === "/roadmap") return <RoadmapPage onNavigate={onNavigate} />;
  if (path === "/legal/privacy-policy" || path === "/legal/terms-of-service") {
    return <LegalPage path={path} onNavigate={onNavigate} />;
  }
  if (article) return <ArticlePage article={article} onNavigate={onNavigate} />;
  return null;
}
