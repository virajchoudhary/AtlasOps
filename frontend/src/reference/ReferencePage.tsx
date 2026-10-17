import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Mail,
  Minus,
  Plus,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  COST_DAYS,
  EXTERNAL_AWARDS,
  FAQ_ITEMS,
  FEATURE_CARDS,
  PRICING_BENEFITS,
  SHOWCASE_INTRO,
  SHOWCASE_PROJECTS,
  TESTIMONIALS,
  type Edition,
  type FAQItem,
} from "./content";
import { AsciiPreview } from "./components/AsciiPreview";
import { ReferenceFooterOrbit } from "./components/ReferenceFooterOrbit";
import { ReferenceNavigation } from "./components/ReferenceNavigation";
import { Reveal } from "./components/Reveal";
import { AboutDrawer } from "./components/AboutDrawer";
import { ReelDialog } from "./components/ReelDialog";
import { RepoExplorer } from "./components/RepoExplorer";
import "./reference.css";

function parseEdition(value: string): Edition | null {
  switch (value) {
    case "next":
    case "astro":
      return value;
    default:
      return null;
  }
}

function referenceHomeHref() {
  return window.location.pathname.endsWith("/reference.html")
    ? "/reference.html"
    : "/reference";
}

function AccessLink({
  edition = "next",
  label = "Get access",
  light = false,
  toPricing = false,
}: {
  edition?: Edition;
  label?: string;
  light?: boolean;
  toPricing?: boolean;
}) {
  return (
    <a
      className={`reference-access-link ${light ? "is-light" : ""}`}
      href={toPricing ? "#pricing" : `https://www.contentarchitecture.dev/checkout?plan=${edition}`}
      target={toPricing ? undefined : "_blank"}
      rel={toPricing ? undefined : "noreferrer"}
    >
      <span>{label}</span>
      <ArrowUpRight size={12} aria-hidden="true" />
    </a>
  );
}

function EmailSignup({ compact = false }: { compact?: boolean }) {
  const [result, setResult] = useState("");

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setResult("This local reproduction does not submit email addresses.");
  };

  return (
    <form
      className={`reference-email-form ${compact ? "is-compact" : ""}`}
      onSubmit={onSubmit}
    >
      <label className="reference-sr-only" htmlFor={compact ? "reference-footer-email" : "reference-dialog-email"}>
        Email
      </label>
      <input
        id={compact ? "reference-footer-email" : "reference-dialog-email"}
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
      <span className="reference-form-status" role="status">
        {result}
      </span>
    </form>
  );
}

function NewsletterDialog({ onClose }: { onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="reference-dialog-layer">
      <section
        aria-labelledby="reference-dialog-title"
        aria-modal="true"
        className="reference-newsletter-dialog"
        role="dialog"
      >
        <div className="reference-dialog-heading">
        <h2 id="reference-dialog-title">Not buying today? Stay close.</h2>
          <button
            className="reference-icon-button"
            type="button"
            ref={closeRef}
            aria-label="Close newsletter dialog"
            onClick={onClose}
          >
            <X size={15} aria-hidden="true" />
          </button>
        </div>
        <p>One short email when the repo changes or a discount goes live. Nothing else, unsubscribe anytime.</p>
        <EmailSignup compact />
      </section>
    </div>
  );
}

