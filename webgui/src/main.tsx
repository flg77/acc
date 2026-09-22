import React from "react";
import ReactDOM from "react-dom/client";
import "@patternfly/react-core/dist/styles/base.css";
// The ACC layer of the design system: the state vocabulary and the four
// patterns PatternFly has no word for.  The same file the preview cards use —
// the card is the spec, nothing is drawn twice.
import "../design-system/acc.css";
import "./styles.css";
import App from "./App";

// PatternFly themes by a class on <html>; follow the OS setting, live.
const dark = window.matchMedia("(prefers-color-scheme: dark)");
const applyTheme = () =>
  document.documentElement.classList.toggle("pf-v6-theme-dark", dark.matches);
applyTheme();
dark.addEventListener("change", applyTheme);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
