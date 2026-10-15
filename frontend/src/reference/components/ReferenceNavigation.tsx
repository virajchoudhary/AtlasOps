import { ArrowDown, ArrowUpRight, Menu, Plus, X } from "lucide-react";
import { useEffect, useRef, useState, type MouseEvent } from "react";

const NAV_ITEMS = [
  { id: "features", label: "Features" },
  { id: "the-repo", label: "The repo" },
  { id: "showcase", label: "Showcase" },
  { id: "pricing", label: "Pricing" },
  { id: "faq", label: "FAQ" },
] as const;

const TICKER_ITEMS = Array.from({ length: 8 }, (_, index) => ({
  id: index,
  text: "NOW AVAILABLE WITH ASTRO",
}));

function AstroTicker({ className = "" }: { className?: string }) {
  return (
    <div className={`reference-ticker ${className}`.trim()} aria-label="Now available with Astro">
      <div className="reference-ticker-track" aria-hidden="true">
        {TICKER_ITEMS.map(({ id, text }) => (
          <span key={id}>{text}</span>
        ))}
      </div>
    </div>
  );
}

function homeHref() {
  return window.location.pathname.endsWith("/reference.html")
    ? "/reference.html"
    : "/reference";
}

type ReferenceNavigationProps =
  | {
      mode?: "home";
      learnMoreOpen: boolean;
      onLearnMore: () => void;
    }
  | {
      mode: "secondary";
      pathname: string;
      onNavigate: (href: string) => void;
    };

