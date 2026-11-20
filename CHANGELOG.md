# Changelog

All notable changes to fennec-environment-setup, the suite detection and planning hub.

The sections below carry the published tag each change actually shipped in
(`Liso7150/fennec-environment-setup`, `v0.1.0` … `v0.9.0`). An earlier revision
of this file numbered the same history on a 1.x/2.x ladder that was never
published; those tags still exist locally (`v1.2.0` … `v2.0.0`) and each one
points at the same commit as a published `v0.x` tag. Dates are the tag publish
dates.

## [Unreleased] — 1.0.0 in preparation

Nothing at 1.0.0 is published. The newest release here is `v0.9.0`
(2026-11-11T19:29Z). Measured on the working tree: 73 tests pass
(`python -m unittest discover -s tests`, 2026-11-20).

### Added
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

### Removed
- the second browser lane. `jev` is gone from `repos.json` (concept and credit), the
  `--jev-root` / `--web-engine` flags are gone, and so are the `jev_root` / `web_engine`
  config keys. Both are retired rather than unknown: a config written while they existed
  still loads, because `read_config` prunes the pair, and the next `write_config` drops it
- `web_task` has one lane: Fennec's own loop on Liquid's hosted **d1.2**, which needs a
  `LIQUID_API_KEY` and an interpreter that can import `browser_harness.helpers`

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
