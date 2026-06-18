/*
 * CatalogBrowse — the catalog -> install centerpiece (proposal 035, PR-3 / G3).
 *
 * Three stages, one page:
 *   1. BROWSE   — useK8sWatchResource lists AccCatalog in the active project,
 *                 grouped by tier (trusted > tp > community > self) then sorted
 *                 by priority (higher first, matching the resolver's tie-break).
 *                 Each catalog card surfaces its source + signing floor and an
 *                 "Install a package" action.
 *   2. INSTALL  — a form builds an AccPackageInstall object (catalogRef + package
 *                 name + version constraint + allowUnsigned) and creates it with
 *                 the SDK's k8sCreate, in the current project namespace.
 *   3. MONITOR  — after create, watch the new AccPackageInstall by name until it
 *                 reaches Installed / Failed, surfacing phase + installedVersion
 *                 + contentSha256 + conditions (reusing the PR-2 status helpers).
 *
 * Architecture honesty (035 non-goals: NO custom backend):
 *   AccCatalog is a *source* declaration — its spec carries tier / mode / url /
 *   path / requiredSigner / priority, but NOT an enumerable package list. The
 *   advertised packages live in the catalog's rendered index.json, which the
 *   operator's acc-pkg resolver reads at install time. The console plugin runs
 *   only against the K8s API with the user's token, so it cannot (and must not)
 *   fetch that index cross-origin. The browse step therefore lists the catalog
 *   *layers* and the install form takes the @scope/name package as operator
 *   input, pinned to a chosen catalog via catalogRef. The operator reconciler +
 *   the signature-verified pull path do the actual resolution (031/032).
 *
 * React-17 compatible (the 4.18 console shares React 17 via module federation):
 * function components only, no React-18-only APIs.
 */
import * as React from 'react';
import {
  ActionGroup,
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  Checkbox,
  DescriptionList,
  Divider,
  Form,
  FormGroup,
  FormHelperText,
  Gallery,
  HelperText,
  HelperTextItem,
  Label,
  Modal,
  ModalVariant,
  PageSection,
  Split,
  SplitItem,
  Stack,
  StackItem,
  TextInput,
  Title,
} from '@patternfly/react-core';
import {
  k8sCreate,
  ResourceLink,
  useActiveNamespace,
  useK8sWatchResource,
} from '@openshift-console/dynamic-plugin-sdk';
import { AccCatalogModel, AccPackageInstallModel } from '../models';
import { AccCatalogKind, AccPackageInstallKind } from '../types';
import { gvk } from '../pages/gvk';
import {
  ConditionsTable,
  DetailItem,
  EmptyBox,
  ErrorBox,
  LoadingBox,
  PhaseLabel,
} from './status';

// ---------------------------------------------------------------------------
// Shared constants
// ---------------------------------------------------------------------------

/**
 * Tier ordering used for the browse grouping. Mirrors the AccCatalog CRD's tier
 * enum (trusted > tp > community > self) and the resolver's trust precedence: a
 * higher-trust tier wins ties during package resolution.
 */
const TIER_ORDER: ReadonlyArray<string> = ['trusted', 'tp', 'community', 'self'];

const TIER_LABEL: Record<string, string> = {
  trusted: 'Trusted (ACC-curated)',
  tp: 'Third-party (verified partner)',
  community: 'Community (self-attested)',
  self: 'Self (private / local)',
};

