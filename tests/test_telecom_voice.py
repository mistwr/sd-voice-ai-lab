"""Exercise the production TTS selection without downloading speech models."""
import ast
import copy
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest


class Piper:
    kind = "piper"
    sample_rate = 22050

    def with_profile(self, **settings):
        selected = Piper()
        selected.settings = settings
        return selected


class Kokoro:
    kind = "kokoro"
    sample_rate = 24000

    def __init__(self, url):
        self.url = url


class Fallback:
    def __init__(self, *, tts, max_retry_per_tts, sample_rate):
        self.engines = tts

    def on(self, event, callback):
        pass


def select_telecom_voice(kokoro_url):
    # LiveKit/Piper imports load external models. Compile only the real routing
    # statement and its real helpers; replace the external TTS constructors.
    source = ast.parse((Path(__file__).parents[1] / "worker/web_agent.py").read_text())
    helpers = [node for node in source.body if
               isinstance(node, ast.FunctionDef) and node.name in
               {"voice_for_profile", "xai_like_open_source_tts"} or
               isinstance(node, ast.Assign) and any(
                   isinstance(target, ast.Name) and target.id == "VOICE_PROFILES"
                   for target in node.targets)]
    entrypoint = next(node for node in source.body if
                      isinstance(node, ast.AsyncFunctionDef) and node.name == "entrypoint")
    routing = next(node for node in ast.walk(entrypoint) if
                   isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                   and node.test.id == "is_telecom_profile")
    # Retain all production branches; this case must select the Telecom branch.
    namespace = {"base_tts": Piper(), "kokoro_url": kokoro_url,
                 "is_telecom_profile": True, "logger": logging.getLogger("test"),
                 "KokoroRemoteTTS": Kokoro, "tts": SimpleNamespace(FallbackAdapter=Fallback)}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), *copy.deepcopy(helpers), copy.deepcopy(routing)],
        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "worker/web_agent.py", "exec"), namespace)
    return namespace["tts_engine"]


class TelecomVoiceTests(unittest.TestCase):
    def test_telecom_uses_kokoro_as_primary_voice(self):
        engine = select_telecom_voice("http://configured-kokoro:8080")
        self.assertIsInstance(engine, Fallback)
        self.assertEqual(engine.engines[0].kind, "kokoro")
        self.assertEqual(engine.engines[0].url, "http://configured-kokoro:8080")

    def test_telecom_retains_clear_piper_as_failure_fallback(self):
        engine = select_telecom_voice("http://configured-kokoro:8080")
        self.assertIsInstance(engine, Fallback)
        self.assertEqual(engine.engines[1].kind, "piper")
        self.assertEqual(engine.engines[1].settings["length_scale"], 1.17)

    def test_missing_kokoro_configuration_keeps_calls_speaking(self):
        self.assertEqual(select_telecom_voice("").kind, "piper")


if __name__ == "__main__":
    unittest.main()
