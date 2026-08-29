package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.telecom.Call
import android.telecom.InCallService
import org.json.JSONObject

class SdInCallService : InCallService() {
    override fun onCallAdded(call: Call) {
        super.onCallAdded(call)
        currentCall = call
        call.registerCallback(callback)
        reportState(call.state)
    }

    override fun onCallRemoved(call: Call) {
        call.unregisterCallback(callback)
        report("ENDED")
        if (currentCall === call) currentCall = null
        super.onCallRemoved(call)
    }

    private val callback = object : Call.Callback() {
        override fun onStateChanged(call: Call, state: Int) {
            reportState(state)
        }
    }

    private fun reportState(state: Int) {
        when (state) {
            Call.STATE_DIALING, Call.STATE_CONNECTING -> report("DIALING")
            Call.STATE_RINGING -> report("RINGING")
            Call.STATE_ACTIVE -> {
                report("ACTIVE")
                probeCallAudio()
            }
            Call.STATE_DISCONNECTED -> report("ENDED")
        }
    }

    private fun probeCallAudio() {
        Thread {
            val payload = try {
                AudioCapabilities.probeVoiceCallCapture(this)
            } catch (t: Throwable) {
                JSONObject()
                    .put("voice_call_capture", false)
                    .put("reason", t.javaClass.simpleName)
                    .put("error", t.message ?: "Audio probe failed")
            }
            try {
                GatewayApi.event(this, "AUDIO_CAPABILITY", pendingCallId, pendingCommandId, payload)
            } catch (_: Throwable) { }
        }.start()
    }

    private fun report(type: String) {
        Thread {
            try {
                GatewayApi.event(this, type, pendingCallId, pendingCommandId, JSONObject())
            } catch (_: Throwable) { }
        }.start()
    }

    companion object {
        @Volatile var pendingCallId: String? = null
        @Volatile var pendingCommandId: String? = null
        @Volatile private var currentCall: Call? = null

        fun hangupCurrentCall(context: Context, callId: String?) {
            currentCall?.disconnect()
            try {
                GatewayApi.event(context, "ENDED", callId ?: pendingCallId, pendingCommandId)
            } catch (_: Throwable) { }
        }
    }
}
