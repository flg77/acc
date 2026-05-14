// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Package filewatch wraps fsnotify with the small amount of policy ACC's
// operator-side role-sync controller (proposal 010 PR-2) needs:
//
//   - Watch a configurable RolesRoot path containing <id>/role.yaml files.
//   - Debounce: editors typically write-then-rename, producing multiple
//     events for one logical save.  We collapse events within DebounceWindow
//     into a single RoleFileChanged emission per <id>.
//   - Emit on a typed channel rather than fsnotify.Event so the controller
//     stays decoupled from filesystem details.
//
// The watcher is intentionally read-only: it only emits events.  Writing
// back to the file system in CRD-source-of-truth mode is PR-3's job.
package filewatch

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/fsnotify/fsnotify"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// RoleFileChanged is the typed event emitted when a role.yaml file under
// the watched RolesRoot is created or modified.
type RoleFileChanged struct {
	// ID is the role identifier — the name of the parent directory under
	// RolesRoot.  Matches the AgentCollective resource name.
	ID string

	// Path is the absolute path to the role.yaml file that triggered
	// the event.  Useful for logging and for the controller's read path.
	Path string

	// Op is the underlying fsnotify operation that triggered the event
	// (CREATE / WRITE / RENAME).  Provided for observability only — the
	// controller should not branch on it.
	Op fsnotify.Op
}

// Watcher watches RolesRoot for changes to <id>/role.yaml files and
// emits debounced RoleFileChanged events on Events().
//
// Lifecycle:
//
//	w := filewatch.NewWatcher("/var/lib/acc/roles", filewatch.DefaultDebounce)
//	if err := w.Start(ctx); err != nil { ... }
//	for ev := range w.Events() { ... }
//	// Cancelling ctx closes Events() after draining in-flight debounces.
type Watcher struct {
	// RolesRoot is the absolute path to the directory containing
	// per-role subdirectories.  Each subdirectory may contain a
	// role.yaml file.
	RolesRoot string

	// DebounceWindow collapses multiple fsnotify events for the same
	// role ID within this window into a single emitted event.  Zero
	// disables debouncing (every fsnotify event passes through — used
	// in tests to keep timing deterministic).
	DebounceWindow time.Duration

	// events is the outbound channel.  Buffered so the controller's
	// reconciler can be momentarily slow without dropping events.
	events chan RoleFileChanged

	// fs is the underlying fsnotify watcher.  Created in Start().
	fs *fsnotify.Watcher

	// pending tracks per-ID debounce timers so a flurry of events
	// from one logical save collapses into one emit.
	pendingMu sync.Mutex
	pending   map[string]*time.Timer
}

// DefaultDebounce is the recommended debounce window for production —
// long enough to swallow editor write-rename storms, short enough that
// the operator sees the change within a single reconcile cycle.
const DefaultDebounce = 500 * time.Millisecond

// NewWatcher constructs a Watcher with the given root + debounce window.
// Use filewatch.DefaultDebounce in production; zero in tests.
func NewWatcher(rolesRoot string, debounce time.Duration) *Watcher {
	return &Watcher{
		RolesRoot:      rolesRoot,
		DebounceWindow: debounce,
		events:         make(chan RoleFileChanged, 32),
		pending:        make(map[string]*time.Timer),
	}
}

// Events returns the receive-only channel of RoleFileChanged events.
// The channel is closed after Start's context is cancelled and any
// pending debounce timers have drained.
func (w *Watcher) Events() <-chan RoleFileChanged {
	return w.events
}

