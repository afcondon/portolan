# Hylograph Tool Ecosystem

## Overview

A family of code understanding tools built on the Hylograph visualization
libraries, united by the goal: **build the tools necessary to understand
code, databases, and systems in the age of AI.**

```
Humboldt (concept)
  "the overarching explorer"
  │
  ├── Portolan ← system-level explorer (THIS PROJECT)
  │     discovers topology from filesystem inspection
  │     all languages, all components, ports & protocols
  │
  └── Minard Family ← language-specific deep explorers
        │
        ├── Minard-PS  (current Minard)
        │     PureScript: docs.json, corefn, type classes,
        │     module re-exports, declaration AST
        │
        ├── Minard-Rust  (planned)
        │     cargo metadata, trait impls, crate features,
        │     derive macros, lifetime annotations
        │
        ├── Minard-Python  (planned)
        │     AST analysis, import graph, class hierarchies,
        │     type stubs (mypy/pyright), decorator patterns
        │
        ├── Minard-Node  (planned)
        │     TypeScript AST, package.json deps, module graph,
        │     JS FFI bridge visibility (complements Minard-PS)
        │
        └── Minard-DB  (planned, was "Humboldt")
              schema introspection, FK graph, normal form
              analysis, denormalization detection, query
              plan visualization, migration history
```

## Shared Infrastructure

- **Hylograph**: all tools use the same visualization libraries
  (canvas, D3 kernel, graph, layout, selection, simulation, HATS)
- **DuckDB**: all tools store data in the same database instance,
  namespaced by prefix (minard_*, portolan_*, etc.)
- **Cross-linking**: Portolan components can reference Minard project
  IDs. Minard can link out to the Portolan system view. Minard-DB can
  reference tables that Portolan discovered as backing a service.

## Design Philosophy

- **Language-native depth over generic breadth.** Each Minard variant
  understands its language's type system, module system, and idioms at
  full depth. A Rust Minard knows about trait coherence rules. A Python
  Minard knows about `__init__.py` package semantics. This is better
  than one tool that treats all languages as "files with functions."

- **Portolan stays shallow but goes wide.** It sees everything but
  understands nothing deeply. When you want depth, it hands off to the
  appropriate Minard-*.

- **Discovery over declaration.** Tools should work on a codebase
  you've never seen before. No setup files, no manifests authored by
  the user. The codebase *is* the input.

## Why Now

AI-assisted development (Claude Code and similar) makes multi-component
systems the natural output. When the cost of scaffolding a new service
drops to "describe what you want," projects become systems of small
components rather than monoliths. The tooling for understanding these
systems hasn't kept up. GitHub shows you a language bar. We can do
better.
