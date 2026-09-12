"""Explicit, opt-in installation of the pinned public Nemotron CPU runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.request import urlopen

RUNTIME_SHA = "4f9676226f667d14608487df744f375db87127f8"
SENTENCEPIECE_SHA = "17d7580d6407802f85855d2cc9190634e2c95624"
MODEL_REVISION = "ebe59e5a817142986528bbbee5dba8db7b38ed50"
MODEL_NAME = "nemotron-speech-streaming-en-0.6b.q8_0.gguf"
MODEL_SIZE = 699872960
MODEL_SHA256 = "d9a01898d2a611c8764e23a1c2f45e70bbd5a425dc4de93692ac951dd603812d"
MODEL_URL = (
    "https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b/resolve/"
    f"{MODEL_REVISION}/{MODEL_NAME}"
)
DEFAULT_RUNTIME_DIR = Path.home() / ".local/share/voicebox-studio/nemotron"
EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments/nemotron"


def run(command: list[str], *, timeout: float = 600) -> None:
    print("+ " + shlex.join(command), flush=True)
    subprocess.run(command, check=True, timeout=timeout)


def verify_model(path: Path) -> None:
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        raise ValueError(f"Expected the approved {MODEL_SIZE}-byte model at {path}")
    digest = hashlib.sha256()
    with path.open("rb") as model:
        while chunk := model.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != MODEL_SHA256:
        raise ValueError("Nemotron model SHA-256 mismatch; refusing to load or replace it")


def check_disk(directory: Path, *, required_bytes: int = 3 * 1024**3) -> None:
    existing = directory
    while not existing.exists():
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    print(f"Disk available: {free / 1024**3:.2f} GiB; required: {required_bytes / 1024**3:.2f} GiB")
    if free < required_bytes:
        raise ValueError("Insufficient free disk for bounded native setup")


def download_model(path: Path) -> None:
    if path.exists():
        verify_model(path)
        return
    check_disk(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = path.with_suffix(path.suffix + ".part")
    if partial.exists():
        raise ValueError(
            f"An incomplete download exists at {partial}; inspect/remove it explicitly"
        )
    digest = hashlib.sha256()
    total = 0
    try:
        with urlopen(MODEL_URL, timeout=60) as response, partial.open("xb") as output:
            os.chmod(partial, 0o600)
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MODEL_SIZE:
                    raise ValueError("Nemotron download exceeds the approved byte limit")
                output.write(chunk)
                digest.update(chunk)
            if total != MODEL_SIZE or digest.hexdigest() != MODEL_SHA256:
                raise ValueError("Nemotron download size or SHA-256 mismatch")
        partial.rename(path)
    finally:
        partial.unlink(missing_ok=True)
    print(f"Verified {total} bytes, SHA-256 {MODEL_SHA256}", flush=True)


def checkout(path: Path, url: str, revision: str) -> None:
    if (path / ".git").exists():
        head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True)
        if head.strip() != revision:
            raise ValueError(f"Existing source at {path} is not the pinned revision")
        run(["git", "-C", str(path), "diff", "--quiet", "HEAD"])
        return
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"Refusing to overwrite nonempty directory: {path}")
    run(["git", "init", "-q", str(path)])
    run(["git", "-C", str(path), "remote", "add", "origin", url])
    run(["git", "-C", str(path), "fetch", "--depth", "1", "origin", revision])
    run(["git", "-C", str(path), "checkout", "--detach", "FETCH_HEAD"])


def build_runtime(directory: Path) -> None:
    check_disk(directory)
    cmake = EXPERIMENT / ".venv/bin/cmake"
    ninja = EXPERIMENT / ".venv/bin/ninja"
    if not cmake.is_file() or not ninja.is_file():
        raise ValueError("Run uv sync --project experiments/nemotron before --build")
    source = directory / "source"
    sp = directory / "sentencepiece"
    sp_build = directory / "sentencepiece-build"
    build = directory / "build"
    checkout(source, "https://github.com/NVIDIA/NeMo-Speech.cpp.git", RUNTIME_SHA)
    checkout(sp, "https://github.com/google/sentencepiece.git", SENTENCEPIECE_SHA)
    run(
        [
            "git",
            "-C",
            str(source),
            "submodule",
            "update",
            "--init",
            "--depth",
            "1",
            "ggml",
            "third_party/cpp-httplib",
        ]
    )
    run(
        [
            str(cmake),
            "-G",
            "Ninja",
            "-S",
            str(sp),
            "-B",
            str(sp_build),
            f"-DCMAKE_MAKE_PROGRAM={ninja}",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DSPM_BUILD_TEST=OFF",
            "-DSPM_ENABLE_SHARED=OFF",
            "-DSPM_ENABLE_TCMALLOC=OFF",
            "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
            "-DSPM_ABSL_PROVIDER=internal",
            "-DSPM_PROTOBUF_PROVIDER=internal",
        ]
    )
    run([str(cmake), "--build", str(sp_build), "--target", "sentencepiece-static", "-j", "2"])
    options = [
        "-DNEMO_SPEECH_BUILD_HTTP=ON",
        "-DNEMO_SPEECH_BUILD_MIC_CAPTURE=OFF",
        "-DNEMO_SPEECH_BUILD_DIAR=OFF",
        "-DNEMO_SPEECH_HTTP_TLS=OFF",
        "-DGGML_CUDA=OFF",
        "-DGGML_METAL=OFF",
        "-DGGML_VULKAN=OFF",
        "-DGGML_OPENMP=OFF",
    ]
    run(
        [
            str(cmake),
            "--preset",
            "cpu-asr",
            "-S",
            str(source),
            "-B",
            str(build),
            f"-DCMAKE_MAKE_PROGRAM={ninja}",
            f"-DSENTENCEPIECE_LIB={sp_build / 'src/libsentencepiece.a'}",
            f"-DSENTENCEPIECE_INCLUDE_DIR={sp / 'src'}",
            *options,
        ]
    )
    run([str(cmake), "--build", str(build), "-j", "2"])
    run([str(build / "bin/nemo-speech"), "--version"])
    licenses = directory / "licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(source / name, licenses / name)
    for name, relative in {
        "sentencepiece-LICENSE": "LICENSE",
        "sentencepiece-absl-LICENSE": "third_party/absl/LICENSE",
        "sentencepiece-darts-LICENSE": "third_party/darts_clone/LICENSE",
        "sentencepiece-protobuf-LICENSE": "third_party/protobuf-lite/LICENSE",
    }.items():
        shutil.copyfile(sp / relative, licenses / name)
    (directory / "build-metadata.json").write_text(
        json.dumps(
            {
                "runtime_revision": RUNTIME_SHA,
                "sentencepiece_revision": SENTENCEPIECE_SHA,
                "preset": "cpu-asr",
                "options": options,
                "jobs": 2,
            },
            indent=2,
        )
        + "\n"
    )


def sidecar_command(directory: Path, model: Path, port: int) -> list[str]:
    if not 1024 <= port <= 65535:
        raise ValueError("Sidecar port must be between 1024 and 65535")
    return [
        str(directory / "build/bin/nemo-speech"),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--device",
        "cpu",
        "--asr-model",
        str(model.resolve()),
        "--no-ui",
        "--threads",
        "4",
        "--max-upload-mb",
        "64",
        "--read-timeout",
        "30",
        "--write-timeout",
        "10",
        "--asr.batching.enabled=false",
        "--asr.batching.state_arena_slots",
        "1",
        "--asr.streaming.rnnt_right_context",
        "1",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--model-path", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--build", action="store_true", help="Build pinned native CPU code only")
    actions.add_argument("--download", action="store_true", help="Download only the approved GGUF")
    actions.add_argument("--check", action="store_true", help="Verify local binary and model")
    actions.add_argument("--serve", action="store_true", help="Run installed sidecar in foreground")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    directory = args.runtime_dir.expanduser().resolve()
    model = (args.model_path or directory / MODEL_NAME).expanduser().resolve()
    try:
        if args.build or args.download:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock = directory / ".setup.lock"
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise ValueError("Nemotron setup is already running (.setup.lock exists)") from None
            try:
                os.close(descriptor)
                if args.build:
                    build_runtime(directory)
                else:
                    download_model(model)
            finally:
                lock.unlink()
        else:
            verify_model(model)
            binary = directory / "build/bin/nemo-speech"
            if not binary.is_file():
                raise ValueError("Native sidecar missing; run the explicit --build step")
            if args.check:
                run([str(binary), "--version"])
                print("Approved model verified; CPU sidecar is installed (not started).")
                print(shlex.join(sidecar_command(directory, model, args.port)))
            else:
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", args.port))
                command = sidecar_command(directory, model, args.port)
                print("+ " + shlex.join(command), flush=True)
                # Do not inherit model overrides that could load/download unapproved auxiliaries.
                environment = {
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("NEMO_SPEECH_")
                }
                os.execve(binary, command, environment)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Nemotron setup: {error}\n")


if __name__ == "__main__":
    main()
