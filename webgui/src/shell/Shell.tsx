// The application shell: masthead with the eight-item navigation, the
// environment bar under it, and the routed page (design-system/shell/app-shell.html).
//
// Routes are hash routes (#/agentset/agents): the SPA is served by
// StaticFiles, which has no fallback for unknown paths, and it also runs behind
// oauth2-proxy and inside a Showroom tab — a hash survives all three, and gives
// every page a URL that can be bookmarked or pasted.

import { useMemo } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import {
  FormSelect,
  FormSelectOption,
  Masthead,
  MastheadBrand,
  MastheadContent,
  MastheadLogo,
  MastheadMain,
  Nav,
  NavItem,
  NavList,
  Page,
  PageSection,
  Tab,
  TabTitleText,
  Tabs,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { useSnapshot } from "../state/snapshot";
import { EnvironmentBar } from "./EnvironmentBar";
import { useEnvironment } from "./EnvironmentContext";
import { SECTIONS } from "./sections";
import type { Section } from "./sections";

function SectionPage() {
  const { section: path, tab } = useParams();
  const navigate = useNavigate();
  const { env } = useEnvironment();
  const section = SECTIONS.find((s) => s.path === path);
  if (!section) return <Navigate to="/overview" replace />;

  // A tab whose capability this environment lacks is not offered: it could only
  // answer with an error.
  const tabs = section.tabs.filter(
    (t) => !t.capability || !env || env.capabilities[t.capability]?.available !== false,
  );
  const active = tabs.find((t) => t.key === tab) ?? tabs[0];

  return (
    <>
      {tabs.length > 1 && (
        <PageSection type="tabs" aria-label={`${section.label} — screens`}>
          <Tabs
            activeKey={active.key}
            onSelect={(_, key) => navigate(`/${section.path}/${String(key)}`)}
            aria-label={`${section.label} — screens`}
            usePageInsets
          >
            {tabs.map((t) => (
              <Tab key={t.key} eventKey={t.key} title={<TabTitleText>{t.label}</TabTitleText>} />
            ))}
          </Tabs>
        </PageSection>
      )}
      <PageSection aria-label={section.label}>{active.element}</PageSection>
    </>
  );
}

function Navigation({ current }: { current: Section | undefined }) {
  return (
    <Nav variant="horizontal" aria-label="Global">
      <NavList>
        {SECTIONS.map((s) => (
          <NavItem
            key={s.path}
            itemId={s.path}
            isActive={current?.path === s.path}
            to={`#/${s.path}`}
            title={s.question}
          >
            {s.label}
          </NavItem>
        ))}
      </NavList>
    </Nav>
  );
}

export function Shell({
  collectives,
  collectiveId,
  onCollective,
}: {
  collectives: string[];
  collectiveId: string;
  onCollective: (cid: string) => void;
}) {
  const { pathname } = useLocation();
  const { env, who } = useEnvironment();
  const { connected } = useSnapshot();
  const current = useMemo(
    () => SECTIONS.find((s) => pathname.split("/")[1] === s.path),
    [pathname],
  );

  return (
    <Page
      masthead={
        <Masthead>
          <MastheadMain>
            <MastheadBrand>
              <MastheadLogo component="a" href="#/overview">
                ACC
              </MastheadLogo>
            </MastheadBrand>
          </MastheadMain>
          <MastheadContent>
            <Toolbar isStatic>
              <ToolbarContent>
                <ToolbarItem isOverflowContainer>
                  <Navigation current={current} />
                </ToolbarItem>
                {collectives.length > 1 && (
                  <ToolbarItem align={{ default: "alignEnd" }}>
                    <FormSelect
                      value={collectiveId}
                      onChange={(_, v) => onCollective(v)}
                      aria-label="Collective"
                    >
                      {collectives.map((c) => (
                        <FormSelectOption key={c} value={c} label={c} />
                      ))}
                    </FormSelect>
                  </ToolbarItem>
                )}
              </ToolbarContent>
            </Toolbar>
          </MastheadContent>
        </Masthead>
      }
    >
      <EnvironmentBar env={env} who={who} connected={connected} />
      <Routes>
        <Route path="/" element={<Navigate to="/overview" replace />} />
        <Route path="/:section/:tab?" element={<SectionPage />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Routes>
    </Page>
  );
}
