import { ArrowDown, ArrowUpRight, Menu, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";

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

export function ReferenceNavigation({
  learnMoreOpen,
  onLearnMore,
}: {
  learnMoreOpen: boolean;
  onLearnMore: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [miniMapOpen, setMiniMapOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const [scrollProgress, setScrollProgress] = useState(0);

  useEffect(() => {
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
  }, []);

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
    if (!menuOpen && !miniMapOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        setMiniMapOpen(false);
      }
    };
    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (
        event.target instanceof Element &&
        !event.target.closest(".reference-navigation") &&
        !event.target.closest(".reference-minimap-controls")
      ) {
        setMenuOpen(false);
        setMiniMapOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
    };
  }, [menuOpen, miniMapOpen]);

  const closeMenus = () => {
    setMenuOpen(false);
    setMiniMapOpen(false);
  };

  const sectionLinks = (className: string) =>
    NAV_ITEMS.map(({ id, label }) => (
      <a
        className={`${className} ${activeSection === id ? "is-active" : ""}`}
        href={`#${id}`}
        key={id}
        onClick={closeMenus}
      >
        {label}
      </a>
    ));

  return (
    <>
      <header className="reference-navigation">
        <a className="reference-brand" href={homeHref()} aria-label="Home">
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
              <a className="reference-brand-desktop" href={homeHref()} aria-label="Home">
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
                  href={`#${id}`}
                >
                  {label}
                </a>
              </li>
            ))}
            <li>
              <a href="/blog">Blog</a>
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
            <a className="reference-mobile-link" href="/blog" onClick={closeMenus}>
              Blog
            </a>
          </nav>
        )}
      </header>

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
            aria-expanded={learnMoreOpen}
            onClick={onLearnMore}
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
  );
}
