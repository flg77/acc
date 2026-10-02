// Credentials — introduce or replace a credential by name (UX-07).
//
// Write-only. The page shows names: which models use each one and how many agents
// see it in their mount (from their heartbeats). A value is typed into a masked
// field, sent once, and the field is cleared — it is never fetched back, because
// the server has no route that returns one (on a cluster the UI ServiceAccount may
// patch the Secret, not read it).

import { useEffect, useState } from "react";
import { Alert, Button, Form, FormGroup, FormHelperText, HelperText, HelperTextItem, Label, TextInput, Title } from "@patternfly/react-core";
import { fetchSecrets, writeSecret } from "../../api/client";
import type { SecretList, SecretRow } from "../../api/client";
import { Table } from "../../components/Table";
import type { Column } from "../../components/Table";
import { useEnvironment } from "../../shell/EnvironmentContext";

const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

export function CredentialsPage() {
  const { who } = useEnvironment();
  const [list, setList] = useState<SecretList | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    fetchSecrets().then(setList).catch((e) => setNote({ ok: false, text: String(e) }));
  useEffect(() => { void load(); }, []);

  const target = list?.target;
  const canWrite = !!target?.writable && who?.role !== "viewer";
  const mounted = list?.agents.mounted ?? 0;

  const submit = async () => {
    setBusy(true);
    try {
      const done = await writeSecret(name, value);
      setNote({ ok: true, text: `${done.name} written to ${done.target.where} — ${done.note}` });
      setName("");
      await load();
    } catch (e) {
      setNote({ ok: false, text: `Writing ${name} failed: ${e}` });
    } finally {
      // Cleared whatever happened: a value left in the field outlives the task.
      setValue("");
      setBusy(false);
    }
  };

  const columns: Column<SecretRow>[] = [
    { key: "name", label: "Name", render: (r) => <code>{r.name}</code> },
    {
      key: "used_by",
      label: "Used by",
      render: (r) => (r.used_by.length ? r.used_by.join(", ") : <span className="acc-reason">no model in the registry</span>),
    },
    {
      key: "seen",
      label: "Agents that see it",
      render: (r) =>
        mounted === 0 ? "—" : (
          <Label isCompact color={r.seen_by === mounted ? "green" : r.seen_by ? "orange" : "grey"}>
            {r.seen_by} of {mounted}
          </Label>
        ),
    },
    ...(canWrite
      ? [{
          key: "actions",
          label: "",
          render: (r: SecretRow) => (
            <Button size="sm" variant="secondary" onClick={() => { setName(r.name); setValue(""); }}>
              {r.seen_by ? "Replace" : "Set"}
            </Button>
          ),
        }]
      : []),
  ];

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">Credentials</Title>
      <p className="acc-reason">
        Values go into the secret source the agents read on every call — never onto the bus, into a
        transcript or a log, and never back to this page.
      </p>

      {target && !target.writable && (
        <Alert isInline variant="info" title="Credentials are not written from here" component="p">{target.reason}</Alert>
      )}
      {target?.writable && who?.role === "viewer" && (
        <Alert isInline variant="info" title="Writing a credential needs the operator role" component="p" />
      )}
      {list && list.agents.env > 0 && (
        <Alert isInline variant="warning" component="p"
          title={`${list.agents.env} agent(s) read credentials from their environment, not the mount`}>
          A credential written here does not reach them; they need ACC_SECRET_SOURCE=mounted.
        </Alert>
      )}
      {note && <Alert isInline variant={note.ok ? "success" : "danger"} title={note.text} component="p" />}

      {list && (
        <Table ariaLabel="Credential names" columns={columns} rows={list.rows} rowKey={(r) => r.name} />
      )}

      {canWrite && (
        <Form
          autoComplete="off"
          onSubmit={(e) => { e.preventDefault(); void submit(); }}
        >
          <Title headingLevel="h2" size="lg">Set a credential</Title>
          <FormGroup label="Name" isRequired fieldId="secret-name">
            <TextInput id="secret-name" value={name} onChange={(_, v) => setName(v.trim())} isRequired
              validated={name && !NAME.test(name) ? "error" : "default"} />
            <FormHelperText>
              <HelperText>
                <HelperTextItem>The environment-variable name the agents read, e.g. MAAS_API_KEY.</HelperTextItem>
              </HelperText>
            </FormHelperText>
          </FormGroup>
          <FormGroup label="Value" isRequired fieldId="secret-value">
            <TextInput id="secret-value" type="password" autoComplete="new-password" spellCheck={false}
              value={value} onChange={(_, v) => setValue(v)} isRequired />
            <FormHelperText>
              <HelperText>
                <HelperTextItem>
                  Written to {target?.where}. Replaces any value already there; it cannot be read back.
                </HelperTextItem>
              </HelperText>
            </FormHelperText>
          </FormGroup>
          <div>
            <Button type="submit" variant="primary" isLoading={busy}
              isDisabled={busy || !NAME.test(name) || !value}>
              Write {name || "credential"}
            </Button>
          </div>
        </Form>
      )}
    </div>
  );
}
