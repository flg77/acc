/*
 * AgentCorpus list page (proposal 035, PR-2).
 *
 * Columns: deploy mode / version / phase / collective count (matches the CRD
 * printer columns). Fields from acc.redhat.io_agentcorpora.yaml.
 */
import * as React from 'react';
import {
  ResourceLink,
  RowProps,
  TableData,
  TableColumn,
} from '@openshift-console/dynamic-plugin-sdk';
import { AgentCorpusModel } from '../models';
import { AgentCorpusKind } from '../types';
import { ResourceListPage } from '../components/list';
import { PhaseLabel } from '../components/status';
import { gvk } from './gvk';

const columns: TableColumn<AgentCorpusKind>[] = [
  { title: 'Name', id: 'name', sort: 'metadata.name' },
  { title: 'Mode', id: 'mode', sort: 'spec.deployMode' },
  { title: 'Version', id: 'version', sort: 'spec.version' },
  { title: 'Phase', id: 'phase', sort: 'status.phase' },
  { title: 'Collectives', id: 'collectives' },
];

const AgentCorpusRow: React.FC<RowProps<AgentCorpusKind>> = ({ obj, activeColumnIDs }) => (
  <>
    <TableData id="name" activeColumnIDs={activeColumnIDs}>
      <ResourceLink
        groupVersionKind={gvk(AgentCorpusModel)}
        name={obj.metadata?.name}
        namespace={obj.metadata?.namespace}
      />
    </TableData>
    <TableData id="mode" activeColumnIDs={activeColumnIDs}>
      {obj.spec?.deployMode || '-'}
    </TableData>
    <TableData id="version" activeColumnIDs={activeColumnIDs}>
      {obj.status?.currentVersion || obj.spec?.version || '-'}
    </TableData>
    <TableData id="phase" activeColumnIDs={activeColumnIDs}>
      <PhaseLabel phase={obj.status?.phase} />
    </TableData>
    <TableData id="collectives" activeColumnIDs={activeColumnIDs}>
      {obj.spec?.collectives?.length ?? 0}
    </TableData>
  </>
);

const AgentCorpusList: React.FC<{ namespace?: string }> = ({ namespace }) => (
  <ResourceListPage<AgentCorpusKind>
    model={AgentCorpusModel}
    namespace={namespace}
    columns={columns}
    Row={AgentCorpusRow}
    helpText="A full Agentic Cell Corpus deployment: infrastructure, collectives, and governance."
  />
);

export default AgentCorpusList;