// Start begins watching RolesRoot.  It walks the existing directory
// tree to add watches for each <id>/ subdirectory, then runs a
// background goroutine that translates fsnotify events into typed
// RoleFileChanged emissions.  Returns when the initial setup is
// complete; callers should not block on it.
//
// New role directories created after Start are handled: the watcher
// watches RolesRoot itself, so a CREATE on a new subdirectory adds
// a watch for it.
func (w *Watcher) Start(ctx context.Context) error {
	if w.RolesRoot == "" {
		return errors.New("filewatch: RolesRoot is empty")
	}
	abs, err := filepath.Abs(w.RolesRoot)
	if err != nil {
		return fmt.Errorf("filewatch: resolve RolesRoot: %w", err)
	}
	w.RolesRoot = abs
	if info, err := os.Stat(w.RolesRoot); err != nil || !info.IsDir() {
		return fmt.Errorf("filewatch: RolesRoot %q is not a directory", w.RolesRoot)
	}

	w.fs, err = fsnotify.NewWatcher()
	if err != nil {
		return fmt.Errorf("filewatch: create fsnotify watcher: %w", err)
	}

	// Watch RolesRoot itself (catches new <id>/ directories).
	if err := w.fs.Add(w.RolesRoot); err != nil {
		_ = w.fs.Close()
		return fmt.Errorf("filewatch: add RolesRoot watch: %w", err)
	}

	// Walk existing subdirectories and add watches.
	entries, err := os.ReadDir(w.RolesRoot)
	if err != nil {
		_ = w.fs.Close()
		return fmt.Errorf("filewatch: read RolesRoot: %w", err)
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		sub := filepath.Join(w.RolesRoot, entry.Name())
		if err := w.fs.Add(sub); err != nil {
			// Non-fatal: log + continue.  A single bad subdir
			// shouldn't stop the whole watcher.
			log.FromContext(ctx).Info(
				"filewatch: skipping subdir",
				"path", sub, "err", err.Error(),
			)
		}
	}

	go w.run(ctx)
	return nil
}

// run is the watcher's event loop.  Exits when ctx is cancelled or
// the fsnotify Events channel closes.
func (w *Watcher) run(ctx context.Context) {
	logger := log.FromContext(ctx).WithName("filewatch")
	defer close(w.events)
	defer w.fs.Close()

	for {
		select {
		case <-ctx.Done():
			w.drainPending()
			return

		case event, ok := <-w.fs.Events:
			if !ok {
				w.drainPending()
				return
			}
			w.handle(ctx, event)

		case err, ok := <-w.fs.Errors:
			if !ok {
				return
			}
			logger.Info("filewatch: watcher error", "err", err.Error())
		}
	}
}

// handle decides whether an fsnotify event is a role.yaml change we
// care about and either emits immediately or starts/resets a debounce
// timer.
func (w *Watcher) handle(ctx context.Context, ev fsnotify.Event) {
	// Case A: a brand-new subdirectory under RolesRoot.  Add a watch
	// for it so subsequent role.yaml writes are captured.
	if ev.Op&fsnotify.Create != 0 {
		if info, err := os.Stat(ev.Name); err == nil && info.IsDir() {
			parent := filepath.Dir(ev.Name)
			if parent == w.RolesRoot {
				_ = w.fs.Add(ev.Name)
			}
			return
		}
	}

	// Case B: an event on the role.yaml file itself.
	if filepath.Base(ev.Name) != "role.yaml" {
		return
	}
	// Only react to writes / creates / renames.  Plain Chmod is noisy
	// on some editors and not a content change.
	if ev.Op&(fsnotify.Write|fsnotify.Create|fsnotify.Rename) == 0 {
		return
	}

	// Derive the role ID from the parent directory name.
	parent := filepath.Dir(ev.Name)
	if !strings.HasPrefix(parent, w.RolesRoot) {
		return
	}
	roleID := filepath.Base(parent)
	if roleID == "" || roleID == "." || roleID == ".." {
		return
	}

	emit := RoleFileChanged{ID: roleID, Path: ev.Name, Op: ev.Op}

	if w.DebounceWindow <= 0 {
		w.send(ctx, emit)
		return
	}

	w.pendingMu.Lock()
	if t, ok := w.pending[roleID]; ok {
		t.Stop()
	}
	w.pending[roleID] = time.AfterFunc(w.DebounceWindow, func() {
		w.pendingMu.Lock()
		delete(w.pending, roleID)
		w.pendingMu.Unlock()
		w.send(ctx, emit)
	})
	w.pendingMu.Unlock()
}

// send delivers an event with respect for context cancellation.
func (w *Watcher) send(ctx context.Context, ev RoleFileChanged) {
	select {
	case w.events <- ev:
	case <-ctx.Done():
	}
}

// drainPending stops any in-flight debounce timers.  Called at
// shutdown so we don't leak goroutines.
func (w *Watcher) drainPending() {
	w.pendingMu.Lock()
	defer w.pendingMu.Unlock()
	for id, t := range w.pending {
		t.Stop()
		delete(w.pending, id)
	}
}
