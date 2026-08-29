package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.sqrt

/**
 * Runtime diagnostics for direct GSM call capture capability.
 *
 * Important: STATE_INITIALIZED is not enough. Some Samsung/SoC combinations
 * allow AudioRecord to initialize while returning silence. We therefore read
 * PCM for a short period and calculate RMS/peak before claiming useful capture.
 *
 * No permission bypass is attempted here. Stock / privileged / rooted results
 * are reported exactly as observed on the real device.
 */
object AudioCapabilities {
    private const val CAPTURE_AUDIO_OUTPUT = "android.permission.CAPTURE_AUDIO_OUTPUT"
    private const val READ_WINDOW_MS = 900L
    private const val NON_SILENT_RMS = 18.0

    private data class ProbeConfig(val source: Int, val name: String, val rate: Int)

    fun probeVoiceCallCapture(context: Context): JSONObject {
        val recordAudioGranted = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED
        val captureOutputGranted =
            context.checkCallingOrSelfPermission(CAPTURE_AUDIO_OUTPUT) == PackageManager.PERMISSION_GRANTED

        val result = JSONObject()
            .put("record_audio_granted", recordAudioGranted)
            .put("capture_audio_output_granted", captureOutputGranted)
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("device", Build.DEVICE)
            .put("hardware", Build.HARDWARE)
            .put("board", Build.BOARD)
            .put("sdk", Build.VERSION.SDK_INT)

        if (!recordAudioGranted) {
            return result
                .put("voice_call_capture", false)
                .put("reason", "RECORD_AUDIO_NOT_GRANTED")
                .put("probes", JSONArray())
        }

        val configs = listOf(
            ProbeConfig(MediaRecorder.AudioSource.VOICE_CALL, "VOICE_CALL_16K", 16000),
            ProbeConfig(MediaRecorder.AudioSource.VOICE_CALL, "VOICE_CALL_8K", 8000),
            ProbeConfig(MediaRecorder.AudioSource.VOICE_DOWNLINK, "VOICE_DOWNLINK_16K", 16000),
            ProbeConfig(MediaRecorder.AudioSource.VOICE_DOWNLINK, "VOICE_DOWNLINK_8K", 8000),
        )

        val probes = JSONArray()
        var useful: JSONObject? = null
        for (cfg in configs) {
            val probe = probeSource(cfg)
            probes.put(probe)
            if (useful == null && probe.optBoolean("non_silent", false)) useful = probe
        }

        result.put("probes", probes)
        if (useful != null) {
            result.put("voice_call_capture", true)
            result.put("reason", "NON_SILENT_DIRECT_CAPTURE")
            result.put("working_source", useful.optString("name"))
            result.put("working_rate", useful.optInt("sample_rate"))
            result.put("rms", useful.optDouble("rms"))
            result.put("peak", useful.optInt("peak"))
        } else {
            val anyInitialized = (0 until probes.length()).any {
                probes.getJSONObject(it).optBoolean("initialized", false)
            }
            result.put("voice_call_capture", false)
            result.put("reason", if (anyInitialized) "INITIALIZED_BUT_SILENT_OR_UNREADABLE" else "NO_DIRECT_SOURCE_INITIALIZED")
        }
        return result
    }

    private fun probeSource(cfg: ProbeConfig): JSONObject {
        val out = JSONObject()
            .put("name", cfg.name)
            .put("source", cfg.source)
            .put("sample_rate", cfg.rate)

        val minBuffer = AudioRecord.getMinBufferSize(
            cfg.rate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        )
        out.put("min_buffer", minBuffer)
        if (minBuffer <= 0) {
            return out.put("initialized", false).put("reason", "INVALID_MIN_BUFFER")
        }

        var recorder: AudioRecord? = null
        return try {
            recorder = AudioRecord(
                cfg.source,
                cfg.rate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                (minBuffer * 2).coerceAtLeast(cfg.rate / 5)
            )
            val initialized = recorder.state == AudioRecord.STATE_INITIALIZED
            out.put("initialized", initialized)
            if (!initialized) return out.put("reason", "AUDIO_RECORD_NOT_INITIALIZED")

            recorder.startRecording()
            val recording = recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING
            out.put("recording", recording)
            if (!recording) return out.put("reason", "START_RECORDING_FAILED")

            val buffer = ShortArray((cfg.rate / 50).coerceAtLeast(160)) // about 20 ms
            val deadline = System.currentTimeMillis() + READ_WINDOW_MS
            var frames = 0
            var samples = 0L
            var sumSquares = 0.0
            var peak = 0
            var readErrors = 0

            while (System.currentTimeMillis() < deadline) {
                val n = recorder.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
                if (n > 0) {
                    frames++
                    samples += n
                    for (i in 0 until n) {
                        val v = buffer[i].toInt()
                        val av = kotlin.math.abs(v)
                        if (av > peak) peak = av
                        sumSquares += v.toDouble() * v.toDouble()
                    }
                } else {
                    readErrors++
                    if (readErrors >= 3) break
                }
            }

            val rms = if (samples > 0) sqrt(sumSquares / samples) else 0.0
            val nonSilent = samples > 0 && rms >= NON_SILENT_RMS && peak >= 64
            out.put("frames", frames)
                .put("samples", samples)
                .put("read_errors", readErrors)
                .put("rms", rms)
                .put("peak", peak)
                .put("non_silent", nonSilent)
                .put("reason", if (nonSilent) "OK_NON_SILENT" else "SILENT_OR_TOO_LOW")
            out
        } catch (security: SecurityException) {
            out.put("initialized", false)
                .put("reason", "SECURITY_EXCEPTION")
                .put("error", security.message ?: "Protected call source denied")
        } catch (t: Throwable) {
            out.put("initialized", false)
                .put("reason", t.javaClass.simpleName)
                .put("error", t.message ?: "Unknown audio error")
        } finally {
            try {
                if (recorder?.recordingState == AudioRecord.RECORDSTATE_RECORDING) recorder.stop()
            } catch (_: Throwable) { }
            try { recorder?.release() } catch (_: Throwable) { }
        }
    }
}
