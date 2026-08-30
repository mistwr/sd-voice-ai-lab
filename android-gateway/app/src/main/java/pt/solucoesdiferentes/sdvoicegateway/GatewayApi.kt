package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

object GatewayApi {
    private fun connection(context: Context, path: String, contentType: String = "application/json"): HttpURLConnection {
        val prefs = context.getSharedPreferences("gateway", Context.MODE_PRIVATE)
        val base = prefs.getString("api_url", "")?.trimEnd('/') ?: ""
        require(base.startsWith("https://") || base.startsWith("http://")) { "API URL inválido" }
        return (URL(base + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 10_000
            readTimeout = 45_000
            doOutput = true
            setRequestProperty("Content-Type", contentType)
            setRequestProperty("x-device-key", prefs.getString("device_key", "") ?: "")
            setRequestProperty("x-device-token", prefs.getString("device_token", "") ?: "")
        }
    }

    fun poll(context: Context): JSONObject {
        val conn = connection(context, "/api/gateway/poll")
        conn.outputStream.use { it.write("{}".toByteArray()) }
        return readJson(conn)
    }

    fun event(context: Context, eventType: String, callId: String?, commandId: String?, payload: JSONObject = JSONObject()) {
        val conn = connection(context, "/api/gateway/event")
        val body = JSONObject()
            .put("event_type", eventType)
            .put("call_id", callId)
            .put("command_id", commandId)
            .put("payload", payload)
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        readJson(conn)
    }

    fun sofiaRespond(context: Context, customerText: String, memory: JSONObject = JSONObject()): JSONObject {
        val conn = connection(context, "/api/sofia/respond")
        val body = JSONObject()
            .put("text", customerText)
            .put("memory", memory)
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        return readJson(conn)
    }

    fun uploadRecording(context: Context, file: File, callId: String?, commandId: String?, durationMs: Long): JSONObject {
        require(file.exists() && file.isFile && file.length() > 0) { "Gravação local não encontrada" }
        val conn = connection(context, "/api/gateway/recording", "audio/mp4")
        conn.setRequestProperty("x-recording-name", URLEncoder.encode(file.name, Charsets.UTF_8.name()))
        conn.setRequestProperty("x-call-id", callId ?: "")
        conn.setRequestProperty("x-command-id", commandId ?: "")
        conn.setRequestProperty("x-duration-ms", durationMs.toString())
        conn.setFixedLengthStreamingMode(file.length())
        file.inputStream().use { input -> conn.outputStream.use { output -> input.copyTo(output, 64 * 1024) } }
        return readJson(conn)
    }

    private fun readJson(conn: HttpURLConnection): JSONObject {
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
        val text = BufferedReader(InputStreamReader(stream)).use { it.readText() }
        if (conn.responseCode !in 200..299) throw IllegalStateException("HTTP ${conn.responseCode}: $text")
        return if (text.isBlank()) JSONObject() else JSONObject(text)
    }
}
