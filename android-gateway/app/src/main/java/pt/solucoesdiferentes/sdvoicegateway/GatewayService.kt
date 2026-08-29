package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

class GatewayService : Service() {
    private val executor = Executors.newSingleThreadScheduledExecutor()
    private var loop: ScheduledFuture<*>? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startForeground(1101, notification("Gateway online"))
        loop = executor.scheduleWithFixedDelay({ pollOnce() }, 0, 4, TimeUnit.SECONDS)
    }

    override fun onDestroy() {
        loop?.cancel(true)
        executor.shutdownNow()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun pollOnce() {
        try {
            val response = GatewayApi.poll(this)
            val command = response.optJSONObject("command") ?: return
            val commandId = command.getString("id")
            val callId = command.optString("call_id").ifBlank { null }
            when (command.getString("command_type")) {
                "MAKE_CALL" -> makeCall(commandId, callId, command.getJSONObject("payload"))
                "HANGUP" -> SdInCallService.hangupCurrentCall(this, callId)
                "STOP_CALLS" -> Unit
                else -> GatewayApi.event(this, "FAILED", callId, commandId, JSONObject().put("error", "Comando desconhecido"))
            }
        } catch (_: Throwable) {
            // Keep the service alive; the next heartbeat/poll retries automatically.
        }
    }

    private fun makeCall(commandId: String, callId: String?, payload: JSONObject) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED) {
            GatewayApi.event(this, "FAILED", callId, commandId, JSONObject().put("error", "CALL_PHONE não concedida"))
            return
        }

        val phone = payload.getString("phone_number")
        SdInCallService.pendingCallId = callId
        SdInCallService.pendingCommandId = commandId
        GatewayApi.event(this, "DIALING", callId, commandId, JSONObject().put("phone_number", phone))

        val intent = Intent(Intent.ACTION_CALL, Uri.parse("tel:$phone")).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(intent)
    }

    private fun createChannel() {
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(NotificationChannel("gateway", "SD Voice Gateway", NotificationManager.IMPORTANCE_LOW))
    }

    private fun notification(text: String): Notification =
        NotificationCompat.Builder(this, "gateway")
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle("SD Voice AI")
            .setContentText(text)
            .setOngoing(true)
            .build()
}
