/*
 * AccPackageInstall list page (proposal 035, PR-2).
 *
 * Columns: package name / constraint / phase / installed version (matches the
 * CRD's printer columns) plus targetCorpus. Fields from
 * acc.redhat.io_accpackageinstalls.yaml.
 */
import * as React from 'react';
import {
  ResourceLink,
  RowProps,
  TableData,
  TableColumn,
} from '@openshift-console/dynamic-plugin-sdk';
import { AccPackageInstallModel } from '../models';
import { AccPackageInstallKind } from '../types';
import { ResourceListPage } from '../components/list';
import { PhaseLabel } from '../components/status';
import { gvk } from './gvk';

const columns: TableColumn<AccPackageInstallKind>[] = [
  { title: 'Name', id: 'name', sort: 'metadata.name' },
  { title: 'Package', id: 'package', sort: 'spec.name' },
  { title: 'Constraint', id: 'constraint', sort: 'spec.constraint' },
  { title: 'Phase', id: 'phase', sort: 'status.phase' },
  { title: 'Installed version', id: 'installedVersion', sort: 'status.installedVersion' },
  { title: 'Target corpus', id: 'targetCorpus', sort: 'spec.targetCorpus' },
];

const Row: React.FC<RowProps<AccPackageInstallKind>> = ({ obj, activeColumnIDs }) => (
  <>
    <TableData id="name" activeColumnIDs={activeColumnIDs}>
      <ResourceLink
        groupVersionKind={gvk(AccPackageInstallModel)}
        name={obj.metadata?.name}
        namespace={obj.metadata?.namespace}
      />
    </TableData>
    <TableData id="package" activeColumnIDs={activeColumnIDs}>
      {obj.spec?.name || '-'}
    </TableData>
    <TableData id="constraint" activeColumnIDs={activeColumnIDs}>
      {obj.spec?.constraint || 'latest'}
    </TableData>
    <TableData id="phase" activeColumnIDs={activeColumnIDs}>
      <PhaseLabel phase={obj.status?.phase} />
    </TableData>
    <TableData id="installedVersion" activeColumnIDs={activeColumnIDs}>
      {obj.status?.installedVersion || '-'}
    </TableData>
    <TableData id="targetCorpus" activeColumnIDs={activeColumnIDs}>
      {obj.spec?.targetCorpus || 'all'}
    </TableData>
  </>
);

const AccPackageInstallList: React.FC<{ namespace?: string }> = ({ namespace }) => (
  <ResourceListPage<AccPackageInstallKind>
    model={AccPackageInstallModel}
    namespace={namespace}
    columns={columns}
    Row={Row}
    helpText="Signed role packages the operator installs onto agent pods."
  />
);

export default AccPackageInstallList;
