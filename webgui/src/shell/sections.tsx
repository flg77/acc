// The navigation: eight questions instead of seventeen screens
// (webgui/design-system/shell/app-shell.html).
//
// A section is a question a person asks; its tabs are the screens that answer
// it.  Screens that have not been rebuilt on PatternFly yet are the legacy
// ones from screens.tsx / tracing.tsx, wrapped by <Legacy>; each one moves out
// as it is rebuilt.  Nothing was dropped: every screen of the old navigation
// is reachable from here.

import type { ReactElement } from "react";
import {
  Compliance,
  Configuration,
  Diagnostics,
  Ecosystem,
  Help,
  Infuse,
  RoleEditor,
} from "../screens";
import { AuditTimeline, PlanDag, TraceDestination, TraceWaterfall } from "../tracing";
import { AgentsetPage } from "../pages/Agentset";
import { OverviewPage } from "../pages/Overview";
import { BoardPage } from "../pages/work/Board";
import { CommsPage } from "../pages/work/Comms";
import { PromptPage } from "../pages/work/Prompt";
import { CatalogsPage } from "../pages/packages/Catalogs";
import { MarketplacePage } from "../pages/packages/Marketplace";

export interface Tab {
  key: string;
  label: string;
  element: ReactElement;
  /** An environment capability the tab needs; hidden where it is unavailable. */
  capability?: string;
}

export interface Section {
  path: string;
  label: string;
  question: string;
  tabs: Tab[];
}

/** A screen still on the old stylesheet — scoped so it cannot restyle the shell. */
const legacy = (el: ReactElement) => <div className="legacy">{el}</div>;

export const SECTIONS: Section[] = [
  {
    path: "overview",
    label: "Overview",
    question: "Is it well?",
    tabs: [{ key: "overview", label: "Overview", element: <OverviewPage /> }],
  },
  {
    path: "agentset",
    label: "Agentset",
    question: "What runs, and what should run?",
    tabs: [
      { key: "agents", label: "Agents", element: <AgentsetPage /> },
      { key: "roles", label: "Roles", element: legacy(<Ecosystem />) },
      { key: "infuse", label: "Infuse", element: legacy(<Infuse />) },
      { key: "role-editor", label: "Role editor", element: legacy(<RoleEditor />) },
    ],
  },
  {
    path: "work",
    label: "Work",
    question: "What are the agents doing, and what are they asking?",
    tabs: [
      { key: "prompt", label: "Prompt", element: <PromptPage /> },
      { key: "board", label: "Board", element: <BoardPage /> },
      { key: "comms", label: "Comms", element: <CommsPage /> },
    ],
  },
  {
    path: "packages",
    label: "Packages",
    question: "What can be installed, and from where?",
    tabs: [
      { key: "marketplace", label: "Marketplace", element: <MarketplacePage /> },
      { key: "catalogs", label: "Catalogs", element: <CatalogsPage /> },
    ],
  },
  {
    path: "models",
    label: "Models",
    question: "What does each agent run, and what does the platform offer?",
    tabs: [{ key: "configuration", label: "Models & settings", element: legacy(<Configuration />) }],
  },
  {
    path: "governance",
    label: "Governance",
    question: "Which rules hold, and who decides?",
    tabs: [{ key: "compliance", label: "Compliance", element: legacy(<Compliance />) }],
  },
  {
    path: "traces",
    label: "Traces",
    question: "Where do the turns go, and what happened in one?",
    tabs: [
      { key: "destination", label: "Where the turns go", element: legacy(<TraceDestination />) },
      { key: "waterfall", label: "Waterfall", element: legacy(<TraceWaterfall />) },
      { key: "plan", label: "PLAN DAG", element: legacy(<PlanDag />) },
      {
        key: "audit",
        label: "Audit chain",
        element: legacy(<AuditTimeline />),
        capability: "trace.audit",
      },
    ],
  },
  {
    path: "settings",
    label: "Settings",
    question: "Everything else.",
    tabs: [
      { key: "diagnostics", label: "Diagnostics", element: legacy(<Diagnostics />) },
      { key: "help", label: "Help", element: legacy(<Help />) },
    ],
  },
];
