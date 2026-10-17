import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export function ReelDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [mediaState, setMediaState] = useState<"loading" | "ready" | "error">("loading");

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
      className="reference-reel-dialog"
      aria-label="Watch reel"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <video
        className="reference-reel-player"
        src="/reference/assets/content-architecture-reel.mp4"
        autoPlay
        controls
        playsInline
        preload="auto"
        aria-label="The Content Architecture product reel"
        onCanPlay={() => setMediaState("ready")}
        onError={() => setMediaState("error")}
      />
      {mediaState === "loading" && (
        <p className="reference-reel-status" role="status">
          Loading reel...
        </p>
      )}
      {mediaState === "error" && (
        <p className="reference-reel-status" role="alert">
          The reel could not be loaded.
        </p>
      )}
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
