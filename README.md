![Fennec Environment Setup 1.0.0 — the hub that detects your device, plans a feature closure, fetches the right repos, provisions their dependencies, and writes one shared runtime configuration. Python 3.10+, standard library only, 89 tests, 10 features, 11 repos managed, MIT license.](docs/hero.png)

---

## At a glance

| Tests | Features | Repos managed | Required third-party deps | Subcommands |
|:---:|:---:|:---:|:---:|:---:|
| 89 in 3 files | 10 | 11 | 0 | 7 |

This is an argparse CLI, not an installable package; run `setup.py` directly. The only optional
third-party import is `openvino`, used to probe for an Intel NPU.

## Pipeline

```text
python setup.py detect    device report and conservative recommendation; read-only
python setup.py plan      dependency closure and actions; read-only
python setup.py fetch     clone or verify selected checkouts
python setup.py install   fetch, provision, optionally build, then write config
python setup.py update    fast-forward installed clean checkouts
```

The hub never starts servers. It writes `~/.fennec/config.json` only after every install action
succeeds. Planning and fetch-only operations never download a model.

## Service boundaries

| Service | Default address | Role |
|---|---|---|
| `fennec-core/server.py` | `127.0.0.1:8787` | canonical supervised PWA/API; advertises `supervised-agent`, `tool-execution`, `per-call-approvals`, and `agent-polling` |
| Rust/Axum `fennec-agent` | `127.0.0.1:8788` | auxiliary chat and integrations; advertises `chat-only`; never a coding fallback |
| Native Fennec engine | `127.0.0.1:8184` | inference and telemetry, supervised by core |

The Rust service binds exactly to `127.0.0.1:8788` unless `FENNEC_AGENT_ADDR` overrides it. A
bind error does not select port 8787 or another port. The PWA assets live in
`fennec-coding-agent/web`, but core serves them at the canonical 8787 origin.

## Prerequisites

| Requirement | Needed for |
|---|---|
| Python 3.10+ | all commands |
| Git | fetch, install, and update |
| Rust (`cargo`) | `install --build` |

## Quick start

```bash
python setup.py detect                                      # inspect the device
python setup.py plan --features inference,desktop,coding-agent
python setup.py plan --all --json                           # read-only machine-readable plan
python setup.py install --features inference,desktop        # provision and write config
python setup.py install --features inference --download-model
```

