import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReferenceSecondaryPages } from "./ReferenceSecondaryPages";

afterEach(cleanup);

describe("public secondary reference pages", () => {
  it("shows every observed article on the public Blog page and routes article navigation", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<ReferenceSecondaryPages pathname="/blog/" onNavigate={onNavigate} />);

    expect(screen.getByRole("heading", { level: 1, name: "Knowledge Hub" })).toBeInTheDocument();
    expect(document.title).toBe("Content Architecture Blog | Next.js + Sanity Engineering");
    const links = screen.getAllByRole("link", { name: /Entry 00\d:/ });
    expect(links).toHaveLength(3);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/blog/the-content-architecture-four-months-in",
      "/blog/serve-content-to-ai-agents-llms-txt-markdown-sanity",
      "/blog/stale-sanity-content-nextjs-caching-revalidation",
    ]);

    await user.click(links[1]);
    expect(onNavigate).toHaveBeenCalledWith(
      "/blog/serve-content-to-ai-agents-llms-txt-markdown-sanity",
    );
  });

  it("supports the articles deep-link alias and keeps the roadmap home link local", async () => {
    const onNavigate = vi.fn();
    render(<ReferenceSecondaryPages pathname="/blog/articles" onNavigate={onNavigate} />);
    expect(screen.getByRole("heading", { level: 1, name: "Knowledge Hub" })).toBeInTheDocument();
    expect(document.title).toBe("Content Architecture Blog | Next.js + Sanity Engineering");

    cleanup();
    render(<ReferenceSecondaryPages pathname="/roadmap" onNavigate={onNavigate} />);
    await userEvent.click(screen.getAllByRole("link", { name: /LEARN MORE/i })[0]);
    expect(onNavigate).toHaveBeenCalledWith("/reference");
  });

  it("supports article links under both observed and articles-prefixed paths", () => {
    const onNavigate = () => {};
    const articleTitle = "Your Site Is Invisible to AI Agents (and Your CMS Can Fix It)";
    const { rerender } = render(
      <ReferenceSecondaryPages
        pathname="/blog/articles/serve-content-to-ai-agents-llms-txt-markdown-sanity"
        onNavigate={onNavigate}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: articleTitle })).toBeInTheDocument();

    rerender(
      <ReferenceSecondaryPages
        pathname="/blog/serve-content-to-ai-agents-llms-txt-markdown-sanity"
        onNavigate={onNavigate}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: articleTitle })).toBeInTheDocument();
  });

  it("renders the captured long-form article structure, images, code and article index navigation", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <ReferenceSecondaryPages
        pathname="/blog/serve-content-to-ai-agents-llms-txt-markdown-sanity"
        onNavigate={onNavigate}
      />,
    );

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Your Site Is Invisible to AI Agents (and Your CMS Can Fix It)",
      }),
    ).toBeInTheDocument();
    expect(document.title).toBe("Serve Your Site to AI Agents: llms.txt and Markdown in Sanity");
    expect(screen.getByRole("heading", { level: 2, name: "Common questions" })).toBeInTheDocument();
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(screen.getByText(/A served file reads like this:/)).toBeInTheDocument();
    expect(screen.getAllByText(/llms\.txt/).length).toBeGreaterThan(3);

    await user.click(screen.getByRole("link", { name: /ALL ARTICLES/i }));
    expect(onNavigate).toHaveBeenCalledWith("/blog");
  });

  it("renders each legal policy section as complete readable copy", () => {
    const noop = () => {};
    const { rerender } = render(
      <ReferenceSecondaryPages pathname="/legal/privacy-policy" onNavigate={noop} />,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Privacy Policy" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Changes to this policy" })).toBeInTheDocument();
    expect(screen.getByText(/card numbers never reach this site or its operator/)).toBeInTheDocument();
    const contactLinks = screen.getAllByRole("link", { name: "hello@edoardolunardi.dev" });
    expect(contactLinks.length).toBeGreaterThanOrEqual(1);
    expect(contactLinks.every((link) => (
      link.getAttribute("href") === "mailto:hello@edoardolunardi.dev"
    ))).toBe(true);

    rerender(
      <ReferenceSecondaryPages pathname="/legal/terms-of-service" onNavigate={noop} />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Terms of Service" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Warranty and liability" })).toBeInTheDocument();
    expect(screen.getByText(/all sales are final and no refunds are offered/)).toBeInTheDocument();
    expect(screen.getByText(/These terms govern your purchase and use/)).toBeInTheDocument();
  });

  it("keeps secondary-page footer links local and the signup preview inert", async () => {
    const onNavigate = vi.fn();
    render(
      <ReferenceSecondaryPages
        pathname="/legal/privacy-policy"
        onNavigate={onNavigate}
      />,
    );

    const footer = screen.getByRole("contentinfo");
    expect(within(footer).getByRole("navigation", { name: "Footer" })).toBeInTheDocument();
    expect(within(footer).getByRole("link", { name: /Blog/ })).toHaveAttribute("href", "/blog");
    expect(within(footer).getByRole("link", { name: /Privacy policy/ })).toHaveAttribute(
      "href",
      "/legal/privacy-policy",
    );
    expect(within(footer).getByRole("link", { name: /Terms of service/ })).toHaveAttribute(
      "href",
      "/legal/terms-of-service",
    );

    const preview = within(footer).getByLabelText("Newsletter signup preview");
    expect(preview.querySelector("form, input, button")).toBeNull();
    await userEvent.click(within(footer).getByRole("link", { name: /Blog/ }));
    expect(onNavigate).toHaveBeenCalledWith("/blog");
  });

  it("preserves roadmap sections while leaving public vote controls disabled", () => {
    render(<ReferenceSecondaryPages pathname="/roadmap" onNavigate={() => {}} />);

    expect(screen.getByRole("heading", { level: 1, name: "Roadmap" })).toBeInTheDocument();
    for (const lane of ["SHIPPED", "IN PROGRESS", "PLANNED"]) {
      expect(screen.getByRole("region", { name: lane })).toBeInTheDocument();
    }
    const shipped = screen.getByRole("region", { name: "SHIPPED" });
    expect(within(shipped).getByRole("heading", { name: "Sanity + Astro version" })).toBeInTheDocument();
    expect(within(shipped).getByRole("heading", { name: "AI-generated llms.txt" })).toBeInTheDocument();
    expect(shipped.querySelectorAll(".secondary-roadmap-card-footer")).toHaveLength(3);
    expect(screen.getAllByRole("button", { name: /Voting is not available/ })).toHaveLength(4);
    expect(screen.getAllByRole("button", { name: /Voting is not available/ }).every((button) => (
      button.hasAttribute("disabled")
    ))).toBe(true);
  });

  it("returns no secondary surface for unrelated application routes", () => {
    const { container } = render(
      <ReferenceSecondaryPages pathname="/incidents" onNavigate={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("keeps the full section structure and source links for the other visible articles", async () => {
    const onNavigate = vi.fn();
    const { container, rerender } = render(
      <ReferenceSecondaryPages
        pathname="/blog/the-content-architecture-four-months-in"
        onNavigate={onNavigate}
      />,
    );

    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(14);
    expect(container.querySelectorAll("pre")).toHaveLength(9);
    await userEvent.click(screen.getByRole("link", { name: "the caching deep dive" }));
    expect(onNavigate).toHaveBeenCalledWith("/blog/stale-sanity-content-nextjs-caching-revalidation");

    rerender(
      <ReferenceSecondaryPages
        pathname="/blog/stale-sanity-content-nextjs-caching-revalidation"
        onNavigate={onNavigate}
      />,
    );
    const article = screen.getByRole("article");
    expect(within(article).getAllByRole("heading", { level: 2 })).toHaveLength(10);
    expect(within(article).getAllByRole("heading", { level: 3 })).toHaveLength(6);
    expect(container.querySelectorAll("pre")).toHaveLength(2);
    expect(screen.getByRole("img", { name: /Sanity GROQ-powered webhooks/ })).toBeInTheDocument();
    expect(container.textContent).toMatch(/The Sanity CDN, controlled by the useCdn flag/);
  });
});
