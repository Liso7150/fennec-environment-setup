![Fennec Environment Setup 1.0.0 — the hub that detects your device, plans a feature closure, fetches the right repos, provisions their dependencies, and writes one shared runtime configuration. Python 3.10+, standard library only, 73 tests, 10 features, 11 repos managed, MIT license.](docs/hero.png)

---

## At a glance

| Tests | Features | Repos managed | Third-party deps | Subcommands |
|:---:|:---:|:---:|:---:|:---:|
| 73 in 3 files | 10 | 11 | 0 (`openvino` optional) | 7 |

Counts from `tests/`, `repos.json` and `setup.py`. This is an argparse CLI, not an
installable package — run `setup.py` directly. The sole optional third-party import is
`openvino`, used to probe for an Intel NPU.

## Pipeline

```
 python setup.py
      │
      ├─ detect ──► device report + conservative recommendation (read-only)
      ├─ plan ────► dependency closure over the feature manifest (read-only)
      ├─ fetch ───► clone or verify checkouts (refuses foreign / dirty / ahead)
      ├─ install ─► fetch + provision deps + optional engine build
      │             └─► writes ~/.fennec/config.json only after every action succeeded
      └─ update ──► git pull --ff-only on installed clean Fennec checkouts
```

Servers are never started for you — the plan prints the exact commands to run.

## Prerequisites

| Requirement | Needed for |
|---|---|
| Python 3.10+ | everything |
| Git | fetch / install / update |
| Rust (`cargo`) | `install --build` only |

## Quick start

```bash
python setup.py detect                                      # device + recommended config
python setup.py plan --features inference,desktop,coding-agent
python setup.py plan --all --json                           # machine-readable, read-only
python setup.py install --features inference,desktop
python setup.py install --features inference --build --model <model-dir>
```

