import os
import time
import unittest
from unittest.mock import patch

from xai_twilio_bridge import XaiTwilioBridge, _consent_answer


class XaiTwilioBridgeTests(unittest.TestCase):
    def test_consent_requires_clear_answer_and_handles_pt_accents(self):
        self.assertEqual(_consent_answer("Sim, autorizo."), "granted")
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
