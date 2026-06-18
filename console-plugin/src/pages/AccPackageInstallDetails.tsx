/*
 * AccPackageInstall detail page (proposal 035, PR-2).
 *
 * The oversight headline (035 step 2.3): installedVersion / installPath /
 * contentSha256, alongside the resolution spec (package, constraint, catalogRef,
 * targetCorpus, allowUnsigned) and status conditions. Fields from
 * acc.redhat.io_accpackageinstalls.yaml.
 */
import * as React from 'react';
import { ClipboardCopy, DescriptionList, Label } from '@patternfly/react-core';
import { Timestamp } from '@openshift-console/dynamic-plugin-sdk';
import { AccPackageInstallModel } from '../models';
import { AccPackageInstallKind } from '../types';
import { DetailPage, SectionCard, SectionGrid } from '../components/detail';
import { ConditionsTable, DetailItem, PhaseLabel } from '../components/status';

const AccPackageInstallDetails: React.FC<{ name?: string; namespace?: string }> = ({
  name,
  namespace,
}) => (
  <DetailPage<AccPackageInstallKind>
    model={AccPackageInstallModel}
    name={name}
    namespace={namespace}
  >
    {(obj) => (
      <SectionGrid>
        <SectionCard title="Package">
          <DescriptionList isHorizontal>
            <DetailItem label="Package" always>
              {obj.spec?.name}
            </DetailItem>
            <DetailItem label="Constraint">{obj.spec?.constraint || 'latest'}</DetailItem>
            <DetailItem label="Catalog ref">{obj.spec?.catalogRef}</DetailItem>
            <DetailItem label="Target corpus">{obj.spec?.targetCorpus || 'all corpora'}</DetailItem>
            <DetailItem label="Allow unsigned" always>
              <Label color={obj.spec?.allowUnsigned ? 'orange' : 'green'} isCompact>
                {obj.spec?.allowUnsigned ? 'true (signing floor bypassed)' : 'false'}
              </Label>
            </DetailItem>
          </DescriptionList>
        </SectionCard>

        <SectionCard title="Install status">
          <DescriptionList isHorizontal>
            <DetailItem label="Phase" always>
              <PhaseLabel phase={obj.status?.phase} />
            </DetailItem>
            <DetailItem label="Installed version">{obj.status?.installedVersion}</DetailItem>
            <DetailItem label="Install path">{obj.status?.installPath}</DetailItem>
            <DetailItem label="Content SHA-256">
              {obj.status?.contentSha256 ? (
                <ClipboardCopy isReadOnly hoverTip="Copy" clickTip="Copied" variant="inline-compact">
                  {obj.status.contentSha256}
                </ClipboardCopy>
              ) : undefined}
            </DetailItem>
            <DetailItem label="Last installed">
              {obj.status?.lastInstalledAt ? (
                <Timestamp timestamp={obj.status.lastInstalledAt} />
              ) : undefined}
            </DetailItem>
            <DetailItem label="Observed generation">{obj.status?.observedGeneration}</DetailItem>
          </DescriptionList>
        </SectionCard>

        <SectionCard title="Conditions">
          <ConditionsTable conditions={obj.status?.conditions} />
        </SectionCard>
      </SectionGrid>
    )}
  </DetailPage>
);

export default AccPackageInstallDetails;
