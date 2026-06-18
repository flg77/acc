/*
 * AgentCorpus detail page (proposal 035, PR-2).
 *
 * The "complete picture" oversight surface (035 G2): phase + version,
 * infrastructure readiness, the per-collective status table (phase, ready/
 * desired agents, and the SharedModel binding from 026 G1), discovered RHOAI
 * models, the interaction-surface URLs, and status conditions. Every field is
 * from acc.redhat.io_agentcorpora.yaml.
 */
import * as React from 'react';
import { Button, DescriptionList } from '@patternfly/react-core';
import { ResourceLink } from '@openshift-console/dynamic-plugin-sdk';
import { AgentCollectiveModel, AgentCorpusModel } from '../models';
import { AgentCorpusKind, CorpusCollectiveStatus, RHOAIModelRef, SharedModelStatus } from '../types';
import { DetailPage, SectionCard, SectionGrid } from '../components/detail';
import { BoolLabel, ConditionsTable, DetailItem, PhaseLabel } from '../components/status';
import { gvk } from './gvk';

const agentSummary = (m?: { [role: string]: number }): string => {
  const entries = Object.entries(m || {});
  if (!entries.length) {
    return '-';
  }
  return entries.map(([role, n]) => `${role}: ${n}`).join(', ');
};

/** Render the SharedModel binding (026 G1): shows "shared from <ns>" when cross-namespace. */
const SharedModelCell: React.FC<{ shared?: SharedModelStatus; ownNs?: string }> = ({
  shared,
  ownNs,
}) => {
  if (!shared || !shared.inferenceService) {
    return <>-</>;
  }
  const ns = shared.namespace;
  const isShared = shared.shared ?? (!!ns && ns !== ownNs);
  return (
    <span>
      {ns ? (
        <ResourceLink
          groupVersionKind={{ group: 'serving.kserve.io', version: 'v1beta1', kind: 'InferenceService' }}
          name={shared.inferenceService}
          namespace={ns}
          inline
        />
      ) : (
        shared.inferenceService
      )}
      {isShared && ns ? ` (shared from ${ns})` : ''}
    </span>
  );
};

