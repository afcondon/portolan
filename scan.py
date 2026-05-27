#!/usr/bin/env python3
"""Portolan scanner — discover components, languages, and services in a directory tree.

Usage:
    python3 scan.py /path/to/project
    python3 scan.py /path/to/project --json
    python3 scan.py /path/to/project --json --out results.json
"""

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Layer 1 — What's here?
# ---------------------------------------------------------------------------

MANIFEST_SIGNATURES = {
    "Cargo.toml":       "rust",
    "spago.yaml":       "purescript",
    "spago.dhall":      "purescript",
    "tsconfig.json":    "typescript",
    "package.json":     "node",
    "pyproject.toml":   "python",
    "setup.py":         "python",
    "setup.cfg":        "python",
    "go.mod":           "go",
    "mix.exs":          "elixir",
    "Gemfile":          "ruby",
    "build.gradle":     "java",
    "build.gradle.kts": "kotlin",
    "pom.xml":          "java",
    "Makefile":         "make",
    "CMakeLists.txt":   "cpp",
    "deno.json":        "deno",
}

# Build orchestrators — these indicate a build system but not the source language.
# When a directory has both a build orchestrator AND a language manifest,
# the language manifest wins.
BUILD_ORCHESTRATORS = {"Makefile", "CMakeLists.txt", "Justfile"}

# Priority order: when multiple language manifests exist, prefer the more specific one.
LANGUAGE_PRIORITY = {
    "purescript": 10,
    "rust": 10,
    "go": 10,
    "elixir": 10,
    "typescript": 9,
    "python": 8,
    "node": 6,
    "java": 6,
    "kotlin": 6,
    "ruby": 6,
    "cpp": 5,
    "deno": 5,
    "make": 1,  # lowest — build orchestrator, not a language
}

EXTENSION_LANGUAGES = {
    ".rs":    "rust",
    ".purs":  "purescript",
    ".erl":   "erlang",
    ".ex":    "elixir",
    ".exs":   "elixir",
    ".py":    "python",
    ".js":    "javascript",
    ".mjs":   "javascript",
    ".ts":    "typescript",
    ".tsx":   "typescript",
    ".jsx":   "javascript",
    ".go":    "go",
    ".rb":    "ruby",
    ".java":  "java",
    ".kt":    "kotlin",
    ".c":     "c",
    ".cpp":   "cpp",
    ".h":     "c",
    ".hpp":   "cpp",
    ".hs":    "haskell",
    ".lua":   "lua",
    ".sh":    "shell",
    ".sql":   "sql",
    ".html":  "html",
    ".css":   "css",
    ".scss":  "css",
    ".json":  "json",
    ".yaml":  "yaml",
    ".yml":   "yaml",
    ".toml":  "toml",
    ".md":    "markdown",
}

SKIP_DIRS = {
    "node_modules", ".git", "target", "output", ".spago", "__pycache__",
    ".cache", "dist", "build", ".next", ".nuxt", "vendor", ".cargo",
    "deps", "_build", ".elixir_ls", ".package-cache", "elm-stuff",
    ".venv", "venv", ".env", "env", "site-packages", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".psci_modules",
}

BUILD_SYSTEM_FILES = {
    "Makefile": "make",
    "Justfile": "just",
    "Dockerfile": "docker",
    "docker-compose.yml": "docker-compose",
    "docker-compose.yaml": "docker-compose",
    ".github": "github-actions",
    ".gitlab-ci.yml": "gitlab-ci",
    "Jenkinsfile": "jenkins",
    "Taskfile.yml": "taskfile",
}


def scan_directory(root: Path):
    """Walk the tree, collecting file counts and sizes per language."""
    lang_files = defaultdict(lambda: {"count": 0, "bytes": 0, "loc": 0})
    manifests_found = {}
    build_systems = set()
    all_files = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            rel_path = str(rel / fname)

            if fname in MANIFEST_SIGNATURES:
                manifests_found[rel_path] = {
                    "file": fname,
                    "language": MANIFEST_SIGNATURES[fname],
                    "dir": str(rel),
                }

            if fname in BUILD_SYSTEM_FILES:
                build_systems.add(BUILD_SYSTEM_FILES[fname])
            if fname == ".github" or (Path(dirpath) / fname).is_dir():
                continue

            ext = fpath.suffix.lower()
            lang = EXTENSION_LANGUAGES.get(ext)
            if lang:
                try:
                    size = fpath.stat().st_size
                    loc = count_lines(fpath)
                except (OSError, UnicodeDecodeError):
                    size = 0
                    loc = 0
                lang_files[lang]["count"] += 1
                lang_files[lang]["bytes"] += size
                lang_files[lang]["loc"] += loc
                all_files.append({"path": rel_path, "lang": lang, "loc": loc})

    # Check for build system dirs
    if (root / ".github").is_dir():
        build_systems.add("github-actions")

    return lang_files, manifests_found, build_systems, all_files


