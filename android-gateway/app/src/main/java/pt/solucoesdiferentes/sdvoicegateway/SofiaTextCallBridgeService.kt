package pt.solucoesdiferentes.sdvoicegateway

import android.accessibilityservice.AccessibilityService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

class SofiaTextCallBridgeService : AccessibilityService() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile private var aiBusy = false
    @Volatile private var lastCustomerText = ""
    @Volatile private var lastCustomerAt = 0L
    private var memory = JSONObject()
    private var pendingCustomer = ""
    private val pendingRunnable = Runnable {
        val text = pendingCustomer.trim()
        if (text.isNotBlank() && prefs.getBoolean("bridge_ai_enabled", false)) maybeGenerateOfflineReply(text)
    }

    private val allowedPackages = setOf(
        "com.samsung.android.incallui",
        "com.samsung.android.callassistant",
        "com.samsung.android.dialer",
        "com.samsung.android.app.telephonyui"
    )

    private val remoteMessageId = "com.samsung.android.incallui:id/remote_message"
    private val replyEditId = "com.samsung.android.incallui:id/text_call_message_edit_text"

    override fun onServiceConnected() {
        super.onServiceConnected()
        prefs.edit()
            .putBoolean("bridge_service_connected", true)
            .putString("bridge_ai_status", "Ponte ligada · à espera da Chamada de texto Samsung")
            .putString("diag_TEXT_CALL_BRIDGE", JSONObject()
                .put("service_connected", true)
                .put("mode", "TEXT_CALL_BRIDGE_52")
                .put("ai_enabled", prefs.getBoolean("bridge_ai_enabled", false))
                .put("auto_send", prefs.getBoolean("bridge_auto_send", false))
                .toString())
            .apply()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString() ?: return
        if (pkg !in allowedPackages) return

        val root = rootInActiveWindow ?: return
        val nodes = JSONArray()
        val visibleTexts = mutableListOf<String>()
        val editables = mutableListOf<AccessibilityNodeInfo>()
        collectNodes(root, nodes, visibleTexts, editables, 0)

        val replyField = findNodeByViewIdAcrossWindows(replyEditId)
        val remoteMessages = findTextsByViewIdAcrossWindows(remoteMessageId)
        val customerCandidate = remoteMessages.lastOrNull()?.trim()?.takeIf { it.length >= 2 }
        val textCallSurface = pkg == "com.samsung.android.incallui" && replyField != null

        val snapshot = JSONObject()
            .put("mode", "TEXT_CALL_BRIDGE_52")
            .put("package", pkg)
            .put("event_type", event.eventType)
            .put("event_time", event.eventTime)
            .put("text_call_surface", textCallSurface)
            .put("node_count", nodes.length())
            .put("editable_count", if (replyField != null) 1 else editables.size)
            .put("remote_messages", JSONArray(remoteMessages.takeLast(12)))
            .put("customer_candidate", customerCandidate ?: JSONObject.NULL)
            .put("visible_texts", JSONArray(visibleTexts.takeLast(50)))
            .put("nodes", nodes)
            .put("ai_enabled", prefs.getBoolean("bridge_ai_enabled", false))
            .put("ai_busy", aiBusy)
            .put("auto_send", prefs.getBoolean("bridge_auto_send", false))

        prefs.edit().putString("diag_TEXT_CALL_BRIDGE_LAST_EVENT", snapshot.toString()).apply()
        if (!textCallSurface) return

        prefs.edit()
            .putString("diag_TEXT_CALL_BRIDGE", snapshot.toString())
            .putString("bridge_customer_candidate", customerCandidate ?: "")
            .putInt("bridge_editable_count", 1)
            .putLong("bridge_last_textcall_event_at", System.currentTimeMillis())
            .apply()

        if (prefs.getBoolean("bridge_ai_enabled", false) && !customerCandidate.isNullOrBlank()) {
            scheduleStableCustomer(customerCandidate)
        }
    }

    private fun scheduleStableCustomer(text: String) {
        val clean = text.trim()
        if (clean == pendingCustomer) return
        pendingCustomer = clean
        mainHandler.removeCallbacks(pendingRunnable)
        mainHandler.postDelayed(pendingRunnable, 900L)
    }

    private fun maybeGenerateOfflineReply(customerText: String) {
        val clean = customerText.trim()
        if (clean.length < 2 || aiBusy) return
        val now = System.currentTimeMillis()
        if (clean.equals(lastCustomerText, true) && now - lastCustomerAt < 25_000L) return
        val lastReply = prefs.getString("bridge_last_ai_reply", "").orEmpty().trim()
        if (lastReply.isNotBlank() && clean.equals(lastReply, true)) return

        lastCustomerText = clean
        lastCustomerAt = now
        aiBusy = true
        prefs.edit()
            .putString("bridge_ai_status", "Cliente ouvido · Sofia a pensar…")
            .putString("bridge_last_customer_for_ai", clean)
            .apply()

        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    ensureLocalModelReady()
                    SofiaLocalEngine.respond(applicationContext, clean, memory)
                }
                var reply = result.optString("reply", "").trim()
                reply = sanitizeReply(reply)
                result.optJSONObject("memory")?.let { memory = it }
                require(reply.isNotBlank()) { "Resposta local vazia" }

                val fill = fillReplyWithRetries(reply)
                prefs.edit()
                    .putString("bridge_last_ai_reply", reply)
                    .putString("bridge_ai_status", if (fill) "Resposta escrita no Samsung" else "Resposta gerada; campo Samsung não encontrado")
                    .putBoolean("bridge_last_ai_fill_result", fill)
                    .putString("bridge_last_ai_outcome", result.optString("outcome", "CONTINUE"))
                    .apply()

                if (fill && prefs.getBoolean("bridge_auto_send", false)) {
                    delay(500L)
                    guardedAutoSend(reply)
                }
            } catch (t: Throwable) {
                prefs.edit().putString("bridge_ai_status", "Erro Sofia: ${t.message ?: t.javaClass.simpleName}").apply()
            } finally {
                aiBusy = false
            }
        }
    }

    private fun sanitizeReply(raw: String): String {
        var text = raw.replace(Regex("(?is)<think>.*?</think>"), "").trim()
        if (text.contains("<think>", true)) text = text.substringBefore("<think>").trim()
        return text.replace(Regex("(?i)^sofia\\s*:\\s*"), "").trim().take(360)
    }

    private fun ensureLocalModelReady() {
        if (SofiaLocalEngine.isReady(applicationContext)) return
        val saved = prefs.getString("offline_model_path", "").orEmpty().trim()
        var file = saved.takeIf { it.isNotBlank() }?.let(::File)
            ?.takeIf { it.exists() && it.length() > 100_000_000L }
        if (file == null) {
            file = File(filesDir, "models").listFiles()
                ?.filter { it.isFile && it.extension.equals("gguf", true) && it.length() > 100_000_000L }
                ?.maxByOrNull { it.length() }
            if (file != null) prefs.edit().putString("offline_model_path", file.absolutePath).apply()
        }
        requireNotNull(file) { "GGUF não encontrado" }
        kotlinx.coroutines.runBlocking { SofiaLocalEngine.load(applicationContext, file.absolutePath) }
    }

    private suspend fun fillReplyWithRetries(reply: String): Boolean {
        repeat(6) {
            val field = findNodeByViewIdAcrossWindows(replyEditId)
            if (field != null && setText(field, reply)) return true
            delay(180L)
        }
        return false
    }

    private fun guardedAutoSend(expectedReply: String) {
        if (!prefs.getBoolean("bridge_auto_send", false)) return
        val field = findNodeByViewIdAcrossWindows(replyEditId)
        val current = field?.text?.toString()?.trim().orEmpty()
        if (current != expectedReply.trim()) {
            setAiStatus("Auto-envio bloqueado: texto da caixa mudou")
            return
        }

        val send = findSingleSendNodeAcrossWindows()
        if (send != null) {
            val clicked = try { send.performAction(AccessibilityNodeInfo.ACTION_CLICK) } catch (_: Throwable) { false }
            recordSend(clicked, "button")
            if (clicked) return
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && field != null) {
            val ime = try {
                field.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
            } catch (_: Throwable) { false }
            recordSend(ime, "ime_enter")
            if (ime) return
        }
        setAiStatus("Auto-envio bloqueado: controlo Enviar não identificado")
    }

    private fun recordSend(ok: Boolean, method: String) {
        prefs.edit()
            .putBoolean("bridge_last_auto_send_result", ok)
            .putString("bridge_last_auto_send_method", method)
            .putLong("bridge_last_auto_send_at", System.currentTimeMillis())
            .putString("bridge_ai_status", if (ok) "✓ Sofia respondeu e enviou automaticamente" else "Auto-envio falhou")
            .apply()
    }

    private fun setAiStatus(value: String) {
        prefs.edit().putString("bridge_ai_status", value).apply()
    }

    private fun roots(): List<AccessibilityNodeInfo> {
        val result = mutableListOf<AccessibilityNodeInfo>()
        windows?.forEach { w -> w.root?.let { result += it } }
        rootInActiveWindow?.let { active -> if (result.none { it == active }) result += active }
        return result
    }

    private fun findNodeByViewIdAcrossWindows(viewId: String): AccessibilityNodeInfo? {
        roots().forEach { root ->
            try { root.findAccessibilityNodeInfosByViewId(viewId)?.firstOrNull()?.let { return it } } catch (_: Throwable) {}
        }
        return null
    }

    private fun findTextsByViewIdAcrossWindows(viewId: String): List<String> {
        val out = mutableListOf<String>()
        roots().forEach { root ->
            try {
                root.findAccessibilityNodeInfosByViewId(viewId)?.forEach { n ->
                    val t = n.text?.toString()?.trim().orEmpty()
                    if (t.isNotBlank()) out += t
                }
            } catch (_: Throwable) {}
        }
        return out.distinct()
    }

    private fun findSingleSendNodeAcrossWindows(): AccessibilityNodeInfo? {
        val matches = mutableListOf<AccessibilityNodeInfo>()
        fun walk(node: AccessibilityNodeInfo, depth: Int) {
            if (depth > 18) return
            val text = node.text?.toString()?.trim().orEmpty()
            val desc = node.contentDescription?.toString()?.trim().orEmpty()
            val id = node.viewIdResourceName.orEmpty()
            val label = text.equals("Enviar", true) || text.equals("Send", true) ||
                desc.equals("Enviar", true) || desc.equals("Send", true) ||
                desc.contains("Enviar", true) || desc.contains("Send", true)
            val idMatch = id.contains("send", true) || id.contains("submit", true)
            if (node.isClickable && node.isEnabled && (label || idMatch)) matches += AccessibilityNodeInfo.obtain(node)
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                try { walk(child, depth + 1) } finally { child.recycle() }
            }
        }
        roots().forEach { walk(it, 0) }
        return matches.distinctBy { it.viewIdResourceName + "|" + it.text + "|" + it.contentDescription }.singleOrNull()
    }

    private fun setText(node: AccessibilityNodeInfo, value: String): Boolean = try {
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value) }
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    } catch (_: Throwable) { false }

    private fun collectNodes(
        node: AccessibilityNodeInfo,
        out: JSONArray,
        texts: MutableList<String>,
        editables: MutableList<AccessibilityNodeInfo>,
        depth: Int
    ) {
        if (depth > 18 || out.length() >= 280) return
        val text = node.text?.toString()?.trim().orEmpty()
        val desc = node.contentDescription?.toString()?.trim().orEmpty()
        val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) node.hintText?.toString()?.trim().orEmpty() else ""
        val cls = node.className?.toString().orEmpty()
        val id = node.viewIdResourceName.orEmpty()
        if (text.isNotBlank()) texts += text
        if (desc.isNotBlank() && desc != text) texts += desc
        val editable = node.isEditable || cls.contains("EditText", true)
        if (editable) editables += AccessibilityNodeInfo.obtain(node)
        if (text.isNotBlank() || desc.isNotBlank() || hint.isNotBlank() || editable || node.isClickable) {
            out.put(JSONObject()
                .put("text", text).put("desc", desc).put("hint", hint).put("class", cls)
                .put("view_id", id).put("editable", editable).put("clickable", node.isClickable)
                .put("focused", node.isFocused).put("enabled", node.isEnabled))
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try { collectNodes(child, out, texts, editables, depth + 1) } finally { child.recycle() }
        }
    }

    override fun onInterrupt() {
        prefs.edit().putBoolean("bridge_service_connected", false).apply()
    }

    override fun onDestroy() {
        mainHandler.removeCallbacks(pendingRunnable)
        prefs.edit().putBoolean("bridge_service_connected", false).apply()
        scope.cancel()
        super.onDestroy()
    }
}
