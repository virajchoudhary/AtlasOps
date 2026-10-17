import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

function isNotAllowedError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "NotAllowedError";
}

export function ReelDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [mediaState, setMediaState] = useState<"loading" | "ready" | "error">("loading");
  const [transport, setTransport] = useState<"playing" | "paused" | "blocked">("paused");

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    let effectIsCurrent = true;
    dialog.showModal();
    closeButtonRef.current?.focus();
    const video = videoRef.current;
    void video?.play().catch((error: unknown) => {
      if (effectIsCurrent && isNotAllowedError(error)) setTransport("blocked");
    });
    return () => {
      effectIsCurrent = false;
      video?.pause();
      if (dialog.open) dialog.close();
    };
  }, []);

  const close = () => {
    videoRef.current?.pause();
    dialogRef.current?.close();
    onClose();
  };

  const togglePlayback = (video: HTMLVideoElement) => {
    if (video.paused) {
      void video.play().catch((error: unknown) => {
        if (isNotAllowedError(error)) setTransport("blocked");
      });
    } else {
      video.pause();
    }
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
        ref={videoRef}
        className="reference-reel-player"
        src="/reference/assets/content-architecture-reel.mp4"
        autoPlay
        controls={transport === "blocked"}
        playsInline
        preload="auto"
        role={transport === "blocked" ? undefined : "button"}
        tabIndex={0}
        aria-label="The Content Architecture product reel"
        aria-pressed={transport === "blocked" ? undefined : transport === "playing"}
        onClick={(event) => {
          if (transport !== "blocked") togglePlayback(event.currentTarget);
        }}
        onKeyDown={(event) => {
          if (transport !== "blocked" && (event.key === " " || event.key === "Enter")) {
            event.preventDefault();
            togglePlayback(event.currentTarget);
          }
        }}
        onPlay={() => setTransport("playing")}
        onPause={() => setTransport((current) => current === "blocked" ? current : "paused")}
        onEnded={() => setTransport("paused")}
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
