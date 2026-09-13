# 20260913-the-marker-channel — tasks

## Phase 1 (v0.17.4)

### 1.1 The parser
- [ ] Marker-head regexes without the greedy argument group
- [ ] Decode arguments with `json.JSONDecoder().raw_decode`
- [ ] Require the closing `]`; malformed input still yields `args_error`
- [ ] Source order preserved across kinds

### 1.2 The control tokens
- [ ] `strip_control_tokens()` at the openai-compat boundary
- [ ] Keep the prefix before the first token plus each `<|message|>` segment
- [ ] No-op when the text has none

### 1.3 Docs
- [ ] CHANGELOG entry

### Verification
- [ ] `tests/test_marker_channel.py` green
- [ ] Existing dispatch / agent suites green
- [ ] Full sweep, classified against origin/main
- [ ] Lighthouse: an AS-04 turn emitting several markers dispatches all of them
