package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.telecom.Call
import android.telecom.InCallService
import org.json.JSONObject

class SdInCallService : InCallService() {
    @Volatile private var probedThisCall = false

    override fun onCallAdded(call: Call) {
        super.onCallAdded(call)
        currentCall = call
        probedThisCall = false
        call.registerCallback(callback)
        reportState(call.state)
    }

    override fun onCallRemoved(call: Call) {
        call.unregisterCallback(callback)
        report("ENDED")
        if (currentCall === call) currentCall = null
        probedThisCall = false
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
                if (!probedThisCall) {
                    probedThisCall = true
                    probeCallAudio()
                }
            }
            Call.STATE_DISCONNECTED -> report("ENDED")
        }
    }

    private fun probeCallAudio() {
        Thread {
            val privilegePayload = try {
                RootDiagnostics.collect(this)
            } catch (t: Throwable) {
                JSONObject()
                    .put("mode", "UNKNOWN")
                    .put("error", t.message ?: t.javaClass.simpleName)
            }
            try {
                GatewayApi.event(this, "DEVICE_CAPABILITY", pendingCallId, pendingCommandId, privilegePayload)
            } catch (_: Throwable) { }

            val audioPayload = try {
                AudioCapabilities.probeVoiceCallCapture(this)
            } catch (t: Throwable) {
                JSONObject()
                    .put("voice_call_capture", false)
                    .put("reason", t.javaClass.simpleName)
                    .put("error", t.message ?: "Audio probe failed")
            }
            try {
                GatewayApi.event(this, "AUDIO_CAPABILITY", pendingCallId, pendingCommandId, audioPayload)
            } catch (_: Throwable) { }

            val txPayload = try {
                TxAudioDiagnostics.collect(this)
            } catch (t: Throwable) {
                JSONObject()
                    .put("candidate", "UNKNOWN")
                    .put("error", t.message ?: t.javaClass.simpleName)
            }
            try {
                GatewayApi.event(this, "TX_AUDIO_CAPABILITY", pendingCallId, pendingCommandId, txPayload)
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
