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

  it("reuses the reference navigation and points secondary section links at the local home", async () => {
    const onNavigate = vi.fn();
    const { container, rerender } = render(
      <ReferenceSecondaryPages pathname="/blog/articles" onNavigate={onNavigate} />,
    );
    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    const blogLink = within(navigation).getByRole("link", { name: "Blog" });
    for (const [label, id] of [
      ["Features", "features"],
      ["The repo", "the-repo"],
      ["Showcase", "showcase"],
      ["Pricing", "pricing"],
      ["FAQ", "faq"],
    ]) {
      expect(within(navigation).getByRole("link", { name: label })).toHaveAttribute(
        "href",
        `/reference#${id}`,
      );
    }
    expect(within(navigation).getByRole("link", { name: "Home" })).toHaveAttribute(
      "href",
      "/reference",
    );
    expect(blogLink).toHaveClass("is-active");
    expect(blogLink).toHaveAttribute("aria-current", "page");
    expect(container.querySelector(".reference-minimap")).not.toBeInTheDocument();
    expect(container.querySelector(".reference-scroll-cue")).not.toBeInTheDocument();

    rerender(
      <ReferenceSecondaryPages
        pathname="/blog/stale-sanity-content-nextjs-caching-revalidation"
        onNavigate={onNavigate}
      />,
    );
    const articleNavigation = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(
      within(articleNavigation).getByRole("link", { name: "Blog" }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("closes the mobile menu on Escape, outside click, and local navigation", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<ReferenceSecondaryPages pathname="/blog/articles" onNavigate={onNavigate} />);
    const trigger = screen.getByRole("button", { name: /menu/i });

    await user.click(trigger);
    let menu = screen.getByRole("navigation", { name: "Page navigation" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(within(menu).getByRole("link", { name: "Blog" })).toHaveAttribute("aria-current", "page");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("navigation", { name: "Page navigation" })).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    menu = screen.getByRole("navigation", { name: "Page navigation" });
    await user.click(screen.getByRole("heading", { level: 1, name: "Knowledge Hub" }));
    expect(screen.queryByRole("navigation", { name: "Page navigation" })).not.toBeInTheDocument();

    await user.click(trigger);
    menu = screen.getByRole("navigation", { name: "Page navigation" });
    await user.click(within(menu).getByRole("link", { name: "Blog" }));
    expect(onNavigate).toHaveBeenCalledWith("/blog");
    expect(screen.queryByRole("navigation", { name: "Page navigation" })).not.toBeInTheDocument();
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

  it("keeps footer navigation local and makes the newsletter form preview-only", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
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

    const email = within(footer).getByRole("textbox", { name: "Email" });
    expect(email).toHaveAttribute("type", "email");
    expect(email).toBeRequired();
    const submit = within(footer).getByRole("button", { name: /stay updated/i });
    const status = within(footer).getByRole("status");
    expect(status).toBeEmptyDOMElement();

    await user.type(email, "not-an-email");
    await user.click(submit);
    expect(email).toBeInvalid();
    expect(status).toBeEmptyDOMElement();

    await user.clear(email);
    await user.type(email, "person@example.test");
    await user.click(submit);
    expect(status).toHaveTextContent(
      "This local reproduction does not submit or save email addresses.",
    );
    expect(status).toHaveClass("secondary-footer-form-status--visible");
    expect(email).toHaveValue("person@example.test");

    await userEvent.click(within(footer).getByRole("link", { name: /Blog/ }));
    expect(onNavigate).toHaveBeenCalledWith("/blog");
  });

  it("preserves historical roadmap totals while toggling an accessible local vote", async () => {
    const user = userEvent.setup();
    render(<ReferenceSecondaryPages pathname="/roadmap" onNavigate={() => {}} />);

    expect(screen.getByRole("heading", { level: 1, name: "Roadmap" })).toBeInTheDocument();
    for (const lane of ["SHIPPED", "IN PROGRESS", "PLANNED"]) {
      expect(screen.getByRole("region", { name: lane })).toBeInTheDocument();
    }
    const shipped = screen.getByRole("region", { name: "SHIPPED" });
    expect(within(shipped).getByRole("heading", { name: "Sanity + Astro version" })).toBeInTheDocument();
    expect(within(shipped).getByRole("heading", { name: "AI-generated llms.txt" })).toBeInTheDocument();
    expect(shipped.querySelectorAll(".secondary-roadmap-card-footer")).toHaveLength(3);

    const inProgress = screen.getByRole("region", { name: "IN PROGRESS" });
    expect(within(inProgress).getByRole("article")).toHaveClass(
      "secondary-roadmap-card--in-progress",
    );

    const vote = within(shipped).getByRole("button", { name: /31 historical votes/i });
    expect(vote).toHaveAttribute("aria-pressed", "false");
    expect(vote).not.toBeDisabled();
    expect(within(vote).getByText("31")).toBeInTheDocument();

    const feedback = screen.getByRole("status", { name: "Roadmap vote feedback" });
    await user.click(vote);
    expect(vote).toHaveAttribute("aria-pressed", "true");
    expect(within(vote).getByText("31")).toBeInTheDocument();
    expect(feedback).toHaveTextContent(
      "Local vote selected for Sanity + Astro version. Historical total remains 31; nothing was sent or saved.",
    );

    await user.keyboard(" ");
    expect(vote).toHaveAttribute("aria-pressed", "false");
    expect(within(vote).getByText("31")).toBeInTheDocument();
    expect(feedback).toHaveTextContent(
      "Local vote removed for Sanity + Astro version. Historical total remains 31; nothing was sent or saved.",
    );
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
