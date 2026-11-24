"""Serializable action plans and opt-in execution (stdlib bootstrap only)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import (ROOTS, action_environment, resolve_config, resolved_manifest,
                     write_config)
from .devices import detect, recommend
from .fetch import repos_for_features, validate_checkout, fetch_repo, update_installed


class InstallError(RuntimeError):
    pass


# Prebuilt OpenVINO IR embedding model for the accelerator sidecar. Downloading
# this instead of exporting it keeps optimum-intel, and therefore torch/nncf, out
# of the interpreter the Fennec apps also use.
EMBED_IR_REPO = "OpenVINO/bge-base-en-v1.5-int8-ov"

# Mirrors fennec-core/engine.py:_engine_binary, the canonical rule.
FENNEC_DIRS = ("target/release", "target/debug", ".")

# Mirrors fennec-core/outlets.py:BROWSER_ENV: the venv web_task's transport lives in.
# It is home-relative because that is where the core looks for it; --root does not move
# it, and a venv the core cannot find is an install that did not happen.
BROWSER_ENV = (".fennec", "browser-tools", "fennec")
# The transport pinned to the version whose module layout the loop imports
# (browser_harness.helpers.cdp, browser_harness.admin.ensure_daemon). A bare name would
# let an upstream reshape of those two modules break a Fennec build that still expects them.
BROWSER_TRANSPORT = "browser-harness==0.1.13"

# Every first-party crate is edition 2024 with rust-version 1.85, the first stable
# that accepts it. cargo on PATH alone does not prove the toolchain can compile them.
MIN_RUSTC = (1, 85)
_rustc_cache = {}


def _slash(path):
    return str(path).replace("\\", "/")


def rustc_version():
    """The toolchain's rustc version as a tuple, or None when it cannot be read."""
    if "version" not in _rustc_cache:
        _rustc_cache["version"] = None
        exe = shutil.which("rustc")
        if exe:
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                                     timeout=20, encoding="utf-8", errors="replace").stdout
            except (OSError, subprocess.SubprocessError):
                out = ""
            found = re.search(r"\b\d+\.\d+(?:\.\d+)?", out)
            if found:
                _rustc_cache["version"] = tuple(int(p) for p in found.group(0).split("."))
    return _rustc_cache["version"]


def rust_block(why):
    """What a cargo build needs that this machine does not have, or None.

    The old check stopped at "cargo is on PATH", so a pre-1.85 toolchain reached the
    build and failed there with an error naming the edition, not the toolchain.
    """
    if not shutil.which("cargo"):
        return f"Rust: install Rust so cargo is on PATH (rustup) before {why}"
    version = rustc_version()
    if version is not None and version < MIN_RUSTC:
        return (f"Rust: rustc {'.'.join(map(str, version))} cannot {why} "
                f"(needs {'.'.join(map(str, MIN_RUSTC))}+ for edition 2024), run `rustup update`")
    return None


def browser_env(home=None, *, windows=None):
    """Return (venv root, interpreter) for Fennec's own browser venv under home."""
    windows = os.name == "nt" if windows is None else windows
    root = Path(home or Path.home()).joinpath(*BROWSER_ENV, ".venv")
    bins = ("Scripts", "python.exe") if windows else ("bin", "python")
    return _slash(root), _slash(root.joinpath(*bins))


def resolve_engine(engine_root, *, windows=None):
    """Return (path, kind, found) for the Fennec engine under engine_root.

    Fennec is the suite's only engine: a native Rust binary launched directly.
    With nothing built yet, path names the artifact the plan expects afterwards;
    a leftover c/coli launcher from another engine is never selected.
    """
    root = Path(engine_root)
    windows = os.name == "nt" if windows is None else windows
    names = ("fennec.exe", "fennec") if windows else ("fennec",)
    for rel in FENNEC_DIRS:
        for name in names:
            candidate = root / rel / name
            if candidate.is_file():
                return _slash(candidate), "fennec", True
    return _slash(root / "target/release" / names[0]), "fennec", False


def selected_features(manifest, features=None, all_features=False):
    if all_features and features:
        raise ValueError("Choose either --all or --features, not both")
    result = list(manifest["features"]) if all_features else list(dict.fromkeys(
        f.strip() for f in (features or "").split(",") if f.strip()))
    if not result:
        raise ValueError("Select --features a,b or --all; no implicit full installation")
    repos_for_features(manifest, result)
    return result