const tierColor = (tier?: string): React.ComponentProps<typeof Label>['color'] => {
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

/**
 * The semver-valid "latest" constraint. AccPackageInstall.spec.constraint is
 * required by the CRD (minLength 1), so an empty value would be API-rejected;
 * ">=0.0.0" is acc-pkg's documented "match anything -> highest version" default
 * (acc/pkg/fetch.py fetch_and_install default). This is how the proposal's
 * "default to latest" (021) is expressed honestly on the wire.
 */
const LATEST_CONSTRAINT = '>=0.0.0';

/** AccPackageInstall.spec.name pattern, verbatim from the CRD base. */
const PKG_NAME_RE = /^@[a-z0-9][a-z0-9-]*\/[a-z0-9][a-z0-9_-]*$/;

/**
 * The console's all-namespaces sentinel. useActiveNamespace returns this when
 * the project selector is on "All Projects"; an install must target a single
 * concrete namespace, so we ask the operator to pick a project in that case.
 */
const ALL_NAMESPACES_KEY = '#ALL_NS#';

// ---------------------------------------------------------------------------
// Catalog grouping helper
// ---------------------------------------------------------------------------

interface TierGroup {
  tier: string;
  catalogs: AccCatalogKind[];
}

/**
 * Group catalogs by tier in TIER_ORDER, sorting each tier's catalogs by
 * priority descending (higher first) then by name for stable display.
 */
const groupByTierPriority = (catalogs: AccCatalogKind[]): TierGroup[] => {
  const byTier = new Map<string, AccCatalogKind[]>();
  for (const c of catalogs) {
    const tier = c.spec?.tier || 'self';
    const bucket = byTier.get(tier);
    if (bucket) {
      bucket.push(c);
    } else {
      byTier.set(tier, [c]);
    }
  }
  // Known tiers first (in trust order), then any unexpected tiers alphabetically.
  const tiers = [
    ...TIER_ORDER.filter((t) => byTier.has(t)),
    ...[...byTier.keys()].filter((t) => !TIER_ORDER.includes(t)).sort(),
  ];
  return tiers.map((tier) => ({
    tier,
    catalogs: (byTier.get(tier) || []).slice().sort((a, b) => {
      const pa = a.spec?.priority ?? 100;
      const pb = b.spec?.priority ?? 100;
      if (pb !== pa) {
        return pb - pa; // higher priority first
      }
      return (a.metadata?.name || '').localeCompare(b.metadata?.name || '');
    }),
  }));
};

// ---------------------------------------------------------------------------
// 3. Status monitor — follow the created install to Installed / Failed
// ---------------------------------------------------------------------------

const InstallMonitor: React.FC<{ name: string; namespace: string }> = ({ name, namespace }) => {
  const [obj, loaded, loadError] = useK8sWatchResource<AccPackageInstallKind>({
    groupVersionKind: gvk(AccPackageInstallModel),
    name,
    namespace,
    namespaced: true,
    isList: false,
  });

  const phase = obj?.status?.phase;
  const terminal = phase === 'Installed' || phase === 'Failed';

  let banner: React.ReactNode = null;
  if (phase === 'Installed') {
    banner = (
      <Alert variant="success" isInline title="Install complete">
        The operator resolved and installed the package onto the target corpus pods.
      </Alert>
    );
  } else if (phase === 'Failed') {
    banner = (
      <Alert variant="danger" isInline title="Install failed">
        The operator could not complete this install. See the conditions below for the reason.
      </Alert>
    );
  } else {
    banner = (
      <Alert variant="info" isInline title="Install in progress">
        Watching the AccPackageInstall until it reaches Installed or Failed…
      </Alert>
    );
  }

  return (
    <Card data-test="acc-install-monitor">
      <CardTitle>
        <Split hasGutter>
          <SplitItem isFilled>Install status</SplitItem>
          <SplitItem>
            <ResourceLink
              groupVersionKind={gvk(AccPackageInstallModel)}
              name={name}
              namespace={namespace}
            />
          </SplitItem>
        </Split>
      </CardTitle>
      <CardBody>
        {loadError ? (
          <ErrorBox error={loadError} />
        ) : !loaded ? (
          <LoadingBox />
        ) : !obj || !obj.metadata ? (
          <EmptyBox
            title="Waiting for the install object"
            body="The AccPackageInstall was created; waiting for it to appear in the watch cache…"
          />
        ) : (
          <Stack hasGutter>
            <StackItem>{banner}</StackItem>
            <StackItem>
              <DescriptionList isHorizontal>
                <DetailItem label="Phase" always>
                  <PhaseLabel phase={phase} />
                </DetailItem>
                <DetailItem label="Installed version">{obj.status?.installedVersion}</DetailItem>
                <DetailItem label="Content SHA-256">{obj.status?.contentSha256}</DetailItem>
                <DetailItem label="Install path">{obj.status?.installPath}</DetailItem>
              </DescriptionList>
            </StackItem>
            <StackItem>
              <Title headingLevel="h4">Conditions</Title>
              <ConditionsTable conditions={obj.status?.conditions} />
            </StackItem>
            {!terminal ? (
              <StackItem>
                <HelperText>
                  <HelperTextItem variant="indeterminate">
                    This view updates live; you can also open the full install detail via the link
                    above.
                  </HelperTextItem>
                </HelperText>
              </StackItem>
            ) : null}
          </Stack>
        )}
      </CardBody>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// 2. Install form — build an AccPackageInstall and k8sCreate it
// ---------------------------------------------------------------------------

interface InstallFormProps {
  /** Namespace (current project) the install is created in. */
  namespace: string;
  /** Catalog this install is pinned to (its catalogId becomes spec.catalogRef). */
  catalog: AccCatalogKind;
  /** Called with the created object's name so the parent can mount the monitor. */
  onCreated: (name: string) => void;
  onCancel: () => void;
}

/**
 * Derive a DNS-1123 metadata.name from the @scope/name package + a short random
 * suffix (keeps repeated installs of the same package from colliding). The CR's
 * metadata.name is independent of spec.name (which keeps the @scope/name form).
 */
const deriveInstallName = (pkg: string): string => {
  const slug =
    pkg
      .replace(/^@/, '')
      .replace(/[/_]/g, '-')
      .replace(/[^a-z0-9-]/g, '')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 40) || 'pkg';
  const suffix = Math.random().toString(36).slice(2, 7);
  return `${slug}-${suffix}`;
};

const InstallForm: React.FC<InstallFormProps> = ({ namespace, catalog, onCreated, onCancel }) => {
  const catalogId = catalog.spec?.catalogId || catalog.metadata?.name || '';
  const [pkg, setPkg] = React.useState('');
  const [constraint, setConstraint] = React.useState('');
  const [targetCorpus, setTargetCorpus] = React.useState('');
  const [allowUnsigned, setAllowUnsigned] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | undefined>();

  const trimmedPkg = pkg.trim();
  const pkgValid = PKG_NAME_RE.test(trimmedPkg);
  const pkgError = trimmedPkg.length > 0 && !pkgValid;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pkgValid || submitting) {
      return;
    }
    setSubmitting(true);
    setError(undefined);

    // Build the AccPackageInstall. Every spec field is from the CRD base
    // (acc.redhat.io_accpackageinstalls.yaml); none are invented:
    //   name          (required, @scope/name pattern)
    //   constraint    (required, minLength 1; ">=0.0.0" == latest)
    //   catalogRef    (optional, the catalogId of the chosen layer)
    //   targetCorpus  (optional, empty == all corpora in the namespace)
    //   allowUnsigned (optional, default false; surfaced honestly per 034)
    const data: AccPackageInstallKind = {
      apiVersion: `${AccPackageInstallModel.apiGroup}/${AccPackageInstallModel.apiVersion}`,
      kind: AccPackageInstallModel.kind,
      metadata: {
        name: deriveInstallName(trimmedPkg),
        namespace,
      },
      spec: {
        name: trimmedPkg,
        constraint: constraint.trim() || LATEST_CONSTRAINT,
        catalogRef: catalogId || undefined,
        targetCorpus: targetCorpus.trim() || undefined,
        allowUnsigned,
      },
    };

    try {
      const created = await k8sCreate<AccPackageInstallKind>({
        model: AccPackageInstallModel,
        data,
      });
      const createdName = created?.metadata?.name || data.metadata?.name;
      if (createdName) {
        onCreated(createdName);
      }
    } catch (err) {
      setError(
        (err as { message?: string })?.message ||
          'Could not create the AccPackageInstall. Check your project RBAC and try again.',
      );
      setSubmitting(false);
    }
  };

  return (
    <Form onSubmit={onSubmit} data-test="acc-install-form">
      <FormGroup label="Catalog" fieldId="acc-install-catalog">
        <Split hasGutter>
          <SplitItem>
            <Label color={tierColor(catalog.spec?.tier)} isCompact>
              {catalog.spec?.tier || 'self'}
            </Label>
          </SplitItem>
          <SplitItem isFilled>
            <strong>{catalogId}</strong>
          </SplitItem>
        </Split>
        <FormHelperText>
          <HelperText>
            <HelperTextItem>
              The package is pinned to this catalog (spec.catalogRef = {catalogId || '—'}). Project:{' '}
              <strong>{namespace}</strong>.
            </HelperTextItem>
          </HelperText>
        </FormHelperText>
      </FormGroup>

      <FormGroup label="Package" isRequired fieldId="acc-install-package">
        <TextInput
          isRequired
          id="acc-install-package"
          data-test="acc-install-package"
          value={pkg}
          onChange={(_e, v) => setPkg(v)}
          placeholder="@scope/name (e.g. @acc/capital-markets-roles)"
          validated={pkgError ? 'error' : 'default'}
          aria-label="Package name in @scope/name form"
        />
        <FormHelperText>
          <HelperText>
            <HelperTextItem variant={pkgError ? 'error' : 'default'}>
              {pkgError
                ? 'Must be @scope/name — lowercase, the @scope prefix is mandatory.'
                : 'Scoped package to resolve from the catalog, e.g. @acc/coding-roles.'}
            </HelperTextItem>
          </HelperText>
        </FormHelperText>
      </FormGroup>

      <FormGroup label="Version constraint" fieldId="acc-install-constraint">
        <TextInput
          id="acc-install-constraint"
          data-test="acc-install-constraint"
          value={constraint}
          onChange={(_e, v) => setConstraint(v)}
          placeholder="latest"
          aria-label="Semver version constraint"
        />
        <FormHelperText>
          <HelperText>
            <HelperTextItem>
              Semver range — exact (0.1.0), caret (^1.2), tilde (~1.2.3), or bounded (&gt;=1.2
              &lt;2.0). Leave blank for <strong>latest</strong> (resolves to the highest published
              version; sent as {LATEST_CONSTRAINT}).
            </HelperTextItem>
          </HelperText>
        </FormHelperText>
      </FormGroup>

      <FormGroup label="Target corpus" fieldId="acc-install-target">
        <TextInput
          id="acc-install-target"
          data-test="acc-install-target"
          value={targetCorpus}
          onChange={(_e, v) => setTargetCorpus(v)}
          placeholder="all corpora in this project"
          aria-label="Target AgentCorpus name"
        />
        <FormHelperText>
          <HelperText>
            <HelperTextItem>
              Optional AgentCorpus name whose pods receive this install. Leave blank to install into
              every corpus in the project.
            </HelperTextItem>
          </HelperText>
        </FormHelperText>
      </FormGroup>

      <FormGroup fieldId="acc-install-allow-unsigned" label="Signature verification">
        <Checkbox
          id="acc-install-allow-unsigned"
          data-test="acc-install-allow-unsigned"
          label="Allow unsigned packages (bypass the catalog signing floor)"
          isChecked={allowUnsigned}
          onChange={(_e, v) => setAllowUnsigned(v)}
        />
        {allowUnsigned ? (
          <Alert
            variant="warning"
            isInline
            isPlain
            title="Signing floor bypassed for this install"
            className="pf-v5-u-mt-sm"
          >
            allowUnsigned skips the catalog&apos;s cosign verification for this package only. This is
            operator-explicit and audit-logged. Leave it off in production; use it only for local or
            unsigned development packages.
          </Alert>
        ) : (
          <FormHelperText>
            <HelperText>
              <HelperTextItem variant="success">
                Off (recommended) — the catalog&apos;s required signer is enforced.
              </HelperTextItem>
            </HelperText>
          </FormHelperText>
        )}
      </FormGroup>

      {error ? (
        <Alert variant="danger" isInline title="Create failed">
          {error}
        </Alert>
      ) : null}

      <ActionGroup>
        <Button
          type="submit"
          variant="primary"
          isDisabled={!pkgValid || submitting}
          isLoading={submitting}
          data-test="acc-install-submit"
        >
          {submitting ? 'Creating…' : 'Install'}
        </Button>
        <Button variant="link" onClick={onCancel} isDisabled={submitting}>
          Cancel
        </Button>
      </ActionGroup>
    </Form>
  );
};

