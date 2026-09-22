// What every page needs to know about where it runs and who is looking.
// Loaded once by the shell; pages read it instead of asking again.

import { createContext, useContext } from "react";
import type { Environment, Whoami } from "../api/client";

export interface EnvironmentState {
  env: Environment | null;
  who: Whoami | null;
}

export const EnvironmentContext = createContext<EnvironmentState>({ env: null, who: null });

export const useEnvironment = () => useContext(EnvironmentContext);

/** The reason a capability is not available here, or "" when it is. */
export function unavailableReason(env: Environment | null, capability: string): string {
  const cap = env?.capabilities[capability];
  return cap && cap.available === false ? cap.reason : "";
}