def build_plan(manifest, features, *, root=None, config=None, overrides=None,
               build=False, update=False, device=None,
               environ=None, home=None):
    values, path = resolve_config(manifest, root=root, config=config, overrides=overrides,
                                  environ=environ, home=home)
    dev = device or detect(values["models_root"])
    rec = recommend(dev)
    values, path = resolve_config(manifest, root=root, config=config, overrides=overrides,
                                  environ=environ, home=home, recommendation=rec)
    repos = repos_for_features(resolved_manifest(manifest, values), features)
    locations = {r["concept"]: r["local"] for r in repos}
    actions, missing, warnings = [], [], [rec["note"]]
    explicit = set(k for k, v in (overrides or {}).items() if v is not None)
    if root:
        explicit.update(ROOTS)
    if root or "suite_root" in explicit:
        explicit.add("web_root")
    if root or "engine_root" in explicit:
        explicit.add("python")
    def add(label, argv, cwd=None, requires=()):
        actions.append({"label": label, "argv": [str(a) for a in argv], "cwd": cwd,
                        "requires": [str(p) for p in requires]})
    if not shutil.which("git"):
        missing.append("Git: install Git and add it to PATH before fetching")
    if sys.version_info < (3, 10):
        missing.append("Python: use Python 3.10+ with pip")
    packages = []
    visited = set()
    def dependencies(feature):
        if feature in visited:
            return
        visited.add(feature)
        spec = manifest["features"][feature]
        for child in spec.get("features", []):
            dependencies(child)
        packages.extend(spec.get("pip", []))
    for feature in features:
        dependencies(feature)
    packages = list(dict.fromkeys(packages))
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.<>=!~\[\],-]*", p) for p in packages):
        raise ValueError("Invalid dependency specification")
    python = values["python"]
    windows = dev.os == "Windows"
    # Only the core is a Python package now: the CLI left pip when its launcher
    # was ported to Rust, so it is built with cargo like the desktop is.
    needs_python = bool({"core"} & locations.keys() or packages)
    if needs_python:
        if not shutil.which(python) and not Path(python).is_file():
            missing.append(f"Python: install Python 3.10+ on PATH or set FENNEC_PYTHON (configured: {python})")
        add("verify pip", [python, "-m", "pip", "--version"])
        for concept in ("core",):
            if concept in locations:
                project = locations[concept]
                add(f"install editable {concept}", [python, "-m", "pip", "install", "--editable", project],
                    requires=[f"{project}/pyproject.toml"])
        if packages:
            add("install selected optional dependencies", [python, "-m", "pip", "install", *packages])
        if "browser-outlets" in features:
            # The transport belongs to the lane, not to the interpreter every Fennec app
            # runs on: browser-harness brings cdp_use, fetch_use, pillow and websockets with
            # it, and the core deliberately does not depend on any of them.
            venv, venv_python = browser_env(home, windows=windows)
            # Neither step declares a `requires` entry. execute_plan verifies all of them
            # before it runs the first action, and the interpreter the second step calls is
            # the file the first one creates.
            add("create Fennec's browser env", [python, "-m", "venv", venv])
            add("install the browser transport", [venv_python, "-m", "pip", "install", BROWSER_TRANSPORT])
        if "npu" in features:
            # The desktop/IDE sidecar imports openvino from this same interpreter and
            # looks for IR under models_root/embed, so provision both in place.
            embed_dir = f"{values['models_root']}/embed/{EMBED_IR_REPO.split('/')[1]}"
            # No `requires` entry: the interpreter is preflighted above, `python` may be a
            # bare PATH name no file check can resolve, and huggingface_hub is installed by
            # the dependency action that runs before this one.
            add("fetch the sidecar embedding model (prebuilt OpenVINO IR)",
                [python, "-c",
                 "import sys; from huggingface_hub import snapshot_download; "
                 "snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])",
                 EMBED_IR_REPO, embed_dir])
            warnings.append("The NPU/iGPU sidecar runs the device probe and the batched code index only; token generation stays on the Fennec engine.")
            warnings.append("The sidecar uses prebuilt OpenVINO IR, so optimum-intel (and the torch/nncf tree behind it) is never installed here.")
    def crate_sibling(project, name):
        # A cargo build resolves `../name` against the checkout it runs in, which is the
        # suite root. Naming those manifests here makes execute_plan refuse the whole plan
        # when a path-dependency checkout is absent, instead of a cargo run failing
        # halfway through an install with an error that points at neither this hub nor
        # the feature that should have fetched it.
        parent = str(project).replace("\\", "/").rstrip("/").rsplit("/", 1)[0]
        return f"{parent}/{name}/Cargo.toml"

    if "cli" in locations:
        project = locations["cli"]
        cargo = rust_block("building the CLI")
        if cargo:
            missing.append(cargo)
        # The launcher and the glue both path-depend on the fennec-core crates and on
        # fennec-economy (the one Rust implementation of context compaction).
        needs = [f"{project}/Cargo.toml", crate_sibling(project, "fennec-economy")]
        if "core" in locations:
            needs.append(f"{locations['core']}/src/fennec-client/Cargo.toml")
        add("build CLI (cargo build --release)", ["cargo", "build", "--release"], project, needs)
        warnings.append("The CLI is native Rust: `fennec` and its Rust glue come out "
                        "of fennec-cli/target/release, not out of pip.")
    if "desktop" in locations:
        project = locations["desktop"]
        cargo = rust_block("building the desktop app")
        if cargo:
            missing.append(cargo)
        # The app path-depends on the fennec-core Rust workspace, on fennec-economy and on
        # fennec-ui, so the sibling checkouts are part of what makes this build possible.
        needs = [f"{project}/Cargo.toml", crate_sibling(project, "fennec-economy"),
                 crate_sibling(project, "fennec-ui")]
        if "core" in locations:
            needs.append(f"{locations['core']}/src/fennec-client/Cargo.toml")
        add("build desktop (cargo build --release --features gui)",
            ["cargo", "build", "--release", "--features", "gui", "--bin", "fennec-desktop-gui"],
            project, needs)
        warnings.append("The desktop app is native Rust/GPUI (fennec-desktop-gui); it is built, not npm-installed, and needs the suite fetched together.")
        if windows:
            warnings.append("Windows release GPUI shader compilation needs fxc.exe from the Windows SDK on PATH; without it build the debug window (cargo build --features gui --bin fennec-desktop-gui).")
    if "coding-agent" in locations:
        project = locations["coding-agent"]
        cargo = rust_block("building the coding-agent backend")
        if cargo:
            missing.append(cargo)
        needs = [f"{project}/Cargo.toml", crate_sibling(project, "fennec-economy")]
        if "core" in locations:
            needs.append(f"{locations['core']}/src/fennec-client/Cargo.toml")
            needs.append(f"{locations['core']}/src/fennec-workspace-tools/Cargo.toml")
        add("build coding-agent (cargo build --release)", ["cargo", "build", "--release"], project, needs)
        warnings.append("The PWA/API front door is the Rust fennec-agent binary from fennec-coding-agent/target/release; it binds 127.0.0.1:8787 and falls back to :8788 when occupied.")
    engine_root = values["engine_root"]
    engine_path, engine_kind, engine_found = resolve_engine(engine_root, windows=windows)
    model = values.get("model")
    model_status = ("existing path; the engine reads it as-is, format and size unchecked"
                    if model and Path(model).exists() else "missing")
    if "engine" in locations:
        if not engine_found:
            missing.append(f"Engine binary missing: {engine_path}. Build Fennec with cargo build --release in {engine_root} (install --build plans it).")
        if build:
            cargo = rust_block("building Fennec")
            if cargo:
                missing.append(cargo)
            add("build Fennec (cargo build --release)", ["cargo", "build", "--release"], engine_root,
                [f"{engine_root}/Cargo.toml"])
            if not model:
                warnings.append("No model selected, so cargo builds the engine only; pass --model /path/to/existing/model to make inference ready.")
        else:
            warnings.append(f"Engine binaries are not built or verified by this plan; run install with --build (needs cargo and rustc {'.'.join(map(str, MIN_RUSTC))}+, the first stable with edition 2024).")
    if "jetbrains" in locations:
        warnings.append("JetBrains plugin is fetched only; build/package it separately with its build-plugin.sh (FENNEC_IDE_PLATFORM plus the IDEA Kotlin compiler), then install the zip from the IDE's plugin settings.")
    if "finetune" in locations:
        warnings.append("Training dependencies are selected; datasets, model weights and training runs are never downloaded/started implicitly.")
    if "openbot" in locations:
        # The hub is allowed to clone this one and nothing more: OpenBot is someone else's
        # program, and the shared core drives it from its own process.
        warnings.append("OpenBot is cloned only: this hub never modifies, patches, vendors, installs dependencies for or starts it. Check what the core found with `fennec outlets`.")
        warnings.append("OpenBot is a separate Next.js service: set it up and start it with its own README commands, then the core reaches it over loopback AG-UI routes (default http://127.0.0.1:3001, override with --openbot-url or FENNEC_OPENBOT_URL).")
        warnings.append("OpenBot issues no machine token for its run API, so only a loopback single-user instance is driven; a remote one is refused by the core.")
    if "core" in locations:
        # d1.2 is the one lane that costs money per step, so the plan says so even when no
        # outlet checkout was selected: the loop runs from an ordinary interpreter.
        warnings.append("web_task is Fennec's own browser loop: it asks a Liquid d1 model one question per step (the hosted d1.2 is a paid API, LIQUID_API_KEY; the free open-weight d1-2B needs a local LIQUID_ENDPOINT instead of a key) and needs an interpreter that can import browser_harness.helpers. --features browser-outlets installs one, --web-python pins any other, and `fennec outlets` says which of the two gates is missing.")
    if "browser-outlets" in features:
        warnings.append(f"The browser transport goes into Fennec's own venv ({browser_env(home, windows=windows)[0]}), never into the interpreter the apps run on; the core looks there by itself, and `fennec outlets` then reports transport_from: \"the Fennec browser env\".")
    if "core" in locations and "openbot" not in locations:
        warnings.append("No OpenBot workspace selected, so coworker_task stays unadvertised; web_task is unaffected, since Fennec's own loop is not a checkout.")
    if model_status == "missing":
        warnings.append("Inference is NOT ready: no existing model selected. Pass --model /path/to/existing/model. No model download is automatic (35B models can be enormous).")
    if values["backend"] != "cpu":
        warnings.append(f"Configured backend {values['backend']!r} requires an independently verified engine build; hardware detection does not prove decoding support.")
    run = []
    if "engine" in locations and model:
        serve = [engine_path, "serve", "--auto-tier", "--model", model,
                 "--ctx", str(values["ctx"]), "--policy", values["policy"],
                 "--host", "127.0.0.1", "--port", "8184"]
        run.append({"label": "engine (after native build and model validation)", "argv": serve})
        # The engine mmaps config.json + *.safetensors and converts nothing, so a packed
        # checkpoint handed to it by hand answers in fluent nonsense instead of erroring.
        warnings.append("fennec serve --model takes a DIRECTORY holding config.json and *.safetensors; it converts nothing and validates nothing before loading. A packed int4 checkpoint or a .gguf folder must go through the core, which resolves and converts it first - that is what the desktop app, the PWA and `fennec-convert` do. Serving one directly is the failure mode, not a shortcut.")
    if "coding-agent" in locations:
        suffix = ".exe" if windows else ""
        agent = Path(locations["coding-agent"]) / "target" / "release" / f"fennec-agent{suffix}"
        run.append({"label": "coding-agent PWA/API (Rust; 127.0.0.1:8787, falls back to :8788)",
                    "argv": [str(agent)]})
    if "core" in locations:
        base = Path(shutil.which(python) or python).parent
        scripts = base if base.name.lower() in ("scripts", "bin") else base / ("Scripts" if windows else "bin")
        entry = str(scripts / ("fennec-agent.exe" if windows else "fennec-agent"))
        # The Python core is a separate orchestration/ACP/compatibility service, not the
        # PWA front door: give it a free port so it never shadows the Rust agent.
        run.append({"label": "Python core compatibility service (ACP/outlets; not the PWA)",
                    "argv": [entry, "serve", "--port", "8790"]})
    if "desktop" in locations:
        suffix = ".exe" if windows else ""
        release = Path(locations["desktop"]) / "target" / "release"
        run.append({"label": "desktop", "cwd": locations["desktop"],
                    "argv": [str(release / f"fennec-desktop-gui{suffix}")]})
    if "mobile" in features or dev.client_only or dev.termux:
        warnings.append("Mobile = Rust coding-agent backend + PWA, on the same device (Termux) or reached from another device over trusted HTTPS with FENNEC_APP_TOKEN. The engine starts on demand; there is no automatic public exposure.")
    if dev.client_only:
        actions = []
        missing.append("Android/iOS is PWA client mode only: execute this installation plan on a Windows/macOS/Linux host")
        run = []
    return {"features": list(features), "device": dev.to_dict(), "recommendation": rec,
            "config_path": str(path), "config": values, "explicit_keys": sorted(explicit),
            "repos": repos, "actions": actions, "build": build, "update": update,
            "engine_launcher": engine_path if "engine" in locations else None,
            "engine_kind": engine_kind if "engine" in locations else None,
            "model_status": model_status, "missing": missing, "warnings": warnings,
            "run": run, "credits": manifest.get("credits", []),
            "config_write": bool(needs_python or build) and not dev.client_only,
            "cache_environment": {k: v for k, v in action_environment(values, {}).items()
                                  if k in ("HF_HOME", "PIP_CACHE_DIR")}}


