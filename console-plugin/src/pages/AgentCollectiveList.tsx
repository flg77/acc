/*
 * AgentCollective list page (proposal 035, PR-2).
 *
 * Columns: collective ID / LLM backend / phase / ready agents (matches the CRD
 * printer columns, plus a roster summary). Fields from
 * acc.redhat.io_agentcollectives.yaml.
 */
import * as React from 'react';
import {
  ResourceLink,
  RowProps,
  TableData,
  TableColumn,
} from '@openshift-console/dynamic-plugin-sdk';
import { AgentCollectiveModel } from '../models';
import { AgentCollectiveKind } from '../types';
import { ResourceListPage } from '../components/list';
import { PhaseLabel } from '../components/status';
import { gvk } from './gvk';

const columns: TableColumn<AgentCollectiveKind>[] = [
  { title: 'Name', id: 'name', sort: 'metadata.name' },
  { title: 'Collective ID', id: 'collectiveId', sort: 'spec.collectiveId' },
  { title: 'LLM', id: 'llm', sort: 'spec.llm.backend' },
  { title: 'Phase', id: 'phase', sort: 'status.phase' },
  { title: 'Agents', id: 'agents' },
];

const readyTotal = (m?: { [role: string]: number }): number =>
  Object.values(m || {}).reduce((a, b) => a + b, 0);

const AgentCollectiveRow: React.FC<RowProps<AgentCollectiveKind>> = ({ obj, activeColumnIDs }) => {
  const ready = readyTotal(obj.status?.readyAgents);
  const desired = readyTotal(obj.status?.desiredAgents);
  const roster = obj.spec?.agents?.length ?? 0;
  return (
    <>
      <TableData id="name" activeColumnIDs={activeColumnIDs}>
        <ResourceLink
          groupVersionKind={gvk(AgentCollectiveModel)}
          name={obj.metadata?.name}
          namespace={obj.metadata?.namespace}
        />
      </TableData>
      <TableData id="collectiveId" activeColumnIDs={activeColumnIDs}>
        {obj.spec?.collectiveId || '-'}
      </TableData>
      <TableData id="llm" activeColumnIDs={activeColumnIDs}>
        {obj.spec?.llm?.backend || '-'}
      </TableData>
      <TableData id="phase" activeColumnIDs={activeColumnIDs}>
        <PhaseLabel phase={obj.status?.phase} />
      </TableData>
      <TableData id="agents" activeColumnIDs={activeColumnIDs}>
        {`${ready}/${desired || roster} ready`}
      </TableData>
    </>
  );
};

const AgentCollectiveList: React.FC<{ namespace?: string }> = ({ namespace }) => (
  <ResourceListPage<AgentCollectiveKind>
    model={AgentCollectiveModel}
    namespace={namespace}
    columns={columns}
    Row={AgentCollectiveRow}
    helpText="A single collective of role-specialized agents within a corpus."
  />
);

export default AgentCollectiveList;
