// Prompt — send a task to a role and read the answer.
//
// Behaviour kept from the old screen: Enter sends, Shift+Enter is a newline; the
// first reply's session id is adopted so the next turn continues the thread
// (RP-02) and a new thread is an explicit act; AUTO is the default mode because an
// unknown mode normalises to AUTO agent-side, so the stricter gate wins on a typo.
// New: the target is picked from the roles that are running, and the page says
// it is waiting — an agent turn can take minutes.

import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  CardBody,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Label,
  Spinner,
  TextArea,
  TextInput,
  Title,
} from "@patternfly/react-core";
import { sendPrompt } from "../../api/client";
import { useSnapshot } from "../../state/snapshot";

type Turn = { kind: "you" | "agent" | "error"; text: string; task?: string };

const MODES = ["AUTO", "PLAN", "ACCEPT_EDITS", "ACCEPT_ALL"];

export function PromptPage() {
  const { collectiveId, snapshot } = useSnapshot();
  const roles = useMemo(
    () =>
      Array.from(
        new Set(
          Object.values((snapshot?.agents ?? {}) as Record<string, any>)
            .map((a) => String(a?.role ?? ""))
            .filter(Boolean),
        ),
      ).sort(),
    [snapshot],
  );
  const [pickedRole, setPickedRole] = useState("");
  const role = pickedRole || (roles.includes("assistant") ? "assistant" : roles[0] ?? "");

  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>(undefined);
  const [mode, setMode] = useState("AUTO");
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    const prompt = text.trim();
    if (!prompt || !role || busy) return;
    setTurns((t) => [...t, { kind: "you", text: prompt }]);
    setText("");
    setBusy(true);
    try {
      const r = await sendPrompt(
        collectiveId, role, prompt, undefined, sessionId, mode, workspace || undefined,
      );
      if (!sessionId && r.session_id) setSessionId(r.session_id);
      setTurns((t) => [...t, { kind: "agent", text: r.output, task: r.task_id.slice(0, 8) }]);
    } catch (e) {
      setTurns((t) => [...t, { kind: "error", text: String(e) }]);
    } finally {
      setBusy(false);
    }
  };

  // Starting a new thread must be explicit — silently continuing forever is as
  // wrong as never continuing at all.
  const newThread = () => {
    setSessionId(undefined);
    setTurns([]);
  };

  return (
    <div className="acc-page">
      <Title headingLevel="h1" size="2xl">
        Prompt
      </Title>
      <p className="acc-source">
        {sessionId
          ? `Continuing thread ${sessionId.slice(0, 8)}`
          : "New thread — the next reply starts one"}
        {" · "}collective <code>{collectiveId || "—"}</code>
      </p>

      <Card isCompact>
        <CardBody>
          <div role="log" aria-live="polite" aria-label="Conversation"
               style={{ maxHeight: "26rem", overflowY: "auto", display: "grid", gap: "var(--pf-t--global--spacer--md)" }}>
            {turns.length === 0 && (
              <p className="acc-source">No messages yet — ask the collective something below.</p>
            )}
            {turns.map((t, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "var(--pf-t--global--spacer--sm)", alignItems: "start" }}>
                <Label color={t.kind === "you" ? "blue" : t.kind === "agent" ? "green" : "red"} isCompact>
                  {t.kind === "you" ? "You" : t.kind === "agent" ? `${role} · ${t.task}` : "Error"}
                </Label>
                <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{t.text}</div>
              </div>
            ))}
            {busy && (
              <div style={{ display: "flex", gap: "var(--pf-t--global--spacer--sm)", alignItems: "center" }}>
                <Spinner size="md" aria-label="Waiting for the agent" />
                <span className="acc-source">
                  {role} is working on it — a turn that calls tools can take minutes.
                </span>
              </div>
            )}
          </div>
        </CardBody>
      </Card>

      {roles.length === 0 && (
        <Alert isInline variant="warning" title="No agent is on the bus" component="p">
          A prompt needs a running role to answer it. Type a role name to try anyway.
        </Alert>
      )}

      <Form onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <FormGroup label="Target role" fieldId="prompt-role">
          {roles.length > 0 ? (
            <FormSelect id="prompt-role" value={role} onChange={(_, v) => setPickedRole(v)}>
              {roles.map((r) => (
                <FormSelectOption key={r} value={r} label={r} />
              ))}
            </FormSelect>
          ) : (
            <TextInput id="prompt-role" value={pickedRole} onChange={(_, v) => setPickedRole(v)} />
          )}
        </FormGroup>
        <FormGroup label="Message" fieldId="prompt-text">
          <TextArea
            id="prompt-text"
            value={text}
            onChange={(_, v) => setText(v)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter inserts a newline (mirrors the TUI).
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            resizeOrientation="vertical"
            placeholder="Ask the collective…  (Enter to send, Shift+Enter for a newline)"
            aria-label="Message"
          />
        </FormGroup>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--pf-t--global--spacer--md)" }}>
          <FormGroup label="Mode" fieldId="prompt-mode">
            <FormSelect id="prompt-mode" value={mode} onChange={(_, v) => setMode(v)}>
              {MODES.map((m) => (
                <FormSelectOption key={m} value={m} label={m} />
              ))}
            </FormSelect>
          </FormGroup>
          <FormGroup label="Workspace" fieldId="prompt-workspace">
            <TextInput
              id="prompt-workspace"
              value={workspace}
              onChange={(_, v) => setWorkspace(v)}
              placeholder="(none — relative to /workspace)"
            />
          </FormGroup>
        </div>
        <div style={{ display: "flex", gap: "var(--pf-t--global--spacer--sm)" }}>
          <Button type="submit" variant="primary" isDisabled={!text.trim() || !role || busy}>
            Send
          </Button>
          <Button variant="secondary" onClick={newThread} isDisabled={!sessionId && turns.length === 0}>
            New thread
          </Button>
        </div>
      </Form>
    </div>
  );
}
