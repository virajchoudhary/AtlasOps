import { ArrowUpRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ABOUT_SECTIONS } from "../content";

function FounderVideo() {
  const [mediaState, setMediaState] = useState<"loading" | "ready" | "error">("loading");

  return (
    <div className="reference-about-video">
      <video
        src="/reference/assets/edoardo-founder-video.mp4"
        autoPlay
        controls
        playsInline
        preload="metadata"
        muted
        loop
        aria-label="Edoardo Lunardi profile video"
        onCanPlay={() => setMediaState("ready")}
        onError={() => setMediaState("error")}
      />
      {mediaState === "loading" && (
        <p className="reference-about-video-status" role="status">
          Loading founder video...
        </p>
      )}
      {mediaState === "error" && (
        <p className="reference-about-video-status" role="alert">
          The founder video could not be loaded.
        </p>
      )}
    </div>
  );
}

export function AboutDrawer({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    dialog.showModal();
    closeButtonRef.current?.focus();

    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);

  const close = () => {
    dialogRef.current?.close();
    onClose();
  };

  return (
    <dialog
      ref={dialogRef}
      className="reference-about-dialog"
      aria-labelledby="reference-about-title"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <nav className="reference-about-index" aria-label="About sections">
        {ABOUT_SECTIONS.map((section) => (
          <a href={`#${section.id}`} key={section.id} onClick={() => undefined}>
            <span>{section.number}</span>
            <span>{section.shortTitle}</span>
          </a>
        ))}
      </nav>
      <div className="reference-about-panel">
        <header className="reference-about-header">
          <div>
            <h2 id="reference-about-title">README / The Content Architecture</h2>
            <p>A personal note from the maintainer</p>
          </div>
          <button
            ref={closeButtonRef}
            className="reference-about-close"
            type="button"
            aria-label="Close Learn More"
            onClick={close}
          >
            <span>Close</span>
            <X size={13} aria-hidden="true" />
          </button>
        </header>
        <div className="reference-about-content">
          {ABOUT_SECTIONS.map((section) => (
            <section id={section.id} key={section.id} className="reference-about-section">
              <h3>{section.number} / {section.title}</h3>
              {section.paragraphs.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
              {section.id === "who-am-i" && (
                <>
                  <p className="reference-founder-bio">
                    I am Edo <img
                      src="/reference/assets/edoardo-portrait.png"
                      width="24"
                      height="24"
                      alt="Man in black turtleneck smiling and looking to the side indoors"
                    />{" "}
                    - Creative Web Engineer, nearly a decade in. Sanity Pioneer 2026,
                    Awwwards jury member, recognized across Awwwards, CSSDA, and FWA. I
                    have shipped for Buck, Disney, Porsche, Red Bull, Le Labo Fragrances,
                    Getty. Design sensibility, technical depth, obsessive about detail.
                    Based in Vienna, working worldwide.
                  </p>
                  <FounderVideo />
                  <p>
                    Find me on{" "}
                    <a href="https://www.instagram.com/edo.tsx" target="_blank" rel="noreferrer">
                      Instagram
                    </a>
                    ,{" "}
                    <a href="https://www.linkedin.com/in/edoardolunardi" target="_blank" rel="noreferrer">
                      LinkedIn
                    </a>
                    , and{" "}
                    <a href="https://x.com/edo_lunardi" target="_blank" rel="noreferrer">
                      X
                    </a>
                    . The work lives at{" "}
                    <a href="https://www.edoardolunardi.dev/" target="_blank" rel="noreferrer">
                      edoardolunardi.dev
                    </a>
                    . Write to{" "}
                    <a href="mailto:hello@edoardolunardi.dev">
                      hello@edoardolunardi.dev
                    </a>
                    .
                    <ArrowUpRight size={11} aria-hidden="true" />
                  </p>
                </>
              )}
              {section.id === "why-this-exists" && (
                <p>
                  If you want the reasoning before you buy, I wrote it all down:{" "}
                  <a href="https://www.edoardolunardi.dev/blog/the-content-architecture-cms-structure" target="_blank" rel="noreferrer">CMS structure</a>,{" "}
                  <a href="https://www.edoardolunardi.dev/blog/the-content-architecture-content-models" target="_blank" rel="noreferrer">content models</a>,{" "}
                  <a href="https://www.edoardolunardi.dev/blog/the-content-architecture-page-composition" target="_blank" rel="noreferrer">page composition</a>, and{" "}
                  <a href="https://www.edoardolunardi.dev/blog/the-content-architecture-content-primitives" target="_blank" rel="noreferrer">content primitives</a>.
                  The thinking behind every decision in the codebase, free to read.
                </p>
              )}
            </section>
          ))}
        </div>
      </div>
    </dialog>
  );
}
