package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.sqrt

/** Stock/no-root experiment: asks public AudioRecord sources to prefer the TELEPHONY input.
 * It never requests CAPTURE_AUDIO_OUTPUT and never executes privileged commands.
 */
object TelephonyPcmDiagnostics {
    private data class Source(val name: String, val id: Int)

    fun collect(context: Context): JSONObject {
        val am = context.getSystemService(AudioManager::class.java)
        val telephony = am?.getDevices(AudioManager.GET_DEVICES_INPUTS)?.firstOrNull { it.type == AudioDeviceInfo.TYPE_TELEPHONY }
        val result = JSONObject()
            .put("mode", "STOCK_ONLY")
            .put("telephony_input_found", telephony != null)
            .put("telephony_input_id", telephony?.id ?: -1)
        if (telephony == null) return result.put("reason", "NO_TELEPHONY_INPUT")

        val sources = listOf(
            Source("MIC", MediaRecorder.AudioSource.MIC),
            Source("VOICE_RECOGNITION", MediaRecorder.AudioSource.VOICE_RECOGNITION),
            Source("VOICE_COMMUNICATION", MediaRecorder.AudioSource.VOICE_COMMUNICATION),
            Source("UNPROCESSED", MediaRecorder.AudioSource.UNPROCESSED)
        )
        val probes = JSONArray()
        for (source in sources) probes.put(probe(source, telephony))
        return result.put("probes", probes)
    }

    private fun probe(source: Source, device: AudioDeviceInfo): JSONObject {
        val rate = 16000
        val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val bufferSize = maxOf(min, rate * 2) // >= 1 s PCM
        var record: AudioRecord? = null
        return try {
            record = AudioRecord(source.id, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize)
            val initialized = record.state == AudioRecord.STATE_INITIALIZED
            val preferredAccepted = if (initialized) record.setPreferredDevice(device) else false
            val preferredId = record.preferredDevice?.id ?: -1
            if (!initialized) return JSONObject().put("name", source.name).put("initialized", false)

            val pcm = ShortArray(rate / 2) // 500 ms: enough to establish whether real samples flow
            record.startRecording()
            val read = record.read(pcm, 0, pcm.size, AudioRecord.READ_BLOCKING)
            record.stop()

            var peak = 0
            var sumSq = 0.0
            var nonZero = 0
            if (read > 0) for (i in 0 until read) {
                val v = kotlin.math.abs(pcm[i].toInt())
                if (v > peak) peak = v
                if (v != 0) nonZero++
                sumSq += v.toDouble() * v.toDouble()
            }
            val rms = if (read > 0) sqrt(sumSq / read) else 0.0
            JSONObject()
                .put("name", source.name)
                .put("source", source.id)
                .put("initialized", true)
                .put("preferred_device_accepted", preferredAccepted)
                .put("preferred_device_id", preferredId)
                .put("routed_device_id", record.routedDevice?.id ?: -1)
                .put("read_samples", read)
                .put("nonzero_samples", nonZero)
                .put("peak", peak)
                .put("rms", Math.round(rms * 100.0) / 100.0)
        } catch (t: Throwable) {
            JSONObject().put("name", source.name).put("initialized", false)
                .put("error", t.javaClass.simpleName).put("detail", t.message ?: "")
        } finally {
            try { record?.release() } catch (_: Throwable) { }
        }
    }
}
