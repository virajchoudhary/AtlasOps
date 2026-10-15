import type { DetailedHTMLProps, HTMLAttributes } from "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "mux-player": DetailedHTMLProps<
        HTMLAttributes<HTMLElement> & {
          "playback-id"?: string;
          "thumbnail-time"?: string;
          autoplay?: string;
          preload?: string;
          muted?: "" | boolean;
          loop?: "" | boolean;
          "disable-cookies"?: "" | boolean;
          "disable-tracking"?: "" | boolean;
        },
        HTMLElement
      >;
    }
  }
}
