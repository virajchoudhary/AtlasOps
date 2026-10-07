import { AnimatePresence, motion, MotionConfig } from "motion/react";
import {
  Activity,
  BookOpen,
  ChartNoAxesColumnIncreasing,
  FileSearch,
  GitBranch,
  LayoutDashboard,
  MoreHorizontal,
  Network,
  Play,
  RefreshCw,
  ShieldCheck,
  UsersRound,
} from "lucide-react";
import { useEffect, useState } from "react";
import { HashRouter, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { Catalog } from "../../api/types";
import type { CatalogStatus } from "../../api/useCatalog";
import { displayText } from "../../lib/format";
import { AgentsPage } from "../../pages/AgentsPage";
import { EvaluationsPage } from "../../pages/EvaluationsPage";
import { EvidencePage } from "../../pages/EvidencePage";
import { IncidentsPage } from "../../pages/IncidentsPage";
import { ModelsPage } from "../../pages/ModelsPage";
import { OverviewPage } from "../../pages/OverviewPage";
import { RunbooksPage } from "../../pages/RunbooksPage";
import { SystemPage } from "../../pages/SystemPage";
import { DemoPage } from "../../pages/DemoPage";
import { Button } from "../ui/button";

const navigation = [
  { to: "/", label: "Overview", icon: LayoutDashboard, group: "Workspace" },
  { to: "/demo", label: "Rehearsal", icon: Play, group: "Workspace" },
  { to: "/agents", label: "Agents", icon: UsersRound, group: "Workspace" },
  { to: "/models", label: "Models", icon: GitBranch, group: "Workspace" },
  { to: "/evaluations", label: "Evaluations", icon: ChartNoAxesColumnIncreasing, group: "Workspace" },
  { to: "/incidents", label: "Incidents", icon: Activity, group: "Workspace" },
  { to: "/evidence", label: "Evidence", icon: FileSearch, group: "Workspace" },
  { to: "/runbooks", label: "Runbooks", icon: BookOpen, group: "Reference" },
  { to: "/system", label: "System", icon: ShieldCheck, group: "Reference" },
];

const mainMobileRoutes = new Set(["/", "/demo", "/evaluations", "/evidence"]);
const pageHeadings = new Map(navigation.map((item) => [item.to, item.label]));

function ShellContent({
  catalog,
  status,
  refreshFailed,
  reload,
}: {
  catalog: Catalog | null;
  status: CatalogStatus;
  refreshFailed: boolean;
  reload: () => void;
}) {
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const currentHeading = pageHeadings.get(location.pathname) ?? "Overview";
  const productName = displayText(catalog?.product?.name ?? "AtlasOps");
  const apiLabel = status === "ready"
    ? refreshFailed ? "Refresh failed · showing previous snapshot" : "Snapshot available"
    : status === "refreshing" ? "Refreshing snapshot"
      : status === "loading" ? "Reading snapshot" : "API unavailable";
  const apiTone = refreshFailed ? "unavailable" : status;

  const pageProps = { catalog, loading: status === "loading" || status === "refreshing" };

  useEffect(() => {
    const main = document.querySelector(".main-content");
    if (main) main.scrollTop = 0;
  }, [location.pathname]);

  useEffect(() => {
    if (!moreOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMoreOpen(false);
    };
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (event.target instanceof Element && !event.target.closest(".mobile-more")) {
        setMoreOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOnOutsideClick);
    };
  }, [moreOpen]);

  return (
    <MotionConfig reducedMotion="user">
      <div className="app-frame">
        <a className="skip-link" href="#main-content">Skip to content</a>
        <aside className="sidebar" aria-label="Primary navigation">
          <NavLink to="/" className="brand-lockup" aria-label={`${productName} overview`}>
            <span className="brand-mark" aria-hidden="true"><Network size={19} /></span>
            <span className="brand-wordmark">
              <strong>{productName}</strong>
              <small>RESEARCH CONSOLE</small>
            </span>
          </NavLink>

          <nav className="sidebar-nav">
            {["Workspace", "Reference"].map((group) => (
              <div className="nav-group" key={group}>
                <p className="nav-group-label">{group}</p>
                {navigation.filter((item) => item.group === group).map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      end={item.to === "/"}
                      title={item.label}
                      aria-label={item.label}
                      className={({ isActive }) => `nav-link ${isActive ? "is-active" : ""}`}
                    >
                      <Icon size={17} aria-hidden="true" />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </div>
            ))}
          </nav>

          <div className="sidebar-foot">
            <div className="sidebar-foot-icon" aria-hidden="true"><ShieldCheck size={17} /></div>
            <div>
              <strong>{catalog?.product?.operator_enabled ? "Governed operator" : "Read-only demo"}</strong>
              <span>{catalog?.product?.operator_enabled ? "Exact-action approval" : "Local evidence snapshot"}</span>
            </div>
          </div>
        </aside>

        <div className="main-shell">
          <header className="topbar">
            <div className="topbar-context">
              <span className="topbar-root">AtlasOps</span>
              <span className="topbar-separator" aria-hidden="true">/</span>
              <span className="topbar-current">{currentHeading}</span>
            </div>
            <div className="topbar-actions">
              <span className={`api-indicator api-indicator--${apiTone}`} role="status">
                <i aria-hidden="true" />
                {apiLabel}
              </span>
              <Button
                variant="quiet"
                size="icon"
                className={`refresh-button ${status === "refreshing" ? "is-refreshing" : ""}`}
                aria-label="Refresh repository snapshot"
                title="Refresh snapshot"
                onClick={reload}
                disabled={status === "loading" || status === "refreshing"}
              >
                <RefreshCw size={16} aria-hidden="true" />
              </Button>
            </div>
          </header>

          <main id="main-content" className="main-content" tabIndex={-1}>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={location.pathname}
                className="route-content"
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -3 }}
                transition={{ duration: 0.16, ease: "easeOut" }}
              >
                <Routes location={location}>
                  <Route path="/" element={<OverviewPage {...pageProps} />} />
                  <Route path="/demo" element={<DemoPage />} />
                  <Route path="/agents" element={<AgentsPage {...pageProps} />} />
                  <Route path="/models" element={<ModelsPage {...pageProps} />} />
                  <Route path="/evaluations" element={<EvaluationsPage {...pageProps} />} />
                  <Route path="/incidents" element={<IncidentsPage {...pageProps} />} />
                  <Route path="/evidence" element={<EvidencePage {...pageProps} />} />
                  <Route path="/runbooks" element={<RunbooksPage {...pageProps} />} />
                  <Route path="/system" element={<SystemPage {...pageProps} />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </motion.div>
            </AnimatePresence>
          </main>

          <nav className="mobile-nav" aria-label="Mobile navigation">
            {navigation.filter((item) => mainMobileRoutes.has(item.to)).map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  aria-label={item.label}
                  className={({ isActive }) => `mobile-nav-link ${isActive ? "is-active" : ""}`}
                  onClick={() => setMoreOpen(false)}
                >
                  <Icon size={18} aria-hidden="true" />
                  <span>{item.label}</span>
                </NavLink>
              );
            })}
            <div className="mobile-more">
              <Button
                variant="quiet"
                size="sm"
                className={`mobile-more-trigger ${moreOpen ? "is-active" : ""}`}
                aria-expanded={moreOpen}
                aria-controls="additional-navigation"
                onClick={() => setMoreOpen((open) => !open)}
              >
                <MoreHorizontal size={18} aria-hidden="true" />
                <span>More</span>
              </Button>
              {moreOpen && (
                <div id="additional-navigation" className="mobile-more-menu" role="region" aria-label="Additional navigation">
                  {navigation.filter((item) => !mainMobileRoutes.has(item.to)).map((item) => {
                    const Icon = item.icon;
                    return (
                      <NavLink
                        key={item.to}
                        to={item.to}
                        className={({ isActive }) => `mobile-more-link ${isActive ? "is-active" : ""}`}
                        onClick={() => setMoreOpen(false)}
                      >
                        <Icon size={16} aria-hidden="true" />
                        <span>{item.label}</span>
                      </NavLink>
                    );
                  })}
                </div>
              )}
            </div>
          </nav>
        </div>
      </div>
    </MotionConfig>
  );
}

export function AppShell(props: {
  catalog: Catalog | null;
  status: CatalogStatus;
  refreshFailed: boolean;
  reload: () => void;
}) {
  return (
    <HashRouter>
      <ShellContent {...props} />
    </HashRouter>
  );
}
