package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.media.MediaRecorder
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object CallSessionRecorder {
    @Volatile private var recorder: MediaRecorder? = null
    @Volatile private var outputFile: File? = null
    @Volatile private var startedAt: Long = 0L

    @Synchronized
    fun start(context: Context): JSONObject {
        if (recorder != null) return JSONObject().put("recording", true).put("reason", "already_started")
        val dir = File(context.filesDir, "calls").apply { mkdirs() }
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
        val file = File(dir, "sofia-$stamp.m4a")
        return try {
            val r = if (android.os.Build.VERSION.SDK_INT >= 31) MediaRecorder(context) else @Suppress("DEPRECATION") MediaRecorder()
            r.setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioEncodingBitRate(64000)
            r.setAudioSamplingRate(16000)
            r.setOutputFile(file.absolutePath)
            r.prepare()
            r.start()
            recorder = r
            outputFile = file
            startedAt = System.currentTimeMillis()
            JSONObject()
                .put("recording", true)
                .put("mode", "STOCK_BEST_EFFORT")
                .put("source", "VOICE_COMMUNICATION")
                .put("local_file", file.name)
                .put("started_at", startedAt)
        } catch (t: Throwable) {
            try { recorder?.release() } catch (_: Throwable) {}
            recorder = null
            outputFile = null
            JSONObject()
                .put("recording", false)
                .put("mode", "STOCK_BEST_EFFORT")
                .put("error", t.javaClass.simpleName)
                .put("message", t.message ?: "recording unavailable")
        }
    }

    @Synchronized
    fun stop(context: Context): JSONObject {
        val r = recorder ?: return JSONObject().put("recording", false).put("reason", "not_started")
        val file = outputFile
        val start = startedAt
        return try {
            r.stop()
            r.release()
            recorder = null
            outputFile = null
            startedAt = 0L
            val bytes = file?.length() ?: 0L
            val duration = if (start > 0) System.currentTimeMillis() - start else 0L
            val saved = file?.exists() == true && bytes > 0
            if (saved && file != null) {
                context.getSharedPreferences("gateway", Context.MODE_PRIVATE).edit()
                    .putString("latest_recording_file", file.name)
                    .putLong("latest_recording_bytes", bytes)
                    .putLong("latest_recording_duration_ms", duration)
                    .putLong("latest_recording_saved_at", System.currentTimeMillis())
                    .apply()
            }
            JSONObject()
                .put("recording", false)
                .put("saved", saved)
                .put("local_file", file?.name ?: "")
                .put("bytes", bytes)
                .put("duration_ms", duration)
        } catch (t: Throwable) {
            try { r.release() } catch (_: Throwable) {}
            recorder = null
            outputFile = null
            startedAt = 0L
            JSONObject()
                .put("recording", false)
                .put("saved", false)
                .put("error", t.javaClass.simpleName)
                .put("message", t.message ?: "stop failed")
        }
    }

    fun latestFile(context: Context): File? {
        val name = context.getSharedPreferences("gateway", Context.MODE_PRIVATE)
            .getString("latest_recording_file", null) ?: return null
        val file = File(File(context.filesDir, "calls"), name)
        return file.takeIf { it.exists() && it.isFile && it.length() > 0 }
    }
}
