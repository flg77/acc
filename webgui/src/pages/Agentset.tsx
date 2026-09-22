// The Agentset page (KW-03): what runs, and what should run.
//
// Declared (collective.yaml on the edge, the AgentCollective in a cluster)
// beside what the bus says is running. The rule that decides each row's state
// lives once, in Python (acc.deployment.compare) — this page only draws it, in
// the vocabulary of webgui/design-system/patterns/declared-observed.html.
//
// Read-only. The action below is the gated-action pattern: it says why it is
// not available instead of being missing or failing.

import { useEffect, useState } from "react";
import { Alert, Button, EmptyState, EmptyStateBody, Title } from "@patternfly/react-core";
import { fetchAgentset } from "../api/client";
import type { Agentset, AgentRow, AgentState } from "../api/client";
import { useSnapshot } from "../state/snapshot";
import { unavailableReason, useEnvironment } from "../shell/EnvironmentContext";

const STATE_LABEL: Record<AgentState, string> = {
  converged: "Converged",
  converging: "Converging",
  awaiting: "Awaiting pack",
  drift: "Drift",
  missing: "Not on the bus",
  unknown: "Unknown",
};

function StateChip({ state }: { state: AgentState }) {
  return <span className={`acc-state is-${state}`}>{STATE_LABEL[state]}</span>;
}

/** Two values of one fact: equal → shown once; different → both, declared first. */
function DeclaredObserved({
  declared,
  observed,
  observedKnown,
  labelObserved = "running",
}: {
  declared: string;
  observed: string[];
  observedKnown: boolean;
  labelObserved?: string;
}) {
  const shown = observed.join(", ");
  if (!observedKnown || (declared && !shown)) {
    return <span className="acc-do is-same"><code>{declared || "—"}</code></span>;
  }
  if (!declared) {
    return <span className="acc-do is-same"><code>{shown || "—"}</code></span>;
  }
  if (observed.includes(declared) && observed.length === 1) {
    return <span className="acc-do is-same"><code>{declared}</code></span>;
  }
  return (
    <span className="acc-do is-drift">
      <span className="acc-do__label">declared</span>
      <code>{declared}</code>
      <span className="acc-do__label">{labelObserved}</span>
      <code className="acc-do__observed">{shown}</code>
    </span>
  );
}

function Replicas({ row, bus }: { row: AgentRow; bus: boolean }) {
  if (!bus || row.replicas_running === row.replicas_declared) {
    return <span>{row.replicas_declared}</span>;
  }
  return (
    <span className="acc-do is-drift">
      <span className="acc-do__label">declared</span>
      <span>{row.replicas_declared}</span>
      <span className="acc-do__label">running</span>
      <span className="acc-do__observed">{row.replicas_running}</span>
    </span>
  );
}

const age = (readAt: number) => Math.max(0, Math.round(Date.now() / 1000 - readAt));

