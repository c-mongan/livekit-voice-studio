"""One exclusive local Voicebox-backed LiveKit room agent."""

import asyncio
import importlib.util
import logging
import os
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Never

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobRequest, cli, llm, stt
from livekit.plugins import openai, silero, voicebox
from livekit.plugins.voicebox.errors import VoiceboxError

from examples.hermes_api import HermesAPIError, HermesConfig, HermesRunsClient
from examples.hermes_llm import ApprovalRequest, ApprovalResolution, HermesLLM

server = AgentServer(host="127.0.0.1")
logger = logging.getLogger("voicebox.example")
_room_accepted = False
AZURE_REQUIRED = (
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "AZURE_OPENAI_ACCOUNT",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_SPEECH_ACCOUNT",
    "AZURE_SPEECH_REGION",
)


async def azure_resource_key(account: str) -> str:
    """Use the operator's CLI identity; credentials stay in memory, never in logs."""
    if not shutil.which("az"):
        raise RuntimeError(
            "Azure CLI is required for this local Azure example. Sign in with az login."
        )
    process = await asyncio.create_subprocess_exec(
        "az",
        "cognitiveservices",
        "account",
        "keys",
        "list",
        "--subscription",
        os.environ["AZURE_SUBSCRIPTION_ID"],
        "--resource-group",
        os.environ["AZURE_RESOURCE_GROUP"],
        "--name",
        account,
        "--query",
        "key1",
        "--output",
        "tsv",
        "--only-show-errors",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(30):
            stdout, _ = await process.communicate()
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0 or not stdout.strip():
        raise RuntimeError(
            "Azure CLI could not retrieve the configured resource credential. "
            "Check az login, the subscription and resource-key permissions."
        )
    return stdout.decode().strip()


async def _unwired_approval(_request: ApprovalRequest) -> None:
    raise RuntimeError("Hermes approval handling is not connected; the request was not approved.")


def hermes_config(*, session_id: str) -> HermesConfig:
    return HermesConfig(
        base_url=os.environ.get("HERMES_API_BASE_URL", "http://127.0.0.1:8642"),
        api_key=os.environ.get("HERMES_API_SERVER_KEY", ""),
        profile=os.environ.get("HERMES_PROFILE", "default"),
        session_id=session_id,
    )


async def configured_ai(
    *,
    on_approval: Callable[[ApprovalRequest], Awaitable[None]] | None = None,
    on_approval_resolved: Callable[[ApprovalResolution], Awaitable[None]] | None = None,
) -> tuple[stt.STT[Never], llm.LLM[Never]]:
    speech_choice, reasoning_choice = provider_choices()
    if speech_choice not in ("azure", "openai", "nemotron"):
        raise RuntimeError("Unsupported speech recognition provider.")
    if reasoning_choice not in ("hermes", "azure", "openai", "copilot", "codex"):
        raise RuntimeError("Unsupported reasoning provider.")
    language_model: llm.LLM[Never]
    if reasoning_choice == "hermes":
        client = HermesRunsClient(
            hermes_config(session_id=os.environ.get("HERMES_VOICE_SESSION_ID", ""))
        )
        try:
            await client.preflight()
            language_model = HermesLLM(
                client=client,
                on_approval=on_approval or _unwired_approval,
                on_approval_resolved=on_approval_resolved,
            )
        except BaseException:
            await client.aclose()
            raise
    elif reasoning_choice == "azure":
        llm_key = await azure_resource_key(os.environ["AZURE_OPENAI_ACCOUNT"])
        language_model = openai.LLM.with_azure(
            model=os.environ.get("AZURE_OPENAI_MODEL", "gpt-4.1-nano"),
            azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.environ.get("OPENAI_API_VERSION", "2024-10-21"),
            api_key=llm_key,
        )
    elif reasoning_choice == "openai":
        language_model = openai.LLM(model="gpt-4.1-mini")
    else:
        from examples.agent_llm import AgentLLM

        language_model = AgentLLM(
            provider=reasoning_choice,
            model=os.environ.get("VOICEBOX_LLM_MODEL", "gpt-5.6-luna"),
            reasoning_effort=os.environ.get("VOICEBOX_REASONING_EFFORT", "low"),
            allow_restricted_agent=(
                reasoning_choice == "codex" and os.environ.get("VOICEBOX_CODEX_RESTRICTED") == "1"
            ),
        )
    try:
        if speech_choice == "azure":
            from livekit.plugins import azure

            speech_key = await azure_resource_key(os.environ["AZURE_SPEECH_ACCOUNT"])
            recognizer: stt.STT[Never] = azure.STT(
                speech_key=speech_key,
                speech_region=os.environ["AZURE_SPEECH_REGION"],
                language="en-GB",
            )
        elif speech_choice == "openai":
            recognizer = openai.STT(model="gpt-4o-mini-transcribe")
        else:
            from examples.nemotron_stt import NemotronSTT

            recognizer = NemotronSTT(
                base_url=os.environ.get("NEMOTRON_URL", "http://127.0.0.1:8766")
            )
        return recognizer, language_model
    except BaseException:
        await language_model.aclose()
        raise


def provider_choices() -> tuple[str, str]:
    legacy = os.environ.get("VOICEBOX_AI_PROVIDER", "openai")
    return (
        os.environ.get("VOICEBOX_STT_PROVIDER", legacy),
        os.environ.get("VOICEBOX_LLM_PROVIDER", legacy),
    )


def configured_provider() -> voicebox.TTS:
    profile = os.environ.get("VOICEBOX_PROFILE")
    if not profile:
        raise RuntimeError("Set VOICEBOX_PROFILE to an authorized profile ID or exact name.")
    return voicebox.TTS(
        profile=profile,
        base_url=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"),
        engine=os.environ.get("VOICEBOX_ENGINE", "qwen"),
        model_size=os.environ.get("VOICEBOX_MODEL_SIZE", "0.6B"),
    )


async def check_setup(*, require_loaded: bool = True, local_voice: bool = False) -> list[str]:
    """Readiness only: never generates, downloads, or prints credential values."""
    problems = []
    speech_choice, choice = provider_choices()
    names = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    if "openai" in (choice, speech_choice):
        names.append("OPENAI_API_KEY")
    if "azure" in (choice, speech_choice):
        names.extend(("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP"))
        if choice == "azure":
            names.extend(
                ("AZURE_OPENAI_ACCOUNT", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")
            )
        if speech_choice == "azure":
            names.extend(("AZURE_SPEECH_ACCOUNT", "AZURE_SPEECH_REGION"))
        if not shutil.which("az"):
            problems.append("Install Azure CLI and sign in with az login.")
        if importlib.util.find_spec("livekit.plugins.azure") is None:
            problems.append("Install the azure extra before running the Azure-backed example.")
    if choice == "hermes":
        api_key = os.environ.get("HERMES_API_SERVER_KEY", "")
        if not api_key.strip():
            problems.append(
                "Set HERMES_API_SERVER_KEY in the workspace .env or process environment."
            )
        try:
            config = HermesConfig(
                base_url=os.environ.get("HERMES_API_BASE_URL", "http://127.0.0.1:8642"),
                api_key=api_key or "readiness-validation",
                profile=os.environ.get("HERMES_PROFILE", "default"),
                session_id="studio-readiness",
            )
        except ValueError as error:
            problems.append(str(error))
        else:
            if api_key.strip():
                client = HermesRunsClient(config)
                try:
                    await client.preflight()
                except (HermesAPIError, ValueError) as error:
                    problems.append(str(error))
                finally:
                    await client.aclose()
    elif choice in ("copilot", "codex"):
        if not shutil.which(choice):
            problems.append(f"Install and sign in to the selected {choice} CLI.")
        if choice == "codex" and os.environ.get("VOICEBOX_CODEX_RESTRICTED") != "1":
            problems.append("Confirm restricted Codex mode in Studio settings before connecting.")
    elif choice not in ("openai", "azure"):
        problems.append("Choose Hermes, Azure, OpenAI, Copilot or Codex for reasoning.")
    if speech_choice == "nemotron":
        binary = os.environ.get("NEMOTRON_SERVER_BINARY", "")
        model_path = os.environ.get("NEMOTRON_MODEL_PATH", "")
        if not Path(binary).is_file() or not Path(model_path).is_file():
            problems.append("Set up the local Nemotron runtime and model explicitly.")
    elif speech_choice not in ("openai", "azure"):
        problems.append("Choose Nemotron, Azure or OpenAI for speech recognition.")
    for name in names:
        if not os.environ.get(name, "").strip():
            problems.append(f"Set {name} in the workspace .env or process environment.")
    if os.environ.get("VOICEBOX_EXCLUSIVE") != "1":
        problems.append("Stop other Voicebox consumers, then set VOICEBOX_EXCLUSIVE=1.")
    if not local_voice and not os.environ.get("VOICEBOX_PROFILE"):
        problems.append("Select an authorized VOICEBOX_PROFILE in the workspace .env.")
        return problems
    if local_voice:
        return problems
    try:
        provider = configured_provider()
        async with provider:
            await provider.health()
            await provider.resolve_profile()
            await provider.check_idle()
            readiness = await provider.model_readiness()
            if not readiness.downloaded:
                problems.append(
                    "Voicebox cannot see cached Qwen TTS 0.6B weights. Restore the existing "
                    "model cache before proceeding; this command never downloads models."
                )
            elif readiness.downloading:
                problems.append("Qwen TTS 0.6B download is in progress; wait for completion.")
            elif require_loaded and not readiness.loaded:
                problems.append(
                    "Qwen TTS 0.6B is cached but not loaded. Run an authorized local smoke "
                    "synthesis before starting the room agent."
                )
    except (VoiceboxError, ValueError) as error:
        problems.append(str(error))
    return problems


async def accept_one_room(request: JobRequest) -> None:
    global _room_accepted
    # The server process admits only one room for its lifetime. Restart for another.
    if _room_accepted:
        await request.reject()
        return
    _room_accepted = True
    await request.accept()


@server.rtc_session(on_request=accept_one_room)
async def entrypoint(ctx: JobContext) -> None:
    if os.environ.get("VOICEBOX_EXCLUSIVE") != "1":
        raise RuntimeError("Set VOICEBOX_EXCLUSIVE=1 only after stopping other Voicebox consumers.")
    provider = configured_provider()
    ctx.add_shutdown_callback(provider.aclose)
    await provider.resolve_profile()
    await provider.check_idle()
    readiness = await provider.model_readiness()
    if not readiness.downloaded or not readiness.loaded:
        raise RuntimeError(
            "Qwen TTS 0.6B must already be cached and loaded. "
            "Run the explicitly authorized local smoke test first; no download is automatic."
        )
    logger.info("Voicebox profile and cached model are ready.")
    vad = await asyncio.to_thread(silero.VAD.load)
    speech, language_model = await configured_ai()
    logger.info("Speech and language providers are configured.")
    ctx.add_shutdown_callback(speech.aclose)
    ctx.add_shutdown_callback(language_model.aclose)
    session: AgentSession[None] = AgentSession(
        vad=vad,
        stt=speech,
        llm=language_model,
        tts=provider,
        turn_handling={"interruption": {"mode": "vad"}},
    )
    await session.start(
        room=ctx.room,
        record=False,
        agent=Agent(
            instructions=(
                "You are a concise voice assistant using a synthetic voice. "
                "Use short sentences, no markup, and at most three sentences per response."
            )
        ),
    )
    logger.info("Room session started; generating the synthetic greeting.")
    await session.say("Hello. This is a synthetic voice assistant.")
    logger.info("Synthetic greeting finished.")


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    if sys.argv[1:] == ["check"]:
        problems = asyncio.run(check_setup())
        for problem in problems:
            print(f"NOT READY: {problem}")
        if problems:
            raise SystemExit(1)
        print(
            "READY: configured room agent, selected profile, cached/loaded model, no tracked work."
        )
        print("Credential validity and audible room playback still require a real connection.")
    else:
        cli.run_app(server)
