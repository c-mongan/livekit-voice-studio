import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import make_wav
from tools import studio_doctor as doctor
from tools import studio_service


def checks(report):
    assert report["version"] == 1
    assert set(report) == {"version", "checks"}
    assert len({item["id"] for item in report["checks"]}) == len(report["checks"])
    for item in report["checks"]:
        assert set(item) == {"id", "status", "message", "action"}
        assert item["status"] in {"pass", "missing", "unverified"}
        assert item["message"]
        assert isinstance(item["action"], str)
    return {item["id"]: item for item in report["checks"]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(doctor, "package_installed", lambda name: True)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(doctor.shutil, "which", lambda *args, **kwargs: "/fixture/bin/cli")
    return root, {"HOME": str(home)}


@pytest.fixture
def configured(setup):
    root, env = setup
    (root / "web/dist/assets").mkdir(parents=True)
    (root / "web/dist/index.html").write_text("<html>Studio</html>")
    snapshot = root / "private-snapshot"
    (snapshot / "speech_tokenizer").mkdir(parents=True)
    for name in (
        "model.safetensors",
        "tokenizer.json",
        "tokenizer_config.json",
        "speech_tokenizer/config.json",
        "speech_tokenizer/model.safetensors",
    ):
        (snapshot / name).write_text("{}")
    (snapshot / "config.json").write_text(
        json.dumps({"model_type": "qwen3_tts", "tts_model_type": "base", "tts_model_size": "0b6"})
    )
    bundle = root / "private-bundle"
    bundle.mkdir()
    audio = make_wav(seconds=1)
    (bundle / "reference.wav").write_bytes(audio)
    (bundle / "voice.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": "PRIVATE PROFILE",
                "transcript": "PRIVATE TRANSCRIPT",
                "authorized": True,
                "audio_sha256": hashlib.sha256(audio).hexdigest(),
            }
        )
    )
    from tools.setup_nemotron import MODEL_SIZE

    binary = root / "native/build/bin/nemo-speech"
    binary.parent.mkdir(parents=True)
    binary.write_text("not executed")
    binary.chmod(0o700)
    model = root / "private-model.gguf"
    with model.open("wb") as handle:
        handle.truncate(MODEL_SIZE)
    env.update(
        VOICEBOX_TTS_BACKEND="mlx",
        VOICEBOX_MLX_MODEL_PATH=str(snapshot),
        VOICEBOX_VOICE_BUNDLE=str(bundle),
        VOICEBOX_EXCLUSIVE="1",
        VOICEBOX_STT_PROVIDER="nemotron",
        VOICEBOX_LLM_PROVIDER="copilot",
        NEMOTRON_SERVER_BINARY=str(binary),
        NEMOTRON_MODEL_PATH=str(model),
        LIVEKIT_URL="wss://private-project.example",
        LIVEKIT_API_KEY="PRIVATE KEY",
        LIVEKIT_API_SECRET="PRIVATE SECRET",
    )
    return root, env


def test_fresh_checkout_needs_no_env_library_or_installed_dependencies(setup, monkeypatch):
    root, env = setup
    monkeypatch.setattr(doctor, "package_installed", lambda name: False)
    monkeypatch.setattr(doctor.shutil, "which", lambda *args, **kwargs: None)
    before = list(root.rglob("*"))
    report = doctor.run_checks(root, env)
    found = checks(report)
    assert found["dependencies"]["status"] == "missing"
    assert found["frontend"]["status"] == "missing"
    assert found["livekit"]["status"] == "missing"
    assert found["voice"]["status"] == "missing"
    assert doctor.exit_code(report) == 1
    assert list(root.rglob("*")) == before
    assert not (Path(env["HOME"]) / ".local").exists()


def test_configured_is_not_authenticated_or_model_ready(configured):
    root, env = configured
    report = doctor.run_checks(root, env)
    found = checks(report)
    for name in (
        "dependencies",
        "hardware",
        "frontend",
        "qwen",
        "speech",
        "livekit",
        "reasoning",
        "voice",
    ):
        assert found[name]["status"] == "pass", found[name]
    for name in ("account-readiness", "model-readiness"):
        assert found[name]["status"] == "unverified"
    assert "auth" in found["account-readiness"]["message"].lower()
    assert doctor.exit_code(report) == 0
    text = json.dumps(report)
    for private in ("PRIVATE", str(root), env["HOME"], env["LIVEKIT_URL"]):
        assert private not in text


