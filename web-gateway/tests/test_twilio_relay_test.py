import os
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from fastapi import HTTPException
from twilio_relay_test import TwilioRelayTestBridge, _echo_reply


class TwilioRelayTestBridgeTests(unittest.TestCase):
    def _bridge(self):
        env = {
            "LUMIN_VOICE_TRANSPORT": "twilio-relay-test",
            "LUMIN_GATEWAY_PUBLIC_URL": "https://gateway.example.test",
            "LUMIN_TWILIO_TEST_PHONE": "+351912345678",
            "LUMIN_TWILIO_RELAY_TEST_SECRET": "s" * 40,
            "TWILIO_ACCOUNT_SID": "AC-test",
            "TWILIO_API_KEY_SID": "SK-test",
            "TWILIO_API_KEY_SECRET": "api-secret",
            "TWILIO_AUTH_TOKEN": "auth-token",
            "SD_DIALER_SUPABASE_URL": "https://database.example.test",
            "SD_DIALER_SUPABASE_SERVICE_ROLE_KEY": "service-key",
        }
        with patch.dict(os.environ, env, clear=False):
            return TwilioRelayTestBridge({})

    def test_enabled_only_for_telecom_sd_dialer_calls(self):
        bridge = self._bridge()
        self.assertTrue(bridge.should_handle("sd-dialer", {"id": "lumin-telecom"}))
        self.assertTrue(bridge.should_handle("sd-dialer-manual", {"id": "lumin-telecom"}))
        self.assertFalse(bridge.should_handle("lumin-call-api", {"id": "lumin-telecom"}))
        self.assertFalse(bridge.should_handle("sd-dialer", {"id": "lumin-energia"}))

    def test_only_configured_phone_is_allowed(self):
        bridge = self._bridge()
        bridge.assert_test_phone("+351 912 345 678")
        with self.assertRaises(HTTPException) as error:
            bridge.assert_test_phone("+351912345679")
        self.assertEqual(error.exception.status_code, 403)

    def test_signed_stream_token_is_bound_to_call_id_and_expiry(self):
        bridge = self._bridge()
        token = bridge._stream_token("call-a")
        self.assertTrue(bridge._valid_stream_token("call-a", token))
        self.assertFalse(bridge._valid_stream_token("call-b", token))

    def test_twiml_selects_portugal_voice_and_google_telephony_stt(self):
        root = ET.fromstring(self._bridge()._build_twiml("call-a", "token"))
        relay = root.find("./Connect/ConversationRelay")
        self.assertEqual(relay.attrib["language"], "pt-PT")
        self.assertEqual(relay.attrib["ttsProvider"], "ElevenLabs")
        self.assertEqual(relay.attrib["voice"], "TsZfI8Nbn2Xd7ArC76n9")
        self.assertEqual(relay.attrib["transcriptionProvider"], "Google")
        self.assertEqual(relay.attrib["speechModel"], "telephony")
        self.assertEqual(relay.find("Parameter").attrib, {"name": "token", "value": "token"})

    def test_echo_confirms_the_words_heard_and_recognises_the_end_phrase(self):
        self.assertEqual(
            _echo_reply("Sou cliente MEO, pago 20 euros e prefiro amanhã às 7."),
            "Percebi: Sou cliente MEO, pago 20 euros e prefiro amanhã às 7. Está correto? Se não, diga a frase novamente.",
        )
        self.assertIsNone(_echo_reply("Terminar teste"))


if __name__ == "__main__":
    unittest.main()
