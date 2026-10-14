import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { RingCanvas } from "../RingCanvas";

const rootElement = document.querySelector<HTMLDivElement>("#root");
if (!rootElement) throw new Error("RingCanvas fixture root is missing.");

const params = new URLSearchParams(window.location.search);
const isOffscreenMode = params.has("offscreen");

function Fixture() {
  const [ringText, setRingText] = useState("The Content Architecture");
  const [ringsVisible, setRingsVisible] = useState(true);

  if (isOffscreenMode) {
    return (
      <main className="fixture fixture--offscreen">
        <section className="fixture-copy">
          <h1>Content Architecture rings</h1>
        </section>
        <div className="fixture-offscreen-space" />
        <section className="fixture-offscreen fixture-field" aria-label="Offscreen ring field">
          {ringsVisible && <RingCanvas text={ringText} ariaLabel="Rotating typographic rings" />}
        </section>
      </main>
    );
  }

  return (
    <main className="fixture">
      <section className="fixture-copy">
        <h1>The stack agents don't reinvent.</h1>
        <div className="fixture-controls">
          <button
            type="button"
            onClick={() => setRingText((text) =>
              text === "The Content Architecture" ? "Operationally verified" : "The Content Architecture")}
          >
            Change ring text
          </button>
          <button
            type="button"
            aria-pressed={ringsVisible}
            onClick={() => setRingsVisible((visible) => !visible)}
          >
            Toggle rings
          </button>
        </div>
      </section>
      <section className="fixture-field" aria-label="Typographic rings">
        {ringsVisible && <RingCanvas text={ringText} ariaLabel="Thirty rotating typographic rings" />}
      </section>
    </main>
  );
}

createRoot(rootElement).render(<StrictMode><Fixture /></StrictMode>);