def print_plan(plan, machine=False):
    if machine:
        print(json.dumps(plan, indent=2))
        return
    print("Plan only; no downloads, installs, builds, config writes or servers started.")
    print("Features: " + ", ".join(plan["features"]))
    print(f"Device: {plan['device']['os']} {plan['device']['arch']}; detected hardware={plan['device']['tier']}")
    print(f"Runtime: backend={plan['config']['backend']} ctx={plan['config']['ctx']} policy={plan['config']['policy']}")
    for repo in plan["repos"]:
        print(f"  checkout {repo['repo']} [{repo.get('branch', 'main')}] -> {repo['local']}")
    for action in plan["actions"]:
        print(f"  {action['label']}: {json.dumps(action['argv'])}" + (f" (cwd={action['cwd']})" if action["cwd"] else ""))
    print(f"Config after successful relevant install only: {plan['config_path']}")
    for message in plan["missing"] + plan["warnings"]:
        print("  NOTE: " + message)
    for credit in plan["credits"]:
        print(f"Credit: {credit['name']} — {credit['upstream']} ({credit['role']})")


def _run_action(action, env):
    argv = list(action["argv"])
    try:
        result = subprocess.run(argv, cwd=action.get("cwd"), env=env, shell=False)
    except OSError as exc:
        raise InstallError(f"{action['label']} could not start: {exc}") from exc
    if result.returncode:
        raise InstallError(f"{action['label']} failed (exit {result.returncode}); configuration was not written")


