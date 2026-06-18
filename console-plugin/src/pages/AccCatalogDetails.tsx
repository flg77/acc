/*
 * AccCatalog detail page (proposal 035, PR-2).
 *
 * Surfaces the catalog source (mode/url/path), tier/priority, the required
 * cosign signer (the signing floor), and status conditions / lastRenderedAt.
 * Fields are from acc.redhat.io_acccatalogs.yaml.
 */
import * as React from 'react';
import { DescriptionList } from '@patternfly/react-core';
import { Timestamp } from '@openshift-console/dynamic-plugin-sdk';
import { AccCatalogModel } from '../models';
import { AccCatalogKind } from '../types';
import { DetailPage, SectionCard, SectionGrid } from '../components/detail';
import { ConditionsTable, DetailItem, PhaseLabel } from '../components/status';

const AccCatalogDetails: React.FC<{ name?: string; namespace?: string }> = ({
  name,
  namespace,
}) => (
  <DetailPage<AccCatalogKind> model={AccCatalogModel} name={name} namespace={namespace}>
    {(obj) => {
      const signer = obj.spec?.requiredSigner;
      return (
        <SectionGrid>
          <SectionCard title="Catalog">
            <DescriptionList isHorizontal>
              <DetailItem label="Catalog ID" always>
                {obj.spec?.catalogId}
              </DetailItem>
              <DetailItem label="Tier">
                {obj.spec?.tier ? <PhaseLabel phase={obj.spec.tier} /> : undefined}
              </DetailItem>
              <DetailItem label="Mode">{obj.spec?.mode}</DetailItem>
              <DetailItem label="Priority" always>
                {obj.spec?.priority ?? 100}
              </DetailItem>
              <DetailItem label="URL">{obj.spec?.url}</DetailItem>
              <DetailItem label="Path">{obj.spec?.path}</DetailItem>
            </DescriptionList>
          </SectionCard>

          <SectionCard title="Required signer">
            <DescriptionList isHorizontal>
              <DetailItem label="Issuer">{signer?.issuer}</DetailItem>
              <DetailItem label="Subject pattern">{signer?.subjectPattern}</DetailItem>
              <DetailItem label="Key path">{signer?.keyPath}</DetailItem>
              {!signer || (!signer.issuer && !signer.subjectPattern && !signer.keyPath) ? (
                <DetailItem label="Verification" always>
                  Not specified
                </DetailItem>
              ) : null}
            </DescriptionList>
          </SectionCard>

          <SectionCard title="Status">
            <DescriptionList isHorizontal>
              <DetailItem label="Last rendered">
                {obj.status?.lastRenderedAt ? (
                  <Timestamp timestamp={obj.status.lastRenderedAt} />
                ) : undefined}
              </DetailItem>
              <DetailItem label="Observed generation">
                {obj.status?.observedGeneration}
              </DetailItem>
            </DescriptionList>
          </SectionCard>

          <SectionCard title="Conditions">
            <ConditionsTable conditions={obj.status?.conditions} />
          </SectionCard>
        </SectionGrid>
      );
    }}
  </DetailPage>
);

export default AccCatalogDetails;
