/*
 * Shared list-page factory (proposal 035, PR-2).
 *
 * All four CR list pages share the same shape: a header, a name/label filter,
 * and a VirtualizedTable fed by a live useK8sWatchResource list watch (the
 * logged-in user's token -> per-user RBAC is automatic). Each page supplies its
 * own columns + Row; this factory owns the watch + filter + table wiring.
 */
import * as React from 'react';
import {
  K8sModel,
  K8sResourceCommon,
  ListPageBody,
  ListPageFilter,
  ListPageHeader,
  RowProps,
  TableColumn,
  useK8sWatchResource,
  useListPageFilter,
  VirtualizedTable,
} from '@openshift-console/dynamic-plugin-sdk';
import { ErrorBox } from './status';
import { gvk } from '../pages/gvk';

export interface ResourceListPageProps<T extends K8sResourceCommon> {
  /** The K8sModel for the watched resource (from models.ts). */
  model: K8sModel;
  /** Namespace injected by the console for namespaced resource list pages. */
  namespace?: string;
  /** Column definitions (id + title; sort optional). */
  columns: TableColumn<T>[];
  /** Row renderer for one resource. */
  Row: React.FC<RowProps<T>>;
  /** Optional override for the page title (defaults to model.labelPlural). */
  title?: string;
  /** Optional help text under the title. */
  helpText?: React.ReactNode;
}

/**
 * Generic namespaced resource list page. Watches all objects of one kind in the
 * active namespace and renders a filtered VirtualizedTable.
 */
export function ResourceListPage<T extends K8sResourceCommon>({
  model,
  namespace,
  columns,
  Row,
  title,
  helpText,
}: ResourceListPageProps<T>): JSX.Element {
  const [data, loaded, loadError] = useK8sWatchResource<T[]>({
    groupVersionKind: gvk(model),
    namespace,
    namespaced: !!namespace,
    isList: true,
  });

  const [staticData, filteredData, onFilterChange] = useListPageFilter(data);

  return (
    <>
      <ListPageHeader title={title || model.labelPlural} helpText={helpText} />
      <ListPageBody>
        {loadError ? (
          <ErrorBox error={loadError} />
        ) : (
          <>
            <ListPageFilter
              data={staticData}
              loaded={loaded}
              onFilterChange={onFilterChange}
              hideLabelFilter
            />
            <VirtualizedTable<T>
              data={filteredData}
              unfilteredData={staticData}
              loaded={loaded}
              loadError={loadError}
              columns={columns}
              Row={Row}
            />
          </>
        )}
      </ListPageBody>
    </>
  );
}