// ---------------------------------------------------------------------------
// 1. Browse — one catalog card
// ---------------------------------------------------------------------------

const CatalogCard: React.FC<{
  catalog: AccCatalogKind;
  onInstall: (catalog: AccCatalogKind) => void;
}> = ({ catalog, onInstall }) => {
  const spec = catalog.spec;
  const signer = spec?.requiredSigner;
  const source =
    spec?.mode === 'https' ? spec?.url : spec?.mode === 'file' ? spec?.path : undefined;
  const signerText = signer?.keyPath
    ? `keypair: ${signer.keyPath}`
    : signer?.subjectPattern || signer?.issuer || 'unspecified';

  return (
    <Card isFullHeight data-test="acc-catalog-card">
      <CardTitle>
        <Split hasGutter>
          <SplitItem isFilled>
            <ResourceLink
              groupVersionKind={gvk(AccCatalogModel)}
              name={catalog.metadata?.name}
              namespace={catalog.metadata?.namespace}
              inline
            />
          </SplitItem>
          <SplitItem>
            <Label color={tierColor(spec?.tier)} isCompact>
              {spec?.tier || 'self'}
            </Label>
          </SplitItem>
        </Split>
      </CardTitle>
      <CardBody>
        <Stack hasGutter>
          <StackItem>
            <DescriptionList isHorizontal isCompact>
              <DetailItem label="Catalog ID" always>
                {spec?.catalogId}
              </DetailItem>
              <DetailItem label="Mode">{spec?.mode}</DetailItem>
              <DetailItem label="Priority" always>
                {spec?.priority ?? 100}
              </DetailItem>
              <DetailItem label="Source">{source}</DetailItem>
              <DetailItem label="Required signer" always>
                {signerText}
              </DetailItem>
            </DescriptionList>
          </StackItem>
          <StackItem>
            <Button
              variant="secondary"
              onClick={() => onInstall(catalog)}
              data-test="acc-catalog-install-btn"
            >
              Install a package
            </Button>
          </StackItem>
        </Stack>
      </CardBody>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// CatalogBrowse — the page
// ---------------------------------------------------------------------------

const CatalogBrowse: React.FC<{ namespace?: string }> = ({ namespace: nsProp }) => {
  const [activeNamespace] = useActiveNamespace();
  // Prefer the console-injected namespace prop; else the active project.
  const namespace = nsProp || activeNamespace;
  const isAllNamespaces = !namespace || namespace === ALL_NAMESPACES_KEY;

  const [catalogs, loaded, loadError] = useK8sWatchResource<AccCatalogKind[]>(
    isAllNamespaces
      ? null
      : {
          groupVersionKind: gvk(AccCatalogModel),
          namespace,
          namespaced: true,
          isList: true,
        },
  );

  // Modal + monitor state.
  const [formCatalog, setFormCatalog] = React.useState<AccCatalogKind | null>(null);
  const [createdName, setCreatedName] = React.useState<string | null>(null);

  const groups = React.useMemo(() => groupByTierPriority(catalogs || []), [catalogs]);

  const header = (
    <PageSection variant="light">
      <Title headingLevel="h1">Catalog browse</Title>
      <HelperText>
        <HelperTextItem variant="indeterminate">
          Browse the package catalogs available in this project, grouped by trust tier, then install
          a role package onto your corpora. The operator resolves and signature-verifies the package
          before installing it.
        </HelperTextItem>
      </HelperText>
    </PageSection>
  );

  let body: React.ReactNode;
  if (isAllNamespaces) {
    body = (
      <EmptyBox
        title="Select a project"
        body="Catalog browse installs a package into a single project. Choose a project from the selector above to see its catalogs."
      />
    );
  } else if (loadError) {
    body = <ErrorBox error={loadError} />;
  } else if (!loaded) {
    body = <LoadingBox />;
  } else if (!catalogs || catalogs.length === 0) {
    body = (
      <EmptyBox
        title="No catalogs in this project"
        body={`No AccCatalog resources found in "${namespace}". An AgentCorpus normally bootstraps the default acc-canonical catalog; create an AccCatalog to add layers.`}
      />
    );
  } else {
    body = (
      <Stack hasGutter>
        {createdName ? (
          <StackItem>
            <InstallMonitor name={createdName} namespace={namespace} />
            <Divider className="pf-v5-u-my-md" />
          </StackItem>
        ) : null}
        {groups.map((group) => (
          <StackItem key={group.tier}>
            <Title headingLevel="h2" className="pf-v5-u-mb-sm">
              <Split hasGutter>
                <SplitItem>
                  <Label color={tierColor(group.tier)}>{group.tier}</Label>
                </SplitItem>
                <SplitItem isFilled>{TIER_LABEL[group.tier] || group.tier}</SplitItem>
              </Split>
            </Title>
            <Gallery hasGutter minWidths={{ default: '320px' }}>
              {group.catalogs.map((catalog) => (
                <CatalogCard
                  key={catalog.metadata?.uid || catalog.metadata?.name}
                  catalog={catalog}
                  onInstall={setFormCatalog}
                />
              ))}
            </Gallery>
          </StackItem>
        ))}
      </Stack>
    );
  }

  return (
    <>
      {header}
      <PageSection>{body}</PageSection>
      <Modal
        variant={ModalVariant.medium}
        title="Install a package"
        isOpen={!!formCatalog}
        onClose={() => setFormCatalog(null)}
        data-test="acc-install-modal"
      >
        {formCatalog && !isAllNamespaces ? (
          <InstallForm
            namespace={namespace}
            catalog={formCatalog}
            onCreated={(name) => {
              setCreatedName(name);
              setFormCatalog(null);
            }}
            onCancel={() => setFormCatalog(null)}
          />
        ) : null}
      </Modal>
    </>
  );
};

export default CatalogBrowse;
