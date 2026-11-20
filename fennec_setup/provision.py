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


def _slash(path):
    return str(path).replace("\\", "/")


def browser_env(home=None, *, windows=None):
    """Return (venv root, interpreter) for Fennec's own browser venv under home."""
    windows = os.name == "nt" if windows is None else windows
    root = Path(home or Path.home()).joinpath(*BROWSER_ENV, ".venv")
    bins = ("Scripts", "python.exe") if windows else ("bin", "python")
    return _slash(root), _slash(root.joinpath(*bins))


def resolve_engine(engine_root, *, windows=None):
    """Return (path, kind, found) for the engine installed under engine_root.

    Fennec is a native Rust binary and is launched directly; Colibri was a Python
    launcher (c/coli) and still works from any configured root, because the CLI
    verbs and the OpenAI-compatible HTTP contract are identical. Fennec wins when
    both are installed. With nothing built yet, path names the artifact the plan
    expects afterwards: a Cargo.toml (or an empty root) is Fennec, a c/ tree is
    Colibri.
    """
    root = Path(engine_root)
    windows = os.name == "nt" if windows is None else windows
    names = ("fennec.exe", "fennec") if windows else ("fennec",)
    for rel in FENNEC_DIRS:
        for name in names:
            candidate = root / rel / name
            if candidate.is_file():
                return _slash(candidate), "fennec", True
    launcher = root / "c" / "coli"
    if launcher.is_file():
        return _slash(launcher), "colibri", True
    if (root / "Cargo.toml").is_file() or not (root / "c").is_dir():
        return _slash(root / "target/release" / names[0]), "fennec", False
    return _slash(launcher), "colibri", False


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
               build=False, native_target="fennec", update=False, device=None,
               environ=None, home=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", native_target):
        raise ValueError("Native target must be a simple Makefile target, not options or shell text")
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
        if not shutil.which("cargo"):
            missing.append("Rust: install Rust so cargo is on PATH (rustup) before building the CLI")
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
        if not shutil.which("cargo"):
            missing.append("Rust: install Rust so cargo is on PATH (rustup) before building the desktop app")
        # The app path-depends on the fennec-core Rust workspace, on fennec-economy and on
        # fennec-ui, so the sibling checkouts are part of what makes this build possible.
        needs = [f"{project}/Cargo.toml", crate_sibling(project, "fennec-economy"),
                 crate_sibling(project, "fennec-ui")]
        if "core" in locations:
            needs.append(f"{locations['core']}/src/fennec-client/Cargo.toml")
        add("build desktop (cargo build --release)", ["cargo", "build", "--release"], project, needs)
        warnings.append("The desktop app is native Rust (egui/eframe); it is built, not npm-installed, and needs the suite fetched together.")
    engine_root = values["engine_root"]
    engine_path, engine_kind, engine_found = resolve_engine(engine_root, windows=windows)
    model = values.get("model")
    model_status = "existing path; format/size not validated" if model and Path(model).exists() else "missing"
    if "engine" in locations:
        if not engine_found:
            if engine_kind == "fennec":
                missing.append(f"Engine binary missing: {engine_path}. Build Fennec with cargo build --release in {engine_root} (install --build plans it).")
            else:
                missing.append(f"Engine launcher missing: {engine_path}. Use a complete engine checkout.")
        elif engine_kind == "colibri":
            warnings.append(f"No Fennec binary under {engine_root}; the legacy Colibri launcher {engine_path} is used through Python. Build Fennec with cargo build --release to replace it.")
        if build:
            if engine_kind == "fennec":
                if not shutil.which("cargo"):
                    missing.append("Rust: install Rust so cargo is on PATH (rustup) before building Fennec")
                add("build Fennec (cargo build --release)", ["cargo", "build", "--release"], engine_root,
                    [f"{engine_root}/Cargo.toml"])
                if not model:
                    warnings.append("No model selected, so cargo builds the engine only; pass --model /path/to/existing/model to make inference ready.")
            else:
                for tool, choices in (("make", ("make",)), ("C compiler", ("cc", "gcc", "clang"))):
                    if not any(shutil.which(choice) for choice in choices):
                        missing.append(f"Native toolchain: install {tool} (on Windows use a compatible MSYS2/MinGW toolchain)")
                if model and Path(model).is_dir():
                    add("build engine for the selected model", [python, engine_path, "build", "--model", model],
                        requires=[f"{engine_root}/c/Makefile"])
                    add("verify engine for the selected model", [python, engine_path, "info", "--model", model],
                        requires=[f"{model}/config.json"])
                else:
                    add("build selected native target", ["make", "-C", f"{engine_root}/c", native_target],
                        requires=[f"{engine_root}/c/Makefile"])
                    warnings.append(f"No model selected, so make builds the {native_target!r} target. With a model, the launcher picks its engine family automatically.")
        else:
            warnings.append("Engine binaries are not built or verified by this plan; run install with --build (needs a compatible toolchain).")
    if "jetbrains" in locations:
        warnings.append("JetBrains plugin is fetched only; build/package it separately using its README and JDK/Gradle requirements.")
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
        # Fennec is exec'd directly; only the legacy Colibri launcher needs Python.
        serve = [engine_path, "serve", "--auto-tier", "--model", model,
                 "--ctx", str(values["ctx"]), "--policy", values["policy"],
                 "--host", "127.0.0.1", "--port", "8184"]
        run.append({"label": "engine (after native build and model validation)",
                    "argv": serve if engine_kind == "fennec" else [python, *serve]})
    if "core" in locations:
        base = Path(shutil.which(python) or python).parent
        scripts = base if base.name.lower() in ("scripts", "bin") else base / ("Scripts" if windows else "bin")
        entry = str(scripts / ("fennec-agent.exe" if windows else "fennec-agent"))
        # The advice printed at the end is a command the user types; a bare
        # fennec-agent only prints argparse and quits.
        run.append({"label": "shared agent (reads shared config)", "argv": [entry, "serve"]})
    if "desktop" in locations:
        suffix = ".exe" if windows else ""
        release = Path(locations["desktop"]) / "target" / "release"
        # The gpui binary is the current shell. A checkout built without the
        # `gui` feature has only the earlier egui one, so the advice names a
        # binary that exists instead of one the user cannot run.
        names = [f"fennec-desktop-gui{suffix}", f"fennec-desktop{suffix}"]
        exe = next((n for n in names if (release / n).exists()), names[0])
        run.append({"label": "desktop", "cwd": locations["desktop"],
                    "argv": [str(release / exe)]})
    if "mobile" in features or dev.client_only or dev.termux:
        warnings.append("Mobile = shared core + coding-agent PWA, on the same device (Termux) or reached from another device over trusted HTTPS with FENNEC_APP_TOKEN. The engine starts on demand; there is no automatic public exposure.")
    if dev.client_only:
        actions = []
        missing.append("Android/iOS is PWA client mode only: execute this installation plan on a Windows/macOS/Linux host")
        run = []
    return {"features": list(features), "device": dev.to_dict(), "recommendation": rec,
            "config_path": str(path), "config": values, "explicit_keys": sorted(explicit),
            "repos": repos, "actions": actions, "build": build, "update": update,
            "native_target": native_target, "engine_launcher": engine_path if "engine" in locations else None,
            "engine_kind": engine_kind if "engine" in locations else None,
            "model_status": model_status, "missing": missing, "warnings": warnings,
            "run": run, "credits": manifest.get("credits", []),
            "config_write": bool(needs_python or build) and not dev.client_only,
            "cache_environment": {k: v for k, v in action_environment(values, {}).items()
                                  if k in ("HF_HOME", "PIP_CACHE_DIR", "npm_config_cache")}}


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
    parser.add_argument("--build", action="store_true", help="include/execute the engine build with install (cargo build --release for Fennec)")
    parser.add_argument("--native-target", default="fennec", help="legacy Colibri c/Makefile target when no model is selected (default fennec); unused for Fennec")
    parser.add_argument("--update", action="store_true", help="fast-forward clean matching tracked branches only")
    parser.add_argument("--json", action="store_true", help="machine-readable plan (plan mode only)")


def plan_from_args(manifest, args, features=None):
    from .config import KEYS
    overrides = {key: getattr(args, key, None) for key in KEYS}
    return build_plan(manifest, features or selected_features(manifest, args.features, args.all),
                      root=args.root, config=args.config, overrides=overrides,
                      build=args.build, native_target=args.native_target, update=args.update)
