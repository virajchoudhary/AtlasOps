import { X } from "lucide-react";
import { useEffect, useRef } from "react";

const REEL_PLAYBACK_ID = "DK00HHX701aluFidRzbGHE5uEqyhWfB00Gsqv02JfSzYQL4";

export function ReelDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (!customElements.get("mux-player")) {
      const existingScript = document.querySelector<HTMLScriptElement>(
        "script[data-reference-mux-player]",
      );
      if (!existingScript) {
        const script = document.createElement("script");
        script.src = "https://cdn.jsdelivr.net/npm/@mux/mux-player@3.13.3/dist/mux-player.js";
        script.type = "module";
        script.async = true;
        script.dataset.referenceMuxPlayer = "true";
        document.head.append(script);
      }
    }

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
      className="reference-reel-dialog"
      aria-label="Watch reel"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <mux-player
        className="reference-reel-player"
        playback-id={REEL_PLAYBACK_ID}
        thumbnail-time="1"
        preload="auto"
        autoplay="any"
        disable-cookies=""
        disable-tracking=""
        aria-label="The Content Architecture product reel"
      />
      <button
        ref={closeButtonRef}
        className="reference-reel-close"
        type="button"
        onClick={close}
      >
        <span>Close</span>
        <X size={12} aria-hidden="true" />
      </button>
    </dialog>
  );
}