export function AgentsetPage() {
  const { collectiveId } = useSnapshot();
  const { env, who } = useEnvironment();
  const [data, setData] = useState<Agentset | null>(null);
  const [error, setError] = useState("");
  const [, tick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetchAgentset(collectiveId)
        .then((d) => alive && (setData(d), setError("")))
        .catch((e) => alive && setError(String(e)));
    load();
    const poll = setInterval(load, 5000);
    const clock = setInterval(() => tick((n) => n + 1), 1000);
    return () => {
      alive = false;
      clearInterval(poll);
      clearInterval(clock);
    };
  }, [collectiveId]);

  const reason = unavailableReason(env, "agentset.write");
  const canAsk = who?.role !== "viewer";

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">
        Agentset
      </Title>
      {data && (
        <p className="acc-source">
          Declared in <code>{data.declared_in || "—"}</code>
          {data.version && <> · corpus {data.version}</>} · read {age(data.read_at)} s ago ·{" "}
          {data.bus ? "running: from the bus" : "running: no bus connection yet"}
        </p>
      )}

      {error && (
        <Alert isInline variant="danger" title="The agentset could not be read" component="p">
          {error}
        </Alert>
      )}
      {data?.errors.map((e) => (
        <Alert key={e} isInline variant="danger" title="Could not read a declaration" component="p">
          {e}
        </Alert>
      ))}
      {!data && !error && <p className="acc-source">Reading…</p>}

      {data && data.rows.length === 0 && data.errors.length === 0 && (
        <EmptyState titleText="No agents declared" headingLevel="h2" variant="sm">
          <EmptyStateBody>
            Nothing this deployment can read declares an agent — see the line above for where
            it looked.
            {data.undeclared.length > 0 &&
              " The agents listed below run here without being declared there."}
          </EmptyStateBody>
        </EmptyState>
      )}

      {data && data.rows.length > 0 && (
        <table
          className="pf-v6-c-table pf-m-compact pf-m-grid-md"
          role="grid"
          aria-label="Agentset: declared beside running"
        >
          <thead className="pf-v6-c-table__thead">
            <tr className="pf-v6-c-table__tr" role="row">
              <th className="pf-v6-c-table__th" role="columnheader" scope="col">Role</th>
              <th className="pf-v6-c-table__th" role="columnheader" scope="col">Replicas</th>
              <th className="pf-v6-c-table__th" role="columnheader" scope="col">Model</th>
              <th className="pf-v6-c-table__th" role="columnheader" scope="col">State</th>
            </tr>
          </thead>
          <tbody className="pf-v6-c-table__tbody" role="rowgroup">
            {data.rows.map((r) => (
              <tr className="pf-v6-c-table__tr" role="row" key={`${r.collective}/${r.role}`}>
                <td className="pf-v6-c-table__td" role="cell" data-label="Role">
                  <code>{r.role}</code>
                </td>
                <td className="pf-v6-c-table__td" role="cell" data-label="Replicas">
                  <Replicas row={r} bus={data.bus} />
                </td>
                <td className="pf-v6-c-table__td" role="cell" data-label="Model">
                  <DeclaredObserved
                    declared={r.model_declared}
                    observed={r.models_running}
                    observedKnown={data.bus}
                  />
                </td>
                <td className="pf-v6-c-table__td" role="cell" data-label="State">
                  <StateChip state={r.state} />
                  {r.reason && <span className="acc-reason">{r.reason}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data && data.undeclared.length > 0 && (
        <>
          <Title headingLevel="h2" size="lg">
            Running, declared nowhere this deployment can read
          </Title>
          <table
            className="pf-v6-c-table pf-m-compact pf-m-grid-md"
            role="grid"
            aria-label="Running without a declaration"
          >
            <thead className="pf-v6-c-table__thead">
              <tr className="pf-v6-c-table__tr" role="row">
                <th className="pf-v6-c-table__th" role="columnheader" scope="col">Role</th>
                <th className="pf-v6-c-table__th" role="columnheader" scope="col">Running</th>
                <th className="pf-v6-c-table__th" role="columnheader" scope="col">Model</th>
              </tr>
            </thead>
            <tbody className="pf-v6-c-table__tbody" role="rowgroup">
              {data.undeclared.map((u) => (
                <tr className="pf-v6-c-table__tr" role="row" key={u.role}>
                  <td className="pf-v6-c-table__td" role="cell" data-label="Role"><code>{u.role}</code></td>
                  <td className="pf-v6-c-table__td" role="cell" data-label="Running">{u.replicas_running}</td>
                  <td className="pf-v6-c-table__td" role="cell" data-label="Model">
                    <code>{u.models_running.join(", ") || "—"}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {data && data.packages.length > 0 && (
        <>
          <Title headingLevel="h2" size="lg">
            Packages
          </Title>
          <table
            className="pf-v6-c-table pf-m-compact pf-m-grid-md"
            role="grid"
            aria-label="Packages"
          >
            <thead className="pf-v6-c-table__thead">
              <tr className="pf-v6-c-table__tr" role="row">
                <th className="pf-v6-c-table__th" role="columnheader" scope="col">Package</th>
                <th className="pf-v6-c-table__th" role="columnheader" scope="col">Declared</th>
                <th className="pf-v6-c-table__th" role="columnheader" scope="col">State</th>
              </tr>
            </thead>
            <tbody className="pf-v6-c-table__tbody" role="rowgroup">
              {data.packages.map((p) => (
                <tr className="pf-v6-c-table__tr" role="row" key={p.name}>
                  <td className="pf-v6-c-table__td" role="cell" data-label="Package"><code>{p.name}</code></td>
                  <td className="pf-v6-c-table__td" role="cell" data-label="Declared">
                    <DeclaredObserved
                      declared={p.constraint}
                      observed={p.installed ? [p.installed] : []}
                      observedKnown
                      labelObserved="installed"
                    />
                  </td>
                  <td className="pf-v6-c-table__td" role="cell" data-label="State">
                    <span className={`acc-state is-${p.phase === "Installed" ? "converged" : "awaiting"}`}>
                      {p.phase || "Unknown"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* The gated-action pattern: unavailable → disabled, with the reason as text. */}
      {data && (
        <div className="acc-gated">
          <Button variant="primary" isAriaDisabled>
            Add agent
          </Button>
          <span className="acc-gated__reason">
            {!canAsk
              ? "You are a viewer — the environment bar says why controls are missing."
              : reason ||
                "The WebGUI cannot change the agentset yet (KW-15). Change collective.yaml and run ./acc-deploy.sh apply."}
          </span>
        </div>
      )}
    </div>
  );
}