function TimeCostSection() {
  return (
    <section className="reference-cost-section" id="time-cost">
      <div className="reference-cost-story">
        <div className="reference-cost-copy">
          <Reveal>
            <h2>The page builder alone costs you days. Every single time.</h2>
            <p className="reference-cost-intro">
              This is the part nobody quotes for and everybody rebuilds. Days gone
              before the real work starts.
            </p>
            <p className="reference-cost-total">
              <span>~24</span>
            </p>
            <div className="reference-cost-legend">
              <span>Hours lost per project</span>
              <span>3 full days</span>
            </div>
          </Reveal>
        </div>
        <div className="reference-cost-table">
          <div className="reference-cost-table-heading">
            <span>Estimate</span>
            <span>Never quoted</span>
          </div>
          {COST_DAYS.map((day) => (
            <div className="reference-cost-day" key={day.day}>
              <div className="reference-cost-day-heading">
                <span>{day.day}</span>
                <span>{day.total}</span>
              </div>
              {day.tasks.map((task) => (
                <div className="reference-cost-task" key={task.label}>
                  <span>{task.label}</span>
                  <i aria-hidden="true" />
                  <span>{task.hours}</span>
                </div>
              ))}
            </div>
          ))}
          <div className="reference-cost-day reference-cost-infinity">
            <div className="reference-cost-day-heading">
              <span>Every prompt</span>
              <span>∞</span>
            </div>
            <div className="reference-cost-task">
              <span>Agent redesigns the architecture</span>
              <i aria-hidden="true" />
              <span>again</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function FeatureSection() {
  return (
    <section className="reference-features-section" id="features">
      <div className="reference-glyph-backdrop" aria-hidden="true">
        {Array.from({ length: 12 }, (_, row) => (
          <p key={row}>
            {Array.from({ length: 3 }, (_, repeat) => (
              <span key={repeat}>THE CONTENT ARCHITECTURE · </span>
            ))}
          </p>
        ))}
      </div>
      <div className="reference-feature-intro">
        <Reveal>
          <h2>Every decision already made. So you can skip to the actual work.</h2>
          <p>
            The production foundation under my client work: hundreds of decisions,
            schema, fetching, structure, SEO, deployment, made once over six years
            and committed. Clone it, rename it, ship. Committed decisions are also
            why agents work here: inside them an agent ships, without them it
            redesigns.
          </p>
        </Reveal>
      </div>
      <div className="reference-feature-grid">
        {FEATURE_CARDS.map((feature, index) => (
          <Reveal
            className={`reference-feature-card reference-feature-card--${index % 3}`}
            key={feature.title}
          >
            <h3>{feature.title}</h3>
            <p>{feature.description}</p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

function ShowcaseSection({
  slide,
  onSlideChange,
}: {
  slide: number;
  onSlideChange: (delta: number) => void;
}) {
  const testimonial = TESTIMONIALS[slide];

  return (
    <section className="reference-showcase-section" id="showcase">
      <div className="reference-showcase-intro">
        <Reveal>
          <h2>The work that gets remembered.</h2>
          <p>
            {SHOWCASE_INTRO}{" "}
            {EXTERNAL_AWARDS.map((award, index) => (
              <span key={award.label}>
                {index > 0 ? (index === 2 ? ", and " : ", ") : ""}
                <a href={award.href} target="_blank" rel="noreferrer">
                  {award.label}
                </a>
              </span>
            ))}
            , and picked up across design directories.
          </p>
        </Reveal>
      </div>
      <div className="reference-showcase-grid">
        {SHOWCASE_PROJECTS.map((project, index) => (
          <Reveal
            className={`reference-project reference-project--${index + 1}`}
            key={project.name}
          >
            <a
              href={project.href}
              target="_blank"
              rel="noreferrer"
              aria-label={`${project.name} ${project.year}. ${project.credits}`}
            >
              <AsciiPreview src={project.image} alt={project.alt} />
              <div className="reference-project-meta">
                <h3>{project.name}</h3>
                <p>{project.credits}</p>
                <span>{project.awards ?? project.year}</span>
              </div>
            </a>
          </Reveal>
        ))}
      </div>
      <div className="reference-testimonial">
        <div className="reference-testimonial-quote">
          <p className="reference-eyebrow">From the people who use it</p>
          <blockquote>“{testimonial.quote}”</blockquote>
          <div className="reference-testimonial-author">
            <img src={testimonial.avatar} alt="" width="40" height="40" loading="lazy" />
            <span>
              <strong>{testimonial.name}</strong>
              <small>{testimonial.role}</small>
            </span>
          </div>
        </div>
        <div className="reference-testimonial-controls">
          <button
            type="button"
            aria-label="Previous testimonial"
            onClick={() => onSlideChange(-1)}
            disabled={slide === 0}
          >
            <ArrowLeft size={16} aria-hidden="true" />
          </button>
          <span aria-live="polite">
            {String(slide + 1).padStart(2, "0")} / {String(TESTIMONIALS.length).padStart(2, "0")}
          </span>
          <button
            type="button"
            aria-label="Next testimonial"
            onClick={() => onSlideChange(1)}
            disabled={slide === TESTIMONIALS.length - 1}
          >
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </section>
  );
}

function PricingSection({
  edition,
  onEditionChange,
}: {
  edition: Edition;
  onEditionChange: (edition: Edition) => void;
}) {
  return (
    <section className="reference-pricing-section" id="pricing">
      <div className="reference-pricing-heading">
        <Reveal>
          <h2>
            <span>Two editions.</span>
            <span>One architecture.</span>
            <span>Lifetime updates.</span>
          </h2>
        </Reveal>
        <div className="reference-early-adopters" aria-label="Trusted by 50 plus engineers">
          <div className="reference-avatar-stack">
            {[
              ["avatar-good-fella.png", "Good Fella Studio"],
              ["avatar-elliott.png", "Elliott Mangham"],
              ["avatar-studioboldest.png", "Studio Boldest"],
              ["avatar-malik.png", "Malik Kotb"],
              ["avatar-minh.jpg", "Minh Chanh"],
            ].map(([src, name]) => (
              <img
                key={src}
                src={`/reference/assets/${src}`}
                alt=""
                width="32"
                height="32"
                title={name}
                loading="lazy"
              />
            ))}
          </div>
          <span>Trusted by 50+ engineers</span>
        </div>
      </div>
      <div className="reference-pricing-ticket">
        <div className="reference-pricing-facts">
          <p>One-time, per edition</p>
          <strong>
            <span>€</span>549
          </strong>
        </div>
        <div className="reference-pricing-details">
          <ol className="reference-pricing-benefits">
            <li className="reference-pricing-benefit--edition">
              <span className="reference-benefit-index">01</span>
              <span className="reference-benefit-text">
                {edition === "next" ? "Next.js 16 + Sanity v6 repo" : "Astro 7 + Sanity v6 repo"}
              </span>
            </li>
            {PRICING_BENEFITS.map((benefit, index) => (
              <li key={benefit}>
                <span className="reference-benefit-index">{String(index + 2).padStart(2, "0")}</span>
                <span className="reference-benefit-text">{benefit}</span>
              </li>
            ))}
          </ol>
          <div className="reference-pricing-buy">
            <fieldset className="reference-edition-picker">
              <legend>Edition</legend>
              <label>
                <input
                  type="radio"
                  name="reference-edition"
                  value="next"
                  checked={edition === "next"}
                  onChange={() => onEditionChange("next")}
                />
                <span>Next.js</span>
              </label>
              <label>
                <input
                  type="radio"
                  name="reference-edition"
                  value="astro"
                  checked={edition === "astro"}
                  onChange={() => onEditionChange("astro")}
                />
                <span>Astro</span>
              </label>
            </fieldset>
            <AccessLink edition={edition} label="Get access" light />
          </div>
        </div>
      </div>
      <p className="reference-pricing-fit">
        For {edition === "next" ? "Next.js" : "Astro"} + Sanity engineers, not no-code.
      </p>
      <p className="reference-sales-note">
        Sales are final. Student, or already own one edition?{" "}
        <a href="mailto:hello@edoardolunardi.dev">Email me</a> for a code.
      </p>
    </section>
  );
}

function FAQSection({
  edition,
  onEditionChange,
}: {
  edition: Edition;
  onEditionChange: (edition: Edition) => void;
}) {
  const [expandedQuestion, setExpandedQuestion] = useState(0);

  return (
    <section className="reference-faq-section" id="faq">
      <div className="reference-faq-heading">
        <Reveal>
          <h2>Before you buy</h2>
        </Reveal>
        <AccessLink edition={edition} label="Get access" />
      </div>
      <div className="reference-faq-list">
        {FAQ_ITEMS.map((item, index) => (
          <FAQRow
            index={index}
            item={item}
            expanded={expandedQuestion === index}
            key={item.question}
            onToggle={() => setExpandedQuestion((open) => (open === index ? -1 : index))}
          />
        ))}
      </div>
      <div className="reference-faq-edition">
        <label htmlFor="reference-faq-edition">Edition</label>
        <select
          id="reference-faq-edition"
          value={edition}
          onChange={(event) => {
            const selected = parseEdition(event.currentTarget.value);
            if (selected) onEditionChange(selected);
          }}
        >
          <option value="next">Next.js</option>
          <option value="astro">Astro</option>
        </select>
      </div>
    </section>
  );
}

function FAQRow({
  index,
  item,
  expanded,
  onToggle,
}: {
  index: number;
  item: FAQItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  const id = `reference-faq-answer-${index + 1}`;
  return (
    <article className={`reference-faq-item ${expanded ? "is-expanded" : ""}`}>
      <button
        type="button"
        className="reference-faq-trigger"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={onToggle}
      >
        <span>
          <small>Q.{String(index + 1).padStart(3, "0")}</small>
          {item.question}
        </span>
        <span className="reference-faq-icon" aria-hidden="true">
          {expanded ? <Minus size={14} /> : <Plus size={14} />}
        </span>
      </button>
      {expanded && (
        <div className="reference-faq-answer" id={id} role="region">
          {item.answer.split("\n\n").map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </div>
      )}
    </article>
  );
}

function FooterSection({
  edition,
  onNewsletterOpen,
}: {
  edition: Edition;
  onNewsletterOpen: () => void;
}) {
  return (
    <footer className="reference-footer">
      <div className="reference-footer-cta">
        <ReferenceFooterOrbit />
        <div className="reference-footer-cta-disc">
          <Reveal>
            <h2>
              The next 3 days
              <br />
              are yours.
            </h2>
            <AccessLink edition={edition} />
          </Reveal>
        </div>
      </div>
      <div className="reference-footer-main">
        <div className="reference-footer-newsletter">
          <a className="reference-footer-logo" href={referenceHomeHref()}>
            <img
              src="/reference/assets/content-architecture-mark.svg"
              width="36"
              height="36"
              alt="The Content Architecture"
            />
          </a>
          <p>One short email when the repo changes or a discount goes live.</p>
          <EmailSignup />
          <button
            className="reference-footer-newsletter-open"
            type="button"
            onClick={onNewsletterOpen}
          >
            Open newsletter dialog <Mail size={13} aria-hidden="true" />
          </button>
        </div>
        <div className="reference-footer-links">
          <div>
            <h3>Resources</h3>
            <a href="/blog">
              Blog <ArrowUpRight size={11} aria-hidden="true" />
            </a>
            <a href="/roadmap">
              Roadmap <ArrowUpRight size={11} aria-hidden="true" />
            </a>
          </div>
          <div>
            <h3>Legal</h3>
            <a
              href="/legal/privacy-policy"
            >
              Privacy policy <ArrowUpRight size={11} aria-hidden="true" />
            </a>
            <a
              href="/legal/terms-of-service"
            >
              Terms of service <ArrowUpRight size={11} aria-hidden="true" />
            </a>
          </div>
        </div>
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

export function ReferencePage({
  heroArtwork,
}: {
  heroArtwork: ReactNode;
}) {
  const [edition, setEdition] = useState<Edition>("next");
  const [testimonialSlide, setTestimonialSlide] = useState(0);
  const [newsletterOpen, setNewsletterOpen] = useState(false);
  const [reelOpen, setReelOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);

  useEffect(() => {
    document.body.classList.add("content-architecture-reference-body");
    return () => document.body.classList.remove("content-architecture-reference-body");
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    try {
      if (window.sessionStorage.getItem("content-architecture-newsletter-dismissed")) return;
    } catch {
      // Private browsing can disable session storage; the dialog remains available manually.
    }

    timer = window.setTimeout(() => setNewsletterOpen(true), 12000);
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  const closeNewsletter = () => {
    setNewsletterOpen(false);
    try {
      window.sessionStorage.setItem("content-architecture-newsletter-dismissed", "1");
    } catch {
      return;
    }
  };

  const changeTestimonial = (delta: number) => {
    setTestimonialSlide((current) =>
      Math.max(0, Math.min(TESTIMONIALS.length - 1, current + delta)),
    );
  };

  return (
    <div className="content-architecture-reference">
      <a className="reference-skip-link" href="#reference-main">
        Skip to content
      </a>
      <ReferenceNavigation
        learnMoreOpen={aboutOpen}
        onLearnMore={() => setAboutOpen(true)}
      />
      <main id="reference-main">
        <div className="reference-dive-track">
          <section className="reference-hero" aria-labelledby="reference-title">
            <div className="reference-hero-copy">
              <div>
                <p className="reference-eyebrow">Full-stack kit for Next.js and Astro.</p>
                <h1 id="reference-title">
                  The stack agents
                  <br className="reference-hero-heading-break" /> don&apos;t reinvent.
                </h1>
                <p className="reference-hero-description">
                  Frontend, Sanity schema, fetch layer, SEO, redirects, forms: six years of
                  decisions, committed. AGENTS.md and a dozen skills load them before your
                  first prompt, and two MCP servers let the agent check its own work.
                </p>
                <div className="reference-hero-actions">
                  <AccessLink toPricing />
                  <button
                    className="reference-watch-button"
                    type="button"
                    aria-haspopup="dialog"
                    onClick={() => setReelOpen(true)}
                  >
                    Watch reel <ArrowUpRight size={12} aria-hidden="true" />
                  </button>
                </div>
              </div>
            </div>
            <div className="reference-hero-artwork" aria-label="Rotating typographic ring artwork">
              {heroArtwork}
            </div>
          </section>
        </div>

        <TimeCostSection />
        <FeatureSection />
        <RepoExplorer />
        <ShowcaseSection
          slide={testimonialSlide}
          onSlideChange={changeTestimonial}
        />
        <PricingSection edition={edition} onEditionChange={setEdition} />
        <FAQSection edition={edition} onEditionChange={setEdition} />
        <FooterSection edition={edition} onNewsletterOpen={() => setNewsletterOpen(true)} />
      </main>
      {newsletterOpen && <NewsletterDialog onClose={closeNewsletter} />}
      {reelOpen && <ReelDialog onClose={() => setReelOpen(false)} />}
      {aboutOpen && <AboutDrawer onClose={() => setAboutOpen(false)} />}
    </div>
  );
}
