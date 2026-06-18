/*
 * AgentCollective detail page (proposal 035, PR-2).
 *
 * Surfaces (035 step 2.3) the agent roster + per-agent model binding +
 * SharedModel:
 *  - Roster: each spec.agents[] role with its replicas, cross-referenced
 *    against status.readyAgents / status.desiredAgents per role.
 *  - Model binding: spec.llm.backend + the per-backend target. For the KServe
 *    (vllm) backend, the inferenceServiceRef + inferenceServiceNamespace is the
 *    collective's own declaration of a (possibly cross-namespace / shared)
 *    model -- the in-CRD source for the SharedModel view at collective scope
 *    (026 G1; the cross-namespace roll-up also appears on the owning
 *    AgentCorpus.status.collectiveStatuses[*].sharedModel).
 * Every field is from acc.redhat.io_agentcollectives.yaml.
 */
import * as React from 'react';
import { DescriptionList, Label } from '@patternfly/react-core';
import { ResourceLink } from '@openshift-console/dynamic-plugin-sdk';
import { AgentCollectiveModel, AgentCorpusModel } from '../models';
import { AgentCollectiveKind, LLMSpec } from '../types';
import { DetailPage, SectionCard, SectionGrid } from '../components/detail';
import { BoolLabel, ConditionsTable, DetailItem, PhaseLabel } from '../components/status';
import { gvk } from './gvk';

/** Roster: union of declared roles (spec.agents) and observed roles (status maps). */
const RosterTable: React.FC<{ obj: AgentCollectiveKind }> = ({ obj }) => {
  const declared = obj.spec?.agents || [];
  const ready = obj.status?.readyAgents || {};
  const desired = obj.status?.desiredAgents || {};
  const roles = Array.from(
    new Set([
      ...declared.map((a) => a.role),
      ...Object.keys(ready),
      ...Object.keys(desired),
    ]),
  ).sort();

  if (!roles.length) {
    return <span className="pf-v5-u-color-200">No agents declared.</span>;
  }

  const replicasOf = (role: string): React.ReactNode => {
    const spec = declared.find((a) => a.role === role);
    return spec?.replicas ?? (spec ? 1 : '-');
  };

  return (
    <table className="pf-v5-c-table pf-m-compact pf-m-grid-md" role="grid" aria-label="Agent roster">
      <thead>
        <tr>
          <th>Role</th>
          <th>Replicas (spec)</th>
          <th>Desired</th>
          <th>Ready</th>
        </tr>
      </thead>
      <tbody>
        {roles.map((role) => (
          <tr key={role}>
            <td>
              <Label isCompact color="blue">
                {role}
              </Label>
            </td>
            <td>{replicasOf(role)}</td>
            <td>{desired[role] ?? '-'}</td>
            <td>{ready[role] ?? 0}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

/** Model binding (spec.llm) -- shows the backend and its concrete target. */
const ModelBinding: React.FC<{ llm?: LLMSpec; ownNs?: string }> = ({ llm, ownNs }) => {
  if (!llm) {
    return <span className="pf-v5-u-color-200">No LLM configured.</span>;
  }
  const rows: React.ReactNode[] = [
    <DetailItem key="backend" label="Backend" always>
      <Label isCompact color="purple">
        {llm.backend}
      </Label>
    </DetailItem>,
  ];

  if (llm.backend === 'vllm' && llm.vllm) {
    const ns = llm.vllm.inferenceServiceNamespace;
    const isShared = !!ns && ns !== ownNs;
    rows.push(
      <DetailItem key="isvc" label="InferenceService" always>
        {llm.vllm.inferenceServiceRef ? (
          <ResourceLink
            groupVersionKind={{
              group: 'serving.kserve.io',
              version: 'v1beta1',
              kind: 'InferenceService',
            }}
            name={llm.vllm.inferenceServiceRef}
            namespace={ns || ownNs}
            inline
          />
        ) : (
          '-'
        )}
        {isShared ? ` (shared from ${ns})` : ''}
      </DetailItem>,
      <DetailItem key="model" label="Model">
        {llm.vllm.model}
      </DetailItem>,
      <DetailItem key="deploy" label="Operator-deployed" always>
        <BoolLabel value={llm.vllm.deploy} />
      </DetailItem>,
    );
  } else if (llm.backend === 'anthropic' && llm.anthropic) {
    rows.push(
      <DetailItem key="model" label="Model" always>
        {llm.anthropic.model}
      </DetailItem>,
    );
  } else if (llm.backend === 'ollama' && llm.ollama) {
    rows.push(
      <DetailItem key="model" label="Model" always>
        {llm.ollama.model}
      </DetailItem>,
      <DetailItem key="url" label="Base URL">
        {llm.ollama.baseUrl}
      </DetailItem>,
    );
  } else if (llm.backend === 'llama_stack' && llm.llamaStack) {
    rows.push(
      <DetailItem key="model" label="Model ID" always>
        {llm.llamaStack.modelId}
      </DetailItem>,
      <DetailItem key="url" label="Base URL">
        {llm.llamaStack.baseUrl}
      </DetailItem>,
    );
  }

  rows.push(
    <DetailItem key="embed" label="Embedding model">
      {llm.embeddingModel}
    </DetailItem>,
  );

  return <DescriptionList isHorizontal>{rows}</DescriptionList>;
};

const AgentCollectiveDetails: React.FC<{ name?: string; namespace?: string }> = ({
  name,
  namespace,
}) => (
  <DetailPage<AgentCollectiveKind>
    model={AgentCollectiveModel}
    name={name}
    namespace={namespace}
  >
    {(obj) => (
      <SectionGrid>
        <SectionCard title="Overview">
          <DescriptionList isHorizontal>
            <DetailItem label="Phase" always>
              <PhaseLabel phase={obj.status?.phase} />
            </DetailItem>
            <DetailItem label="Collective ID" always>
              {obj.spec?.collectiveId}
            </DetailItem>
            <DetailItem label="Corpus">
              {obj.spec?.corpusRef?.name ? (
                <ResourceLink
                  groupVersionKind={gvk(AgentCorpusModel)}
                  name={obj.spec.corpusRef.name}
                  namespace={obj.metadata?.namespace}
                />
              ) : undefined}
            </DetailItem>
            <DetailItem label="KServe ready" always>
              <BoolLabel value={obj.status?.kserveReady} />
            </DetailItem>
            <DetailItem label="Autoscaling active" always>
              <BoolLabel value={obj.status?.scaledObjectsActive} />
            </DetailItem>
            <DetailItem label="SPIFFE ID">{obj.status?.spiffeID}</DetailItem>
          </DescriptionList>
        </SectionCard>

        <SectionCard title="Model binding">
          <ModelBinding llm={obj.spec?.llm} ownNs={obj.metadata?.namespace} />
        </SectionCard>

        <SectionCard title="Agent roster">
          <RosterTable obj={obj} />
        </SectionCard>

        <SectionCard title="Conditions">
          <ConditionsTable conditions={obj.status?.conditions} />
        </SectionCard>
      </SectionGrid>
    )}
  </DetailPage>
);

export default AgentCollectiveDetails;
