// Where this surface runs — always on screen (design-system/shell/environment-bar.html).
//
// Every fact comes from /api/environment and /api/whoami; nothing is guessed
// here. A pod that could not find something out says so in the bar instead of
// leaving the person to work it out from what is missing below.

import { Banner } from "@patternfly/react-core";
import type { Environment, Whoami } from "../api/client";

export function EnvironmentBar({
  env,
  who,
  connected,
}: {
  env: Environment | null;
  who: Whoami | null;
  connected: boolean;
}) {
  if (!env) {
    return (
      <Banner status="warning" isSticky screenReaderText="Environment" role="region" aria-label="Environment">
        <div className="acc-envbar">
          <span className="acc-envbar__kind">Environment unknown</span>
          <span>GET /api/environment did not answer — controls are shown as they are, not as they should be.</span>
        </div>
      </Banner>
    );
  }
  const cluster = env.cluster;
  const viewer = who?.role === "viewer";
  return (
    <Banner color={cluster ? "blue" : "teal"} isSticky screenReaderText="Environment" role="region" aria-label="Environment">
      <div className="acc-envbar">
        <span className="acc-envbar__kind">{cluster ? "Cluster" : "Standalone"}</span>
        {cluster && env.namespace && (
          <Fact label="project" value={env.namespace} />
        )}
        {env.corpus && <Fact label="corpus" value={env.corpus} />}
        {env.runtime && <Fact label="runtime" value={env.runtime} />}
        <span className="acc-envbar__fact" title={`detected by ${env.detected_by}`}>
          <span>{connected ? "live" : "connecting…"}</span>
        </span>
        {who && (
          <span className="acc-envbar__person">
            {who.user} · <strong>{who.role}</strong>
            {viewer && " — read-only"}
          </span>
        )}
      </div>
    </Banner>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <span className="acc-envbar__fact">
      <span>{label}</span>
      <code>{value}</code>
    </span>
  );
}
