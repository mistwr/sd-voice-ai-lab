package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import org.json.JSONObject

/** Stock/no-root TX routing probe. Writes PCM silence only. */
object TelephonyTxDiagnostics {
    fun collect(context: Context): JSONObject {
        val am = context.getSystemService(AudioManager::class.java)
        val telephony = am?.getDevices(AudioManager.GET_DEVICES_OUTPUTS)?.firstOrNull { it.type == AudioDeviceInfo.TYPE_TELEPHONY }
        val out = JSONObject().put("mode", "STOCK_ONLY").put("telephony_output_found", telephony != null).put("telephony_output_id", telephony?.id ?: -1)
        if (telephony == null) return out.put("reason", "NO_TELEPHONY_OUTPUT")
        val rate = 16000
        val min = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        var track: AudioTrack? = null
        return try {
            track = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setBufferSizeInBytes(maxOf(min, 3200)).setTransferMode(AudioTrack.MODE_STREAM).build()
            val initialized = track.state == AudioTrack.STATE_INITIALIZED
            val preferredAccepted = if (initialized) track.setPreferredDevice(telephony) else false
            val preferredId = track.preferredDevice?.id ?: -1
            var written = 0
            if (initialized) {
                val silence = ShortArray(1600) // 100 ms silence only
                track.play()
                written = track.write(silence, 0, silence.size, AudioTrack.WRITE_BLOCKING)
                Thread.sleep(80)
            }
            JSONObject().put("mode", "STOCK_ONLY").put("telephony_output_found", true).put("telephony_output_id", telephony.id)
                .put("initialized", initialized).put("preferred_device_accepted", preferredAccepted).put("preferred_device_id", preferredId)
                .put("routed_device_id", track.routedDevice?.id ?: -1).put("written_samples", written).put("test_signal", "SILENCE_ONLY")
        } catch (t: Throwable) {
            out.put("initialized", false).put("error", t.javaClass.simpleName).put("detail", t.message ?: "")
        } finally {
            try { track?.stop() } catch (_: Throwable) {}
            try { track?.release() } catch (_: Throwable) {}
        }
    }
}