def test_dotenv_is_loaded_without_execution_or_environment_mutation(configured, monkeypatch):
    root, env = configured
    for key in env:
        if key != "HOME":
            monkeypatch.delenv(key, raising=False)
    (root / ".env").write_text(
        "\n".join(f"{key}='{value}'" for key, value in env.items() if key != "HOME")
        + "\nUNUSED=$(touch SHOULD_NOT_EXIST)\n"
    )
    before = dict(os.environ)
    report = doctor.run_checks(root, {"HOME": env["HOME"]})
    assert doctor.exit_code(report) == 0
    assert dict(os.environ) == before
    assert not (root / "SHOULD_NOT_EXIST").exists()
    found = checks(doctor.run_checks(root, {"HOME": env["HOME"], "LIVEKIT_API_KEY": ""}))
    assert found["livekit"]["status"] == "missing"


@pytest.mark.parametrize("contents", ["SECRET='unterminated", "\x00", "x" * 65537])
def test_malformed_dotenv_is_explicit_and_private(configured, contents):
    root, env = configured
    (root / ".env").write_text(contents)
    report = doctor.run_checks(root, env)
    assert checks(report)["environment"]["status"] == "missing"
    assert doctor.exit_code(report) == 1
    assert "unterminated" not in json.dumps(report)


def saved_settings(root, env, **updates):
    library = root / "private-library"
    library.mkdir(exist_ok=True)
    env["VOICEBOX_LIBRARY_DIR"] = str(library)
    settings = {
        "sttProvider": "openai",
        "llmProvider": "openai",
        "llmModel": "gpt-4.1-mini",
        "reasoningEffort": "none",
        "voiceId": None,
        "codexRestrictedApproved": False,
    }
    settings.update(updates)
    (library / "settings.json").write_text(json.dumps(settings))
    return library


def test_saved_provider_settings_override_process_and_dotenv(configured):
    root, env = configured
    saved_settings(root, env)
    env["VOICEBOX_LLM_PROVIDER"] = "unsupported-private-choice"
    env["OPENAI_API_KEY"] = "private"
    env.pop("NEMOTRON_MODEL_PATH")
    assert doctor.exit_code(doctor.run_checks(root, env)) == 0
    env.pop("OPENAI_API_KEY")
    found = checks(doctor.run_checks(root, env))
    assert found["reasoning"]["status"] == "missing"
    assert found["speech"]["status"] == "missing"


@pytest.mark.parametrize("value", ["{", "[]", '{"private":"SECRET"}', "x" * 4097])
def test_bad_saved_settings_never_fall_back_to_valid_environment(configured, value):
    root, env = configured
    library = saved_settings(root, env)
    (library / "settings.json").write_text(value)
    report = doctor.run_checks(root, env)
    found = checks(report)
    assert found["settings"]["status"] == "missing"
    assert found["reasoning"]["status"] == "unverified"
    assert found["voice"]["status"] == "unverified"
    assert doctor.exit_code(report) == 1
    assert "SECRET" not in json.dumps(report)


def test_selected_saved_voice_wins_without_initializing_library(configured):
    root, env = configured
    identifier = "a" * 32
    library = saved_settings(root, env, voiceId=identifier)
    (library / "voices").mkdir()
    Path(env["VOICEBOX_VOICE_BUNDLE"]).rename(library / "voices" / identifier)
    env["VOICEBOX_VOICE_BUNDLE"] = "/missing/private/bundle"
    env["VOICEBOX_TTS_BACKEND"] = "voicebox"
    env["OPENAI_API_KEY"] = "private"
    assert doctor.exit_code(doctor.run_checks(root, env)) == 0


@pytest.mark.parametrize(
    "change,check",
    [
        ({"VOICEBOX_MLX_MODEL_PATH": ""}, "qwen"),
        ({"NEMOTRON_SERVER_BINARY": ""}, "speech"),
        ({"NEMOTRON_MODEL_PATH": ""}, "speech"),
        ({"NEMOTRON_URL": "http://localhost:8766"}, "speech"),
        ({"NEMOTRON_URL": "http://127.0.0.1:bad"}, "speech"),
        ({"LIVEKIT_URL": "not a URL"}, "livekit"),
        ({"VOICEBOX_LLM_PROVIDER": "private-unsupported"}, "reasoning"),
        ({"VOICEBOX_LLM_MODEL": ""}, "reasoning"),
        ({"VOICEBOX_LLM_PROVIDER": "codex"}, "reasoning"),
        ({"VOICEBOX_EXCLUSIVE": "0"}, "exclusive"),
        ({"VOICEBOX_VOICE_BUNDLE": "/missing/private-bundle"}, "voice"),
    ],
)
def test_actionable_missing_configuration(configured, change, check):
    root, env = configured
    env.update(change)
    found = checks(doctor.run_checks(root, env))
    assert found[check]["status"] == "missing"
    assert found[check]["action"]
    assert "private-" not in json.dumps(found)


