package pt.solucoesdiferentes.sdvoicegateway

import android.accessibilityservice.AccessibilityService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
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

    private val allowedPackages = setOf(
        "com.samsung.android.incallui",
        "com.samsung.android.callassistant",
        "com.samsung.android.dialer",
        "com.samsung.android.app.telephonyui"
    )

    private val nonCustomerLabels = setOf(
        "telefone", "ligar", "agora não", "hoje", "ontem", "teclado", "recentes", "contactos",
        "chamada de texto", "chamada efetuada", "chamada recebida", "filtrar chamadas", "procurar",
        "mais opções", "repetir", "urgente?", "ligar-lhe mais tarde", "escrever resposta", "write response",
        "enviar", "send", "em linha", "em linha."
    )

    override fun onServiceConnected() {
        super.onServiceConnected()
        prefs.edit()
            .putBoolean("bridge_service_connected", true)
            .putString("bridge_ai_status", "Ponte ligada · à espera da Chamada de texto Samsung")
            .putString("diag_TEXT_CALL_BRIDGE", JSONObject()
                .put("service_connected", true)
                .put("mode", "TEXT_CALL_BRIDGE_50")
                .put("ai_enabled", prefs.getBoolean("bridge_ai_enabled", false))
                .put("auto_send", prefs.getBoolean("bridge_auto_send", false))
                .toString())
            .apply()
        Toast.makeText(this, "Sofia Text Call Bridge ativo", Toast.LENGTH_SHORT).show()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val pkg = event.packageName?.toString() ?: return
        if (pkg !in allowedPackages) return

        val root = rootInActiveWindow ?: return
        val nodes = JSONArray()
        val visibleTexts = mutableListOf<String>()
        val editableCandidates = mutableListOf<AccessibilityNodeInfo>()
        collectNodes(root, nodes, visibleTexts, editableCandidates, 0)

        val textCallSurface = isTextCallSurface(pkg, visibleTexts, editableCandidates)
        val customerCandidate = if (textCallSurface) pickCustomerText(visibleTexts) else null

        val snapshot = JSONObject()
            .put("mode", "TEXT_CALL_BRIDGE_50")
            .put("package", pkg)
            .put("event_type", event.eventType)
            .put("event_time", event.eventTime)
            .put("text_call_surface", textCallSurface)
            .put("node_count", nodes.length())
            .put("editable_count", editableCandidates.size)
            .put("visible_texts", JSONArray(visibleTexts.takeLast(50)))
            .put("customer_candidate", customerCandidate ?: JSONObject.NULL)
            .put("nodes", nodes)
            .put("ai_enabled", prefs.getBoolean("bridge_ai_enabled", false))
            .put("ai_busy", aiBusy)
            .put("auto_send", prefs.getBoolean("bridge_auto_send", false))

        prefs.edit().putString("diag_TEXT_CALL_BRIDGE_LAST_EVENT", snapshot.toString()).apply()

        if (!textCallSurface) {
            // Do not destroy the useful live-call diagnostic when Samsung returns to Recents.
            prefs.edit()
                .putString("bridge_last_non_textcall_package", pkg)
                .putLong("bridge_last_event_at", System.currentTimeMillis())
                .apply()
            return
        }

        var fillResult: Boolean? = null
        if (prefs.getBoolean("bridge_arm_once", false) && editableCandidates.isNotEmpty()) {
            val draft = prefs.getString("bridge_test_draft", "Olá, sou a Sofia, assistente virtual.").orEmpty().trim()
            if (draft.isNotBlank()) {
                val target = chooseEditable(editableCandidates)
                fillResult = setText(target, draft)
                snapshot.put("fill_attempted", true)
                    .put("fill_result", fillResult)
                    .put("draft_length", draft.length)
                    .put("target_view_id", target.viewIdResourceName ?: JSONObject.NULL)
                    .put("target_class", target.className?.toString() ?: JSONObject.NULL)
                if (fillResult) prefs.edit().putBoolean("bridge_arm_once", false).apply()
            }
        }
        if (fillResult == null) snapshot.put("fill_attempted", false)

        prefs.edit()
            .putString("diag_TEXT_CALL_BRIDGE", snapshot.toString())
            .putString("bridge_customer_candidate", customerCandidate ?: "")
            .putInt("bridge_editable_count", editableCandidates.size)
            .putLong("bridge_last_textcall_event_at", System.currentTimeMillis())
            .apply()

        if (prefs.getBoolean("bridge_ai_enabled", false) && customerCandidate != null && editableCandidates.isNotEmpty()) {
            maybeGenerateOfflineReply(customerCandidate)
        }
    }

    private fun isTextCallSurface(
        pkg: String,
        texts: List<String>,
        editables: List<AccessibilityNodeInfo>
    ): Boolean {
        if (editables.isEmpty()) return false
        val joined = texts.joinToString(" | ").lowercase()
        val hasReplyUi = joined.contains("escrever resposta") || joined.contains("write response") ||
            editables.any { node ->
                val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) node.hintText?.toString().orEmpty() else ""
                val desc = node.contentDescription?.toString().orEmpty()
                hint.contains("resposta", true) || hint.contains("response", true) ||
                    desc.contains("resposta", true) || desc.contains("response", true)
            }
        val privilegedCallUi = pkg == "com.samsung.android.incallui" ||
            pkg == "com.samsung.android.callassistant" || pkg == "com.samsung.android.app.telephonyui"
        return hasReplyUi || (privilegedCallUi && joined.contains("chamada de texto"))
    }

    private fun maybeGenerateOfflineReply(customerText: String) {
        val clean = customerText.trim()
        if (clean.length < 4 || aiBusy) return
        val now = System.currentTimeMillis()
        if (clean.equals(lastCustomerText, true) && now - lastCustomerAt < 20_000L) return
        val lastReply = prefs.getString("bridge_last_ai_reply", "").orEmpty().trim()
        if (lastReply.isNotBlank() && clean.equals(lastReply, true)) return

        lastCustomerText = clean
        lastCustomerAt = now
        aiBusy = true
        prefs.edit()
            .putString("bridge_ai_status", "A pensar: $clean")
            .putString("bridge_last_customer_for_ai", clean)
            .apply()

        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    ensureLocalModelReady()
                    SofiaLocalEngine.respond(applicationContext, clean, memory)
                }
                val reply = result.optString("reply", "").trim()
                result.optJSONObject("memory")?.let { memory = it }
                require(reply.isNotBlank()) { "Resposta local vazia" }

                val fill = fillFreshEditable(reply)
                val autoSend = prefs.getBoolean("bridge_auto_send", false)
                prefs.edit()
                    .putString("bridge_last_ai_reply", reply)
                    .putString("bridge_ai_status", if (fill) "Resposta pronta na caixa Samsung" else "Resposta gerada; campo Samsung não encontrado")
                    .putBoolean("bridge_last_ai_fill_result", fill)
                    .putString("bridge_last_ai_outcome", result.optString("outcome", "CONTINUE"))
                    .apply()

                if (fill && autoSend) {
                    prefs.edit().putString("bridge_ai_status", "Resposta preenchida; a procurar botão Enviar…").apply()
                    mainHandler.postDelayed({ guardedAutoSend(reply) }, 450L)
                } else if (fill) {
                    Toast.makeText(this@SofiaTextCallBridgeService, "Sofia respondeu offline.", Toast.LENGTH_SHORT).show()
                }
            } catch (t: Throwable) {
                prefs.edit().putString("bridge_ai_status", "Erro Sofia offline: ${t.message ?: t.javaClass.simpleName}").apply()
            } finally {
                aiBusy = false
            }
        }
    }

    private fun ensureLocalModelReady() {
        if (SofiaLocalEngine.isReady(applicationContext)) return

        val saved = prefs.getString("offline_model_path", "").orEmpty().trim()
        var file = saved.takeIf { it.isNotBlank() }?.let(::File)
            ?.takeIf { it.exists() && it.length() > 100_000_000L }

        if (file == null) {
            val models = File(filesDir, "models")
            file = models.listFiles()
                ?.filter { it.isFile && it.extension.equals("gguf", true) && it.length() > 100_000_000L }
                ?.maxByOrNull { it.length() }
            if (file != null) prefs.edit().putString("offline_model_path", file.absolutePath).apply()
        }

        requireNotNull(file) { "GGUF não encontrado. Abre ‘Preparar Qwen local’ e descarrega o modelo." }
        kotlinx.coroutines.runBlocking { SofiaLocalEngine.load(applicationContext, file.absolutePath) }
    }

    private fun fillFreshEditable(reply: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val candidates = mutableListOf<AccessibilityNodeInfo>()
        collectEditableNodes(root, candidates, 0)
        if (candidates.isEmpty()) return false
        return setText(chooseEditable(candidates), reply)
    }

    private fun guardedAutoSend(expectedReply: String) {
        if (!prefs.getBoolean("bridge_auto_send", false)) return
        val root = rootInActiveWindow ?: return setAiStatus("Auto-envio: janela Samsung não encontrada")
        val editable = findReplyEditable(root)
        val current = editable?.text?.toString()?.trim().orEmpty()
        if (current != expectedReply.trim()) return setAiStatus("Auto-envio bloqueado: texto da caixa mudou")
        val send = findSendNode(root) ?: return setAiStatus("Auto-envio bloqueado: botão Enviar não identificado")
        val clicked = try { send.performAction(AccessibilityNodeInfo.ACTION_CLICK) } catch (_: Throwable) { false }
        prefs.edit()
            .putBoolean("bridge_last_auto_send_result", clicked)
            .putLong("bridge_last_auto_send_at", System.currentTimeMillis())
            .putString("bridge_ai_status", if (clicked) "✓ Sofia enviou a resposta automaticamente" else "Auto-envio falhou ao clicar")
            .apply()
        if (clicked) Toast.makeText(this, "Sofia enviou automaticamente ✓", Toast.LENGTH_SHORT).show()
    }

    private fun setAiStatus(value: String) {
        prefs.edit().putString("bridge_ai_status", value).apply()
    }

    private fun findReplyEditable(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val candidates = mutableListOf<AccessibilityNodeInfo>()
        collectEditableNodes(root, candidates, 0)
        return candidates.firstOrNull { node ->
            val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) node.hintText?.toString().orEmpty() else ""
            val desc = node.contentDescription?.toString().orEmpty()
            hint.contains("resposta", true) || hint.contains("response", true) ||
                desc.contains("resposta", true) || desc.contains("response", true)
        } ?: candidates.firstOrNull()
    }

    private fun findSendNode(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val matches = mutableListOf<AccessibilityNodeInfo>()
        fun walk(node: AccessibilityNodeInfo, depth: Int) {
            if (depth > 18) return
            val text = node.text?.toString()?.trim().orEmpty()
            val desc = node.contentDescription?.toString()?.trim().orEmpty()
            val id = node.viewIdResourceName.orEmpty()
            val labelMatch = text.equals("Enviar", true) || text.equals("Send", true) ||
                desc.equals("Enviar", true) || desc.equals("Send", true) ||
                desc.contains("Enviar", true) || desc.contains("Send", true)
            val idMatch = id.contains("send", true) || id.contains("submit", true)
            if (node.isClickable && node.isEnabled && (labelMatch || idMatch)) matches += AccessibilityNodeInfo.obtain(node)
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                try { walk(child, depth + 1) } finally { child.recycle() }
            }
        }
        walk(root, 0)
        return if (matches.size == 1) matches.first() else null
    }

    private fun collectEditableNodes(node: AccessibilityNodeInfo, out: MutableList<AccessibilityNodeInfo>, depth: Int) {
        if (depth > 18) return
        val cls = node.className?.toString().orEmpty()
        if (node.isEditable || cls.contains("EditText", true)) out += AccessibilityNodeInfo.obtain(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try { collectEditableNodes(child, out, depth + 1) } finally { child.recycle() }
        }
    }

    private fun collectNodes(node: AccessibilityNodeInfo, out: JSONArray, texts: MutableList<String>, editables: MutableList<AccessibilityNodeInfo>, depth: Int) {
        if (depth > 18 || out.length() >= 260) return
        val text = node.text?.toString()?.trim().orEmpty()
        val desc = node.contentDescription?.toString()?.trim().orEmpty()
        val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) node.hintText?.toString()?.trim().orEmpty() else ""
        val cls = node.className?.toString().orEmpty()
        val viewId = node.viewIdResourceName.orEmpty()
        if (text.isNotBlank()) texts += text
        if (desc.isNotBlank() && desc != text) texts += desc
        val editable = node.isEditable || cls.contains("EditText", true)
        if (editable) editables += AccessibilityNodeInfo.obtain(node)
        if (text.isNotBlank() || desc.isNotBlank() || hint.isNotBlank() || editable || node.isClickable) {
            out.put(JSONObject().put("text", text).put("desc", desc).put("hint", hint).put("class", cls)
                .put("view_id", viewId).put("editable", editable).put("clickable", node.isClickable)
                .put("focused", node.isFocused).put("enabled", node.isEnabled))
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try { collectNodes(child, out, texts, editables, depth + 1) } finally { child.recycle() }
        }
    }

    private fun chooseEditable(candidates: List<AccessibilityNodeInfo>): AccessibilityNodeInfo =
        candidates.firstOrNull { node ->
            val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) node.hintText?.toString().orEmpty() else ""
            val text = node.text?.toString().orEmpty()
            val desc = node.contentDescription?.toString().orEmpty()
            listOf(hint, text, desc).any { it.contains("resposta", true) || it.contains("response", true) }
        } ?: candidates.first()

    private fun setText(node: AccessibilityNodeInfo, value: String): Boolean = try {
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value) }
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    } catch (_: Throwable) { false }

    private fun pickCustomerText(texts: List<String>): String? = texts.asReversed().firstOrNull { raw ->
        val t = raw.trim()
        val l = t.lowercase()
        t.length >= 4 && l !in nonCustomerLabels &&
            !l.contains("tecla") && !l.matches(Regex("^\\d{1,2}:\\d{2}$")) &&
            !l.matches(Regex("^\\d{1,3}%$")) && !l.matches(Regex("^\\(?\\d+\\)?$"))
    }

    override fun onInterrupt() { prefs.edit().putBoolean("bridge_service_connected", false).apply() }
    override fun onDestroy() {
        prefs.edit().putBoolean("bridge_service_connected", false).apply()
        scope.cancel()
        super.onDestroy()
    }
}
