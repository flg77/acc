# Decision: User-facing interface theming

**Change ID:** 20260817-decision-interface-theming
**Date:** 2026-08-17
**Status:** **Declined** — recorded, not scheduled
**Author:** flg

---

## What was considered

Operator-customisable presentation for the terminal interface — colour
schemes, banner and spinner styling, installable visual packs — as a
product feature with its own management surface.

## Decision

**Declined.** ACC will not build theming as a product feature. **Accessibility is split out
and is not declined.**

## Reasoning

The underlying capability is nearly free — the terminal framework supports
styling and the interface already has a stylesheet. What is being declined is
*productising* it: a theme registry, installable packs, a gallery and the
management surface around them.

That work has an ongoing cost and no operator benefit. It also pulls the product
toward a personal-assistant aesthetic that sits badly with a compliance-oriented
tool; a user choosing a colour scheme is not a capability a governed runtime needs
to own.

The important distinction is that **accessibility is not theming**. High-contrast
presentation, colour-blind-safe palettes and respecting terminal capability are
legitimate requirements with real users behind them, and declining theming must
not be read as declining those.

## What we do instead

Leave the stylesheet as the single presentation definition. Raise
accessibility as its own item with concrete requirements — contrast targets,
colour-blind-safe palette selection, behaviour on limited-colour terminals — and
treat it on merit rather than as a subset of customisation.

## Conditions for reopening

Reopen theming only if operators ask for it specifically. Raise accessibility
independently and do not wait for this decision to be revisited.

> This is a decision record, not a proposal. It exists so the absence is
> deliberate and traceable rather than an oversight, and so the same idea does
> not get re-raised without new information.
