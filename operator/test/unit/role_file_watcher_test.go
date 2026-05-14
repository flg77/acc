// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

// Tests for filewatch.Watcher.  Proposal 010 PR-2.
package unit_test

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/redhat-ai-dev/agentic-cell-corpus/operator/internal/filewatch"
)

// rolesRoot creates a fresh roles-root directory with optional
// pre-existing role subdirectories.  Returns the absolute path.
func rolesRoot(t *testing.T, ids ...string) string {
	t.Helper()
	dir := t.TempDir()
	for _, id := range ids {
		if err := os.MkdirAll(filepath.Join(dir, id), 0o755); err != nil {
			t.Fatalf("mkdir %s: %v", id, err)
		}
	}
	return dir
}

func TestWatcher_EmptyRolesRootRejected(t *testing.T) {
	w := filewatch.NewWatcher("", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := w.Start(ctx); err == nil {
		t.Fatal("expected error for empty RolesRoot")
	}
}

func TestWatcher_NonexistentRolesRootRejected(t *testing.T) {
	w := filewatch.NewWatcher("/does/not/exist/anywhere", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := w.Start(ctx); err == nil {
		t.Fatal("expected error for missing RolesRoot")
	}
}

func TestWatcher_EmitsEventOnRoleYamlWrite(t *testing.T) {
	root := rolesRoot(t, "coding_agent")
	w := filewatch.NewWatcher(root, 0) // no debounce for determinism
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := w.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}

	// Give the watcher a moment to register kqueue/inotify hooks.
	time.Sleep(50 * time.Millisecond)

	path := filepath.Join(root, "coding_agent", "role.yaml")
	if err := os.WriteFile(path, []byte("role_definition:\n  purpose: x\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	select {
	case ev := <-w.Events():
		if ev.ID != "coding_agent" {
			t.Errorf("ID: got %q", ev.ID)
		}
		if filepath.Base(ev.Path) != "role.yaml" {
			t.Errorf("Path: got %q", ev.Path)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for event")
	}
}

func TestWatcher_IgnoresNonRoleYamlFiles(t *testing.T) {
	root := rolesRoot(t, "coding_agent")
	w := filewatch.NewWatcher(root, 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := w.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	time.Sleep(50 * time.Millisecond)

	// Write README.md — should not emit.
	path := filepath.Join(root, "coding_agent", "README.md")
	if err := os.WriteFile(path, []byte("not a role"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	select {
	case ev := <-w.Events():
		t.Errorf("unexpected event for README.md: %+v", ev)
	case <-time.After(300 * time.Millisecond):
		// expected — no event
	}
}

func TestWatcher_DetectsNewSubdirectory(t *testing.T) {
	root := rolesRoot(t) // no pre-existing subdirs
	w := filewatch.NewWatcher(root, 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := w.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	time.Sleep(50 * time.Millisecond)

	// Create a new subdirectory, then write role.yaml inside it.
	newDir := filepath.Join(root, "fresh_role")
	if err := os.MkdirAll(newDir, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	time.Sleep(100 * time.Millisecond) // give the watcher time to add the new dir

	path := filepath.Join(newDir, "role.yaml")
	if err := os.WriteFile(path, []byte("role_definition:\n  purpose: y\n"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}

	select {
	case ev := <-w.Events():
		if ev.ID != "fresh_role" {
			t.Errorf("ID: got %q, want fresh_role", ev.ID)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timed out — watcher did not pick up new subdir")
	}
}

func TestWatcher_DebounceCollapsesWriteBursts(t *testing.T) {
	root := rolesRoot(t, "noisy_role")
	w := filewatch.NewWatcher(root, 200*time.Millisecond)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := w.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	time.Sleep(50 * time.Millisecond)

	path := filepath.Join(root, "noisy_role", "role.yaml")
	// Write three times in quick succession — common editor pattern.
	for i := 0; i < 3; i++ {
		if err := os.WriteFile(path, []byte("role_definition:\n  purpose: v\n"), 0o644); err != nil {
			t.Fatalf("write %d: %v", i, err)
		}
		time.Sleep(20 * time.Millisecond)
	}

	// Wait for the debounce window to elapse.
	time.Sleep(400 * time.Millisecond)

	// Drain — should see exactly one event for the three writes.
	count := 0
	for {
		select {
		case <-w.Events():
			count++
		case <-time.After(100 * time.Millisecond):
			if count != 1 {
				t.Errorf("expected exactly 1 debounced event, got %d", count)
			}
			return
		}
	}
}

func TestWatcher_ContextCancelClosesEvents(t *testing.T) {
	root := rolesRoot(t, "role_x")
	w := filewatch.NewWatcher(root, 0)
	ctx, cancel := context.WithCancel(context.Background())

	if err := w.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}

	cancel()
	// Events channel should close within reasonable time.
	select {
	case _, ok := <-w.Events():
		if ok {
			t.Error("expected channel closed, got value")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("events channel not closed after context cancel")
	}
}