Or use the **[installer](https://github.com/Liso7150/fennec-installer)**, which bootstraps
this hub and walks you through feature selection interactively (`python install.py`).

## Commands

| Command | What it does |
|---|---|
| `detect [--disk <path>]` | OS, arch, CPU/RAM, NVIDIA/Vulkan/Apple GPUs, Intel NPU, free disk, Python |
| `features` | list installable features and their dependency closure |
| `plan --features … \| --all [--json]` | read-only action plan; `--dry-run` on fetch/install does the same |
| `fetch --features …` | clone or verify checkouts only |
| `install --features … [--build] [--model <model-dir>]` | provision deps, optionally build the engine, then write the config |
| `update` | fast-forward every installed clean checkout |
| `version` / `--version` | print `fennec-setup 1.0.0` |

| Action | Guarantee |
|---|---|
| `plan` / `--dry-run` | no clone, no install, no config write, no network mutation, no server |
| `fetch` | refuses a foreign origin, a dirty tree or local commits — a patched legacy engine is never reset behind your back |
| `install` | config written **only after every action succeeded** |
| `update` | `--ff-only`; dirty/ahead/foreign checkouts refused; upstream outlets never pulled — `fetch --update` and `update` both print a skip note for OpenBot, which moves only inside its own checkout |

## Features

| Feature | Requires | Notes |
|---|---|---|
| `inference` | engine, core, cli | local Fennec engine plus the shared runtime/CLI |
| `desktop` | engine, core, desktop, coding-agent | Electron app; packaging is a separate step |
| `coding-agent` | engine, core, coding-agent | shared agent server plus web/PWA UI |
| `jetbrains` | engine, core, jetbrains | IDE plugin; package it separately |
| `speech` | core | local STT sidecar on `127.0.0.1:8183`, started on demand |
| `npu` | core | accelerator sidecar only, **not** a decoder; prebuilt IR embedding model, no optimum-intel, no torch |
| `fine-tune` | engine, finetune | explicit opt-in; nothing downloads or trains implicitly |
| `mobile` | core, coding-agent | PWA on the same device (desktop or Termux phone); engine on demand |
| `browser-outlets` | core, openbot | `coworker_task` gets OpenBot, cloned only: nothing installed, patched or started. `web_task` needs no checkout at all — it is Fennec's own loop on a Liquid **d1** model, so it wants an endpoint to ask - a `LIQUID_API_KEY` for the hosted `d1.2`, or a `LIQUID_ENDPOINT` for the free local `d1-2B` - and an interpreter that can import `browser_harness.helpers`; this feature builds that interpreter as a venv of its own at `~/.fennec/browser-tools/fennec/.venv` and never touches the one the apps run on |
| `installer` | installer | download the portable bootstrap itself |

`desktop`, `coding-agent`, `jetbrains`, `speech` and `mobile` also provision the STT pins
`faster-whisper==1.2.1` and `av>=14,<18`. `npu` provisions `openvino`,
`openvino-tokenizers`, `numpy`, `huggingface-hub` and fetches the prebuilt IR embedding
model into `<models_root>/embed/bge-base-en-v1.5-int8-ov`.

## Layout

| Concept | Repo | Local path |
|---|---|---|
| Engine | `Liso7150/fennec` | `~/Fennec` |
| Shared core | `Liso7150/fennec-core` | `~/Fennec-Suite/fennec-core` |
| CLI wrapper | `Liso7150/fennec-cli` | `~/Fennec-Suite/fennec-cli` |
| Desktop app | `Liso7150/fennec-desktop` | `~/Fennec-Suite/fennec-desktop` |
| Coding agent | `Liso7150/fennec-coding-agent` | `~/Fennec-Suite/fennec-coding-agent` |
| JetBrains | `Liso7150/fennec-jetbrains` | `~/Fennec-Suite/fennec-jetbrains` |
| Installer | `Liso7150/fennec-installer` | `~/Fennec-Suite/fennec-installer` |
| Fine-tune | `Liso7150/fennec-finetune` | `~/Fennec-Suite/fennec-finetune` |
| OpenBot outlet | `CopilotKit/OpenBot` (upstream) | `~/Fennec-Suite/OpenBot` |

Pass `--root <path>` to keep every checkout and cache under one folder: `ROOT/engine`,
`ROOT/suite`, `ROOT/models`, `ROOT/cache`, with this hub at `ROOT/setup`. An older manifest
or config may still carry absolute drive-letter roots from a previous install; those are
recognised **only so they can be migrated**. A config that already points at a Colibri root
keeps it, and when nothing chose a root an existing `~/Colibri` checkout is the fallback
engine root.

The engine is built with `cargo build --release`. The plan probes `target/release`,
`target/debug` and the root for `fennec(.exe)` and launches the binary directly; only a
root with no Fennec binary falls back to `python c/coli`.

## Config contract

One file, `~/.fennec/config.json` (override with `FENNEC_CONFIG`), plus matching `FENNEC_*`
environment variables, is shared by every app in the suite. The workspace defaults to
`suite_root`. Caches (`HF_HOME`, pip, npm) live under `cache_root`, never inside the repos.
The optional outlets have keys too — `openbot_url` / `FENNEC_OPENBOT_URL`, and `web_python` /
`FENNEC_WEB_PYTHON`, which pins which interpreter runs Fennec's own browser loop when you do
not want the venv this hub builds — and they stay unwritten
unless you set them. The keys of the lane that used to be a second choice, `jev_root` and
`web_engine`, are retired: a config written back then still loads, because the pair is pruned
on read, and the next write leaves them out. Secrets never enter this
file; only `FENNEC_API_KEY` and `FENNEC_APP_TOKEN` carry them, and `LIQUID_API_KEY` (the
d1.2 decision API) live in the environment, never in the config; `LIQUID_ENDPOINT` is
a plain address, not a secret, and may be set or unset.

> [!NOTE]
> There is no shared virtualenv. Dependencies go into the interpreter the apps resolve
> (`python` on PATH, or the one you configure).

Phones can run the core and PWA locally in Termux, with the engine on demand when a
compatible small model exists; Android browsers and iOS are PWA clients of a Fennec host.

## Tests

```bash
python -m unittest discover -s tests -v     # 73 checks across 3 files
```

The suite is offline: hardware probes and git are injected, no checkout is cloned and no
server is started. The shared-config validator is compared against `fennec-core`'s
authoritative loader whenever a core checkout is available, and that comparison skips when
it is not.

## Credits

The engine is ours; the suite builds on upstream projects it does not fork or vendor:

| Upstream | Role |
|---|---|
| [Fennec](https://github.com/Liso7150/fennec) | the engine: one native Rust binary, OpenAI-compatible HTTP on `127.0.0.1:8184` |
| [Colibri](https://github.com/JustVugg/colibri) | base idea: device-tiered MoE inference; kept as the legacy `c/coli` fallback |
| [AirLLM](https://github.com/lyogavin/airllm) | layer-streamed inference and streamed LoRA training |
| [browser-harness](https://github.com/browser-use/browser-harness) | the CDP transport Fennec's own loop drives Chrome with, pip-installed from PyPI (pinned) into that venv; a dependency of the lane, never a checkout this hub edits |
| [OpenBot](https://github.com/CopilotKit/OpenBot) | coworker workspace behind `coworker_task`; its loopback AG-UI routes, used as upstream |
| [Liquid d1](https://api.liquid.ai/decisions/v1/systemone) | decision model behind `web_task`: Fennec's own loop asks it for one operation + element index per step. The hosted `d1.2` is a paid API (`LIQUID_API_KEY`); the free open-weight `d1-2B` answers the same call on loopback (`LIQUID_ENDPOINT`), nothing cloned or installed for either |