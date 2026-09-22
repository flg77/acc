// Marketplace — the packages the configured catalogs offer.
//
// Where install is not this page's to do (a cluster: it is an AccPackageInstall the
// operator reconciles), there is ONE notice above the table with the environment's
// own reason — not a disabled button on every row (design-system/patterns/gated-action).
// Where it is, Install stages a PROPOSE_INFUSE marker; nothing is installed until an
// operator dispatches it — the page says so.

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  EmptyState,
  EmptyStateBody,
  Label,
  SearchInput,
  Title,
} from "@patternfly/react-core";
import { fetchAvailableRoles, installRole } from "../../api/client";
import type { CatalogError, MarketRow } from "../../api/client";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { unavailableReason, useEnvironment } from "../../shell/EnvironmentContext";

export function MarketplacePage() {
  const { env, who } = useEnvironment();
  const [rows, setRows] = useState<MarketRow[]>([]);
  const [catalogErrors, setCatalogErrors] = useState<CatalogError[]>([]);
  const [podCluster, setPodCluster] = useState(false);
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [staged, setStaged] = useState<{ ok: boolean; text: string } | null>(null);

  const load = (f: string) =>
    fetchAvailableRoles(f)
      .then((r) => {
        setRows(r.rows);
        setCatalogErrors(r.catalog_errors);
        setPodCluster(r.cluster);
        setErr("");
        setLoaded(true);
      })
      .catch((e) => { setErr(String(e)); setLoaded(true); });
  useEffect(() => { void load(""); }, []);

  const install = async (name: string) => {
    setStaged({ ok: true, text: `Staging ${name}…` });
    try {
      const r = await installRole(name);
      setStaged({
        ok: true,
        text:
          `Staged a PROPOSE_INFUSE marker for ${r.target_name}@${r.target_constraint} — ` +
          `nothing is installed until an operator dispatches it: ${r.install_marker}`,
      });
    } catch (e) {
      setStaged({ ok: false, text: `Could not stage ${name}: ${e}` });
    }
  };

  const reason =
    unavailableReason(env, "package.install") ||
    (podCluster
      ? "Packages are installed by the operator: create an AccPackageInstall for the package — this pod has no package installer of its own."
      : "");
  const canInstall = !reason && who?.role !== "viewer";

  const columns: Column<MarketRow>[] = [
    { key: "name", label: "Package", render: (r) => <code>{r.name}</code> },
    { key: "version", label: "Version", render: (r) => r.version },
    {
      key: "tier",
      label: "Tier",
      render: (r) => <Label isCompact color="blue">{r.tier}</Label>,
    },
    { key: "catalog", label: "Catalog", render: (r) => <>{r.catalog_id} <span className="acc-source">({r.catalog_mode})</span></> },
    { key: "signer", label: "Signer", render: (r) => r.signer || "—" },
    ...(canInstall
      ? [{
          key: "install",
          label: "",
          render: (r: MarketRow) => (
            <Button size="sm" variant="secondary" onClick={() => install(r.name)}>Install</Button>
          ),
        }]
      : []),
  ];

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Marketplace</Title>
      <SearchInput
        placeholder="Filter by name…"
        value={filter}
        onChange={(_, v) => setFilter(v)}
        onSearch={(_, v) => void load(v)}
        onClear={() => { setFilter(""); void load(""); }}
        aria-label="Filter packages by name"
      />

      {reason && (
        <Alert isInline variant="info" title="Install is not done from here" component="p">{reason}</Alert>
      )}
      {err && <Alert isInline variant="danger" title="The catalogs could not be read" component="p">{err}</Alert>}
      {catalogErrors.map((c) => (
        <Alert key={c.id} isInline variant="warning" title={`${c.id} is unreachable — its packages are hidden`} component="p">
          {c.url}: {c.error}
        </Alert>
      ))}
      {staged && (
        <Alert isInline variant={staged.ok ? "info" : "danger"} title={staged.text} component="p" />
      )}

      {loaded && rows.length === 0 && !err && catalogErrors.length === 0 && (
        <EmptyState titleText="No packages" headingLevel="h2" variant="sm">
          <EmptyStateBody>
            {filter ? `No package matches “${filter}”.` : "The configured catalogs list no package."}
          </EmptyStateBody>
        </EmptyState>
      )}
      {rows.length > 0 && (
        <Table ariaLabel="Packages" columns={columns} rows={rows} rowKey={(r, i) => `${r.name}@${r.version}-${i}`} />
      )}
    </div>
  );
}
