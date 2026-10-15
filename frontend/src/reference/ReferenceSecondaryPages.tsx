import { ArrowLeft, ArrowUp, ArrowUpRight } from "lucide-react";
import type { MouseEvent, ReactNode } from "react";
import { useEffect } from "react";
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

function PageFrame({ children, className = "" }: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`reference-secondary-page ${className}`}>
      <SecondaryPattern />
      {children}
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
    <PageFrame className="secondary-blog-index">
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
    <PageFrame className="secondary-article-page">
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

function RoadmapCard({ item }: {
  item: RoadmapItem;
}) {
  return (
    <article className="secondary-roadmap-card">
      <header>
        <span>{item.number}</span>
        <span>{item.category}</span>
      </header>
      <h3>{item.title}</h3>
      <p>{item.summary}</p>
      <button
        className="secondary-roadmap-votes"
        type="button"
        disabled
        aria-label={`${item.votes} votes. Voting is not available in this local reproduction.`}
        title="Voting is not connected in this local reproduction."
      >
        <ArrowUp size={12} aria-hidden="true" />
        <span>{String(item.votes).padStart(2, "0")}</span>
      </button>
    </article>
  );
}

function RoadmapPage({ onNavigate }: { onNavigate: (href: string) => void }) {
  return (
    <PageFrame className="secondary-roadmap-page">
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
                  {lane.items.map((item) => <RoadmapCard key={item.number} item={item} />)}
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
      </main>
    </PageFrame>
  );
}

function LegalPage({ path }: { path: string }) {
  const page = legalPages.find((candidate) => candidate.title === (
    path === "/legal/privacy-policy" ? "Privacy Policy" : "Terms of Service"
  ));
  if (!page) return null;

  return (
    <PageFrame className="secondary-legal-page">
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
    return <LegalPage path={path} />;
  }
  if (article) return <ArticlePage article={article} onNavigate={onNavigate} />;
  return null;
}
