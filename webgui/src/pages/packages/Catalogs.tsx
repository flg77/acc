// Catalogs — the layered catalogs the Marketplace resolves against (WS-C1).
//
// Only the workspace layer is mutable. Where catalogs are not this page's to change
// (a cluster: they are the operator's AccCatalog objects) there is ONE notice with the
// environment's reason and no form. Fixes to the old screen: a priority is committed
// when the field is left (the old one called the API on every keystroke), and
// removing a catalog asks first.

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  EmptyState,
  EmptyStateBody,
  ExpandableSection,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Label,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  ModalVariant,
  TextInput,
  Title,
} from "@patternfly/react-core";
import { addCatalog, fetchCatalogs, removeCatalog, setCatalogPriority } from "../../api/client";
import type { CatalogRow } from "../../api/client";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { unavailableReason, useEnvironment } from "../../shell/EnvironmentContext";

const EMPTY_FORM = {
  catalog_id: "", tier: "community", mode: "https", url: "", path: "",
  issuer: "", subject_pattern: "", priority: 100,
};

function PriorityField({ row, onCommit }: { row: CatalogRow; onCommit: (p: number) => void }) {
  const [value, setValue] = useState(String(row.priority));
  useEffect(() => setValue(String(row.priority)), [row.priority]);
  const commit = () => {
    const n = parseInt(value, 10);
    if (Number.isFinite(n) && n !== row.priority) onCommit(n);
    else setValue(String(row.priority));
  };
  return (
    <TextInput
      type="number"
      value={value}
      onChange={(_, v) => setValue(v)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") commit(); }}
      aria-label={`Priority of ${row.id}`}
      style={{ inlineSize: "6rem" }}
    />
  );
}

export function CatalogsPage() {
  const { env, who } = useEnvironment();
  const [cats, setCats] = useState<CatalogRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [adding, setAdding] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<CatalogRow | null>(null);

  const load = () =>
    fetchCatalogs()
      .then((c) => { setCats(c); setLoaded(true); })
      .catch((e) => { setNote({ ok: false, text: String(e) }); setLoaded(true); });
  useEffect(() => { void load(); }, []);

  const reason = unavailableReason(env, "catalog.write");
  const canEdit = !reason && who?.role !== "viewer";

  const run = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      setNote({ ok: true, text: label });
      await load();
    } catch (e) {
      setNote({ ok: false, text: `${label} failed: ${e}` });
    }
  };
  const set = (k: keyof typeof EMPTY_FORM, v: string | number) => setForm({ ...form, [k]: v });

  const columns: Column<CatalogRow>[] = [
    {
      key: "id",
      label: "Catalog",
      render: (c) => (
        <>
          <strong>{c.id}</strong>
          {c.shadowed_by && <span className="acc-reason">shadowed by {c.shadowed_by}</span>}
        </>
      ),
    },
    { key: "layer", label: "Layer", render: (c) => <Label isCompact variant="outline">{c.layer}</Label> },
    { key: "tier", label: "Tier", render: (c) => <Label isCompact color="blue">{c.tier}</Label> },
    { key: "mode", label: "Mode", render: (c) => c.mode },
    { key: "where", label: "Location", render: (c) => <code>{c.url || c.path || "—"}</code> },
    {
      key: "signer",
      label: "Required signer",
      render: (c) => (
        <>
          {c.required_signer.issuer || "—"}
          <span className="acc-reason">{c.required_signer.subject_pattern || "—"}</span>
        </>
      ),
    },
    {
      key: "priority",
      label: "Priority",
      render: (c) =>
        canEdit && !c.read_only ? (
          <PriorityField row={c} onCommit={(p) => run(`Priority of ${c.id} set to ${p}`, () => setCatalogPriority(c.id, p))} />
        ) : (
          <>{c.priority}{c.read_only && <span className="acc-reason">read-only</span>}</>
        ),
    },
    ...(canEdit
      ? [{
          key: "actions",
          label: "",
          render: (c: CatalogRow) =>
            c.read_only ? null : (
              <Button size="sm" variant="secondary" isDanger onClick={() => setConfirmRemove(c)}>Remove</Button>
            ),
        }]
      : []),
  ];

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Catalogs</Title>

      {reason && (
        <Alert isInline variant="info" title="Catalogs are not changed from here" component="p">{reason}</Alert>
      )}
      {note && <Alert isInline variant={note.ok ? "success" : "danger"} title={note.text} component="p" />}

      {loaded && cats.length === 0 ? (
        <EmptyState titleText="No catalog configured" headingLevel="h2" variant="sm">
          <EmptyStateBody>Without a catalog the Marketplace has nothing to list.</EmptyStateBody>
        </EmptyState>
      ) : (
        <Table ariaLabel="Configured catalogs" columns={columns} rows={cats} rowKey={(c) => `${c.layer}:${c.id}`} />
      )}

      {canEdit && (
        <ExpandableSection toggleText="Add a catalog" isExpanded={adding} onToggle={(_, e) => setAdding(e)}>
          <Form
            onSubmit={(e) => {
              e.preventDefault();
              void run(`Added ${form.catalog_id}`, async () => {
                await addCatalog(form);
                setForm({ ...form, catalog_id: "", url: "", path: "" });
              });
            }}
          >
            <FormGroup label="Catalog id" isRequired fieldId="cat-id">
              <TextInput id="cat-id" value={form.catalog_id} onChange={(_, v) => set("catalog_id", v)} isRequired />
            </FormGroup>
            <FormGroup label="Tier" fieldId="cat-tier">
              <FormSelect id="cat-tier" value={form.tier} onChange={(_, v) => set("tier", v)}>
                {["community", "standard", "premium"].map((t) => <FormSelectOption key={t} value={t} label={t} />)}
              </FormSelect>
            </FormGroup>
            <FormGroup label="Mode" fieldId="cat-mode">
              <FormSelect id="cat-mode" value={form.mode} onChange={(_, v) => set("mode", v)}>
                {["https", "oci", "local"].map((m) => <FormSelectOption key={m} value={m} label={m} />)}
              </FormSelect>
            </FormGroup>
            <FormGroup label="URL" fieldId="cat-url">
              <TextInput id="cat-url" value={form.url} onChange={(_, v) => set("url", v)} />
            </FormGroup>
            <FormGroup label="Path (local mode)" fieldId="cat-path">
              <TextInput id="cat-path" value={form.path} onChange={(_, v) => set("path", v)} />
            </FormGroup>
            <FormGroup label="Required signer — issuer" isRequired fieldId="cat-issuer">
              <TextInput id="cat-issuer" value={form.issuer} onChange={(_, v) => set("issuer", v)} isRequired />
            </FormGroup>
            <FormGroup label="Required signer — subject pattern" fieldId="cat-subject">
              <TextInput id="cat-subject" value={form.subject_pattern} onChange={(_, v) => set("subject_pattern", v)} />
            </FormGroup>
            <FormGroup label="Priority" fieldId="cat-priority">
              <TextInput
                id="cat-priority"
                type="number"
                value={String(form.priority)}
                onChange={(_, v) => set("priority", parseInt(v, 10) || 100)}
              />
            </FormGroup>
            <div>
              <Button type="submit" variant="primary" isDisabled={!form.catalog_id || !form.issuer}>
                Add catalog
              </Button>
            </div>
          </Form>
        </ExpandableSection>
      )}

      <Modal
        variant={ModalVariant.small}
        isOpen={confirmRemove !== null}
        onClose={() => setConfirmRemove(null)}
        aria-labelledby="remove-catalog-title"
      >
        <ModalHeader title="Remove this catalog?" labelId="remove-catalog-title" titleIconVariant="warning" />
        <ModalBody>
          {confirmRemove && (
            <>
              <code>{confirmRemove.id}</code> ({confirmRemove.layer} layer) stops being consulted by the
              Marketplace. Packages that came only from it disappear from the list; nothing already
              installed is removed.
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button
            variant="danger"
            onClick={() => {
              const c = confirmRemove;
              setConfirmRemove(null);
              if (c) void run(`Removed ${c.id}`, () => removeCatalog(c.id));
            }}
          >
            Remove
          </Button>
          <Button variant="link" onClick={() => setConfirmRemove(null)}>Cancel</Button>
        </ModalFooter>
      </Modal>
    </div>
  );
}
