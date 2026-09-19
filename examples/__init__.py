"""Local examples and optional Studio tooling; not part of the published plugin."""

import os

# Set before importing Silero/ONNX Runtime: its API opt-out is too late for
# initialization telemetry. Studio examples do not need the native uploader.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"
