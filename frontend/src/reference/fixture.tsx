import React from "react";
import ReactDOM from "react-dom/client";
import { ReferencePage } from "./ReferencePage";
import { RingCanvas } from "./RingCanvas";
import "./reference.css";
import "../styles.css";

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Missing reference fixture root");

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <ReferencePage
      heroArtwork={
        <RingCanvas
          text="THE CONTENT ARCHITECTURE"
          ariaLabel="Rotating Content Architecture typography"
        />
      }
    />
  </React.StrictMode>,
);
