package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.media.AudioManager
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit

/**
 * Read-only diagnostics for possible same-device TTS -> GSM uplink routing.
 *
 * This intentionally does not change mixer controls. It only reports clues
 * needed to choose a verified device profile later (Qualcomm incall_music,
 * Samsung/ABOX NSRC routing, or no known digital path).
 */
object TxAudioDiagnostics {
    fun collect(context: Context): JSONObject {
        val root = RootDiagnostics.collect(context)
        val out = JSONObject()
            .put("mode", root.optString("mode"))
            .put("hardware", Build.HARDWARE)
            .put("board", Build.BOARD)
            .put("model", Build.MODEL)

        val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        out.put("audio_mode", audio.mode)
        out.put("speakerphone_on", audio.isSpeakerphoneOn)
        out.put("music_active", audio.isMusicActive)

        if (!root.optBoolean("su_available", false)) {
            out.put("root_mixer_scan", false)
            out.put("candidate", "UNKNOWN_STOCK")
            return out
        }

        val commands = listOf(
            "command -v tinymix || true",
            "for p in /vendor/bin/tinymix /system/bin/tinymix /system/xbin/tinymix /data/local/tmp/tinymix; do [ -x \"$p\" ] && echo \"TINYMIX:$p\"; done",
            "cat /proc/asound/cards 2>/dev/null | head -20",
            "TM=$(command -v tinymix 2>/dev/null); [ -z \"$TM\" ] && [ -x /vendor/bin/tinymix ] && TM=/vendor/bin/tinymix; [ -n \"$TM\" ] && $TM 2>&1 | grep -iE 'incall|nsrc|bridge|voice tx|voice rx|multimedia|sifs|uaif' | head -80 || true"
        )

        val scan = runRoot(commands.joinToString("; "))
        out.put("root_mixer_scan", scan.first)
        out.put("mixer_output", scan.second.take(12000))

        val text = scan.second.lowercase()
        val candidates = JSONArray()
        if ("incall_music" in text || "incall music" in text) candidates.put("QUALCOMM_INCALL_MUSIC")
        if ("abox nsrc" in text || ("nsrc" in text && "sifs" in text)) candidates.put("SAMSUNG_ABOX_NSRC")
        if ("voice tx" in text && "multimedia" in text) candidates.put("VOICE_TX_MULTIMEDIA_MIXER")
        out.put("candidates", candidates)
        out.put("candidate", if (candidates.length() > 0) candidates.getString(0) else "NO_KNOWN_MIXER_FOUND")
        return out
    }

    private fun runRoot(command: String): Pair<Boolean, String> {
        return try {
            val process = ProcessBuilder("su", "-c", command).redirectErrorStream(true).start()
            val finished = process.waitFor(2500, TimeUnit.MILLISECONDS)
            if (!finished) {
                process.destroyForcibly()
                false to "timeout"
            } else {
                val text = BufferedReader(InputStreamReader(process.inputStream)).use { it.readText() }
                (process.exitValue() == 0) to text
            }
        } catch (t: Throwable) {
            false to (t.javaClass.simpleName + ":" + (t.message ?: ""))
        }
    }
}
