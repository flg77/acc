/*
 * TypeScript shapes for the four acc.redhat.io custom resources (proposal 035,
 * PR-2). These are the *subset* of spec/status the oversight pages read; they
 * are NOT a full mirror of the CRD schema.
 *
 * GROUND TRUTH: every field below is copied verbatim (camelCase JSON key) from
 * the operator CRD bases / Go types -- do not invent fields here:
 *   operator/api/v1alpha1/agentcorpus_types.go
 *   operator/api/v1alpha1/agentcollective_types.go
 *   operator/api/v1alpha1/acccatalog_types.go
 *   operator/api/v1alpha1/accpackageinstall_types.go
 *   operator/config/crd/bases/acc.redhat.io_*.yaml
 *
 * The (group, version, kind, plural) identities live in models.ts and are
 * CI-asserted against the CRD bases by tests/test_console_plugin_models_parity.py.
 */
import { K8sResourceCommon } from '@openshift-console/dynamic-plugin-sdk';

/**
 * The standard metav1.Condition shape used in status.conditions[] across all
 * four CRDs (controller-gen emits the upstream apimachinery Condition schema).
 */
export interface K8sCondition {
  type: string;
  status: 'True' | 'False' | 'Unknown';
  reason?: string;
  message?: string;
  lastTransitionTime?: string;
  observedGeneration?: number;
}

// ---------------------------------------------------------------------------
// AgentCorpus (agentcorpora)
// ---------------------------------------------------------------------------

/** SharedModelStatus -- AgentCorpusStatus.collectiveStatuses[*].sharedModel (026 G1). */
export interface SharedModelStatus {
  inferenceService?: string;
  namespace?: string;
  shared?: boolean;
  url?: string;
}

/** CollectiveStatus embedded in AgentCorpusStatus.collectiveStatuses. */
export interface CorpusCollectiveStatus {
  phase?: string;
  readyAgents?: { [role: string]: number };
  desiredAgents?: { [role: string]: number };
  scaledObjectsActive?: boolean;
  kserveReady?: boolean;
  conditions?: K8sCondition[];
  sharedModel?: SharedModelStatus;
}

export interface InfrastructureStatus {
  natsReady?: boolean;
  natsVersion?: string;
  natsLeafConnected?: boolean;
  redisReady?: boolean;
  redisVersion?: string;
  milvusConnected?: boolean;
  opaBundleReady?: boolean;
  otelCollectorReady?: boolean;
}

export interface RHOAIModelRef {
  name: string;
  namespace: string;
  url?: string;
}

export interface AgentCorpusKind extends K8sResourceCommon {
  spec?: {
    deployMode?: string;
    version: string;
    collectives?: { name: string }[];
  };
  status?: {
    phase?: string;
    observedGeneration?: number;
    conditions?: K8sCondition[];
    infrastructure?: InfrastructureStatus;
    collectiveStatuses?: { [name: string]: CorpusCollectiveStatus };
    kafkaBridgeReady?: boolean;
    rhoaiProjectRegistered?: boolean;
    webguiDeployed?: boolean;
    tuiDeployed?: boolean;
    webguiURL?: string;
    tuiURL?: string;
    defaultCatalogBootstrapped?: boolean;
    availableRHOAIModels?: RHOAIModelRef[];
    currentVersion?: string;
    pendingUpgradeVersion?: string;
  };
}

// ---------------------------------------------------------------------------
// AgentCollective (agentcollectives)
// ---------------------------------------------------------------------------

/** One entry of AgentCollectiveSpec.agents -- the roster row. */
export interface AgentRoleSpec {
  role: string;
  replicas?: number;
}

/** AgentCollectiveSpec.llm -- the per-collective model binding. */
export interface LLMSpec {
  backend: string;
  ollama?: { baseUrl?: string; model?: string };
  anthropic?: { model?: string };
  vllm?: {
    inferenceServiceRef?: string;
    inferenceServiceNamespace?: string;
    model?: string;
    deploy?: boolean;
  };
  llamaStack?: { baseUrl?: string; modelId?: string };
  embeddingModel?: string;
}

export interface AgentCollectiveKind extends K8sResourceCommon {
  spec?: {
    collectiveId: string;
    corpusRef?: { name?: string };
    agents?: AgentRoleSpec[];
    llm?: LLMSpec;
  };
  status?: {
    phase?: string;
    observedGeneration?: number;
    conditions?: K8sCondition[];
    readyAgents?: { [role: string]: number };
    desiredAgents?: { [role: string]: number };
    scaledObjectsActive?: boolean;
    kserveReady?: boolean;
    spiffeID?: string;
  };
}

// ---------------------------------------------------------------------------
// AccCatalog (acccatalogs)
// ---------------------------------------------------------------------------

export interface CatalogRequiredSigner {
  issuer?: string;
  subjectPattern?: string;
  keyPath?: string;
}

export interface AccCatalogKind extends K8sResourceCommon {
  spec?: {
    catalogId: string;
    tier: string;
    mode: string;
    url?: string;
    path?: string;
    requiredSigner?: CatalogRequiredSigner;
    priority?: number;
  };
  status?: {
    observedGeneration?: number;
    conditions?: K8sCondition[];
    lastRenderedAt?: string;
  };
}

// ---------------------------------------------------------------------------
// AccPackageInstall (accpackageinstalls)
// ---------------------------------------------------------------------------

export interface AccPackageInstallKind extends K8sResourceCommon {
  spec?: {
    name: string;
    constraint?: string;
    catalogRef?: string;
    targetCorpus?: string;
    allowUnsigned?: boolean;
  };
  status?: {
    observedGeneration?: number;
    phase?: string;
    installedVersion?: string;
    installPath?: string;
    contentSha256?: string;
    lastInstalledAt?: string;
    conditions?: K8sCondition[];
  };
}