`--download-model` requires the selected core checkout and downloads the Fennec-ready
[`Liso/qwen36-35b-colibri`](https://huggingface.co/Liso/qwen36-35b-colibri) checkpoint into
`<models-root>/qwen36-35b`, then selects it. The download is up to **22.78 GiB**, keeps existing
files, supports resume, and verifies the required JSON, tokenizer, and safetensors files.
`HF_TOKEN` is read only from the environment and is never written to the config.

To use an existing model instead, pass `--model <model-dir>`. `--model` and `--download-model`
are mutually exclusive. A `--dry-run` or fetch-only command does not download either selection.

The engine is built with `cargo build --release`. The hub probes `target/release`,
`target/debug`, and the engine root for `fennec(.exe)` and invokes that binary directly; it does
not substitute another engine.

Alternatively, use the [installer](https://github.com/Liso7150/fennec-installer) for an
interactive feature-selection flow:

```bash
python install.py
```

## Commands

| Command | Purpose |
|---|---|
| `detect [--disk <path>]` | report OS, architecture, CPU/RAM, GPUs, Intel NPU, disk, and Python |
| `features` | list features and their dependency closure |
| `plan --features … \| --all [--json]` | print a read-only action plan |
| `fetch --features …` | clone or verify checkouts only |
| `install --features … [--build] [--model <dir> \| --download-model]` | provision dependencies, optionally build, and write config |
| `update` | fast-forward installed clean checkouts |
| `version` / `--version` | print `fennec-setup 1.0.0` |

| Action | Guarantee |
|---|---|
| `plan` / `--dry-run` | no clone, install, model download, config write, network mutation, or server |
| `fetch` | refuses foreign origins, dirty trees, and local commits |
| `install` | writes config only after every action succeeds |
| `update` | uses `--ff-only`; dirty, ahead, and foreign checkouts are refused |

OpenBot is an upstream outlet. `fetch --update` and `update` do not pull it; it moves only
inside its own checkout.

## Features

| Feature | Requires | Notes |
|---|---|---|
| `inference` | engine, core, cli | native Fennec inference with the shared runtime and CLI |
| `desktop` | engine, core, desktop, coding-agent | native Rust/GPUI app; packaging is separate |
| `coding-agent` | engine, core, coding-agent | core-hosted supervised PWA/API plus auxiliary Rust chat/integrations |
| `jetbrains` | engine, core, jetbrains | IDE plugin; package it separately |
| `speech` | core | local STT sidecar on `127.0.0.1:8183`, started on demand |
| `npu` | core | accelerator sidecar, not a decoder; prebuilt IR embedding model, no Optimum Intel or Torch |
| `fine-tune` | engine, finetune | explicit opt-in; no implicit download or training |
| `mobile` | core, coding-agent | same-device PWA on desktop or Termux; engine on demand |
| `browser-outlets` | core, openbot | `coworker_task` uses OpenBot; `web_task` uses Fennec's Liquid d1 browser loop |
| `installer` | installer | fetch the portable bootstrap itself |

`desktop`, `coding-agent`, `jetbrains`, `speech`, and `mobile` provision
`faster-whisper==1.2.1` and `av>=14,<18`. `npu` provisions `openvino`,
`openvino-tokenizers`, NumPy, and `huggingface-hub`, and fetches the embedding model into
`<models-root>/embed/bge-base-en-v1.5-int8-ov`.

The browser outlet builds its own virtual environment under
`~/.fennec/browser-tools/fennec/.venv`; it never changes the interpreter used by the apps.
`web_task` needs either `LIQUID_API_KEY` for hosted d1.2 or `LIQUID_ENDPOINT` for a local
System One-compatible d1-2B server. Fennec's engine exposes
`/v1/chat/completions`, not `/v1/systemone`, so it is not a substitute for that decision
endpoint.

## Layout

| Concept | Repository | Default checkout |
|---|---|---|
| Engine | `Liso7150/fennec` | `~/Fennec` |
| Shared core | `Liso7150/fennec-core` | `~/Fennec-Suite/fennec-core` |
| CLI | `Liso7150/fennec-cli` | `~/Fennec-Suite/fennec-cli` |
| Desktop | `Liso7150/fennec-desktop` | `~/Fennec-Suite/fennec-desktop` |
| Coding agent | `Liso7150/fennec-coding-agent` | `~/Fennec-Suite/fennec-coding-agent` |
| Shared UI | `Liso7150/fennec-ui` | `~/Fennec-Suite/fennec-ui` |
| Shared economy | `Liso7150/fennec-economy` | `~/Fennec-Suite/fennec-economy` |
| JetBrains | `Liso7150/fennec-jetbrains` | `~/Fennec-Suite/fennec-jetbrains` |
| Installer | `Liso7150/fennec-installer` | `~/Fennec-Suite/fennec-installer` |
| Fine-tune | `Liso7150/fennec-finetune` | `~/Fennec-Suite/fennec-finetune` |
| OpenBot | `CopilotKit/OpenBot` | `~/Fennec-Suite/OpenBot` |

Pass `--root <path>` to place `engine`, `suite`, `models`, and `cache` under one directory, with
this hub at `ROOT/setup`. Older absolute roots are recognized only for migration. Fennec is the
only supported engine; a leftover Colibri checkout is never selected.

## Config contract

Every app reads `~/.fennec/config.json`, or the file selected by `FENNEC_CONFIG`, plus matching
`FENNEC_*` environment variables. The workspace defaults to `suite_root`; Hugging Face and pip
caches live under `cache_root`, not inside repositories.

Optional outlet keys are `openbot_url` / `FENNEC_OPENBOT_URL` and `web_python` /
`FENNEC_WEB_PYTHON`. Retired `jev_root` and `web_engine` entries are pruned when loaded and omitted
on the next write. Secrets never enter the JSON file: `FENNEC_API_KEY`, `FENNEC_APP_TOKEN`, and
`LIQUID_API_KEY` remain environment variables. `LIQUID_ENDPOINT` is a non-secret address.

There is no shared virtualenv. Dependencies go into the interpreter resolved from `python` or the
configured interpreter path.

Phones can run core and the PWA locally in Termux when a compatible model is available. Android
browsers and iOS use a PWA client connected to a Fennec host.

## Tests

```bash
python -m unittest discover -s tests -v     # 89 checks across 3 files
```

Tests are offline: hardware probes and Git are injected, no checkout is cloned, and no server is
started. When a core checkout is available, its configuration loader is compared with the shared
validator; that comparison otherwise skips.

## Credits

| Upstream | Role |
|---|---|
| [Fennec](https://github.com/Liso7150/fennec) | native Rust inference engine on `127.0.0.1:8184` |
| [Colibri](https://github.com/JustVugg/colibri) | device-tiered MoE design lineage; never fetched or launched by this hub |
| [AirLLM](https://github.com/lyogavin/airllm) | layer-streamed inference and streamed LoRA training lineage |
| [browser-harness](https://github.com/browser-use/browser-harness) | pinned CDP transport installed into the browser-tools environment |
| [OpenBot](https://github.com/CopilotKit/OpenBot) | upstream coworker workspace behind `coworker_task` |
| [Liquid d1](https://api.liquid.ai/decisions/v1/systemone) | decision model used by `web_task`; hosted d1.2 is paid, local d1-2B requires a compatible server |
