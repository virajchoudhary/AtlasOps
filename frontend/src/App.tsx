import { useCatalog } from "./api/useCatalog";
import { AppShell } from "./components/layout/AppShell";

export default function App() {
  const { catalog, status, refreshFailed, reload } = useCatalog();
  return (
    <AppShell
      catalog={catalog}
      status={status}
      refreshFailed={refreshFailed}
      reload={reload}
    />
  );
}
