import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AboutDrawer } from "./AboutDrawer";
import { ReelDialog } from "./ReelDialog";

const dialogPrototype = HTMLDialogElement.prototype;
let originalShowModal: PropertyDescriptor | undefined;
let originalClose: PropertyDescriptor | undefined;

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  originalShowModal = Object.getOwnPropertyDescriptor(dialogPrototype, "showModal");
  originalClose = Object.getOwnPropertyDescriptor(dialogPrototype, "close");
  Object.defineProperty(dialogPrototype, "showModal", {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(dialogPrototype, "close", {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.removeAttribute("open");
    },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  if (originalShowModal) {
    Object.defineProperty(dialogPrototype, "showModal", originalShowModal);
  } else {
    Reflect.deleteProperty(dialogPrototype, "showModal");
  }
  if (originalClose) {
    Object.defineProperty(dialogPrototype, "close", originalClose);
  } else {
    Reflect.deleteProperty(dialogPrototype, "close");
  }
});

describe("reference media", () => {
  it("does not expose native controls when closing aborts pending autoplay", async () => {
    let rejectPlay: (reason?: unknown) => void = () => undefined;
    const playPromise = new Promise<void>((_resolve, reject) => {
      rejectPlay = reject;
    });
    vi.mocked(HTMLMediaElement.prototype.play).mockReturnValue(playPromise);
    const onClose = vi.fn();
    render(<ReelDialog onClose={onClose} />);
    const dialog = screen.getByRole("dialog", { name: "Watch reel" });
    const player = screen.getByLabelText("The Content Architecture product reel");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await act(async () => {
      rejectPlay(new DOMException("Playback was interrupted by pause.", "AbortError"));
      await Promise.resolve();
    });

    expect(dialog).not.toHaveAttribute("open");
    expect(player).not.toHaveAttribute("controls");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("ignores stale autoplay rejection after StrictMode cleanup", async () => {
    let rejectStalePlay: (reason?: unknown) => void = () => undefined;
    let rejectCurrentPlay: (reason?: unknown) => void = () => undefined;
    const stalePlay = new Promise<void>((_resolve, reject) => {
      rejectStalePlay = reject;
    });
    const currentPlay = new Promise<void>((_resolve, reject) => {
      rejectCurrentPlay = reject;
    });
    vi.mocked(HTMLMediaElement.prototype.play)
      .mockReturnValueOnce(stalePlay)
      .mockReturnValueOnce(currentPlay);
    render(
      <StrictMode>
        <ReelDialog onClose={vi.fn()} />
      </StrictMode>,
    );
    const player = screen.getByLabelText("The Content Architecture product reel");

    await act(async () => {
      rejectStalePlay(new DOMException("Autoplay blocked.", "NotAllowedError"));
      await Promise.resolve();
    });
    expect(player).not.toHaveAttribute("controls");

    await act(async () => {
      rejectCurrentPlay(new DOMException("Autoplay blocked.", "NotAllowedError"));
      await Promise.resolve();
    });
    expect(player).toHaveAttribute("controls");
  });

  it("plays the local product reel with accessible loading and failure states", () => {
    const onClose = vi.fn();
    const { container } = render(<ReelDialog onClose={onClose} />);
    const dialog = screen.getByRole("dialog", { name: "Watch reel" });
    const player = screen.getByLabelText("The Content Architecture product reel");
    const closeButton = screen.getByRole("button", { name: "Close" });

    expect(player.tagName).toBe("VIDEO");
    expect(player).toHaveAttribute("src", "/reference/assets/content-architecture-reel.mp4");
    expect(player).toHaveAttribute("autoplay");
    expect(player).not.toHaveAttribute("controls");
    expect(player).toHaveAttribute("role", "button");
    expect(player).toHaveAttribute("tabindex", "0");
    expect(player).toHaveAttribute("playsinline");
    expect(closeButton).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("Loading reel...");
    expect(container.querySelector('script[src^="https://"]')).not.toBeInTheDocument();

    fireEvent.canPlay(player);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.play(player);
    expect(player).toHaveAttribute("aria-pressed", "true");
    fireEvent.pause(player);
    expect(player).toHaveAttribute("aria-pressed", "false");

    fireEvent.error(player);
    expect(screen.getByRole("alert")).toHaveTextContent("The reel could not be loaded.");

    const cancel = new Event("cancel", { cancelable: true });
    fireEvent(dialog, cancel);
    expect(cancel.defaultPrevented).toBe(true);
    expect(dialog).not.toHaveAttribute("open");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("plays the local founder clip muted and looping with accessible media states", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { container } = render(<AboutDrawer onClose={onClose} />);
    const dialog = screen.getByRole("dialog", {
      name: "README / The Content Architecture",
    });
    const player = screen.getByLabelText("Edoardo Lunardi profile video");

    expect(player.tagName).toBe("VIDEO");
    expect(player).toHaveAttribute("src", "/reference/assets/edoardo-founder-video.mp4");
    expect(player).toHaveAttribute("autoplay");
    expect(player).not.toHaveAttribute("controls");
    expect(container.querySelector("media-controller")).toBeInTheDocument();
    expect(container.querySelector("media-time-range")).toBeInTheDocument();
    expect(container.querySelector("media-mute-button")).toBeInTheDocument();
    expect(container.querySelector("media-volume-range")).toBeInTheDocument();
    expect(container.querySelector("media-fullscreen-button")).toBeInTheDocument();
    expect(player).toBeInstanceOf(HTMLVideoElement);
    if (!(player instanceof HTMLVideoElement)) throw new Error("Expected a video element");
    expect(player.muted).toBe(true);
    expect(player).toHaveAttribute("loop");
    expect(player).toHaveAttribute("playsinline");
    expect(player).toHaveAttribute("preload", "metadata");
    expect(screen.getByRole("status")).toHaveTextContent("Loading founder video...");
    expect(container.querySelector('script[src^="https://"]')).not.toBeInTheDocument();

    fireEvent.canPlay(player);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    fireEvent.error(player);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The founder video could not be loaded.",
    );

    await user.click(screen.getByRole("button", { name: "Close Learn More" }));
    expect(dialog).not.toHaveAttribute("open");
    expect(onClose).toHaveBeenCalledOnce();
  });
});
