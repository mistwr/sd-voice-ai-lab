package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.telecom.Call
import android.telecom.InCallService
import org.json.JSONObject

class SdInCallService : InCallService() {
    @Volatile private var probedThisCall = false
    @Volatile private var recordingStarted = false

    override fun onCallAdded(call: Call) {
        super.onCallAdded(call)
        currentCall = call
        probedThisCall = false
        recordingStarted = false
        saveDiagnostic("CALL", "Call detected state=${call.state}")
        call.registerCallback(callback)
        reportState(call.state)
    }

    override fun onCallRemoved(call: Call) {
        finishRecordingAndUpload()
        call.unregisterCallback(callback)
        report("ENDED")
        saveDiagnostic("CALL", "Call ended")
        if (currentCall === call) currentCall = null
        probedThisCall = false
        super.onCallRemoved(call)
    }

    private val callback = object : Call.Callback() {
        override fun onStateChanged(call: Call, state: Int) { reportState(state) }
    }

    private fun reportState(state: Int) {
        saveDiagnostic("STATE", stateName(state))
        when (state) {
            Call.STATE_DIALING, Call.STATE_CONNECTING -> report("DIALING")
            Call.STATE_RINGING -> report("RINGING")
            Call.STATE_ACTIVE -> {
                report("ACTIVE")
                startAiSessionIfEnabled()
                if (!probedThisCall) {
                    probedThisCall = true
                    probeCallAudio()
                }
            }
            Call.STATE_DISCONNECTED -> {
                finishRecordingAndUpload()
                report("ENDED")
            }
        }
    }

    private fun startAiSessionIfEnabled() {
        val p = getSharedPreferences("gateway", Context.MODE_PRIVATE)
        val record = p.getBoolean("record_calls", true)
        val transcribe = p.getBoolean("transcribe_calls", true)
        val upload = p.getBoolean("upload_recordings", true)
        sendCapability(
            "AI_SESSION_STARTED",
            JSONObject()
                .put("recording_requested", record)
                .put("transcription_requested", transcribe)
                .put("upload_requested", upload)
                .put("mode", "STOCK_ONLY")
        )
        if (record && !recordingStarted) {
            val result = CallSessionRecorder.start(this)
            recordingStarted = result.optBoolean("recording", false)
            sendCapability("CALL_RECORDING_STARTED", result)
        }
        if (transcribe) {
            sendCapability(
                "TRANSCRIPTION_REQUESTED",
                JSONObject()
                    .put("enabled", true)
                    .put("status", "POST_CALL_RECORDING_PENDING")
                    .put("live_pcm_status", "UNAVAILABLE")
                    .put("note", "Post-call transcription can use the uploaded recording. Do not fabricate a transcript.")
            )
        }
    }

    private fun finishRecordingAndUpload() {
        if (!recordingStarted) return
        val result = CallSessionRecorder.stop(this)
        recordingStarted = false
        sendCapability("CALL_RECORDING_STOPPED", result)

        val prefs = getSharedPreferences("gateway", Context.MODE_PRIVATE)
        val callId = pendingCallId
        val commandId = pendingCommandId
        prefs.edit()
            .putString("latest_recording_call_id", callId)
            .putString("latest_recording_command_id", commandId)
            .apply()

        if (!result.optBoolean("saved", false) || !prefs.getBoolean("upload_recordings", true)) return
        val file = CallSessionRecorder.latestFile(this) ?: return
        val duration = result.optLong("duration_ms", prefs.getLong("latest_recording_duration_ms", 0L))

        Thread {
            try {
                val upload = GatewayApi.uploadRecording(applicationContext, file, callId, commandId, duration)
                saveDiagnostic("RECORDING_UPLOAD", upload.toString())
                prefs.edit()
                    .putString("latest_recording_remote_path", upload.optString("path", ""))
                    .putString("latest_recording_upload_status", "uploaded")
                    .apply()
                sendCapability("RECORDING_UPLOADED", upload)
            } catch (t: Throwable) {
                val error = JSONObject()
                    .put("uploaded", false)
                    .put("local_file", file.name)
                    .put("error", t.javaClass.simpleName)
                    .put("message", t.message ?: "upload failed")
                saveDiagnostic("RECORDING_UPLOAD", error.toString())
                prefs.edit().putString("latest_recording_upload_status", "failed").apply()
            }
        }.start()
    }

