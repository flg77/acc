// Evals — promptfoo, garak and MLflow for this deployment (vault proposal 058,
// Phase 4).
//
// promptfoo is the operator's switch (058 Q6: "a container which can be
// enabled by the operator, also from within the webgui"): an ordinary key,
// written here through /api/config/set; the acc-promptfoo container reads it
// every round, so no restart is needed. The garak release gate and the
// guardrails' enforce mode are posture: shown here, changed only through an
// oversight proposal (Governance), never from a browser session.

import { useEffect, useState } from "react";
import {
  Alert,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Label,
  Switch,
} from "@patternfly/react-core";
import { fetchConfiguration, setConfigValue } from "../../api/client";
import type { ConfigEntry } from "../../api/client";
import { useEnvironment } from "../../shell/EnvironmentContext";

const KEYS = [
  "evals.promptfoo_enabled",
  "evals.promptfoo_interval_s",
  "evals.mlflow_tracking_uri",
  "evals.garak_gate",
  "evals.garak_probes",
  "compliance.owasp_enforce",
  "compliance.cat_a_enforce",
];

export function EvalsPage() {
  const { who } = useEnvironment();
  const canAct = who?.role !== "viewer";
  const [entries, setEntries] = useState<Record<string, ConfigEntry>>({});
  const [note, setNote] = useState<{ kind: "info" | "danger"; text: string } | null>(null);

  const load = () =>
    fetchConfiguration()
      .then((r) => setEntries(Object.fromEntries(r.entries.filter((e) => KEYS.includes(e.key)).map((e) => [e.key, e]))))
      .catch((e) => setNote({ kind: "danger", text: String(e) }));
  useEffect(() => {
    load();
  }, []);

  const pf = entries["evals.promptfoo_enabled"];
  const toggle = async (on: boolean) => {
    try {
      const r = await setConfigValue("evals.promptfoo_enabled", on ? "true" : "false");
      setNote({ kind: "info", text: `promptfoo ${on ? "enabled" : "disabled"} — ${r.note ?? "the container reads it on its next round"}` });
      load();
    } catch (e) {
      setNote({ kind: "danger", text: String(e) });
    }
  };

  return (
    <>
      {note && <Alert isInline variant={note.kind} title={note.text} />}
      <Card>
        <CardHeader><CardTitle>promptfoo on the golden prompts</CardTitle></CardHeader>
        <CardBody>
          <Switch
            id="evals-promptfoo"
            label="Run promptfoo"
            isChecked={pf?.value === true || pf?.value === "true"}
            isDisabled={!canAct || !pf?.writable}
            onChange={(_, on) => toggle(on)}
          />
          <p className="hint">
            The acc-promptfoo container (deploy/evals) runs the generated config every{" "}
            {String(entries["evals.promptfoo_interval_s"]?.value ?? 86400)} s while this is on.
            {pf && !pf.writable && pf.write_block_reason ? ` Not writable here: ${pf.write_block_reason}.` : ""}
          </p>
        </CardBody>
      </Card>
      <Card>
        <CardHeader><CardTitle>Posture — changed only through oversight</CardTitle></CardHeader>
        <CardBody>
          <DescriptionList isCompact>
            {["evals.garak_gate", "compliance.owasp_enforce", "compliance.cat_a_enforce", "evals.garak_probes", "evals.mlflow_tracking_uri"].map((k) => (
              <DescriptionListGroup key={k}>
                <DescriptionListTerm>{k}</DescriptionListTerm>
                <DescriptionListDescription>
                  <code>{JSON.stringify(entries[k]?.value ?? null)}</code>{" "}
                  {entries[k]?.posture && <Label color="orange" isCompact>posture</Label>}
                </DescriptionListDescription>
              </DescriptionListGroup>
            ))}
          </DescriptionList>
          <p className="hint">
            The edge-secure profile sets these (acc-cli profile apply edge_secure); from here, a change is a
            proposal in Governance.
          </p>
        </CardBody>
      </Card>
    </>
  );
}
