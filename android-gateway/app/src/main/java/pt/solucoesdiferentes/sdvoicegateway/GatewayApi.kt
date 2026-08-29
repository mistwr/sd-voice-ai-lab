package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

object GatewayApi {
    private fun connection(context: Context, path: String): HttpURLConnection {
        val prefs = context.getSharedPreferences("gateway", Context.MODE_PRIVATE)
        val base = prefs.getString("api_url", "")?.trimEnd('/') ?: ""
        require(base.startsWith("https://") || base.startsWith("http://")) { "API URL inválido" }
        return (URL(base + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 10_000
            readTimeout = 15_000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
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

    private fun readJson(conn: HttpURLConnection): JSONObject {
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
        val text = BufferedReader(InputStreamReader(stream)).use { it.readText() }
        if (conn.responseCode !in 200..299) throw IllegalStateException("HTTP ${conn.responseCode}: $text")
        return if (text.isBlank()) JSONObject() else JSONObject(text)
    }
}