export function ReferenceNavigation(props: ReferenceNavigationProps) {
  const isSecondaryPage = props.mode === "secondary";
  const secondaryPage = isSecondaryPage ? props : undefined;
  const homePage = isSecondaryPage ? undefined : props;
  const [menuOpen, setMenuOpen] = useState(false);
  const [miniMapOpen, setMiniMapOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const [scrollProgress, setScrollProgress] = useState(0);
  const menuTriggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (isSecondaryPage) return;
    const sections = NAV_ITEMS.map(({ id }) => document.getElementById(id)).filter(
      (section): section is HTMLElement => section !== null,
    );
    if (!("IntersectionObserver" in window)) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
        const section = visible[0]?.target;
        if (section instanceof HTMLElement) setActiveSection(section.id);
      },
      { rootMargin: "-18% 0px -65% 0px", threshold: 0 },
    );
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, [isSecondaryPage]);

  useEffect(() => {
    let animationFrame = 0;
    const updateProgress = () => {
      cancelAnimationFrame(animationFrame);
      animationFrame = requestAnimationFrame(() => {
        const range = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
        setScrollProgress(Math.min(1, Math.max(0, window.scrollY / range)));
      });
    };
    updateProgress();
    window.addEventListener("scroll", updateProgress, { passive: true });
    window.addEventListener("resize", updateProgress);
    return () => {
      cancelAnimationFrame(animationFrame);
      window.removeEventListener("scroll", updateProgress);
      window.removeEventListener("resize", updateProgress);
    };
  }, []);

  useEffect(() => {
    if (!menuOpen && (!miniMapOpen || isSecondaryPage)) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (menuOpen) menuTriggerRef.current?.focus();
        setMenuOpen(false);
        if (!isSecondaryPage) setMiniMapOpen(false);
      }
    };
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (
        event.target instanceof Element &&
        !event.target.closest(".reference-navigation") &&
        (isSecondaryPage || !event.target.closest(".reference-minimap-controls"))
      ) {
        setMenuOpen(false);
        if (!isSecondaryPage) setMiniMapOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
    };
  }, [menuOpen, miniMapOpen, isSecondaryPage]);

  const closeMenus = () => {
    setMenuOpen(false);
    setMiniMapOpen(false);
  };

  const followLink = (event: MouseEvent<HTMLAnchorElement>, href: string) => {
    closeMenus();
    if (
      !secondaryPage ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return;
    }
    event.preventDefault();
    secondaryPage.onNavigate(href);
    const hash = new URL(href, window.location.href).hash.slice(1);
    if (!hash) return;
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        document.getElementById(decodeURIComponent(hash))?.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        });
      });
    });
  };

  const sectionLinks = (className: string) =>
    NAV_ITEMS.map(({ id, label }) => (
      <a
        className={`${className} ${activeSection === id ? "is-active" : ""}`}
        href={secondaryPage ? `/reference#${id}` : `#${id}`}
        key={id}
        onClick={(event) => followLink(event, event.currentTarget.href)}
      >
        {label}
      </a>
    ));
  const activeBlog = secondaryPage !== undefined && (
    secondaryPage.pathname === "/blog" || secondaryPage.pathname.startsWith("/blog/")
  );
  const homeLink = secondaryPage ? "/reference" : homeHref();

  return (
    <>
      <header className="reference-navigation">
        <a
          className="reference-brand"
          href={homeLink}
          aria-label="Home"
          onClick={(event) => followLink(event, homeLink)}
        >
          <img
            src="/reference/assets/content-architecture-symbol.svg"
            width="30"
            height="30"
            alt=""
          />
        </a>
        <button
          className="reference-menu-trigger"
          type="button"
          ref={menuTriggerRef}
          aria-expanded={menuOpen}
          aria-controls="reference-menu"
          onClick={() => setMenuOpen((open) => !open)}
        >
          <Menu size={14} aria-hidden="true" />
          <span>Menu</span>
          {menuOpen ? <X size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
        </button>
        <nav className="reference-desktop-nav" aria-label="Primary navigation">
          <ul>
            <li>
              <a
                className="reference-brand-desktop"
                href={homeLink}
                aria-label="Home"
                onClick={(event) => followLink(event, homeLink)}
              >
                <img
                  src="/reference/assets/content-architecture-symbol.svg"
                  width="30"
                  height="30"
                  alt=""
                />
              </a>
            </li>
            {NAV_ITEMS.map(({ id, label }) => (
              <li key={id}>
                <a
                  className={activeSection === id ? "is-active" : ""}
                  href={secondaryPage ? `/reference#${id}` : `#${id}`}
                  onClick={(event) => followLink(event, event.currentTarget.href)}
                >
                  {label}
                </a>
              </li>
            ))}
            <li>
              <a
                className={activeBlog ? "is-active" : ""}
                href="/blog"
                aria-current={activeBlog ? "page" : undefined}
                onClick={(event) => followLink(event, "/blog")}
              >
                Blog
              </a>
            </li>
          </ul>
          <AstroTicker />
        </nav>
        <AstroTicker className="reference-mobile-ticker" />
        {menuOpen && (
          <nav
            className="reference-mobile-menu"
            id="reference-menu"
            aria-label="Page navigation"
          >
            {sectionLinks("reference-mobile-link")}
            <a
              className={`reference-mobile-link${activeBlog ? " is-active" : ""}`}
              href="/blog"
              aria-current={activeBlog ? "page" : undefined}
              onClick={(event) => followLink(event, "/blog")}
            >
              Blog
            </a>
          </nav>
        )}
      </header>

      {homePage && (
        <>
          <aside className="reference-minimap" aria-label="Page overview">
            <div className="reference-minimap-map" aria-hidden="true">
              <i className="reference-minimap-block reference-minimap-block--hero" />
              <i className="reference-minimap-block reference-minimap-block--cost" />
              <i className="reference-minimap-block reference-minimap-block--features" />
              <i className="reference-minimap-block reference-minimap-block--repo" />
              <i className="reference-minimap-block reference-minimap-block--showcase" />
              <i className="reference-minimap-block reference-minimap-block--footer" />
              <i
                className="reference-minimap-window"
                style={{ top: `${scrollProgress * 82}%` }}
              />
            </div>
            <div className="reference-minimap-controls">
              <button
                className="reference-learn-more"
                type="button"
                aria-haspopup="dialog"
                aria-expanded={homePage.learnMoreOpen}
                onClick={homePage.onLearnMore}
              >
                Learn more <ArrowUpRight size={10} aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-label={miniMapOpen ? "Close page overview" : "Open page overview"}
                aria-expanded={miniMapOpen}
                onClick={() => setMiniMapOpen((open) => !open)}
              >
                {miniMapOpen ? <X size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
              </button>
            </div>
            {miniMapOpen && (
              <nav className="reference-minimap-menu" aria-label="Page overview links">
                {sectionLinks("reference-minimap-link")}
              </nav>
            )}
          </aside>

          <a className="reference-scroll-cue" href="#time-cost">
            <ArrowDown size={12} aria-hidden="true" />
            <span>Scroll to dive in</span>
          </a>
        </>
      )}
    </>
  );
}
