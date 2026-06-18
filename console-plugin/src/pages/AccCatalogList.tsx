/*
 * AccCatalog list page (proposal 035, PR-2).
 *
 * Columns surface the catalog tier / mode / priority / signer (020 WS-B
 * "catalogs list ... tier/priority/signer"). All fields are from the
 * AccCatalog CRD base (acc.redhat.io_acccatalogs.yaml).
 */
import * as React from 'react';
import {
  ResourceLink,
  RowProps,
  TableData,
  TableColumn,
} from '@openshift-console/dynamic-plugin-sdk';
import { Label } from '@patternfly/react-core';
import { AccCatalogModel } from '../models';
import { AccCatalogKind } from '../types';
import { ResourceListPage } from '../components/list';
import { gvk } from './gvk';

const columns: TableColumn<AccCatalogKind>[] = [
  { title: 'Name', id: 'name', sort: 'metadata.name' },
  { title: 'Catalog ID', id: 'catalogId', sort: 'spec.catalogId' },
  { title: 'Tier', id: 'tier', sort: 'spec.tier' },
  { title: 'Mode', id: 'mode', sort: 'spec.mode' },
  { title: 'Priority', id: 'priority', sort: 'spec.priority' },
  { title: 'Required signer', id: 'signer' },
];

const tierColor = (tier?: string) => {
  switch (tier) {
    case 'trusted':
      return 'green';
    case 'tp':
      return 'blue';
    case 'community':
      return 'gold';
    default:
      return 'grey';
  }
};

const Row: React.FC<RowProps<AccCatalogKind>> = ({ obj, activeColumnIDs }) => {
  const signer = obj.spec?.requiredSigner;
  const signerText = signer?.keyPath
    ? `key: ${signer.keyPath}`
    : signer?.subjectPattern || signer?.issuer || '-';
  return (
    <>
      <TableData id="name" activeColumnIDs={activeColumnIDs}>
        <ResourceLink
          groupVersionKind={gvk(AccCatalogModel)}
          name={obj.metadata?.name}
          namespace={obj.metadata?.namespace}
        />
      </TableData>
      <TableData id="catalogId" activeColumnIDs={activeColumnIDs}>
        {obj.spec?.catalogId || '-'}
      </TableData>
      <TableData id="tier" activeColumnIDs={activeColumnIDs}>
        {obj.spec?.tier ? (
          <Label color={tierColor(obj.spec.tier)} isCompact>
            {obj.spec.tier}
          </Label>
        ) : (
          '-'
        )}
      </TableData>
      <TableData id="mode" activeColumnIDs={activeColumnIDs}>
        {obj.spec?.mode || '-'}
      </TableData>
      <TableData id="priority" activeColumnIDs={activeColumnIDs}>
        {obj.spec?.priority ?? '-'}
      </TableData>
      <TableData id="signer" activeColumnIDs={activeColumnIDs}>
        {signerText}
      </TableData>
    </>
  );
};

const AccCatalogList: React.FC<{ namespace?: string }> = ({ namespace }) => (
  <ResourceListPage<AccCatalogKind>
    model={AccCatalogModel}
    namespace={namespace}
    columns={columns}
    Row={Row}
    helpText="Layered package catalogs resolved by acc-pkg at install time."
  />
);

export default AccCatalogList;
