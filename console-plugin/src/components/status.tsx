/*
 * Shared oversight UI helpers (proposal 035, PR-2).
 *
 * Small, dependency-light building blocks reused by every list + detail page:
 *  - PhaseLabel    : colour-codes a CRD .status.phase
 *  - ConditionsTable: renders metav1.Condition[] (status.conditions)
 *  - DetailItem    : a labelled value cell for the detail DescriptionLists
 *  - LoadingBox / ErrorBox / EmptyBox: uniform watch state surfaces
 *
 * React-17 compatible (the 4.18 console shares React 17 via module federation):
 * no React-18-only APIs, function components only.
 */
import * as React from 'react';
import {
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
  EmptyStateVariant,
  Label,
  Spinner,
} from '@patternfly/react-core';
import { Timestamp } from '@openshift-console/dynamic-plugin-sdk';
import { K8sCondition } from '../types';

type LabelColor = React.ComponentProps<typeof Label>['color'];

/**
 * Map a CRD phase string to a PatternFly Label colour. Covers the phases of
 * all four CRDs (CorpusPhase, CollectivePhase, AccPackageInstall phase).
 */
const phaseColor = (phase?: string): LabelColor => {
  switch (phase) {
    case 'Ready':
    case 'Installed':
      return 'green';
    case 'Progressing':
    case 'Installing':
    case 'Pending':
      return 'blue';
    case 'Degraded':
    case 'UpgradeApprovalPending':
      return 'orange';
    case 'Error':
    case 'Failed':
      return 'red';
    default:
      return 'grey';
  }
};

/** Colour-coded label for a CRD .status.phase (renders a dash when empty). */
export const PhaseLabel: React.FC<{ phase?: string }> = ({ phase }) =>
  phase ? <Label color={phaseColor(phase)}>{phase}</Label> : <>-</>;

/** Green/red True/False/Unknown label used inside the conditions table. */
export const BoolLabel: React.FC<{ value?: boolean; trueText?: string; falseText?: string }> = ({
  value,
  trueText = 'True',
  falseText = 'False',
}) => (
  <Label color={value ? 'green' : 'grey'} isCompact>
    {value ? trueText : falseText}
  </Label>
);

const conditionColor = (status: K8sCondition['status']): LabelColor => {
  switch (status) {
    case 'True':
      return 'green';
    case 'False':
      return 'red';
    default:
      return 'grey';
  }
};

/**
 * Render status.conditions[] as a compact table. Used on every detail page.
 * Falls back to a "No conditions" note when the controller has not written any.
 */
export const ConditionsTable: React.FC<{ conditions?: K8sCondition[] }> = ({ conditions }) => {
  if (!conditions || conditions.length === 0) {
    return <span className="pf-v5-u-color-200">No conditions reported yet.</span>;
  }
  return (
    <table className="pf-v5-c-table pf-m-compact pf-m-grid-md" role="grid" aria-label="Conditions">
      <thead>
        <tr>
          <th>Type</th>
          <th>Status</th>
          <th>Reason</th>
          <th>Updated</th>
          <th>Message</th>
        </tr>
      </thead>
      <tbody>
        {conditions.map((c) => (
          <tr key={c.type}>
            <td>{c.type}</td>
            <td>
              <Label color={conditionColor(c.status)} isCompact>
                {c.status}
              </Label>
            </td>
            <td>{c.reason || '-'}</td>
            <td>{c.lastTransitionTime ? <Timestamp timestamp={c.lastTransitionTime} /> : '-'}</td>
            <td>{c.message || '-'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

/** One labelled value in a detail-page DescriptionList. Hides when empty unless `always`. */
export const DetailItem: React.FC<{
  label: string;
  children?: React.ReactNode;
  always?: boolean;
}> = ({ label, children, always }) => {
  const empty =
    children === undefined ||
    children === null ||
    children === '' ||
    (Array.isArray(children) && children.length === 0);
  if (empty && !always) {
    return null;
  }
  return (
    <DescriptionListGroup>
      <DescriptionListTerm>{label}</DescriptionListTerm>
      <DescriptionListDescription>{empty ? '-' : children}</DescriptionListDescription>
    </DescriptionListGroup>
  );
};

/** Uniform loading spinner for the watch's pre-loaded state. */
export const LoadingBox: React.FC = () => (
  <EmptyState variant={EmptyStateVariant.sm}>
    <Spinner aria-label="Loading" />
    <EmptyStateBody>Loading…</EmptyStateBody>
  </EmptyState>
);

/** Uniform error surface for a watch loadError (e.g. RBAC forbidden). */
export const ErrorBox: React.FC<{ error: unknown }> = ({ error }) => {
  const msg =
    (error as { message?: string })?.message ||
    (typeof error === 'string' ? error : 'Could not load this resource.');
  return (
    <EmptyState variant={EmptyStateVariant.sm}>
      <EmptyStateHeader titleText="Unable to load" headingLevel="h2" />
      <EmptyStateBody>{msg}</EmptyStateBody>
    </EmptyState>
  );
};

/** Uniform empty surface for a successfully-loaded-but-empty list / missing object. */
export const EmptyBox: React.FC<{ title: string; body?: React.ReactNode }> = ({ title, body }) => (
  <EmptyState variant={EmptyStateVariant.sm}>
    <EmptyStateHeader titleText={title} headingLevel="h2" />
    {body ? <EmptyStateBody>{body}</EmptyStateBody> : null}
  </EmptyState>
);
