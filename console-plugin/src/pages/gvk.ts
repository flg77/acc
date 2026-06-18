/*
 * models.ts K8sModel -> K8sGroupVersionKind adapter (proposal 035, PR-2).
 * ResourceLink / ResourceIcon take a {group, version, kind}; our models carry
 * {apiGroup, apiVersion, kind}. One place to convert.
 */
import { K8sGroupVersionKind, K8sModel } from '@openshift-console/dynamic-plugin-sdk';

export const gvk = (model: K8sModel): K8sGroupVersionKind => ({
  group: model.apiGroup,
  version: model.apiVersion,
  kind: model.kind,
});
