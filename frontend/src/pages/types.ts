import type { Catalog } from "../api/types";

export interface CatalogPageProps {
  catalog: Catalog | null;
  loading: boolean;
}
