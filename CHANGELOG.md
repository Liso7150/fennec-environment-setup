# Changelog

All notable changes to fennec-environment-setup, the suite detection and planning hub.

The sections below carry the published tag each change actually shipped in
(`Liso7150/fennec-environment-setup`, `v0.1.0` … `v1.0.0`). An earlier revision
of this file numbered the same history on a 1.x/2.x ladder that was never
published; those tags still exist locally (`v1.2.0` … `v2.0.0`) and each one
points at the same commit as a published `v0.x` tag. Dates are the tag publish
dates.

## [1.0.0] - 2026-11-24
Version 1.0.0 is the current release.
Test counts come from the
results hub (`benchmarks/artifacts/hub/RESULTS.json`, updated
2026-11-24T13:27:28Z): its `environment-setup` lane runs
`python -m unittest discover -s tests` and reports **89 passed / 0 failed / 0
skipped** after the supervised-core topology change (measured 2026-11-24). The hub's
unittest parser reads `Ran N tests` and carries no subtest figure. An earlier revision
of this file quoted 73 plus 111 subtests, which was stale. Suite-wide totals live in
the hub, not here.

### Added
- `--download-model`: an explicit install action for the Fennec-ready
  `Liso/qwen36-35b-colibri` int4 checkpoint (22.78 GiB). It installs
  `fennec-core[models]`, calls the core's existing completeness-checked downloader,
  and writes `<models_root>/qwen36-35b` into the planned config; plan and fetch-only
  modes never download it
- README hero published
- the `economy` concept (`Liso7150/fennec-economy`) and its `requires` entries on
  `inference`, `desktop` and `coding-agent`. The three Rust crates those features build all
  declare `fennec-economy = { path = "../fennec-economy" }`, so a plan that fetched them
  without it produced a `cargo build --release` that failed on a repo the hub had never
  heard of — measured: `no matching package named 'fennec-economy' found ... required by
  package 'fennec-rs v1.0.0'`.
- `crate_sibling()`: the two cargo builds now list the sibling `fennec-economy/Cargo.toml`
  (and `fennec-ui/Cargo.toml` for the desktop) in their `requires`, so `execute_plan` refuses
  the whole plan with a named missing file instead of failing halfway through an install.
- `web_python` config key (`FENNEC_WEB_PYTHON`): which interpreter runs Fennec's own
  browser loop, for a machine that already has one
- `browser-outlets` now fills the lane's own interpreter: it creates
  `~/.fennec/browser-tools/fennec/.venv` and pip-installs the pinned
  `browser-harness` transport into it, never into the interpreter the apps run on
- The Fennec engine rides the ordinary update path. `setup.py update` →
  `update_installed()` fast-forwards every installed checkout that is not an
  upstream source with `git pull --ff-only --no-rebase origin <branch>`
  (`fennec_setup/fetch.py:129`, present at `b3d0053` and unchanged on the
  working tree). What changed on the
  working tree is that the engine stopped being exempt: its `repos.json` entry
  carries no `upstream` flag, so `_is_upstream()` lets it through and it is pulled
  like core or the CLI rather than reported "untouched"; a dirty, diverged or
  wrong-branch checkout raises instead of moving. Guarded by
  `test_the_fennec_engine_is_pulled_like_any_other_fennec_checkout` and
  `test_an_update_pulls_a_fennec_checkout` (`tests/test_plan.py:640`, `:630`)
- A Colibri checkout is never chosen as an engine root. `ENGINE_FALLBACK` is gone
  from `fennec_setup/config.py`; `~/Colibri` and `D:/Colibri` survive only in
  `ENGINE_LEGACY` (`fennec_setup/config.py:43`), i.e. recognised so an old manifest
  migrates to the Fennec default. Proved on the working tree by
  `test_an_existing_colibri_checkout_is_never_chosen` and
  `test_a_legacy_colibri_manifest_root_is_upgraded_to_fennec`
  (`tests/test_config.py:204`, `:198`), and by `repos.json`, whose Colibri credit
  row now reads "historical credit only; Fennec is the one engine and no Colibri
  checkout is launched, built or fetched"

### Changed
- Setup plans now launch the core-supervised PWA/API on `127.0.0.1:8787`, pin the
  optional Rust auxiliary/chat service to `127.0.0.1:8788` with `FENNEC_AGENT_ADDR`,
  and keep the engine on `8184`. Coding readiness is a four-feature capability
  check, not health or chat (`40bc0a8`)

### Removed
- the legacy Colibri engine fallback that HEAD still carries: at `b3d0053` a
  Colibri checkout on disk was the fallback `engine_root`. On the working tree an
  unbuilt Fennec checkout is reported as missing, never handed to a `c/coli`
  launcher found beside it (`test_a_colibri_launcher_alone_is_not_an_engine`,
  `tests/test_plan.py:505`; `test_a_leftover_colibri_launcher_is_ignored_beside_a_fennec_binary`,
  `:196`)
- the second browser lane. `jev` is gone from `repos.json` (concept and credit), the
  `--jev-root` / `--web-engine` flags are gone, and so are the `jev_root` / `web_engine`
  config keys. Both are retired rather than unknown: a config written while they existed
  still loads, because `read_config` prunes the pair, and the next `write_config` drops it
- `web_task` has one lane: Fennec's own loop on a Liquid **d1** model — hosted **d1.2**
  with `LIQUID_API_KEY`, or a local model through `LIQUID_ENDPOINT` — plus an interpreter
  that can import `browser_harness.helpers`

## [0.9.0] - 2026-11-11

### Changed
- The hub installs the Rust core

## [0.8.0] - 2026-11-11

### Added
- A `browser-outlets` feature (Jev + OpenBot)

### Changed
- Upstream outlets stay cloned-only: the hub never patches them

## [0.7.1] - 2026-11-11

Version bump only.

## [0.7.0] - 2026-11-11

Version bump only.

## [0.6.0] - 2026-11-11

Version bump only.

## [0.5.0] - 2026-11-11

### Added
- `--version` reports the hub version

## [0.4.0] - 2026-11-11

### Changed
- Python is provisioned on PATH instead of into an engine venv
- The plan defaults to portable roots rather than a fixed drive

## [0.3.0] - 2026-11-10

### Changed
- Rebrand the setup hub to Fennec
- The engine is fetched from upstream instead of the private fork

## [0.2.0] - 2026-11-10

### Changed
- The embedding sidecar provisions without dragging torch into the shared interpreter

### Fixed
- Python build caches and packaged outputs are ignored

## [0.1.0] - 2026-11-10

The first published release. It carries the pre-tag history below.

### Added
- `setup.py update`
- A speech feature, with the PyAV < 18 pin that keeps faster-whisper working
- Termux detected as a local host; the mobile core planned on the phone
- Shared config unified with the core, engine plan driven by the launcher
- Offline tests

### Changed
- Mobile feature wording corrected: PWA with an on-demand engine
- The NCM concept removed (deleted by owner decision 2026-11-09)

### Credits
- Initial commit as colibri-environment-setup (Colibri suite; JustVugg/colibri
  + lyogavin/airllm)
