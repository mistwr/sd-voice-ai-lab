package pt.solucoesdiferentes.sdvoicegateway

import android.accessibilityservice.AccessibilityService
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject

class SofiaTextCallBridgeService : AccessibilityService() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }

    private val allowedPackages = setOf(
        "com.samsung.android.incallui",
        "com.samsung.android.callassistant",
        "com.samsung.android.dialer",
        "com.samsung.android.app.telephonyui"
    )

    override fun onServiceConnected() {
        super.onServiceConnected()
        prefs.edit()
            .putBoolean("bridge_service_connected", true)
            .putString("diag_TEXT_CALL_BRIDGE", JSONObject()
                .put("service_connected", true)
                .put("mode", "LAB_READ_AND_FILL_ONLY")
                .put("auto_send", false)
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

        val customerCandidate = pickCustomerText(visibleTexts)
        val snapshot = JSONObject()
            .put("mode", "LAB_READ_AND_FILL_ONLY")
            .put("package", pkg)
            .put("event_type", event.eventType)
            .put("event_time", event.eventTime)
            .put("node_count", nodes.length())
            .put("editable_count", editableCandidates.size)
            .put("visible_texts", JSONArray(visibleTexts.takeLast(40)))
            .put("customer_candidate", customerCandidate ?: JSONObject.NULL)
            .put("nodes", nodes)
            .put("auto_send", false)

        var fillResult: Boolean? = null
        if (prefs.getBoolean("bridge_arm_once", false) && editableCandidates.isNotEmpty()) {
            val draft = prefs.getString(
                "bridge_test_draft",
                "Olá, sou a Sofia, assistente virtual."
            ).orEmpty().trim()

            if (draft.isNotBlank()) {
                val target = chooseEditable(editableCandidates)
                fillResult = setText(target, draft)
                snapshot.put("fill_attempted", true)
                    .put("fill_result", fillResult)
                    .put("draft_length", draft.length)
                    .put("target_view_id", target.viewIdResourceName ?: JSONObject.NULL)
                    .put("target_class", target.className?.toString() ?: JSONObject.NULL)

                if (fillResult) {
                    prefs.edit().putBoolean("bridge_arm_once", false).apply()
                    Toast.makeText(
                        this,
                        "Sofia preencheu a resposta. Não enviou automaticamente.",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }
        }

        if (fillResult == null) snapshot.put("fill_attempted", false)

        prefs.edit()
            .putString("diag_TEXT_CALL_BRIDGE", snapshot.toString())
            .putString("bridge_customer_candidate", customerCandidate ?: "")
            .putInt("bridge_editable_count", editableCandidates.size)
            .putLong("bridge_last_event_at", System.currentTimeMillis())
            .apply()
    }

    override fun onInterrupt() {
        prefs.edit().putBoolean("bridge_service_connected", false).apply()
    }

    override fun onDestroy() {
        prefs.edit().putBoolean("bridge_service_connected", false).apply()
        super.onDestroy()
    }

    private fun collectNodes(
        node: AccessibilityNodeInfo,
        out: JSONArray,
        visibleTexts: MutableList<String>,
        editables: MutableList<AccessibilityNodeInfo>,
        depth: Int
    ) {
        if (depth > 18 || out.length() >= 220) return

        val text = node.text?.toString()?.trim().orEmpty()
        val desc = node.contentDescription?.toString()?.trim().orEmpty()
        val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            node.hintText?.toString()?.trim().orEmpty()
        } else ""
        val cls = node.className?.toString().orEmpty()
        val viewId = node.viewIdResourceName.orEmpty()

        if (text.isNotBlank()) visibleTexts += text
        if (desc.isNotBlank() && desc != text) visibleTexts += desc

        val editable = node.isEditable || cls.contains("EditText", ignoreCase = true)
        if (editable) editables += AccessibilityNodeInfo.obtain(node)

        if (text.isNotBlank() || desc.isNotBlank() || hint.isNotBlank() || editable || node.isClickable) {
            out.put(
                JSONObject()
                    .put("text", text)
                    .put("desc", desc)
                    .put("hint", hint)
                    .put("class", cls)
                    .put("view_id", viewId)
                    .put("editable", editable)
                    .put("clickable", node.isClickable)
                    .put("focused", node.isFocused)
                    .put("enabled", node.isEnabled)
            )
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try {
                collectNodes(child, out, visibleTexts, editables, depth + 1)
            } finally {
                child.recycle()
            }
        }
    }

    private fun chooseEditable(candidates: List<AccessibilityNodeInfo>): AccessibilityNodeInfo {
        return candidates.firstOrNull { node ->
            val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) node.hintText?.toString().orEmpty() else ""
            val text = node.text?.toString().orEmpty()
            val desc = node.contentDescription?.toString().orEmpty()
            listOf(hint, text, desc).any {
                it.contains("Escrever resposta", true) ||
                    it.contains("Write response", true) ||
                    it.contains("resposta", true)
            }
        } ?: candidates.first()
    }

    private fun setText(node: AccessibilityNodeInfo, text: String): Boolean {
        return try {
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        } catch (_: Throwable) {
            false
        }
    }

    private fun pickCustomerText(texts: List<String>): String? {
        val ignored = setOf(
            "em linha.", "em linha", "repetir", "urgente?", "ligar-lhe mais tarde",
            "escrever resposta", "teclado", "mais", "chamada de texto"
        )
        return texts.asReversed().firstOrNull { raw ->
            val t = raw.trim()
            t.length >= 4 &&
                t.lowercase() !in ignored &&
                !t.matches(Regex("^\\d{1,2}:\\d{2}$")) &&
                !t.matches(Regex("^\\d{1,3}%$"))
        }
    }
}
