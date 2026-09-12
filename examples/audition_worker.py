"""One local-only generated audition, under Studio's existing process lease."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import sys

from examples.fast_qwen import FastQwenTTS
from examples.studio_worker import report


async def generate(provider: FastQwenTTS, text: str) -> None:
    await provider.prepare()
    report("model_loaded")
    report("ready")
    async with provider.synthesize(text) as stream:
        async for event in stream:
            pcm = bytes(event.frame.data)
            for offset in range(0, len(pcm), 1920):
                report("audition_audio", pcm=base64.b64encode(pcm[offset : offset + 1920]).decode())
                await asyncio.sleep(0)


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    report("audition_started")
    parent_pid = os.getppid()

    async def parent_watch() -> None:
        while not stop.is_set():
            await asyncio.sleep(2)
            if os.getppid() != parent_pid:
                stop.set()

    watcher = asyncio.create_task(parent_watch())
    stopped = asyncio.create_task(stop.wait())
    provider: FastQwenTTS | None = None
    task: asyncio.Task[None] | None = None
    safe = True
    try:
        raw = await asyncio.to_thread(sys.stdin.buffer.read, 4097)
        body = json.loads(raw)
        if (
            len(raw) > 4096
            or not isinstance(body, dict)
            or set(body) != {"text"}
            or not isinstance(body["text"], str)
            or not 1 <= len(body["text"].strip()) <= 300
        ):
            raise ValueError("Invalid audition input.")
        if stop.is_set():
            return
        provider = FastQwenTTS(
            profile="Local voice",
            model_path=os.environ["VOICEBOX_MLX_MODEL_PATH"],
            voice_bundle=os.environ["VOICEBOX_VOICE_BUNDLE"],
        )
        task = asyncio.create_task(generate(provider, body["text"]))
        done, _ = await asyncio.wait(
            {task, stopped}, timeout=90, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task
        elif not stopped.done():
            report("error", message="Audition timed out. Wait for the local model to drain.")
    except (Exception, asyncio.CancelledError) as error:
        report("error", message="Local audition failed. Check the saved voice and local MLX setup.")
        if isinstance(error, asyncio.CancelledError):
            raise
    finally:
        report("draining")
        for pending in (task, watcher, stopped):
            if pending is not None and not pending.done():
                pending.cancel()
        await asyncio.gather(
            *(item for item in (task, watcher, stopped) if item), return_exceptions=True
        )
        if provider is not None:
            try:
                await provider.aclose()
            except Exception:
                safe = False
                report("uncertain")
        report("finished", safe=safe)


if __name__ == "__main__":
    asyncio.run(run())
