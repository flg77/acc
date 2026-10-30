# 20261030-external-work-board — tasks

Backlog CS-03 and CS-10 (vault `20-backlog/chief-of-staff`), proposal 062 phase A/D.

## 1. Store and signal
- [x] `acc/external_work.py`: `ExternalWorkItem` (bounded progress, `summary()`), `ExternalWorkStore` (Redis / in process, `for_queue`)
- [x] `SIG_EXTERNAL_WORK` (endocrine), `subject_external_work`

## 2. Heartbeat and observer
- [x] arbiter HEARTBEAT `external_work` (newest 50)
- [x] `CollectiveSnapshot.external_work`; heartbeat replaces, `EXTERNAL_WORK` updates one row

## 3. Board
- [x] `KIND_EXTERNAL`, `WorkItem.executor/source/repo/cost_usd/tokens`; stale after 24 h; hand-over detail; no controls on external items
- [x] `executor="acc:<role>"` for the collective's own items
- [x] `filter_snapshot` filters `external_work` by requester
- [x] TUI Board: executor column + detail; WebGUI Board: chip, repo, last progress, executor filter

## 4. Tests and close-out
- [x] `tests/test_external_work_board.py` (17)
- [x] suites touching the board, observer, heartbeat and WebGUI reads: 444 passed; `tsc -b --noEmit` clean
- [ ] PR
- [ ] CS-02 publishes `EXTERNAL_WORK` and writes the store
