"""Offline recovery preflight: no network, credentials or calls required."""
import ast
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent
source = (root / "web_agent.py").read_text(encoding="utf-8")
ast.parse(source)
checks = {
    "LiveKit xAI TTS": 'model="xai/tts-1"' in source,
    "Portuguese voice": 'language="pt-PT"' in source,
    "Deepgram STT": 'deepgram/nova-3' in source,
    "LUMIN worker": 'agent_name="lumin-web"' in source,
    "Piper prewarm dependency": 'prewarm_fnc=prewarm' in source,
}
for label, ok in checks.items():
    print(f"{'OK' if ok else 'FAIL'}: {label}")
if not all(checks.values()):
    sys.exit(1)
print("Static checks passed. Runtime/audio/call functionality NOT tested.")
