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

    for mpath, info in sorted(manifests.items(), key=lambda x: len(x[0])):
        mdir = info["dir"]
        if mdir in seen_dirs:
            continue
        seen_dirs.add(mdir)

        is_workspace_root = mdir == "." and workspace_members
        is_workspace_member = any(
            mdir == wm or mdir.startswith(wm + "/") for wm in workspace_members
        )

        component = {
            "name": derive_component_name(root, mdir, info),
            "path": mdir,
            "language": info["language"],
            "manifest": info["file"],
            "role": "unknown",
        }

        if is_workspace_root and not is_workspace_member:
            component["role"] = "workspace-root"
        elif is_workspace_member:
            component["role"] = "workspace-member"

        components.append(component)

    return components


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
# Output
# ---------------------------------------------------------------------------

def format_human(root: Path, lang_files, components, build_systems):
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

        lines.append(f"  {role_icon} {comp['name']:<30} {comp['language']:<12} {comp['path']}{ports_str}{entry_str}")

    lines.append("")
    lines.append(f"Total: {len(components)} components, {total_loc} LOC, {len(lang_files)} languages")

    return "\n".join(lines)


def format_json(root: Path, lang_files, components, build_systems):
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
    components = detect_services(root, components, all_files)

    if use_json:
        output = format_json(root, lang_files, components, build_systems)
    else:
        output = format_human(root, lang_files, components, build_systems)

    if out_file:
        Path(out_file).write_text(output)
        print(f"Written to {out_file}")
    else:
        print(output)


if __name__ == "__main__":
    main()
