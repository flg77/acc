// Tier and ceiling labels shared by the Knowledge pages and the lineage graph.
// A tier says how far a note has travelled; a ceiling says who may read it.

import { Label } from "@patternfly/react-core";
import type { Tier } from "../api/client";

type Color = "grey" | "blue" | "purple" | "green" | "yellow" | "orange" | "red";

const TIER: Record<Tier, { label: string; color: Color }> = {
  private: { label: "private", color: "grey" },
  shared: { label: "shared", color: "blue" },
  hub: { label: "enterprise hub", color: "purple" },
};

const CEILING: Record<string, Color> = {
  LOW: "green",
  MEDIUM: "yellow",
  HIGH: "orange",
  CRITICAL: "red",
};

export function TierLabel({ tier }: { tier: Tier }) {
  const t = TIER[tier] ?? { label: tier, color: "grey" as Color };
  return <Label isCompact color={t.color}>{t.label}</Label>;
}

export function CeilingLabel({ ceiling }: { ceiling: string }) {
  const c = (ceiling || "CRITICAL").toUpperCase();
  return <Label isCompact variant="outline" color={CEILING[c] ?? "red"}>{c}</Label>;
}
