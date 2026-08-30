package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import org.json.JSONArray
import org.json.JSONObject

/**
 * Stock-only diagnostics for Build 36.
 * No root, no hidden APIs, no privileged permissions.
 */
object StockAudioRouteDiagnostics {
    fun collect(context: Context): JSONObject {
        val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        return JSONObject().apply {
            put("mode", "STOCK_ONLY")
            put("audio_mode", audio.mode)
            put("speakerphone_on", audio.isSpeakerphoneOn)
            put("microphone_muted", audio.isMicrophoneMute)
            put("music_active", audio.isMusicActive)
            put("inputs", devices(audio.getDevices(AudioManager.GET_DEVICES_INPUTS)))
            put("outputs", devices(audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS)))
            put("telephony_input_present", audio.getDevices(AudioManager.GET_DEVICES_INPUTS).any { it.type == AudioDeviceInfo.TYPE_TELEPHONY })
            put("telephony_output_present", audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any { it.type == AudioDeviceInfo.TYPE_TELEPHONY })
            put("public_source_probes", probePublicSources())
        }
    }

    private fun devices(list: Array<AudioDeviceInfo>): JSONArray = JSONArray().apply {
        list.forEach { d ->
            put(JSONObject().apply {
                put("id", d.id)
                put("type", d.type)
                put("type_name", typeName(d.type))
                put("product", d.productName?.toString() ?: "")
                put("is_source", d.isSource)
                put("is_sink", d.isSink)
                put("sample_rates", JSONArray(d.sampleRates.toList()))
                put("channel_counts", JSONArray(d.channelCounts.toList()))
            })
        }
    }

    private fun probePublicSources(): JSONArray {
        val sources = listOf(
            "MIC" to MediaRecorder.AudioSource.MIC,
            "VOICE_RECOGNITION" to MediaRecorder.AudioSource.VOICE_RECOGNITION,
            "VOICE_COMMUNICATION" to MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            "UNPROCESSED" to MediaRecorder.AudioSource.UNPROCESSED
        )
        return JSONArray().apply {
            sources.forEach { (name, source) -> put(probeSource(name, source)) }
        }
    }

    private fun probeSource(name: String, source: Int): JSONObject {
        val sampleRate = 16000
        val min = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        ).coerceAtLeast(2048)
        var record: AudioRecord? = null
        return try {
            record = AudioRecord.Builder()
                .setAudioSource(source)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build()
                )
                .setBufferSizeInBytes(min * 2)
                .build()
            JSONObject().apply {
                put("name", name)
                put("source", source)
                put("initialized", record.state == AudioRecord.STATE_INITIALIZED)
                put("audio_session_id", if (record.state == AudioRecord.STATE_INITIALIZED) record.audioSessionId else -1)
            }
        } catch (t: Throwable) {
            JSONObject().apply {
                put("name", name)
                put("source", source)
                put("initialized", false)
                put("error", "${t.javaClass.simpleName}:${t.message ?: "unknown"}")
            }
        } finally {
            try { record?.release() } catch (_: Throwable) { }
        }
    }

    private fun typeName(type: Int): String = when (type) {
        AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> "BUILTIN_EARPIECE"
        AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "BUILTIN_SPEAKER"
        AudioDeviceInfo.TYPE_WIRED_HEADSET -> "WIRED_HEADSET"
        AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "WIRED_HEADPHONES"
        AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "BLUETOOTH_SCO"
        AudioDeviceInfo.TYPE_BLUETOOTH_A2DP -> "BLUETOOTH_A2DP"
        AudioDeviceInfo.TYPE_BUILTIN_MIC -> "BUILTIN_MIC"
        AudioDeviceInfo.TYPE_TELEPHONY -> "TELEPHONY"
        AudioDeviceInfo.TYPE_USB_DEVICE -> "USB_DEVICE"
        AudioDeviceInfo.TYPE_USB_HEADSET -> "USB_HEADSET"
        AudioDeviceInfo.TYPE_BLE_HEADSET -> "BLE_HEADSET"
        AudioDeviceInfo.TYPE_BLE_SPEAKER -> "BLE_SPEAKER"
        else -> "TYPE_$type"
    }
}