const CollectivesTable: React.FC<{
  statuses?: { [name: string]: CorpusCollectiveStatus };
  namespace?: string;
}> = ({ statuses, namespace }) => {
  const entries = Object.entries(statuses || {});
  if (!entries.length) {
    return <span className="pf-v5-u-color-200">No collective status reported yet.</span>;
  }
  return (
    <table className="pf-v5-c-table pf-m-compact pf-m-grid-md" role="grid" aria-label="Collectives">
      <thead>
        <tr>
          <th>Collective</th>
          <th>Phase</th>
          <th>Ready agents</th>
          <th>Desired agents</th>
          <th>Shared model</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([cname, cs]) => (
          <tr key={cname}>
            <td>
              <ResourceLink
                groupVersionKind={gvk(AgentCollectiveModel)}
                name={cname}
                namespace={namespace}
              />
            </td>
            <td>
              <PhaseLabel phase={cs.phase} />
            </td>
            <td>{agentSummary(cs.readyAgents)}</td>
            <td>{agentSummary(cs.desiredAgents)}</td>
            <td>
              <SharedModelCell shared={cs.sharedModel} ownNs={namespace} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

const RHOAIModelsTable: React.FC<{ models?: RHOAIModelRef[] }> = ({ models }) => {
  if (!models || !models.length) {
    return <span className="pf-v5-u-color-200">No ready RHOAI models discovered.</span>;
  }
  return (
    <table className="pf-v5-c-table pf-m-compact pf-m-grid-md" role="grid" aria-label="RHOAI models">
      <thead>
        <tr>
          <th>InferenceService</th>
          <th>Namespace</th>
          <th>URL</th>
        </tr>
      </thead>
      <tbody>
        {models.map((m) => (
          <tr key={`${m.namespace}/${m.name}`}>
            <td>{m.name}</td>
            <td>{m.namespace}</td>
            <td>{m.url || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

const ExternalLink: React.FC<{ href?: string }> = ({ href }) =>
  href ? (
    <Button
      variant="link"
      isInline
      component="a"
      href={href}
      target="_blank"
      rel="noopener noreferrer"
    >
      {href}
    </Button>
  ) : (
    <></>
  );

const AgentCorpusDetails: React.FC<{ name?: string; namespace?: string }> = ({
  name,
  namespace,
}) => (
  <DetailPage<AgentCorpusKind> model={AgentCorpusModel} name={name} namespace={namespace}>
    {(obj) => {
      const infra = obj.status?.infrastructure;
      return (
        <SectionGrid>
          <SectionCard title="Overview">
            <DescriptionList isHorizontal>
              <DetailItem label="Phase" always>
                <PhaseLabel phase={obj.status?.phase} />
              </DetailItem>
              <DetailItem label="Deploy mode">{obj.spec?.deployMode}</DetailItem>
              <DetailItem label="Version" always>
                {obj.status?.currentVersion || obj.spec?.version}
              </DetailItem>
              <DetailItem label="Pending upgrade">{obj.status?.pendingUpgradeVersion}</DetailItem>
              <DetailItem label="RHOAI project registered" always>
                <BoolLabel value={obj.status?.rhoaiProjectRegistered} />
              </DetailItem>
              <DetailItem label="Default catalog bootstrapped" always>
                <BoolLabel value={obj.status?.defaultCatalogBootstrapped} />
              </DetailItem>
            </DescriptionList>
          </SectionCard>

          <SectionCard title="Infrastructure">
            <DescriptionList isHorizontal>
              <DetailItem label="NATS" always>
                <BoolLabel value={infra?.natsReady} trueText="Ready" falseText="Not ready" />
                {infra?.natsVersion ? ` (${infra.natsVersion})` : ''}
              </DetailItem>
              <DetailItem label="Redis" always>
                <BoolLabel value={infra?.redisReady} trueText="Ready" falseText="Not ready" />
                {infra?.redisVersion ? ` (${infra.redisVersion})` : ''}
              </DetailItem>
              <DetailItem label="Milvus" always>
                <BoolLabel value={infra?.milvusConnected} trueText="Connected" falseText="No" />
              </DetailItem>
              <DetailItem label="OPA bundle" always>
                <BoolLabel value={infra?.opaBundleReady} trueText="Ready" falseText="Not ready" />
              </DetailItem>
              <DetailItem label="OTel collector" always>
                <BoolLabel value={infra?.otelCollectorReady} trueText="Ready" falseText="Not ready" />
              </DetailItem>
              <DetailItem label="Kafka bridge" always>
                <BoolLabel value={obj.status?.kafkaBridgeReady} trueText="Ready" falseText="No" />
              </DetailItem>
            </DescriptionList>
          </SectionCard>

          <SectionCard title="Interaction surfaces">
            <DescriptionList isHorizontal>
              <DetailItem label="WebGUI deployed" always>
                <BoolLabel value={obj.status?.webguiDeployed} />
              </DetailItem>
              <DetailItem label="WebGUI URL">
                <ExternalLink href={obj.status?.webguiURL} />
              </DetailItem>
              <DetailItem label="TUI deployed" always>
                <BoolLabel value={obj.status?.tuiDeployed} />
              </DetailItem>
              <DetailItem label="TUI URL">
                <ExternalLink href={obj.status?.tuiURL} />
              </DetailItem>
            </DescriptionList>
          </SectionCard>

          <SectionCard title="Collectives">
            <CollectivesTable
              statuses={obj.status?.collectiveStatuses}
              namespace={obj.metadata?.namespace}
            />
          </SectionCard>

          <SectionCard title="Discovered RHOAI models">
            <RHOAIModelsTable models={obj.status?.availableRHOAIModels} />
          </SectionCard>

          <SectionCard title="Conditions">
            <ConditionsTable conditions={obj.status?.conditions} />
          </SectionCard>
        </SectionGrid>
      );
    }}
  </DetailPage>
);

export default AgentCorpusDetails;
