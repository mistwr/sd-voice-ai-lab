import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

from xai_twilio_bridge import XaiTwilioBridge, _consent_answer, _pt_number, _pt_time


class XaiTwilioBridgeTests(unittest.TestCase):
    def test_consent_requires_clear_answer_and_handles_pt_accents(self):
        self.assertEqual(_consent_answer("Sim, autorizo."), "granted")
        self.assertEqual(_consent_answer("Sim, pago 20 euros."), "granted")
        self.assertEqual(_consent_answer("Pode guardar a transcrição."), "granted")
        self.assertEqual(_consent_answer("Não autorizo."), "denied")
        self.assertEqual(_consent_answer("Não, obrigado."), "denied")
        self.assertIsNone(_consent_answer("Não sei."))

    def test_stream_token_is_bound_to_call_id_and_expiry(self):
        bridge = XaiTwilioBridge({})
        bridge.stream_secret = "test-secret"
        token = bridge._stream_token("call-a")
        self.assertTrue(bridge._valid_stream_token("call-a", token))
        self.assertFalse(bridge._valid_stream_token("call-b", token))
        expired = f"{int(time.time()) - 1}.invalid"
        self.assertFalse(bridge._valid_stream_token("call-a", expired))

    def test_portuguese_time_numbers_are_spoken_as_words(self):
        self.assertEqual(_pt_number(18, feminine=True), "dezoito")
        self.assertEqual(_pt_number(21, feminine=True), "vinte e uma")
        self.assertEqual(_pt_number(31), "trinta e um")

    def test_confirmation_times_are_unambiguous_in_portuguese(self):
        self.assertEqual(_pt_time(7), "às sete horas da manhã")
        self.assertEqual(_pt_time(7, 30), "às sete horas e meia da manhã")
        self.assertEqual(_pt_time(19), "às dezanove horas")
        self.assertEqual(_pt_time(12, 30), "ao meio-dia e meia")
        self.assertEqual(_pt_time(0), "à meia-noite")

    def test_realtime_session_biases_portuguese_and_slows_output_slightly(self):
        class FakeRealtimeSocket:
            def __init__(self):
                self.incoming = iter((
                    json.dumps({"type": "session.created"}),
                    json.dumps({"type": "session.updated"}),
                ))
                self.sent = []

            async def recv(self):
                return next(self.incoming)

            async def send(self, message):
                self.sent.append(json.loads(message))

        bridge = XaiTwilioBridge({})
        socket = FakeRealtimeSocket()
        asyncio.run(bridge._configure_xai(socket, {"agent": {}}))
        session = socket.sent[0]["session"]
        transcription = session["audio"]["input"]["transcription"]
        self.assertEqual(transcription["language_hint"], "pt-PT")
        self.assertIn("MEO", transcription["keyterms"])
        self.assertEqual(session["audio"]["output"]["speed"], 0.92)
        self.assertEqual(session["replace"]["Compara+"], "Compara mais")

    def test_only_telecom_sd_dialer_calls_use_direct_transport(self):
        with patch.dict(os.environ, {"LUMIN_VOICE_TRANSPORT": "xai-twilio"}):
            bridge = XaiTwilioBridge({})
        telecom = {"id": "lumin-telecom"}
        other_agent = {"id": "lumin-energia"}
        self.assertTrue(bridge.should_handle("sd-dialer", telecom))
        self.assertTrue(bridge.should_handle("sd-dialer-manual", telecom))
        self.assertFalse(bridge.should_handle("lumin-call-api", telecom))
        self.assertFalse(bridge.should_handle("sd-dialer", other_agent))


if __name__ == "__main__":
    unittest.main()
