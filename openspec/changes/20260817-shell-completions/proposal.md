# Proposal: Shell completions for the operator commands

**Change ID:** 20260817-shell-completions
**Date:** 2026-08-17
**Status:** Parked — revisit when someone has a spare afternoon
**Author:** flg

---

## Problem Statement

`acc-cli` and `acc-pkg` expose roughly twenty-eight subcommands between them,
several with their own nested verbs, and there is no discovery aid beyond
`--help`. Operators either memorise the tree or re-read help output, and command
names that exist are routinely missed.

This is small, but it is disproportionately visible: completion is one of the
first signals that a command-line tool is maintained, and its absence is noticed
immediately by anyone who works in a shell all day.

## Current Behavior

No completion support of any kind. Both entry points are argparse-based, so
the command tree is already fully described in code and could be emitted.

## Desired Behavior

    acc-cli completion bash|zsh|fish
    acc-pkg completion bash|zsh|fish

Printing an installable completion script to stdout, with a documented one-line
install per shell. Completion should cover subcommands and flags, and where cheap,
dynamic values that the operator cannot be expected to remember — role names,
collective ids, installed package names.

## Success Criteria

- Tab-completing `acc-cli ` lists all subcommands in each supported shell.
- Flags complete after a subcommand.
- At least one dynamic completion works (roles or installed packages).
- The scripts are generated from the parser, so a new subcommand completes without
  anyone updating a separate list.

## Scope

**In scope**

- bash, zsh and fish scripts for both entry points, generated from the argparse
  definitions.
- Documented installation.
- Dynamic completion where it is cheap and safe.

**Out of scope**

- PowerShell (revisit if operators ask).
- Packaging completions into the container images.
- Restructuring the command tree.

## Implementation options

**A. `shtab`.** Generates completions directly from argparse parsers for all
three shells. Minimal code, one dependency, and new subcommands are picked up
automatically.

**B. Hand-written scripts.** No dependency, full control, and guaranteed to drift
from the parser the first time someone adds a command.

**C. `argcomplete`.** Runtime completion rather than generated scripts; requires
shell-side activation and adds a startup cost to every invocation.

A is the recommendation, with the generated output committed so operators without
the dependency can still install it.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Should dynamic completions call into a live deployment? Convenient, but a
   completion that hangs because the bus is down is worse than no completion.
2. Do the generated scripts get committed, or generated on demand? Committing
   them makes them installable without the dependency and risks staleness.

## Assumptions

- The argparse structure remains the source of truth for the command tree.
- Completion must never require a running collective.
