import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest import mock

from fennec_setup import devices  # noqa: E402
from fennec_setup.devices import Device, detect, recommend  # noqa: E402
from fennec_setup.fetch import load_manifest, repos_for_features  # noqa: E402
from fennec_setup.provision import (BROWSER_TRANSPORT, browser_env, build_plan,  # noqa: E402
                                    print_plan, resolve_engine, selected_features)

CONCEPTS = ("engine", "core", "cli", "desktop", "coding-agent", "ui", "economy",
            "jetbrains", "installer", "finetune")
# Someone else's program: cloned when the user opts into the outlets, never
# installed into, patched, or pulled by an ordinary update.
UPSTREAMS = (("openbot", "CopilotKit/OpenBot"),)
WINDOWS = sys.platform.startswith("win")
FENNEC_EXE = "fennec.exe" if WINDOWS else "fennec"


def manifest():
    return {
        "repos": [{"concept": concept, "repo": f"Liso7150/fennec-{concept}", "branch": "main"}
                  for concept in CONCEPTS]
                 + [{"concept": concept, "repo": repo, "branch": "main", "upstream": True}
                    for concept, repo in UPSTREAMS],
        "features": {
            "inference": {"requires": ["engine", "core", "cli", "economy"]},
            "desktop": {"requires": ["engine", "core", "desktop", "coding-agent", "ui", "economy"]},
            "coding-agent": {"requires": ["engine", "core", "coding-agent", "economy"]},
            "jetbrains": {"requires": ["engine", "core", "jetbrains"]},
            "npu": {"requires": ["core"],
                    "pip": ["openvino", "openvino-tokenizers", "numpy", "huggingface-hub"]},
            "fine-tune": {"requires": ["engine", "finetune"], "pip": ["airllm", "torch"]},
            "mobile": {"requires": ["core", "coding-agent"]},
            "browser-outlets": {"requires": ["core", "openbot"]},
            "installer": {"requires": ["installer"]},
        },
        "credits": [],
    }


def device(**overrides):
    fields = dict(os="Windows" if sys.platform.startswith("win") else "Linux", os_version="test",
                  arch="x86_64", cpu_cores=8, ram_gb=32.0, gpu=[], npu=False, cuda=False,
                  vulkan=False, metal=False, client_only=False, disk_free_gb=100.0,
                  disk_path=".", python="3.12", tier="cpu")
    fields.update(overrides)
    return Device(**fields)


def slash(path):
    return str(path).replace("\\", "/")