def execute_plan(plan, *, install=False, emit=print):
    """Caller explicitly opts into acquisition or installation. Never launches servers."""
    if install and plan["missing"]:
        raise InstallError("Preflight failed: " + "; ".join(plan["missing"]))
    if install and "model" in plan["explicit_keys"] and plan["model_status"] == "missing":
        raise InstallError("--model must point to an existing local file or directory")
    for repo in plan["repos"]:
        validate_checkout(repo, update=plan["update"])
    for repo in plan["repos"]:
        emit(fetch_repo(repo, update=plan["update"]))
    if not install:
        emit("Fetch-only complete. No dependencies installed, engine built, runtime config written or servers started.")
        for message in plan["missing"] + plan["warnings"]:
            emit("Still required / note: " + message)
        return
    # Verify required source files before creating a venv or installing packages.
    for action in plan["actions"]:
        for required in action["requires"]:
            if not Path(required).is_file():
                raise InstallError(f"Missing {required}; cannot {action['label']}. Configuration was not written.")
    env = action_environment(plan["config"])
    env["FENNEC_CONFIG"] = plan["config_path"]
    for action in plan["actions"]:
        emit("Running: " + action["label"])
        _run_action(action, env)
    if plan["build"] and plan["engine_launcher"] and not Path(plan["engine_launcher"]).is_file():
        raise InstallError(f"Engine is still missing after the build ({plan['engine_launcher']}); verify the checkout. Configuration was not written.")
    if plan["config_write"]:
        write_config(plan["config_path"], plan["config"], plan["explicit_keys"], environ=env)
        emit("Runtime configuration saved atomically: " + plan["config_path"])
    emit(f"Selected actions completed ({len(plan['actions'])}); no server was started. Inference readiness is not verified.")
    for message in plan["warnings"]:
        emit("Still required / note: " + message)
    for command in plan["run"]:
        emit("Run separately — " + command["label"] + ": " + json.dumps(command["argv"]) +
             (" (cwd=" + command["cwd"] + ")" if command.get("cwd") else ""))


def add_plan_options(parser):
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--features", help="comma-separated feature names")
    selection.add_argument("--all", action="store_true", help="every feature, including installer and training")
    parser.add_argument("--root", help="clean layout: ROOT/{engine,suite,models,cache}")
    parser.add_argument("--config", help="shared config path; default FENNEC_CONFIG or ~/.fennec/config.json")
    for key in ROOTS + ("python", "model", "engine_url", "web_root", "workspace", "backend",
                        "policy", "openbot_url", "web_python"):
        parser.add_argument("--" + key.replace("_", "-"))
    parser.add_argument("--ctx", type=int)
    parser.add_argument("--build", action="store_true", help="include/execute the native builds with install (cargo build --release for Fennec and the CLI; the desktop adds --features gui)")
    parser.add_argument("--update", action="store_true", help="fast-forward clean matching tracked branches only")
    parser.add_argument("--json", action="store_true", help="machine-readable plan (plan mode only)")


def plan_from_args(manifest, args, features=None):
    from .config import KEYS
    overrides = {key: getattr(args, key, None) for key in KEYS}
    return build_plan(manifest, features or selected_features(manifest, args.features, args.all),
                      root=args.root, config=args.config, overrides=overrides,
                      build=args.build, update=args.update)