    private fun probeCallAudio() {
        Thread {
            val devicePayload = JSONObject()
                .put("manufacturer", android.os.Build.MANUFACTURER)
                .put("model", android.os.Build.MODEL)
                .put("device", android.os.Build.DEVICE)
                .put("hardware", android.os.Build.HARDWARE)
                .put("board", android.os.Build.BOARD)
                .put("sdk", android.os.Build.VERSION.SDK_INT)
                .put("mode", "STOCK_ONLY")
            val routePayload = try { StockAudioRouteDiagnostics.collect(this) } catch (t: Throwable) { JSONObject().put("mode", "STOCK_ONLY").put("error", t.message ?: t.javaClass.simpleName) }
            val pcmPayload = try { TelephonyPcmDiagnostics.collect(this) } catch (t: Throwable) { JSONObject().put("mode", "STOCK_ONLY").put("error", t.message ?: t.javaClass.simpleName) }
            val txPayload = try { TelephonyTxDiagnostics.collect(this) } catch (t: Throwable) { JSONObject().put("mode", "STOCK_ONLY").put("error", t.message ?: t.javaClass.simpleName) }
            val audioPayload = try { AudioCapabilities.probeVoiceCallCapture(this) } catch (t: Throwable) { JSONObject().put("voice_call_capture", false).put("reason", t.javaClass.simpleName).put("error", t.message ?: "Audio probe failed") }
            sendCapability("DEVICE_CAPABILITY", devicePayload)
            sendCapability("STOCK_AUDIO_ROUTE_CAPABILITY", routePayload)
            sendCapability("TELEPHONY_PCM_CAPABILITY", pcmPayload)
            sendCapability("TELEPHONY_TX_CAPABILITY", txPayload)
            sendCapability("AUDIO_CAPABILITY", audioPayload)
        }.start()
    }

    private fun sendCapability(type: String, payload: JSONObject) {
        saveDiagnostic(type, payload.toString())
        try {
            GatewayApi.event(this, type, pendingCallId, pendingCommandId, payload)
        } catch (t: Throwable) {
            saveDiagnostic("${type}_HTTP_ERROR", "${t.javaClass.simpleName}: ${t.message}")
        }
    }

    private fun report(type: String) {
        Thread {
            try { GatewayApi.event(this, type, pendingCallId, pendingCommandId, JSONObject()) }
            catch (t: Throwable) { saveDiagnostic("${type}_HTTP_ERROR", "${t.javaClass.simpleName}: ${t.message}") }
        }.start()
    }

    private fun saveDiagnostic(type: String, value: String) {
        getSharedPreferences("gateway", Context.MODE_PRIVATE).edit()
            .putString("diag_$type", value)
            .putLong("diag_last_at", System.currentTimeMillis())
            .apply()
    }

    private fun stateName(state: Int) = when (state) {
        Call.STATE_NEW -> "NEW"
        Call.STATE_DIALING -> "DIALING"
        Call.STATE_RINGING -> "RINGING"
        Call.STATE_HOLDING -> "HOLDING"
        Call.STATE_ACTIVE -> "ACTIVE"
        Call.STATE_DISCONNECTED -> "DISCONNECTED"
        Call.STATE_CONNECTING -> "CONNECTING"
        Call.STATE_DISCONNECTING -> "DISCONNECTING"
        Call.STATE_SELECT_PHONE_ACCOUNT -> "SELECT_PHONE_ACCOUNT"
        else -> "STATE_$state"
    }

    companion object {
        @Volatile var pendingCallId: String? = null
        @Volatile var pendingCommandId: String? = null
        @Volatile private var currentCall: Call? = null
        fun hangupCurrentCall(context: Context, callId: String?) {
            currentCall?.disconnect()
            try { GatewayApi.event(context, "ENDED", callId ?: pendingCallId, pendingCommandId) } catch (_: Throwable) {}
        }
    }
}
