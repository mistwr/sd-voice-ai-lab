package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.TimeUnit

/**
 * Reports whether this APK is running as stock app, privileged app or with su.
 * This is diagnostics only; it does not modify the device or attempt rooting.
 */
object RootDiagnostics {
    private const val CAPTURE_AUDIO_OUTPUT = "android.permission.CAPTURE_AUDIO_OUTPUT"
    private const val MODIFY_PHONE_STATE = "android.permission.MODIFY_PHONE_STATE"

    fun collect(context: Context): JSONObject {
        val captureGranted = context.checkCallingOrSelfPermission(CAPTURE_AUDIO_OUTPUT) == PackageManager.PERMISSION_GRANTED
        val modifyPhoneGranted = context.checkCallingOrSelfPermission(MODIFY_PHONE_STATE) == PackageManager.PERMISSION_GRANTED
        val su = runSuProbe()

        return JSONObject()
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("device", Build.DEVICE)
            .put("hardware", Build.HARDWARE)
            .put("board", Build.BOARD)
            .put("fingerprint", Build.FINGERPRINT)
            .put("sdk", Build.VERSION.SDK_INT)
            .put("capture_audio_output_granted", captureGranted)
            .put("modify_phone_state_granted", modifyPhoneGranted)
            .put("su_available", su.first)
            .put("su_uid", su.second)
            .put("mode", when {
                captureGranted && su.first -> "ROOT_PRIVILEGED"
                captureGranted -> "PRIVILEGED"
                su.first -> "ROOT_APP"
                else -> "STOCK_APP"
            })
    }

    private fun runSuProbe(): Pair<Boolean, String> {
        return try {
            val process = ProcessBuilder("su", "-c", "id -u").redirectErrorStream(true).start()
            val finished = process.waitFor(1200, TimeUnit.MILLISECONDS)
            if (!finished) {
                process.destroyForcibly()
                false to "timeout"
            } else {
                val text = BufferedReader(InputStreamReader(process.inputStream)).use { it.readText().trim() }
                (process.exitValue() == 0 && text == "0") to text
            }
        } catch (t: Throwable) {
            false to (t.javaClass.simpleName + ":" + (t.message ?: ""))
        }
    }
}
