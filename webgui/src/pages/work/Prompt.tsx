// Prompt — send a task to a role and read the answer.
//
// Behaviour kept from the old screen: Enter sends, Shift+Enter is a newline; the
// first reply's session id is adopted so the next turn continues the thread
// (RP-02) and a new thread is an explicit act; AUTO is the default mode because an
// unknown mode normalises to AUTO agent-side, so the stricter gate wins on a typo.
// New: the target is picked from the roles that are running, and the page says
// it is waiting — an agent turn can take minutes.
// Images (20260830-attachment-delivery-path): uploaded first, sent as sha256
// references. A backend that cannot take one refuses the turn — the reason is
// shown — rather than answering about a picture it never saw; the page warns
// before sending when the configured backend is text-only, but does not block,
// because the role actually asked may be bound elsewhere.

import { useEffect, useMemo, useRef, useState } from "react";
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
import {
  AttachmentCapability,
  AttachmentRef,
  fetchAttachmentCapability,
  sendPrompt,
  uploadAttachment,
} from "../../api/client";
import { useSnapshot } from "../../state/snapshot";

type Turn = {
  kind: "you" | "agent" | "error";
  text: string;
  task?: string;
  images?: string[];
};

const short = (sha: string) => sha.slice(0, 12);

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
  const [attached, setAttached] = useState<AttachmentRef[]>([]);
  const [uploadError, setUploadError] = useState("");
  const [capability, setCapability] = useState<AttachmentCapability | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchAttachmentCapability().then(setCapability).catch(() => setCapability(null));
  }, []);

  const attach = async (files: FileList | null) => {
    setUploadError("");
    for (const file of Array.from(files ?? [])) {
      try {
        const ref = await uploadAttachment(file);
        setAttached((a) => (a.some((x) => x.sha256 === ref.sha256) ? a : [...a, ref]));
      } catch (e) {
        setUploadError(`${file.name}: ${String(e)}`);
      }
    }
    if (fileInput.current) fileInput.current.value = "";
  };

  const send = async () => {
    const prompt = text.trim();
    if (!prompt || !role || busy) return;
    const images = attached.map((a) => a.sha256);
    setTurns((t) => [...t, { kind: "you", text: prompt, images }]);
    setText("");
    setAttached([]);
    setBusy(true);
    try {
      const r = await sendPrompt(
        collectiveId, role, prompt, undefined, sessionId, mode, workspace || undefined,
        images,
      );
      if (!sessionId && r.session_id) setSessionId(r.session_id);
      setTurns((t) => [
        ...t,
        r.blocked
          ? { kind: "error", text: `Refused: ${r.block_reason || "blocked"}`, task: r.task_id.slice(0, 8) }
          : { kind: "agent", text: r.output, task: r.task_id.slice(0, 8) },
      ]);
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
                <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                  {t.text}
                  {t.images && t.images.length > 0 && (
                    <div className="acc-source">
                      {t.images.map((sha) => (
                        <span key={sha}>image <code>{short(sha)}</code>{" "}</span>
                      ))}
                    </div>
                  )}
                </div>
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
        <FormGroup label="Images" fieldId="prompt-images">
          <input
            ref={fileInput}
            id="prompt-images"
            type="file"
            accept={(capability?.supported ?? ["image/png", "image/jpeg", "image/gif", "image/webp"]).join(",")}
            multiple
            hidden
            onChange={(e) => void attach(e.target.files)}
          />
          <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--pf-t--global--spacer--sm)", alignItems: "center" }}>
            <Button variant="secondary" onClick={() => fileInput.current?.click()} isDisabled={busy}>
              Attach image
            </Button>
            {attached.map((a) => (
              <Label
                key={a.sha256}
                isCompact
                onClose={() => setAttached((x) => x.filter((y) => y.sha256 !== a.sha256))}
                closeBtnAriaLabel={`Remove ${a.filename || short(a.sha256)}`}
              >
                {a.filename || "image"} · {short(a.sha256)}
              </Label>
            ))}
          </div>
        </FormGroup>
        {uploadError && (
          <Alert isInline variant="danger" title="Not attached" component="p">
            {uploadError}
          </Alert>
        )}
        {attached.length > 0 && capability && !capability.accepts_images && (
          <Alert isInline variant="warning" title="This deployment's model is not declared to take images" component="p">
            The configured backend ({capability.backend || "unknown"}) sends an image only to a
            model declared <code>accepts_images: true</code> in models.yaml. Unless
            {" "}{role || "the role"} is bound to one, the turn will be refused — it will not be
            answered without the image.
          </Alert>
        )}
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