def test_mlx_hardware_and_optional_dependency_checks(configured, monkeypatch):
    root, env = configured
    monkeypatch.setattr(doctor.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(doctor, "package_installed", lambda name: name != "mlx-audio")
    found = checks(doctor.run_checks(root, env))
    assert found["hardware"]["status"] == "missing"
    assert found["dependencies"]["status"] == "missing"


def test_frontend_without_assets_is_not_a_complete_build(configured):
    root, env = configured
    (root / "web/dist/assets").rmdir()
    assert checks(doctor.run_checks(root, env))["frontend"]["status"] == "missing"


@pytest.mark.parametrize(
    "target,contents",
    [
        ("config.json", '{"model_type":"other-private-model"}'),
        ("config.json", "{"),
        ("speech_tokenizer/model.safetensors", None),
        ("tokenizer.json", None),
    ],
)
def test_qwen_uses_existing_snapshot_rules(configured, target, contents):
    root, env = configured
    path = Path(env["VOICEBOX_MLX_MODEL_PATH"]) / target
    if contents is None:
        path.unlink()
    else:
        path.write_text(contents)
    report = doctor.run_checks(root, env)
    assert checks(report)["qwen"]["status"] == "missing"
    assert "other-private-model" not in json.dumps(report)
    from livekit.plugins.voicebox.errors import ConfigurationError

    from examples.fast_qwen import FastQwenTTS

    runtime = FastQwenTTS.__new__(FastQwenTTS)
    runtime._model_path = Path(env["VOICEBOX_MLX_MODEL_PATH"])
    with pytest.raises(ConfigurationError):
        runtime._validate_snapshot()


def test_static_contracts_match_runtime_limits_and_valid_snapshot(configured):
    root, env = configured
    from livekit.plugins.voicebox import models

    from examples.fast_qwen import FastQwenTTS
    from examples.studio_library import PRESETS

    assert doctor.MAX_JSON_BYTES == models.MAX_JSON_BYTES
    assert doctor.MAX_WAV_BYTES == models.MAX_WAV_BYTES
    assert doctor.PRESETS == PRESETS
    path = Path(env["VOICEBOX_MLX_MODEL_PATH"])
    (path / "tokenizer.json").unlink()
    for name in ("vocab.json", "merges.txt"):
        (path / name).write_text("{}")
    runtime = FastQwenTTS.__new__(FastQwenTTS)
    runtime._model_path = path
    runtime._validate_snapshot()
    assert checks(doctor.run_checks(root, env))["qwen"]["status"] == "pass"


@pytest.mark.parametrize(
    "updates",
    [
        {},
        {"llmProvider": "copilot", "llmModel": "gpt-5.6-luna", "reasoningEffort": "low"},
        {
            "llmProvider": "codex",
            "llmModel": "gpt-5.6-luna",
            "reasoningEffort": "low",
            "codexRestrictedApproved": True,
        },
        {"llmProvider": "azure", "llmModel": "gpt-4.1-nano"},
        {"llmProvider": "ollama", "llmModel": "qwen3:1.7b", "livekitMode": "local"},
        {
            "llmProvider": "openai-compatible",
            "llmModel": "custom",
            "llmBaseUrl": "https://example.test/v1",
        },
        {"llmProvider": "ollama", "llmModel": "qwen3:1.7b", "llmBaseUrl": "https://remote.test/v1"},
        {"llmBaseUrl": "https://user:secret@example.test/v1"},
        {"livekitMode": "unsupported"},
        {"sttProvider": "unsupported"},
        {"sttProvider": []},
        {"llmProvider": []},
        {"llmModel": "unsupported"},
        {"reasoningEffort": "unsupported"},
        {"codexRestrictedApproved": "true"},
        {"llmProvider": "codex", "llmModel": "gpt-5.6-luna", "reasoningEffort": "low"},
        {"voiceId": "../private"},
        {"voiceId": 123},
        {"voiceId": "f" * 32},
        {"extra": "unsupported"},
    ],
)
def test_static_saved_settings_match_runtime_schema(configured, updates):
    root, env = configured
    library_path = saved_settings(root, env, **updates)
    from examples.studio_library import LibraryError, StudioLibrary

    runtime = StudioLibrary.__new__(StudioLibrary)
    runtime.root = library_path
    runtime.voices = library_path / "voices"
    runtime.settings_path = library_path / "settings.json"
    try:
        runtime.settings()
        valid = True
    except LibraryError:
        valid = False
    status = checks(doctor.run_checks(root, env))["settings"]["status"]
    assert status == ("pass" if valid else "missing")


def test_static_voice_check_does_not_claim_audio_decoding(configured):
    root, env = configured
    found = checks(doctor.run_checks(root, env))
    assert "audio decoding remains unverified" in found["voice"]["message"]
    assert "audio decoding" in found["model-readiness"]["message"]


def test_voice_integrity_uses_existing_bundle_validation(configured):
    root, env = configured
    (Path(env["VOICEBOX_VOICE_BUNDLE"]) / "reference.wav").write_bytes(b"invalid")
    assert checks(doctor.run_checks(root, env))["voice"]["status"] == "missing"


@pytest.mark.parametrize("fault", ["size", "executable", "layout"])
def test_native_configuration_is_static_but_meaningful(configured, fault):
    root, env = configured
    if fault == "size":
        Path(env["NEMOTRON_MODEL_PATH"]).write_bytes(b"bad")
    elif fault == "executable":
        Path(env["NEMOTRON_SERVER_BINARY"]).chmod(0o600)
    else:
        old = Path(env["NEMOTRON_SERVER_BINARY"])
        new = old.with_name("unsupported")
        old.rename(new)
        env["NEMOTRON_SERVER_BINARY"] = str(new)
    assert checks(doctor.run_checks(root, env))["speech"]["status"] == "missing"


def test_selected_cli_not_installed_does_not_claim_configured(configured, monkeypatch):
    root, env = configured
    monkeypatch.setattr(doctor.shutil, "which", lambda *args, **kwargs: None)
    found = checks(doctor.run_checks(root, env))
    assert found["reasoning"]["status"] == "missing"
    assert found["account-readiness"]["status"] == "unverified"


def test_selected_azure_configuration_without_authentication(configured):
    root, env = configured
    env.update(
        VOICEBOX_LLM_PROVIDER="azure",
        VOICEBOX_STT_PROVIDER="azure",
        AZURE_SUBSCRIPTION_ID="private",
        AZURE_RESOURCE_GROUP="private",
        AZURE_OPENAI_ACCOUNT="private",
        AZURE_OPENAI_ENDPOINT="https://private.example",
        AZURE_OPENAI_DEPLOYMENT="private",
        AZURE_SPEECH_ACCOUNT="private",
        AZURE_SPEECH_REGION="private",
    )
    assert doctor.exit_code(doctor.run_checks(root, env)) == 0
    env.pop("AZURE_SPEECH_REGION")
    found = checks(doctor.run_checks(root, env))
    assert found["speech"]["status"] == "missing"
    assert found["reasoning"]["status"] == "pass"


def test_dotenv_interpolation_preserves_process_precedence(setup):
    root, env = setup
    (root / ".env").write_text(
        "PREFIX=from-file\nLIVEKIT_API_KEY=${PREFIX}\nOPENAI_API_KEY=${UNSET:-fallback}\n"
    )
    result = doctor._environment(root, {**env, "PREFIX": "from-process"})
    assert result["LIVEKIT_API_KEY"] == "from-process"
    assert result["OPENAI_API_KEY"] == "fallback"


def test_unreadable_saved_settings_are_explicit(configured, monkeypatch):
    root, env = configured
    library = saved_settings(root, env)
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path == library / "settings.json":
            raise PermissionError("PRIVATE PATH")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    report = doctor.run_checks(root, env)
    assert checks(report)["settings"]["status"] == "missing"
    assert "PRIVATE PATH" not in json.dumps(report)


def test_dangling_saved_settings_never_silently_use_defaults(configured):
    root, env = configured
    library = saved_settings(root, env)
    path = library / "settings.json"
    path.unlink()
    path.symlink_to(library / "missing-private-settings")
    found = checks(doctor.run_checks(root, env))
    assert found["settings"]["status"] == "missing"
    assert found["reasoning"]["status"] == "unverified"


@pytest.mark.parametrize("saved", [False, True])
def test_cold_process_blocks_all_writes_network_and_child_processes(configured, saved):
    root, env = configured
    if saved:
        saved_settings(root, env)
        env["OPENAI_API_KEY"] = "fixture-key"
    script = """
import json, os, sys
from pathlib import Path
from tools import studio_doctor as doctor
root, env = json.load(sys.stdin)
def audit(event, args):
    if event == "import" and args[0].split(".")[0] in ("livekit", "numpy", "soundfile",
                                                     "scipy", "mlx", "mlx_audio"):
        raise AssertionError("Doctor imported a native/model runtime: " + args[0])
    if event == "ctypes.dlopen":
        raise AssertionError("Doctor loaded a native library")
    if event in ("os.mkdir", "os.remove", "os.rename", "os.chmod",
                 "socket.connect", "socket.bind", "subprocess.Popen", "os.system"):
        raise RuntimeError("Forbidden side effect: " + event)
    if event == "open" and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
        raise RuntimeError("Forbidden file write")
sys.addaudithook(audit)
doctor.platform.system = lambda: "Darwin"
doctor.platform.machine = lambda: "arm64"
doctor.shutil.which = lambda *args, **kwargs: "/fixture/bin/cli"
doctor.package_installed = lambda name: True
report = doctor.run_checks(Path(root), env)
print(json.dumps(report))
sys.exit(doctor.exit_code(report))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        input=json.dumps([str(root), env]),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "HOME": env["HOME"], "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert not result.stderr
    checks(json.loads(result.stdout))


def test_shell_launcher_fresh_checkout_json_without_env_or_home_library(setup):
    root, env = setup
    source = Path(__file__).resolve().parents[1]
    (root / "tools").mkdir()
    (root / "examples").mkdir()
    shutil.copyfile(
        source / "examples/component_endpoints.py", root / "examples/component_endpoints.py"
    )
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    shutil.copyfile(source / "studio", root / "studio")
    (root / "studio").chmod(0o700)
    for name in ("studio_service.py", "studio_doctor.py"):
        shutil.copyfile(source / "tools" / name, root / "tools" / name)
    before = set(root.rglob("*"))
    result = subprocess.run(
        [str(root / "studio"), "doctor", "--json"],
        cwd=root,
        env={**env, "PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert not result.stderr
    found = checks(json.loads(result.stdout))
    assert found["voice"]["status"] == "missing"
    assert found["livekit"]["status"] == "missing"
    assert found["frontend"]["status"] == "missing"
    assert set(root.rglob("*")) == before
    assert not (Path(env["HOME"]) / ".local").exists()
    assert str(root) not in result.stdout


def test_no_network_processes_library_creation_or_engine_instantiation(configured, monkeypatch):
    root, env = configured
    from examples.fast_qwen import FastQwenTTS
    from examples.studio_library import StudioLibrary

    def forbidden(*args, **kwargs):
        pytest.fail("Doctor attempted a mutating, process, model, or network operation")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(StudioLibrary, "__init__", forbidden)
    monkeypatch.setattr(FastQwenTTS, "__init__", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    before = dict(os.environ)
    assert doctor.exit_code(doctor.run_checks(root, env)) == 0
    assert dict(os.environ) == before


@pytest.mark.parametrize("missing", [False, True])
def test_existing_launcher_routes_doctor_before_macos_service(
    configured, monkeypatch, capsys, missing
):
    root, env = configured
    if missing:
        env["LIVEKIT_API_KEY"] = ""
    monkeypatch.setattr(studio_service, "ROOT", root)
    monkeypatch.setattr(doctor.os, "environ", env)
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    env["VOICEBOX_TTS_BACKEND"] = "voicebox"
    env["VOICEBOX_PROFILE"] = "PRIVATE PROFILE"
    monkeypatch.setattr(sys, "argv", ["studio", "doctor", "--json"])
    with pytest.raises(SystemExit) as error:
        studio_service.main()
    assert error.value.code == int(missing)
    captured = capsys.readouterr()
    checks(json.loads(captured.out))
    assert not captured.err


def test_first_run_local_mode_resolves_dev_transport_without_mutation(setup):
    root, env = setup
    env.update(
        VOICEBOX_LIVEKIT_MODE="local",
        VOICEBOX_LLM_PROVIDER="ollama",
        VOICEBOX_LLM_MODEL="qwen3:1.7b",
        VOICEBOX_REASONING_EFFORT="none",
        LIVEKIT_URL="",
        LIVEKIT_API_KEY="",
        LIVEKIT_API_SECRET="",
    )
    original = env.copy()
    before = set(root.rglob("*"))
    report = doctor.run_checks(root, env)
    found = checks(report)
    assert found["livekit"]["status"] == "pass"
    assert found["reasoning"]["status"] == "pass"
    assert "not authenticated" in found["livekit"]["message"]
    assert env == original
    assert set(root.rglob("*")) == before
    assert "devkey" not in json.dumps(report)


def test_saved_transport_mode_overrides_first_run_environment(configured):
    root, env = configured
    env["VOICEBOX_LIVEKIT_MODE"] = "local"
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        env[name] = ""
    saved_settings(root, env, livekitMode="configured")
    assert checks(doctor.run_checks(root, env))["livekit"]["status"] == "missing"
