import { ChevronDown, ChevronRight, Search, TerminalSquare, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  REPO_READMES,
  REPO_TREES,
  type Edition,
  type RepoEntry,
  type RepoFile,
  type RepoFolder,
} from "../content";

function isFolder(entry: RepoEntry): entry is RepoFolder {
  return entry.kind === "folder";
}

function isFile(entry: RepoEntry): entry is RepoFile {
  return entry.kind === "file";
}

function flattenFiles(entries: readonly RepoEntry[]): RepoFile[] {
  return entries.flatMap((entry) => {
    if (isFile(entry)) return [entry];
    return flattenFiles(entry.children);
  });
}

function FileTree({
  entries,
  path,
  expanded,
  onToggle,
  onOpenFile,
  activeFile,
}: {
  entries: readonly RepoEntry[];
  path: string;
  expanded: ReadonlySet<string>;
  onToggle: (path: string) => void;
  onOpenFile: (file: RepoFile) => void;
  activeFile: string;
}) {
  return (
    <ul className="reference-tree-list">
      {entries.map((entry) => {
        const entryPath = path ? `${path}/${entry.name}` : entry.name;
        if (isFile(entry)) {
          return (
            <li key={entryPath}>
              <button
                className={`reference-tree-file ${activeFile === entryPath ? "is-active" : ""}`}
                type="button"
                onClick={() => onOpenFile(entry)}
                aria-current={activeFile === entryPath ? "page" : undefined}
                title={entryPath}
              >
                <span className="reference-file-glyph" aria-hidden="true" />
                <span>{entry.name}</span>
              </button>
            </li>
          );
        }

        const isExpanded = expanded.has(entryPath);
        return (
          <li key={entryPath}>
            <button
              className="reference-tree-folder"
              type="button"
              aria-expanded={isExpanded}
              onClick={() => onToggle(entryPath)}
            >
              {isExpanded ? (
                <ChevronDown size={12} aria-hidden="true" />
              ) : (
                <ChevronRight size={12} aria-hidden="true" />
              )}
              <span>{entry.name}</span>
            </button>
            {isExpanded && (
              <FileTree
                entries={entry.children}
                path={entryPath}
                expanded={expanded}
                onToggle={onToggle}
                onOpenFile={onOpenFile}
                activeFile={activeFile}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function RepoExplorer() {
  const [edition, setEdition] = useState<Edition>("next");
  const [terminalVisible, setTerminalVisible] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    new Set(["app", "features"]),
  );
  const [activeFile, setActiveFile] = useState("README.md");
  const [terminalInput, setTerminalInput] = useState("");
  const [terminalNotice, setTerminalNotice] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const files = useMemo(() => flattenFiles(REPO_TREES[edition]), [edition]);
  const filteredFiles = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (!query) return files.slice(0, 12);
    return files.filter((file) => file.name.toLowerCase().includes(query));
  }, [files, searchText]);
  const content =
    activeFile === "README.md"
      ? REPO_READMES[edition]
      : files.find((file) => file.name === activeFile)?.content ??
        `${activeFile}\n\nPublic reference preview.`;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        setTerminalVisible((visible) => !visible);
      }
      if (event.key === "Escape") setSearchOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus();
    else setSearchText("");
  }, [searchOpen]);

  const toggleFolder = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const chooseEdition = (nextEdition: Edition) => {
    setEdition(nextEdition);
    setActiveFile("README.md");
    setExpanded(new Set(nextEdition === "next" ? ["app", "features"] : ["src", "pages"]));
  };

  const openFile = (file: RepoFile) => {
    setActiveFile(file.name);
    setSearchOpen(false);
  };

  const submitTerminal = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (terminalInput.trim()) {
      setTerminalNotice("This public reference preview does not execute terminal commands.");
      setTerminalInput("");
    }
  };

  return (
    <section className="reference-repo-section" id="the-repo">
      <div className="reference-repo-window" aria-label="Public repository preview">
        <div className="reference-repo-toolbar">
          <div className="reference-edition-switch" role="group" aria-label="Repository edition">
            <button
              type="button"
              aria-pressed={edition === "next"}
              onClick={() => chooseEdition("next")}
            >
              Next.js
            </button>
            <button
              type="button"
              aria-pressed={edition === "astro"}
              onClick={() => chooseEdition("astro")}
            >
              Astro
            </button>
          </div>
          <span className="reference-repo-caption">This is the actual repo.</span>
          <div className="reference-repo-actions">
            <button
              type="button"
              aria-label={terminalVisible ? "Hide terminal" : "Show terminal"}
              onClick={() => setTerminalVisible((visible) => !visible)}
            >
              <TerminalSquare size={13} aria-hidden="true" />
              <span>Ctrl J</span>
            </button>
            <button type="button" aria-label="Search files" onClick={() => setSearchOpen(true)}>
              <Search size={13} aria-hidden="true" />
              <span>Ctrl K</span>
            </button>
          </div>
        </div>
        <div className={`reference-repo-body ${terminalVisible ? "" : "terminal-hidden"}`}>
          <aside className="reference-file-explorer" aria-label="File explorer">
            <div className="reference-file-explorer-title">Explorer</div>
            <FileTree
              entries={REPO_TREES[edition]}
              path=""
              expanded={expanded}
              onToggle={toggleFolder}
              onOpenFile={openFile}
              activeFile={activeFile}
            />
          </aside>
          <div className="reference-editor">
            <div className="reference-editor-tab">{activeFile}</div>
            <pre aria-label={`${activeFile} contents`}>
              <code>{content}</code>
            </pre>
          </div>
          <aside className="reference-code-minimap" aria-hidden="true">
            {Array.from({ length: 24 }, (_, index) => (
              <i key={index} />
            ))}
          </aside>
        </div>
        {terminalVisible && (
          <section className="reference-terminal" aria-label="Terminal preview">
            <div className="reference-terminal-heading">Terminal</div>
            <div className="reference-terminal-history" aria-live="polite">
              <p>
                <span>~/the-content-architecture-{edition === "next" ? "next-js" : "astro"}</span>
                <b>&gt;</b> get-access <i># €549 · one-time</i>
              </p>
              <p>
                <span>~/the-content-architecture-{edition === "next" ? "next-js" : "astro"}</span>
                <b>&gt;</b> try: git, ls, tree, find, cat README.md
              </p>
              {terminalNotice && <p className="reference-terminal-notice">{terminalNotice}</p>}
            </div>
            <form className="reference-terminal-input" onSubmit={submitTerminal}>
              <label className="reference-sr-only" htmlFor="reference-terminal-command">
                Terminal input
              </label>
              <span aria-hidden="true">&gt;</span>
              <input
                id="reference-terminal-command"
                value={terminalInput}
                onChange={(event) => setTerminalInput(event.currentTarget.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </form>
          </section>
        )}
      </div>
      {searchOpen && (
        <div className="reference-search-backdrop" role="presentation">
          <section
            className="reference-search-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reference-search-title"
          >
            <div className="reference-search-header">
              <h3 id="reference-search-title">Search files</h3>
              <button
                type="button"
                aria-label="Close file search"
                onClick={() => setSearchOpen(false)}
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>
            <label className="reference-sr-only" htmlFor="reference-file-query">
              Search files
            </label>
            <input
              id="reference-file-query"
              ref={searchRef}
              value={searchText}
              onChange={(event) => setSearchText(event.currentTarget.value)}
              placeholder="Search files..."
              autoComplete="off"
            />
            <ul>
              {filteredFiles.length > 0 ? (
                filteredFiles.map((file) => (
                  <li key={`${edition}/${file.name}`}>
                    <button type="button" onClick={() => openFile(file)}>
                      <span className="reference-file-glyph" aria-hidden="true" />
                      {file.name}
                    </button>
                  </li>
                ))
              ) : (
                <li className="reference-search-empty">No matching files</li>
              )}
            </ul>
          </section>
        </div>
      )}
    </section>
  );
}