class DeviceDetectionTests(unittest.TestCase):
    def probe_patches(self, system):
        return (mock.patch.object(devices.platform, "system", return_value=system),
                mock.patch.object(devices, "_ram_gb", return_value=8.0),
                mock.patch.object(devices, "_nvidia_gpus", return_value=[]),
                mock.patch.object(devices, "_vulkan_gpus", return_value=[]),
                mock.patch.object(devices, "_has_intel_npu", return_value=False))

    def test_termux_is_local_not_client_only(self):
        patches = self.probe_patches("Android")
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                mock.patch.dict("os.environ", {"PREFIX": "/data/data/com.termux/files/usr"}):
            dev = detect(disk_path=ROOT)
        self.assertEqual(dev.os, "Android")
        self.assertTrue(dev.termux)
        self.assertFalse(dev.client_only)
        self.assertEqual(recommend(dev)["features"], ["mobile"])
        self.assertIn("Termux", recommend(dev)["note"])

    def test_android_browser_stays_client_only(self):
        patches = self.probe_patches("Android")
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                mock.patch.dict("os.environ", {"PREFIX": "", "ANDROID_ROOT": ""}):
            dev = detect(disk_path=ROOT)
        self.assertTrue(dev.client_only)
        self.assertFalse(dev.termux)
        self.assertEqual(recommend(dev)["features"], ["mobile"])
        self.assertIn("PWA client only", recommend(dev)["note"])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.config = self.home / "config.json"
        self.overrides = {"engine_root": str(self.home / "engine"),
                          "suite_root": str(self.home / "suite"),
                          "models_root": str(self.home / "models"),
                          "cache_root": str(self.home / "cache")}

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, features=("inference",), dev=None, **kwargs):
        return build_plan(manifest(), list(features), overrides=self.overrides,
                          config=str(self.config), environ={}, home=self.home,
                          device=dev or device(), **kwargs)

    def test_planning_never_writes_config_or_fetches(self):
        plan = self.plan()
        self.assertFalse(self.config.exists())
        self.assertEqual(plan["config"]["workspace"], plan["config"]["suite_root"])
        labels = [action["label"] for action in plan["actions"]]
        self.assertIn("install editable core", labels)
        self.assertIn("build CLI (cargo build --release)", labels)
        self.assertTrue(plan["config_write"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_plan(plan)
        self.assertIn("Plan only", out.getvalue())
        engine = next(repo for repo in plan["repos"] if repo["concept"] == "engine")
        self.assertEqual(engine["local"], slash(self.home / "engine"))

    def test_cargo_builds_require_their_path_dependency_checkouts(self):
        # fennec-cli and fennec-desktop both say `fennec-economy = { path = "../fennec-economy" }`
        # (and the desktop adds `../fennec-ui`). A plan that fetches the app but not the crate
        # reaches a cargo error no feature line explains, so the sibling manifests are
        # preflighted like every other requirement.
        cli = next(a for a in self.plan()["actions"]
                   if a["label"] == "build CLI (cargo build --release)")
        self.assertIn(slash(self.home / "suite/fennec-economy/Cargo.toml"), cli["requires"])
        desktop = next(a for a in self.plan(features=("desktop",))["actions"]
                       if a["label"] == "build desktop (cargo build --release)")
        self.assertIn(slash(self.home / "suite/fennec-economy/Cargo.toml"), desktop["requires"])
        self.assertIn(slash(self.home / "suite/fennec-ui/Cargo.toml"), desktop["requires"])

    def test_the_shipped_manifest_fetches_what_its_cargo_builds_compile(self):
        shipped = load_manifest(ROOT / "repos.json")
        concepts = {repo["concept"]: repo for repo in shipped["repos"]}
        self.assertIn("economy", concepts)
        self.assertEqual(concepts["economy"]["repo"], "Liso7150/fennec-economy")
        # Each of these three selects a checkout whose Cargo.toml says
        # `fennec-economy = { path = "../fennec-economy" }`, and two of them are compiled by
        # this hub. `mobile` is exempt: it takes the PWA assets as files and compiles nothing.
        for name in ("inference", "desktop", "coding-agent"):
            needed = set(shipped["features"][name]["requires"])
            self.assertIn("economy", needed,
                          f"{name} selects a crate that path-depends on fennec-economy without fetching it")
        self.assertIn("ui", set(shipped["features"]["desktop"]["requires"]),
                      "the desktop build has no fennec-ui to compile against")

    def test_machine_readable_plan(self):
        plan = self.plan()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_plan(plan, machine=True)
        self.assertIn('"config_path"', out.getvalue())

    def test_client_only_has_no_actions(self):
        plan = self.plan(features=("mobile",), dev=device(os="Android", client_only=True, tier="client"))
        self.assertEqual(plan["actions"], [])
        self.assertFalse(plan["config_write"])
        self.assertTrue(any("PWA client mode" in message for message in plan["missing"]))

    def test_termux_plan_installs_the_local_core(self):
        plan = self.plan(features=("mobile",), dev=device(termux=True))
        labels = [action["label"] for action in plan["actions"]]
        self.assertIn("verify pip", labels)
        self.assertNotIn("create shared venv", labels)
        self.assertIn("install editable core", labels)
        self.assertTrue(plan["config_write"])
        self.assertTrue(plan["run"])
        self.assertTrue(any("same device" in message for message in plan["warnings"]))

    def fennec_binary(self, rel="target/release"):
        """A fake built engine; nothing in these tests ever executes it."""
        binary = self.home / "engine" / rel / FENNEC_EXE
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"")
        return slash(binary)

    def colibri_launcher(self):
        launcher = self.home / "engine/c/coli"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("# legacy launcher\n", encoding="utf-8")
        return slash(launcher)

    def selected_model(self):
        model = self.home / "model"
        model.mkdir(parents=True, exist_ok=True)
        (model / "config.json").write_text("{}", encoding="utf-8")
        return model

    def test_fennec_binary_wins_over_the_colibri_launcher_in_the_same_root(self):
        binary = self.fennec_binary()
        self.colibri_launcher()
        model = self.selected_model()
        plan = build_plan(manifest(), ["inference"], overrides=dict(self.overrides, model=str(model)),
                          config=str(self.config), environ={}, home=self.home, device=device())
        self.assertEqual(plan["engine_kind"], "fennec")
        self.assertEqual(plan["engine_launcher"], binary)
        self.assertEqual(plan["run"][0]["argv"][0], binary)
        self.assertFalse(any("make" in action["argv"] for action in plan["actions"]))
        self.assertFalse(any("Engine binary missing" in message for message in plan["missing"]))

    def test_fennec_build_is_cargo_release_in_the_engine_root(self):
        root = self.home / "engine"
        (root / "Cargo.toml").parent.mkdir(parents=True, exist_ok=True)
        (root / "Cargo.toml").write_text("[package]\nname = 'fennec'\n", encoding="utf-8")
        plan = self.plan(build=True)
        build = next(action for action in plan["actions"]
                     if action["label"] == "build Fennec (cargo build --release)")
        self.assertEqual(build["argv"], ["cargo", "build", "--release"])
        self.assertEqual(build["cwd"], slash(root))
        self.assertEqual(build["requires"], [slash(root / "Cargo.toml")])
        joined = " ".join(" ".join(action["argv"]) for action in plan["actions"])
        self.assertNotIn("make", [action["argv"][0] for action in plan["actions"]])
        self.assertNotIn("c/coli", joined)
        self.assertEqual(plan["engine_kind"], "fennec")

    def test_missing_cargo_is_reported_as_a_rust_requirement(self):
        from fennec_setup import provision
        real_which = provision.shutil.which
        with mock.patch.object(provision.shutil, "which",
                               side_effect=lambda tool: None if tool == "cargo" else real_which(tool)):
            plan = self.plan(build=True)
        self.assertTrue(any("cargo" in message and "Rust" in message for message in plan["missing"]),
                        plan["missing"])
        self.assertTrue(any(action["argv"] == ["cargo", "build", "--release"] for action in plan["actions"]))

    def test_fennec_serve_advice_has_no_python_prefix(self):
        binary = self.fennec_binary()
        model = self.selected_model()
        plan = build_plan(manifest(), ["inference"], overrides=dict(self.overrides, model=str(model)),
                          config=str(self.config), environ={}, home=self.home, device=device())
        serve = plan["run"][0]["argv"]
        self.assertEqual(serve, [binary, "serve", "--auto-tier", "--model", slash(model),
                                 "--ctx", str(plan["config"]["ctx"]), "--policy", plan["config"]["policy"],
                                 "--host", "127.0.0.1", "--port", "8184"])
        self.assertNotIn(plan["config"]["python"], serve)

    def test_an_unbuilt_root_still_names_the_release_binary(self):
        plan = self.plan(build=True)
        expected = slash(self.home / "engine/target/release" / FENNEC_EXE)
        self.assertEqual(plan["engine_launcher"], expected)
        self.assertEqual(plan["engine_kind"], "fennec")
        self.assertTrue(any(expected in message for message in plan["missing"]), plan["missing"])

    def test_legacy_colibri_root_keeps_the_python_launcher_advice(self):
        model = self.selected_model()
        launcher = self.colibri_launcher()
        overrides = dict(self.overrides, model=str(model))
        plan = build_plan(manifest(), ["inference"], overrides=overrides, build=True,
                          config=str(self.config), environ={}, home=self.home, device=device())
        labels = [action["label"] for action in plan["actions"]]
        self.assertIn("build engine for the selected model", labels)
        self.assertIn("verify engine for the selected model", labels)
        build = next(action for action in plan["actions"]
                     if action["label"] == "build engine for the selected model")
        self.assertEqual(build["argv"][1:], [launcher, "build", "--model", slash(model)])
        self.assertEqual(build["requires"], [slash(self.home / "engine/c/Makefile")])
        serve = plan["run"][0]["argv"]
        self.assertEqual(serve[:3], [plan["config"]["python"], launcher, "serve"])
        self.assertIn("--auto-tier", serve)
        self.assertEqual(serve[serve.index("--host") + 1], "127.0.0.1")
        self.assertEqual(serve[serve.index("--port") + 1], "8184")
        self.assertEqual(plan["engine_launcher"], launcher)
        self.assertEqual(plan["engine_kind"], "colibri")
        self.assertTrue(any("legacy Colibri launcher" in message for message in plan["warnings"]))

    def test_legacy_build_without_a_model_uses_a_real_make_target(self):
        self.colibri_launcher()
        plan = self.plan(build=True)
        action = next(action for action in plan["actions"] if "native target" in action["label"])
        self.assertEqual(action["argv"][-1], "fennec")
        self.assertNotIn(".exe", " ".join(action["argv"]))
        self.assertEqual(action["requires"], [slash(self.home / "engine/c/Makefile")])

    def test_npu_feature_provisions_the_sidecar_packages_and_ir_model(self):
        plan = self.plan(features=("inference", "npu"))
        labels = [action["label"] for action in plan["actions"]]
        self.assertIn("install selected optional dependencies", labels)
        self.assertIn("fetch the sidecar embedding model (prebuilt OpenVINO IR)", labels)
        install = next(action for action in plan["actions"]
                       if action["label"] == "install selected optional dependencies")
        for package in ("openvino", "openvino-tokenizers", "numpy", "huggingface-hub"):
            self.assertIn(package, install["argv"])
        for banned in ("optimum", "torch", "nncf"):
            self.assertFalse(any(banned in package for package in install["argv"]),
                             f"{banned} would be dragged into the shared interpreter")
        fetch = next(action for action in plan["actions"]
                     if action["label"] == "fetch the sidecar embedding model (prebuilt OpenVINO IR)")
        self.assertIn("OpenVINO/bge-base-en-v1.5-int8-ov", " ".join(fetch["argv"]))
        self.assertEqual(fetch["argv"][-1].replace("\\", "/"),
                         slash(self.home / "models") + "/embed/bge-base-en-v1.5-int8-ov")
        self.assertTrue(any("sidecar" in message for message in plan["warnings"]))

    def test_browser_outlets_clone_openbot_and_fill_fennecs_own_env(self):
        plan = self.plan(features=("inference", "browser-outlets"))
        locations = {repo["concept"]: repo["local"] for repo in plan["repos"]}
        self.assertEqual(locations["openbot"], slash(self.home / "suite/OpenBot"))
        self.assertNotIn("jev", locations, "the deleted lane must not return as a checkout")
        venv, venv_python = browser_env(self.home, windows=WINDOWS)
        by_label = {action["label"]: action for action in plan["actions"]}
        self.assertEqual(by_label["create Fennec's browser env"]["argv"][-1], venv)
        self.assertEqual(by_label["install the browser transport"]["argv"][0], venv_python)
        self.assertIn(BROWSER_TRANSPORT, by_label["install the browser transport"]["argv"])
        # Neither step may declare a requires entry: execute_plan checks them all before it
        # runs the first action, and the interpreter the second calls is what the first makes.
        self.assertEqual(by_label["create Fennec's browser env"]["requires"], [])
        self.assertEqual(by_label["install the browser transport"]["requires"], [])
        # The transport is the one package that must never reach the shared interpreter,
        # because that is where the desktop, the PWA server and the CLI glue all run.
        others = " ".join(" ".join(action["argv"]) for action in plan["actions"]
                          if action["label"] != "install the browser transport")
        self.assertNotIn("browser-harness", others)
        for action in plan["actions"]:
            self.assertNotIn("OpenBot", " ".join(action["argv"]),
                             f"{action['label']} would install into upstream")
            self.assertNotEqual(action.get("cwd"), locations["openbot"],
                                f"{action['label']} would run inside an outlet checkout")
        self.assertTrue(any("cloned only" in message for message in plan["warnings"]))
        self.assertTrue(any("loopback" in message for message in plan["warnings"]))
        self.assertTrue(any("the Fennec browser env" in message for message in plan["warnings"]),
                        plan["warnings"])
        self.assertFalse([m for m in plan["warnings"] if "TypeSafe" in m or "jev" in m.lower()],
                         "a warning about a lane that no longer exists")
        # web_task's lane is not a checkout, so it is the one thing the clone notes would
        # never mention: a paid decision API and a browser-capable interpreter.
        self.assertTrue(any("d1.2" in message and "LIQUID_API_KEY" in message
                            for message in plan["warnings"]), plan["warnings"])
        # The shared core is what drives both, so it is the only thing installed.
        self.assertIn("install editable core", [action["label"] for action in plan["actions"]])

    def test_one_outlet_lane_is_not_reported_as_no_lane(self):
        # The shipped feature always brings both halves, but the notes are keyed on the
        # locations and the feature name, so a single-purpose plan must describe only itself.
        picked = manifest()
        picked["features"]["coworker"] = {"requires": ["core", "openbot"]}
        picked["features"]["plain-core"] = {"requires": ["core"]}
        with_clone = build_plan(picked, ("inference", "coworker"), overrides=self.overrides,
                                config=str(self.config), environ={}, home=self.home,
                                device=device())
        self.assertFalse(any("coworker_task stays unadvertised" in message
                             for message in with_clone["warnings"]), with_clone["warnings"])
        self.assertFalse([action for action in with_clone["actions"] if "browser" in action["label"]],
                         "an unselected feature must not create a venv")
        without_clone = build_plan(picked, ("inference", "plain-core"), overrides=self.overrides,
                                   config=str(self.config), environ={}, home=self.home,
                                   device=device())
        self.assertTrue(any("coworker_task stays unadvertised" in message
                            for message in without_clone["warnings"]), without_clone["warnings"])
        # No OpenBot checkout is not the same as no web_task: Fennec's own loop needs only
        # an interpreter with the transport and a key.
        self.assertTrue(any("web_task is unaffected" in message
                            for message in without_clone["warnings"]), without_clone["warnings"])

    def test_shipped_manifest_covers_every_sidecar_import(self):
        packages = load_manifest()["features"]["npu"]["pip"]
        for package in ("openvino", "openvino-tokenizers", "numpy", "huggingface-hub"):
            self.assertIn(package, packages)
        self.assertFalse([p for p in packages if "optimum" in p or p == "torch"],
                         "the IR model is downloaded, so no exporter belongs in the sidecar set")

    def test_the_shared_agent_advice_is_a_runnable_command(self):
        plan = self.plan()
        agent = next(command for command in plan["run"] if "shared agent" in command["label"])
        # A bare fennec-agent only prints "the following arguments are required".
        self.assertEqual(agent["argv"][-1], "serve")
        self.assertIn("fennec-agent", Path(agent["argv"][0]).name)

    def test_a_core_without_outlets_says_which_tools_stay_off(self):
        plan = self.plan(features=("inference",))
        self.assertTrue(any("web_task" in message and "coworker_task" in message
                            for message in plan["warnings"]), plan["warnings"])
        # No checkout is not the same as no web_task: Fennec's own loop needs only an
        # interpreter with the transport and a key.
        self.assertTrue(any("coworker_task stays unadvertised" in message
                            for message in plan["warnings"]), plan["warnings"])
        with_outlets = self.plan(features=("inference", "browser-outlets"))
        self.assertFalse(any("No browser outlet selected" in message
                             for message in with_outlets["warnings"]))

    def test_unknown_feature_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(features=("does-not-exist",))

    def test_selection_requires_explicit_choice(self):
        with self.assertRaises(ValueError):
            selected_features(manifest(), None)


class EngineResolverTests(unittest.TestCase):
    """The plan-side copy of fennec-core's canonical engine rule."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def touch(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return slash(path)

    def test_release_then_debug_then_root(self):
        root_binary = self.touch(FENNEC_EXE)
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS), (root_binary, "fennec", True))
        debug = self.touch(f"target/debug/{FENNEC_EXE}")
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS), (debug, "fennec", True))
        release = self.touch(f"target/release/{FENNEC_EXE}")
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS), (release, "fennec", True))

    def test_fennec_wins_when_both_engines_are_installed(self):
        binary = self.touch(f"target/release/{FENNEC_EXE}")
        self.touch("c/coli")
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS), (binary, "fennec", True))

    def test_colibri_launcher_is_the_fallback(self):
        launcher = self.touch("c/coli")
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS), (launcher, "colibri", True))

    def test_posix_never_picks_a_windows_executable(self):
        self.touch("target/release/fennec.exe")
        launcher = self.touch("c/coli")
        self.assertEqual(resolve_engine(self.root, windows=False), (launcher, "colibri", True))
        binary = self.touch("target/release/fennec")
        self.assertEqual(resolve_engine(self.root, windows=False), (binary, "fennec", True))

    def test_an_unbuilt_checkout_expects_the_release_binary(self):
        self.touch("Cargo.toml")
        self.touch("c/Makefile")
        expected = slash(self.root / "target/release" / FENNEC_EXE)
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS), (expected, "fennec", False))

    def test_an_empty_root_expects_fennec_and_a_c_tree_expects_colibri(self):
        expected = slash(self.root / "target/release" / FENNEC_EXE)
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS), (expected, "fennec", False))
        (self.root / "c").mkdir()
        self.assertEqual(resolve_engine(self.root, windows=WINDOWS),
                         (slash(self.root / "c/coli"), "colibri", False))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest()

    def test_feature_closure(self):
        concepts = {repo["concept"] for repo in self.manifest["repos"]}
        for name, spec in self.manifest["features"].items():
            with self.subTest(feature=name):
                self.assertLessEqual(set(spec["requires"]), concepts)
                self.assertLessEqual(set(spec.get("features", [])), set(self.manifest["features"]))

    def test_all_includes_installer_training_and_mobile(self):
        selected = selected_features(self.manifest, None, all_features=True)
        for name in ("installer", "fine-tune", "mobile", "desktop"):
            self.assertIn(name, selected)

    def test_every_feature_resolves_repos(self):
        for name in self.manifest["features"]:
            with self.subTest(feature=name):
                self.assertTrue(repos_for_features(self.manifest, [name]))

    def test_cycle_and_unknown_feature_detected(self):
        cyclic = manifest()
        cyclic["features"]["a"] = {"requires": [], "features": ["b"]}
        cyclic["features"]["b"] = {"requires": [], "features": ["a"]}
        with self.assertRaises(ValueError):
            repos_for_features(cyclic, ["a"])
        with self.assertRaises(ValueError):
            repos_for_features(cyclic, ["missing"])

    def test_the_engine_is_the_fennec_repo_and_is_updatable(self):
        engine = next(repo for repo in self.manifest["repos"] if repo["concept"] == "engine")
        self.assertEqual(engine["repo"], "Liso7150/fennec")
        self.assertEqual(engine["branch"], "main")
        self.assertFalse(engine.get("upstream"), "Fennec is our own repo, so update may pull it")
        self.assertEqual(self.manifest["engine_root"], "~/Fennec")
        self.assertIn("cargo build --release", engine["description"])
        self.assertIn("c/coli", engine["description"], "the legacy fallback stays documented")
        self.assertIn("Fennec", self.manifest["features"]["inference"]["description"])

    def test_upstream_credits_present(self):
        credits = self.manifest.get("credits", [])
        upstreams = " ".join(entry["upstream"] for entry in credits)
        self.assertIn("JustVugg/colibri", upstreams)
        self.assertIn("lyogavin/airllm", upstreams)
        self.assertIn("CopilotKit/OpenBot", upstreams)
        # The loop's transport is third-party code the installer pulls, so it is credited
        # like the upstreams that are only cloned.
        self.assertIn("browser-use/browser-harness", upstreams)
        # d1.2 spends money on every browser step, so it is credited like the code upstreams.
        self.assertIn("api.liquid.ai", upstreams)
        self.assertFalse([entry for entry in credits if "jev" in entry["upstream"].lower()],
                         "a credit for a lane that no longer exists")
        engine = next(entry for entry in credits if "JustVugg/colibri" in entry["upstream"])
        self.assertIn("base idea", engine["role"])
        liquid = next(entry for entry in credits if "api.liquid.ai" in entry["upstream"])
        self.assertIn("LIQUID_API_KEY", liquid["role"])

    def test_outlet_upstreams_are_cloned_only(self):
        repos = {repo["concept"]: repo for repo in self.manifest["repos"]}
        self.assertEqual(repos["openbot"]["repo"], "CopilotKit/OpenBot")
        self.assertTrue(repos["openbot"].get("upstream"))
        self.assertNotEqual(repos["openbot"]["repo"].split("/")[0], self.manifest["account"])
        self.assertFalse([repo for repo in self.manifest["repos"]
                          if "jev" in repo["concept"]], "Jev was deleted, not made optional")
        self.assertFalse([repo for repo in self.manifest["repos"]
                          if repo.get("upstream") and repo["repo"].startswith("Liso7150/")],
                         "a Fennec-owned checkout must stay updatable by 'update'")
        spec = self.manifest["features"]["browser-outlets"]
        self.assertEqual(set(spec["requires"]), {"core", "openbot"})
        self.assertNotIn("pip", spec, "OpenBot is cloned, never installed into")
        # The lane's own dependency goes to the venv, so the description has to say where.
        self.assertIn("browser-harness", spec["description"])
        self.assertIn("~/.fennec/browser-tools/fennec/.venv", spec["description"])

    def test_all_includes_the_optional_outlets(self):
        self.assertIn("browser-outlets",
                      selected_features(self.manifest, None, all_features=True))


class FetchTests(unittest.TestCase):
    """fetch_repo is the one place that moves a checkout, so it carries the promise."""

    def module(self):
        # fennec_setup.fetch the attribute is the fetch() function, not the module.
        import importlib
        return importlib.import_module("fennec_setup.fetch")

    def repo(self, concept, upstream=False):
        return {"concept": concept, "repo": f"x/{concept}", "branch": "main",
                "local": f"/tmp/suite/{concept}", "upstream": upstream}

    def pulled(self, repo, results):
        fetch_module = self.module()
        with mock.patch.object(fetch_module, "validate_checkout", return_value=True), \
             mock.patch.object(fetch_module, "_git", side_effect=results) as git:
            message = fetch_module.fetch_repo(repo, update=True)
        return message, [call.args[0] for call in git.call_args_list]

    def test_an_update_pulls_a_fennec_checkout(self):
        message, calls = self.pulled(self.repo("fennec-core"), ["ok"])
        self.assertIn("updated fennec-core", message)
        self.assertEqual(calls[0][:2], ["pull", "--ff-only"])

    def test_an_update_leaves_an_upstream_outlet_where_its_maintainers_put_it(self):
        message, calls = self.pulled(self.repo("openbot", upstream=True), [])
        self.assertIn("untouched", message)
        self.assertEqual(calls, [], "openbot was moved by an update")

    def test_the_fennec_engine_is_pulled_like_any_other_fennec_checkout(self):
        engine = next(repo for repo in load_manifest()["repos"] if repo["concept"] == "engine")
        message, calls = self.pulled(dict(engine, local="/tmp/engine"), ["ok"])
        self.assertIn("updated engine", message)
        self.assertEqual(calls[0][:2], ["pull", "--ff-only"])

    def test_a_missing_upstream_is_still_cloned(self):
        import tempfile
        fetch_module = self.module()
        repo = self.repo("openbot", upstream=True)
        with tempfile.TemporaryDirectory() as directory:
            repo["local"] = str(Path(directory) / "suite" / "OpenBot")
            with mock.patch.object(fetch_module, "validate_checkout", return_value=False), \
                 mock.patch.object(fetch_module, "_git", return_value="") as git:
                message = fetch_module.fetch_repo(repo, update=True)
        self.assertIn("cloned openbot", message)
        self.assertEqual(git.call_args_list[0].args[0][:2], ["clone", "--branch"])


if __name__ == "__main__":
    unittest.main()
