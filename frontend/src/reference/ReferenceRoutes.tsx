import { useEffect } from "react";
import { ReferencePage } from "./ReferencePage";
import { ReferenceSecondaryPages } from "./ReferenceSecondaryPages";
import { RingCanvas } from "./RingCanvas";
import "./reference.css";

export function ReferenceRoutes({
  pathname,
  onNavigate,
}: {
  pathname: string;
  onNavigate: (href: string) => void;
}) {
  useEffect(() => {
    if (pathname !== "/reference") return;
    const previousTitle = document.title;
    document.title = "The Content Architecture | Reference Reconstruction";
    return () => {
      document.title = previousTitle;
    };
  }, [pathname]);

  if (pathname === "/reference") {
    return (
      <ReferencePage
        heroArtwork={
          <RingCanvas
            text="THE CONTENT ARCHITECTURE"
            ariaLabel="Rotating Content Architecture typography"
          />
        }
      />
    );
  }

  return <ReferenceSecondaryPages pathname={pathname} onNavigate={onNavigate} />;
}
