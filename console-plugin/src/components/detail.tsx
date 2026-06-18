/*
 * Shared detail-page chrome (proposal 035, PR-2).
 *
 * Every CR detail page watches a single namespaced object and renders the same
 * outer shell: a header with a ResourceLink-style title, the common metadata
 * (name / namespace / created / labels), then CR-specific sections supplied by
 * the caller. This component owns the watch-state branching (loading / error /
 * not-found) so each page only describes its own content.
 */
import * as React from 'react';
import { useParams } from 'react-router-dom';
import {
  Card,
  CardBody,
  CardTitle,
  DescriptionList,
  Grid,
  GridItem,
  Label,
  LabelGroup,
  PageSection,
  Stack,
  StackItem,
  Title,
} from '@patternfly/react-core';
import {
  K8sModel,
  K8sResourceCommon,
  ResourceIcon,
  ResourceLink,
  Timestamp,
  useK8sWatchResource,
} from '@openshift-console/dynamic-plugin-sdk';
import { DetailItem, EmptyBox, ErrorBox, LoadingBox } from './status';
import { gvk } from '../pages/gvk';

/** Common metadata card shared by all four detail pages. */
const MetadataCard: React.FC<{ obj: K8sResourceCommon }> = ({ obj }) => {
  const labels = obj.metadata?.labels || {};
  const labelEntries = Object.entries(labels);
  return (
    <Card>
      <CardTitle>Details</CardTitle>
      <CardBody>
        <DescriptionList isHorizontal columnModifier={{ lg: '2Col' }}>
          <DetailItem label="Name" always>
            {obj.metadata?.name}
          </DetailItem>
          <DetailItem label="Namespace">
            {obj.metadata?.namespace ? (
              <ResourceLink kind="Namespace" name={obj.metadata.namespace} />
            ) : undefined}
          </DetailItem>
          <DetailItem label="Created" always>
            {obj.metadata?.creationTimestamp ? (
              <Timestamp timestamp={obj.metadata.creationTimestamp} />
            ) : undefined}
          </DetailItem>
          <DetailItem label="Labels" always>
            {labelEntries.length ? (
              <LabelGroup numLabels={10}>
                {labelEntries.map(([k, v]) => (
                  <Label key={k} isCompact color="blue">
                    {v ? `${k}=${v}` : k}
                  </Label>
                ))}
              </LabelGroup>
            ) : undefined}
          </DetailItem>
        </DescriptionList>
      </CardBody>
    </Card>
  );
};

export interface DetailPageProps<T extends K8sResourceCommon> {
  /** The K8sModel for the watched resource (from models.ts). */
  model: K8sModel;
  /** Object name (route param injected by the console for resource/details pages). */
  name?: string;
  /** Object namespace (injected by the console for namespaced resource pages). */
  namespace?: string;
  /** Render the CR-specific sections below the shared metadata card. */
  children: (obj: T) => React.ReactNode;
}

/**
 * Generic single-object detail page. Watches one namespaced object with the
 * logged-in user's token (useK8sWatchResource) and renders shared chrome +
 * caller-supplied sections.
 */
export function DetailPage<T extends K8sResourceCommon>({
  model,
  name,
  namespace,
  children,
}: DetailPageProps<T>): JSX.Element {
  // The console renders resource/details pages at /k8s/ns/:ns/:plural/:name.
  // Prefer explicit props (when the console injects them), else fall back to the
  // route params so the page resolves the object regardless of how it was
  // reached.
  const params = useParams<{ name?: string; ns?: string }>();
  const objName = name || params.name;
  const objNamespace = namespace || params.ns;

  const [obj, loaded, loadError] = useK8sWatchResource<T>(
    objName
      ? {
          groupVersionKind: gvk(model),
          name: objName,
          namespace: objNamespace,
          namespaced: !!objNamespace,
          isList: false,
        }
      : null,
  );

  let body: React.ReactNode;
  if (loadError) {
    body = <ErrorBox error={loadError} />;
  } else if (!loaded) {
    body = <LoadingBox />;
  } else if (!obj || !obj.metadata) {
    body = (
      <EmptyBox
        title={`${model.label} not found`}
        body={objName ? `No ${model.label} named "${objName}" in this project.` : undefined}
      />
    );
  } else {
    body = (
      <Stack hasGutter>
        <StackItem>
          <MetadataCard obj={obj} />
        </StackItem>
        <StackItem>{children(obj)}</StackItem>
      </Stack>
    );
  }

  return (
    <>
      <PageSection variant="light">
        <Title headingLevel="h1">
          <ResourceIcon groupVersionKind={gvk(model)} />
          {objName || model.label}
        </Title>
      </PageSection>
      <PageSection>{body}</PageSection>
    </>
  );
}

/** Convenience: a Card wrapping one CR-specific section with a title. */
export const SectionCard: React.FC<{ title: string; children: React.ReactNode }> = ({
  title,
  children,
}) => (
  <Card>
    <CardTitle>{title}</CardTitle>
    <CardBody>{children}</CardBody>
  </Card>
);

/** Two-column responsive layout for stacking section cards. */
export const SectionGrid: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <Grid hasGutter>
    {React.Children.map(children, (child, i) => (
      <GridItem key={i} lg={6} md={12}>
        {child}
      </GridItem>
    ))}
  </Grid>
);
