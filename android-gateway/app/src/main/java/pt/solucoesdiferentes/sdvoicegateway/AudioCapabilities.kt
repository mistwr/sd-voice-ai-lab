package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import org.json.JSONObject

/**
 * Runtime probe for direct GSM call capture capability.
 *
 * This does NOT claim that a stock Android device can capture call audio.
 * It deliberately tests the protected VOICE_CALL source and reports the exact
 * capability/error so we can distinguish stock, privileged-app and rooted modes
 * on real Samsung hardware.
 */
object AudioCapabilities {
    private const val CAPTURE_AUDIO_OUTPUT = "android.permission.CAPTURE_AUDIO_OUTPUT"
    private const val SAMPLE_RATE = 16000

    fun probeVoiceCallCapture(context: Context): JSONObject {
        val result = JSONObject()
        val recordAudioGranted = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
        val captureOutputGranted = context.checkCallingOrSelfPermission(CAPTURE_AUDIO_OUTPUT) == PackageManager.PERMISSION_GRANTED

        result.put("record_audio_granted", recordAudioGranted)
        result.put("capture_audio_output_granted", captureOutputGranted)
        result.put("audio_source", "VOICE_CALL")
        result.put("sample_rate", SAMPLE_RATE)

        if (!recordAudioGranted) {
            result.put("voice_call_capture", false)
            result.put("reason", "RECORD_AUDIO_NOT_GRANTED")
            return result
        }

        val minBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuffer <= 0) {
            result.put("voice_call_capture", false)
            result.put("reason", "INVALID_MIN_BUFFER")
            result.put("min_buffer", minBuffer)
            return result
        }

        var recorder: AudioRecord? = null
        return try {
            recorder = AudioRecord(
                MediaRecorder.AudioSource.VOICE_CALL,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBuffer * 2
            )
            val initialized = recorder.state == AudioRecord.STATE_INITIALIZED
            result.put("audio_record_initialized", initialized)
            if (!initialized) {
                result.put("voice_call_capture", false)
                result.put("reason", "AUDIO_RECORD_NOT_INITIALIZED")
                result
            } else {
                recorder.startRecording()
                val recording = recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING
                result.put("voice_call_capture", recording)
                result.put("reason", if (recording) "OK" else "START_RECORDING_FAILED")
                if (recording) recorder.stop()
                result
            }
        } catch (security: SecurityException) {
            result.put("voice_call_capture", false)
            result.put("reason", "SECURITY_EXCEPTION")
            result.put("error", security.message ?: "VOICE_CALL capture denied")
            result
        } catch (t: Throwable) {
            result.put("voice_call_capture", false)
            result.put("reason", t.javaClass.simpleName)
            result.put("error", t.message ?: "Unknown audio error")
            result
        } finally {
            try { recorder?.release() } catch (_: Throwable) { }
        }
    }
}
