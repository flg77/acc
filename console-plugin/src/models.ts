/*
 * K8sModels for the Agentic Cell Corpus CRs (proposal 035, PR-1).
 *
 * These four models are the single source the console pages, watches, and
 * k8sCreate calls bind to. They MUST stay in lockstep with the operator's
 * CRD bases at operator/config/crd/bases/acc.redhat.io_*.yaml.
 *
 * DRIFT GUARD: tests/test_console_plugin_models_parity.py loads every CRD
 * base, extracts (group, version, kind, plural), and asserts a matching
 * MODELS entry exists here and vice-versa. A renamed/added/removed kind or a
 * mistyped plural turns CI red instead of producing a silent empty list in
 * front of the operator (proposal 035 G4 / 020 risk). If you change a model
 * below, the CRD base is authoritative — match it exactly.
 *
 * Ground truth (operator/config/crd/bases, controller-gen v0.16.1):
 *   group:   acc.redhat.io
 *   version: v1alpha1
 *   scope:   Namespaced
 *   kind / plural:
 *     AgentCorpus       / agentcorpora
 *     AgentCollective   / agentcollectives
 *     AccCatalog        / acccatalogs
 *     AccPackageInstall / accpackageinstalls
 */
import { K8sModel } from '@openshift-console/dynamic-plugin-sdk';

/** API group shared by all four ACC CRDs. */
export const ACC_GROUP = 'acc.redhat.io';

/** Served/stored version of all four ACC CRDs. */
export const ACC_VERSION = 'v1alpha1';

export const AgentCorpusModel: K8sModel = {
  apiGroup: ACC_GROUP,
  apiVersion: ACC_VERSION,
  kind: 'AgentCorpus',
  plural: 'agentcorpora',
  label: 'AgentCorpus',
  labelPlural: 'AgentCorpora',
  abbr: 'AC',
  namespaced: true,
  crd: true,
  id: 'agentcorpus',
};

export const AgentCollectiveModel: K8sModel = {
  apiGroup: ACC_GROUP,
  apiVersion: ACC_VERSION,
  kind: 'AgentCollective',
  plural: 'agentcollectives',
  label: 'AgentCollective',
  labelPlural: 'AgentCollectives',
  abbr: 'ACOL',
  namespaced: true,
  crd: true,
  id: 'agentcollective',
};

export const AccCatalogModel: K8sModel = {
  apiGroup: ACC_GROUP,
  apiVersion: ACC_VERSION,
  kind: 'AccCatalog',
  plural: 'acccatalogs',
  label: 'AccCatalog',
  labelPlural: 'AccCatalogs',
  abbr: 'ACAT',
  namespaced: true,
  crd: true,
  id: 'acccatalog',
};

export const AccPackageInstallModel: K8sModel = {
  apiGroup: ACC_GROUP,
  apiVersion: ACC_VERSION,
  kind: 'AccPackageInstall',
  plural: 'accpackageinstalls',
  label: 'AccPackageInstall',
  labelPlural: 'AccPackageInstalls',
  abbr: 'API',
  namespaced: true,
  crd: true,
  id: 'accpackageinstall',
};

/**
 * All four ACC models. The parity gate iterates this array; pages import the
 * individual exports above. Keep every CRD base represented here exactly once.
 */
export const ACC_MODELS: K8sModel[] = [
  AgentCorpusModel,
  AgentCollectiveModel,
  AccCatalogModel,
  AccPackageInstallModel,
];