def count_lines(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Component discovery
# ---------------------------------------------------------------------------

def parse_cargo_workspace(root: Path, manifest_path: str):
    """Extract cargo workspace members from Cargo.toml."""
    toml_path = root / manifest_path
    members = []
    try:
        text = toml_path.read_text()
        in_workspace = False
        in_members = False
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped == "[workspace]":
                in_workspace = True
                continue
            if in_workspace and stripped.startswith("members"):
                in_members = True
                continue
            if in_members:
                if stripped == "]":
                    break
                m = re.search(r'"([^"]+)"', stripped)
                if m:
                    members.append(m.group(1))
            if stripped.startswith("[") and stripped != "[workspace]":
                in_workspace = False
                in_members = False
    except Exception:
        pass
    return members


def parse_spago_workspace(root: Path, manifest_path: str):
    """Extract spago workspace packages from spago.yaml."""
    yaml_path = root / manifest_path
    packages = []
    try:
        text = yaml_path.read_text()
        in_workspace = False
        for line in text.split("\n"):
            if "workspace:" in line and not line.strip().startswith("#"):
                in_workspace = True
                continue
            if in_workspace and line.strip().startswith("- "):
                pkg = line.strip().lstrip("- ").strip()
                if pkg:
                    packages.append(pkg)
            elif in_workspace and not line.startswith(" ") and line.strip():
                break
    except Exception:
        pass
    return packages


def parse_npm_workspace(root: Path, manifest_path: str):
    """Extract npm/yarn workspace members from package.json."""
    pkg_path = root / manifest_path
    try:
        data = json.loads(pkg_path.read_text())
        return data.get("workspaces", [])
    except Exception:
        return []


WORKSPACE_PARSERS = {
    "Cargo.toml": parse_cargo_workspace,
    "spago.yaml": parse_spago_workspace,
    "package.json": parse_npm_workspace,
}


def discover_components(root: Path, manifests: dict):
    """Identify independently buildable components."""
    components = []
    seen_dirs = set()

    workspace_members = []
    for mpath, info in sorted(manifests.items(), key=lambda x: len(x[0])):
        mdir = info["dir"]
        mfile = info["file"]

        parser = WORKSPACE_PARSERS.get(mfile)
        if parser:
            members = parser(root, mpath)
            if members:
                for member in members:
                    member_path = str(Path(mdir) / member) if mdir != "." else member
                    workspace_members.append(member_path)

    # Group all manifests by directory
    dir_manifests = defaultdict(list)
    for mpath, info in manifests.items():
        dir_manifests[info["dir"]].append(info)

    for mdir, dir_infos in sorted(dir_manifests.items(), key=lambda x: len(x[0])):
        if mdir in seen_dirs:
            continue
        seen_dirs.add(mdir)

        # Pick the best language signal: prefer language manifests over build orchestrators
        best_info = max(dir_infos, key=lambda i: LANGUAGE_PRIORITY.get(i["language"], 0))

        compile_target = None
        if best_info["language"] in ("purescript", "typescript"):
            comp_dir = root / mdir if mdir != "." else root
            compile_target = detect_compile_target(comp_dir, best_info["language"])

        is_workspace_root = mdir == "." and workspace_members
        is_workspace_member = any(
            mdir == wm or mdir.startswith(wm + "/") for wm in workspace_members
        )

        component = {
            "name": derive_component_name(root, mdir, best_info),
            "path": mdir,
            "language": best_info["language"],
            "manifest": best_info["file"],
            "role": "unknown",
        }
        if compile_target:
            component["compileTarget"] = compile_target
        # Record all build systems found in this directory
        build_systems_here = [i["file"] for i in dir_infos if i["file"] in BUILD_ORCHESTRATORS]
        if build_systems_here:
            component["buildOrchestrator"] = build_systems_here[0]

        if is_workspace_root and not is_workspace_member:
            component["role"] = "workspace-root"
        elif is_workspace_member:
            component["role"] = "workspace-member"

        components.append(component)

    # Disambiguate components that share a name
    name_counts = {}
    for c in components:
        name_counts[c["name"]] = name_counts.get(c["name"], 0) + 1
    duped_names = {n for n, count in name_counts.items() if count > 1}
    if duped_names:
        for c in components:
            if c["name"] in duped_names:
                path = c["path"]
                parts = Path(path).parts
                suffix = parts[-1] if len(parts) <= 1 else "/".join(parts[-2:])
                if suffix != c["name"]:
                    c["name"] = f"{c['name']} ({suffix})"

    return components


def detect_compile_target(comp_dir: Path, source_lang: str) -> str | None:
    """Detect what runtime language a source-language project compiles to."""
    if source_lang == "purescript":
        spago = comp_dir / "spago.yaml"
        if spago.exists():
            try:
                text = spago.read_text()
                if "purerl" in text or "backend: erl" in text:
                    return "erlang"
                if "backend:" in text and "python" in text:
                    return "python"
            except Exception:
                pass

        src_dir = comp_dir / "src"
        if src_dir.is_dir():
            erl_files = list(src_dir.glob("*.erl"))
            purs_files = list(src_dir.glob("**/*.purs"))
            if erl_files and purs_files:
                return "erlang"

        return "javascript"

    if source_lang == "typescript":
        return "javascript"

    return None


def derive_component_name(root: Path, rel_dir: str, info: dict):
    """Try to extract a meaningful name from the manifest."""
    mfile = info["file"]
    full_path = root / rel_dir / mfile

    try:
        text = full_path.read_text()
        if mfile == "package.json":
            data = json.loads(text)
            name = data.get("name")
            if name:
                return name
        elif mfile == "Cargo.toml":
            for line in text.split("\n"):
                m = re.match(r'^name\s*=\s*"([^"]+)"', line.strip())
                if m:
                    return m.group(1)
        elif mfile in ("spago.yaml", "spago.dhall"):
            for line in text.split("\n"):
                m = re.match(r"^name:\s*(.+)", line.strip())
                if m:
                    return m.group(1).strip().strip("'\"")
    except Exception:
        pass

    if rel_dir and rel_dir != ".":
        return Path(rel_dir).name
    return root.name


# ---------------------------------------------------------------------------
# Layer 2 — What runs?
# ---------------------------------------------------------------------------

PORT_PATTERNS = [
    re.compile(r"""(?:port|PORT|listen|LISTEN|bind|BIND)\s*[:=]\s*(\d{4,5})"""),
    re.compile(r"""(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{4,5})"""),
    re.compile(r"""EXPOSE\s+(\d{4,5})"""),
    re.compile(r"""-p\s+(\d{4,5})"""),
]

ENTRYPOINT_PATTERNS = {
    "rust": [
        ("src/main.rs", "binary"),
        ("src/bin/", "binary"),
    ],
    "python": [
        ("__main__.py", "executable"),
        ("manage.py", "executable"),
        ("app.py", "executable"),
        ("server.py", "service"),
        ("main.py", "executable"),
    ],
    "node": [
        ("server/", "service"),
        ("src/server", "service"),
        ("bin/", "executable"),
    ],
    "purescript": [
        ("src/Main.purs", "executable"),
    ],
    "go": [
        ("main.go", "executable"),
        ("cmd/", "executable"),
    ],
}


def compute_language_profiles(components: list, all_files: list):
    """Compute per-component language breakdown from the file inventory."""
    for comp in components:
        comp_path = comp["path"]
        prefix = comp_path + "/" if comp_path != "." else ""

        profile = defaultdict(lambda: {"loc": 0, "files": 0})
        total_loc = 0
        for f in all_files:
            if prefix and not f["path"].startswith(prefix):
                continue
            if not prefix and comp_path != ".":
                continue
            lang = f.get("lang")
            if lang:
                profile[lang]["loc"] += f["loc"]
                profile[lang]["files"] += 1
                total_loc += f["loc"]

        comp["languageProfile"] = {
            lang: {
                "loc": stats["loc"],
                "files": stats["files"],
                "pct": round(stats["loc"] / total_loc * 100, 1) if total_loc else 0,
            }
            for lang, stats in sorted(profile.items(), key=lambda x: -x[1]["loc"])
        }
        comp["totalLoc"] = total_loc

        # Update primary language from profile if "make" was selected but real code exists
        if comp["language"] == "make" and profile:
            best_lang = max(profile.items(), key=lambda x: x[1]["loc"])
            if best_lang[0] != "make":
                comp["language"] = best_lang[0]


def detect_services(root: Path, components: list, all_files: list):
    """Detect entrypoints, ports, and classify components."""
    for comp in components:
        comp_path = comp["path"]
        comp_dir = root / comp_path if comp_path != "." else root
        lang = comp["language"]

        # Detect entrypoints
        entrypoints = []
        for pattern, kind in ENTRYPOINT_PATTERNS.get(lang, []):
            candidate = comp_dir / pattern
            if candidate.exists():
                entrypoints.append({"path": pattern, "kind": kind})

        # Check package.json for bin/scripts/main
        if lang == "node":
            pkg_json = comp_dir / "package.json"
            if pkg_json.exists():
                try:
                    data = json.loads(pkg_json.read_text())
                    if "bin" in data:
                        entrypoints.append({"path": "bin", "kind": "executable"})
                    scripts = data.get("scripts", {})
                    if "start" in scripts:
                        entrypoints.append({"path": "scripts.start", "kind": "service"})
                    if "dev" in scripts:
                        entrypoints.append({"path": "scripts.dev", "kind": "service"})
                except Exception:
                    pass

        # Check Cargo.toml for [[bin]] or src/main.rs
        if lang == "rust":
            cargo_toml = comp_dir / "Cargo.toml"
            if cargo_toml.exists():
                try:
                    text = cargo_toml.read_text()
                    if "[[bin]]" in text:
                        entrypoints.append({"path": "[[bin]]", "kind": "binary"})
                except Exception:
                    pass

        comp["entrypoints"] = entrypoints

        # Detect ports — scan only config files and the component's own
        # entrypoint/main files, not all source (reduces false positives
        # from client code that references other services' ports).
        ports = set()
        port_scan_files = []
        for f in all_files:
            if not f["path"].startswith(comp_path) and comp_path != ".":
                continue
            fname = Path(f["path"]).name.lower()
            is_config = any(fname.endswith(e) for e in (
                ".toml", ".yaml", ".yml", ".env", ".json", ".dockerfile",
            ))
            is_entrypoint = fname in (
                "main.rs", "main.py", "main.purs", "main.go", "main.js",
                "main.ts", "server.py", "server.js", "server.ts",
                "run.js", "run.py", "app.py", "app.js",
            )
            if is_config or is_entrypoint:
                port_scan_files.append(f["path"])

        for fpath in port_scan_files[:30]:
            try:
                text = (root / fpath).read_text(errors="replace")
                for pat in PORT_PATTERNS:
                    for m in pat.finditer(text):
                        port = int(m.group(1))
                        if 1024 < port < 65535:
                            ports.add(port)
            except Exception:
                pass

        comp["ports"] = sorted(ports)

        # Classify role
        if comp["role"] in ("workspace-root",):
            pass
        elif comp["ports"] or any(e["kind"] == "service" for e in entrypoints):
            comp["role"] = "service"
        elif entrypoints:
            comp["role"] = "executable"
        elif not entrypoints:
            comp["role"] = "library"

    return components


# ---------------------------------------------------------------------------
# Layer 3 — How do they connect?
# ---------------------------------------------------------------------------

PROTOCOL_HINTS = {
    re.compile(r"ws://[^\s\"']+"):          "websocket",
    re.compile(r"wss://[^\s\"']+"):         "websocket",
    re.compile(r"WebSocket"):               "websocket",
    re.compile(r"cowboy"):                  "websocket",
    re.compile(r"\bOSC\b|osc_send|osc_recv|oscMessage"): "osc",
    re.compile(r"\.sock\b|unix.*socket|SOCK_STREAM"):     "unix-socket",
    re.compile(r"grpc|\.proto\b"):          "grpc",
    re.compile(r"fetch\(|axios|http\.get|urllib"): "http",
}


def infer_connections(root: Path, components: list, all_files: list):
    """Infer edges between components from port references, shared deps, and protocol hints."""
    edges = []

    # Build port-to-component index: which component LISTENS on which port
    port_owners = {}
    for comp in components:
        for port in comp.get("ports", []):
            if comp["role"] in ("service", "executable", "binary"):
                port_owners.setdefault(port, []).append(comp["name"])

    # Build dependency index from package manifests
    comp_deps = {}
    for comp in components:
        comp_dir = root / comp["path"] if comp["path"] != "." else root
        deps = extract_dependencies(comp_dir, comp["manifest"], comp["language"])
        comp_deps[comp["name"]] = deps

    # For each component, scan source files for references to other components' ports
    comp_by_name = {c["name"]: c for c in components}

    for comp in components:
        comp_path = comp["path"]
        comp_files = [
            f for f in all_files
            if (f["path"].startswith(comp_path + "/") or comp_path == "."
                or f["path"] == comp_path)
        ]

        port_refs = defaultdict(set)    # port -> set of file paths
        protocol_refs = defaultdict(set)  # protocol -> set of file paths

        for f in comp_files:
            try:
                text = (root / f["path"]).read_text(errors="replace")
            except Exception:
                continue

            # Check for port references
            for pat in PORT_PATTERNS:
                for m in pat.finditer(text):
                    port = int(m.group(1))
                    if 1024 < port < 65535:
                        port_refs[port].add(f["path"])

            # Check for protocol hints
            for pat, proto in PROTOCOL_HINTS.items():
                if pat.search(text):
                    protocol_refs[proto].add(f["path"])

        # Generate edges from port references
        for port, files in port_refs.items():
            if port in port_owners:
                for owner in port_owners[port]:
                    if owner != comp["name"]:
                        edge = {
                            "from": comp["name"],
                            "to": owner,
                            "type": "port-reference",
                            "port": port,
                            "protocol": guess_protocol_for_port(port, protocol_refs),
                            "evidence": sorted(files)[:3],
                        }
                        edges.append(edge)

        # Generate edges from shared dependencies
        for other_comp in components:
            if other_comp["name"] == comp["name"]:
                continue
            other_deps = comp_deps.get(other_comp["name"], set())
            my_deps = comp_deps.get(comp["name"], set())
            # If one component appears as a dependency of the other
            shared = my_deps & other_deps
            shared_interesting = {
                d for d in shared
                if any(kw in d.lower() for kw in
                       ("protocol", "shared", "types", "common", "core", "api"))
            }
            if shared_interesting:
                existing = any(
                    e["from"] == comp["name"] and e["to"] == other_comp["name"]
                    and e["type"] == "shared-dependency"
                    for e in edges
                )
                if not existing:
                    edge = {
                        "from": comp["name"],
                        "to": other_comp["name"],
                        "type": "shared-dependency",
                        "packages": sorted(shared_interesting),
                    }
                    edges.append(edge)

    # Deduplicate: keep one edge per (from, to, type) with strongest evidence
    seen = set()
    deduped = []
    for e in edges:
        key = (e["from"], e["to"], e["type"], e.get("port", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    return deduped


def guess_protocol_for_port(port: int, protocol_refs: dict) -> str:
    """Guess protocol from port number and context."""
    if protocol_refs.get("websocket"):
        return "websocket"
    if protocol_refs.get("osc"):
        return "osc"
    if protocol_refs.get("grpc"):
        return "grpc"
    well_known = {
        80: "http", 443: "https", 8080: "http", 3000: "http",
        5432: "postgres", 3306: "mysql", 6379: "redis", 27017: "mongodb",
    }
    return well_known.get(port, "tcp")


def extract_dependencies(comp_dir: Path, manifest: str, language: str) -> set:
    """Extract dependency names from a component's manifest."""
    deps = set()
    manifest_path = comp_dir / manifest
    if not manifest_path.exists():
        return deps

    try:
        text = manifest_path.read_text()

        if manifest == "package.json":
            data = json.loads(text)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps.update(data.get(key, {}).keys())

        elif manifest == "Cargo.toml":
            in_deps = False
            for line in text.split("\n"):
                stripped = line.strip()
                if stripped in ("[dependencies]", "[dev-dependencies]",
                                "[build-dependencies]"):
                    in_deps = True
                    continue
                if in_deps and stripped.startswith("["):
                    in_deps = False
                if in_deps:
                    m = re.match(r'^(\S+)\s*=', stripped)
                    if m:
                        deps.add(m.group(1))

        elif manifest in ("spago.yaml", "spago.dhall"):
            in_deps = False
            for line in text.split("\n"):
                if "dependencies:" in line:
                    in_deps = True
                    continue
                if in_deps and line.strip().startswith("- "):
                    dep = line.strip().lstrip("- ").strip().strip("'\"")
                    if dep:
                        deps.add(dep)
                elif in_deps and not line.startswith(" ") and line.strip():
                    in_deps = False

        elif manifest in ("pyproject.toml", "setup.py", "setup.cfg"):
            for line in text.split("\n"):
                m = re.match(r'^\s*"?([a-zA-Z0-9_-]+)', line)
                if m and line.strip().startswith('"'):
                    deps.add(m.group(1))
    except Exception:
        pass

    return deps


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def format_human(root: Path, lang_files, components, build_systems, edges=None):
    """Pretty-print results for terminal."""
    lines = []
    lines.append(f"Portolan scan: {root}")
    lines.append("=" * 60)

    # Languages
    lines.append("")
    lines.append("Languages:")
    total_loc = sum(v["loc"] for v in lang_files.values())
    for lang, stats in sorted(lang_files.items(), key=lambda x: -x[1]["loc"]):
        pct = (stats["loc"] / total_loc * 100) if total_loc else 0
        if pct < 0.1:
            continue
        bar = "#" * max(1, int(pct / 2))
        lines.append(f"  {lang:<14} {stats['loc']:>7} LOC  {stats['count']:>4} files  {pct:5.1f}%  {bar}")

    # Build systems
    if build_systems:
        lines.append("")
        lines.append(f"Build systems: {', '.join(sorted(build_systems))}")

    # Components
    lines.append("")
    lines.append("Components:")
    for comp in components:
        role_icon = {
            "service": "[SVC]",
            "executable": "[EXE]",
            "binary": "[BIN]",
            "library": "[LIB]",
            "workspace-root": "[WRK]",
            "unknown": "[???]",
        }.get(comp["role"], "[???]")

        ports_str = ""
        if comp["ports"]:
            ports_str = f"  ports: {', '.join(str(p) for p in comp['ports'])}"

        entry_str = ""
        if comp["entrypoints"]:
            kinds = set(e["kind"] for e in comp["entrypoints"])
            entry_str = f"  entry: {', '.join(kinds)}"

        lang_str = comp['language']
        if comp.get("compileTarget"):
            lang_str += f"→{comp['compileTarget']}"

        loc_str = f"{comp.get('totalLoc', 0):>6} LOC"

        # Show embedded languages (non-primary, > 0.5%)
        profile = comp.get("languageProfile", {})
        embedded = []
        for elang, estats in profile.items():
            if elang != comp["language"] and estats["pct"] >= 0.5:
                embedded.append(f"{elang} {estats['pct']}%")
        embed_str = f"  +[{', '.join(embedded)}]" if embedded else ""

        lines.append(f"  {role_icon} {comp['name']:<30} {lang_str:<20} {loc_str}{ports_str}{entry_str}{embed_str}")

    # Connections
    if edges:
        lines.append("")
        lines.append("Connections:")
        for e in edges:
            if e["type"] == "port-reference":
                proto = e.get("protocol", "tcp")
                lines.append(f"  {e['from']} --> {e['to']}  :{e['port']} ({proto})")
            elif e["type"] == "shared-dependency":
                pkgs = ", ".join(e.get("packages", []))
                lines.append(f"  {e['from']} <-> {e['to']}  shared: [{pkgs}]")

    lines.append("")
    n_edges = len(edges) if edges else 0
    lines.append(f"Total: {len(components)} components, {total_loc} LOC, {len(lang_files)} languages, {n_edges} connections")

    return "\n".join(lines)


def format_json(root: Path, lang_files, components, build_systems, edges=None):
    """Structured JSON output."""
    return json.dumps({
        "root": str(root),
        "languages": {
            lang: {
                "loc": stats["loc"],
                "files": stats["count"],
                "bytes": stats["bytes"],
            }
            for lang, stats in lang_files.items()
        },
        "buildSystems": sorted(build_systems),
        "components": components,
        "connections": edges or [],
    }, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    use_json = "--json" in args
    out_file = None
    if "--out" in args:
        idx = args.index("--out")
        if idx + 1 < len(args):
            out_file = args[idx + 1]

    lang_files, manifests, build_systems, all_files = scan_directory(root)
    components = discover_components(root, manifests)
    compute_language_profiles(components, all_files)
    components = detect_services(root, components, all_files)
    edges = infer_connections(root, components, all_files)

    if use_json:
        output = format_json(root, lang_files, components, build_systems, edges)
    else:
        output = format_human(root, lang_files, components, build_systems, edges)

    if out_file:
        Path(out_file).write_text(output)
        print(f"Written to {out_file}")
    else:
        print(output)


if __name__ == "__main__":
    main()
