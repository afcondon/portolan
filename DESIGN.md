# Portolan

System-level explorer for multi-component, multi-language projects.

Named after the medieval navigational charts that mapped ports and trade
routes from direct observation — no inherited theory, just what the
navigator could see. Portolan does the same: point it at a directory,
discover what's there, infer the connections.

## Context

Part of the Hylograph tool ecosystem alongside the Minard family
(language-specific code explorers) and Humboldt (the overarching
project/system explorer concept, of which Portolan is the first concrete
implementation).

Minard-* tools go deep into one language's type system and module
structure. Portolan stays shallow but goes wide: it sees the whole
system — every language, every executable, every connection — from
filesystem inspection alone.

## Design Principles

1. **Discovery, not declaration.** The user points Portolan at a
   directory. Everything is inferred from files on disk. No config
   files, no manifests written by the user. If Marginalia or other
   runtime registries are available, Portolan can enrich, but it must
   be useful with just a filesystem.

2. **All languages from the beginning.** Not "PureScript plus plugins
   for other languages" — language detection is a core capability, and
   every language is a first-class citizen.

3. **System topology, not code structure.** Portolan cares about
   components, ports, protocols, and connections — not functions, types,
   or modules. That's what the Minard-* tools are for.

4. **Shared storage.** Data lives in the same DuckDB database as Minard,
   enabling cross-tool linking (a Portolan component can reference a
   Minard project ID for deep-dive).

## Discovery Layers

### Layer 1 — What's here? (filesystem + config detection)

Scan the directory tree and identify:

- **Languages**: detect from package manifests first (`Cargo.toml`,
  `package.json`, `spago.yaml`, `pyproject.toml`, `go.mod`, `mix.exs`,
  `Gemfile`, `*.cabal`), then fall back to file extensions. Report LOC
  per language.
- **Build systems**: Makefile, docker-compose.yml, Dockerfile, CI
  configs (.github/workflows, .gitlab-ci.yml), Justfile.
- **Workspace structure**: cargo workspaces, npm workspaces, spago
  workspaces, Python monorepo patterns. Identify which directories are
  independently buildable units.
- **Component boundaries**: each workspace member, each Dockerfile
  target, each directory with its own package manifest is a candidate
  component.

Output: a list of **components**, each with a name, root path, language(s),
LOC, and build system.

### Layer 2 — What runs? (executable/service detection)

For each component, determine if it produces a runnable artifact:

- **Entrypoints**: `main` functions, `bin` entries in Cargo.toml or
  package.json, `scripts` in package.json, `__main__.py`, `console_scripts`
  in pyproject.toml, Procfile entries, docker-compose services.
- **Declared ports**: grep for `listen`, `bind`, port literals in config,
  `EXPOSE` in Dockerfiles, env var names like `PORT`, `HOST`,
  `LISTEN_ADDR`. LaunchAgent/systemd/supervisor configs.
- **Runtime registries**: if Marginalia is available at localhost:3100,
  query it for registered services with ports and start commands. This
  is optional enrichment, not a requirement.

Output: each component is classified as **library**, **executable**,
**service** (long-running with a port), or **tool** (CLI/script).
Services get declared port numbers.

### Layer 3 — How do they connect? (topology inference)

Look for evidence that one component talks to another:

- **Port matching**: component A listens on :3012, component B has a
  URL or config referencing localhost:3012 — infer a connection.
- **Protocol hints**: WebSocket URLs (`ws://`), OSC port patterns,
  HTTP client imports, Unix socket paths (`.sock` files), gRPC proto
  imports.
- **Shared packages**: two components both depend on the same library
  (especially a `-protocol`, `-shared`, `-types` package) — that's a
  declared interface boundary.
- **Docker/compose links**: `depends_on`, network aliases, linked
  services.
- **Config file references**: one component's config names another
  component's hostname/port.

Output: directed edges between components, annotated with protocol and
confidence level.

### Layer 4 — What's the shape? (structural summary)

Aggregate metrics for the system:

- Component count, total LOC, LOC per language
- Dependency depth (longest chain of service-to-service connections)
- Library vs service vs tool ratio
- Age/activity from git (last commit per component, commit velocity)
- Which components are the most-connected hubs

## Visualization

The primary view is a **force-directed graph** rendered with Hylograph:

- Nodes = components
- Node size = LOC (or declaration count where available)
- Node color = primary language
- Edges = inferred connections
- Edge labels = protocol (WS, OSC, HTTP, Unix socket, shared-types)
- Edge thickness = confidence

Secondary views (future):
- Treemap of the filesystem colored by language
- Timeline of component creation/modification
- Dependency matrix (which component depends on which)

## Technology

- **Scanner**: Node.js or Python CLI that walks the filesystem and
  writes results to DuckDB. Runs as a one-shot analysis, not a
  persistent service.
- **Visualization**: PureScript + Hylograph, served as a static page
  or lightweight dev server. Reads from DuckDB (via a thin API or
  directly if using a WASM build).
- **Storage**: tables in the shared CodeExplorer DuckDB, namespaced
  with a `portolan_` prefix to avoid collision with Minard tables.

## MVP Scope

Layer 1 + Layer 2, rendered as a single interactive page.

Given a directory:
1. Discover components (language, LOC, build system, workspace structure)
2. Detect executables and services (entrypoints, ports)
3. Render a force graph + summary table

Layer 3 (connection inference) is the second iteration once the
discovery pipeline is solid.

## Test Case

The Atlantis live-coding ecosystem (`~/work/afc-work/music/live-coding/`)
is the primary test subject — 8+ sibling repos, 3 languages (PureScript,
Rust, Python), runtime composition via OSC/WebSocket/Unix sockets. A
"naive Claude" with no prior knowledge should be able to point Portolan
at this directory and get a useful system map.
