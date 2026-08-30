package pt.solucoesdiferentes.sdvoicegateway

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.io.File

class TextCallBridgeLabActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private lateinit var status: TextView
    private lateinit var primary: Button
    private lateinit var modelButton: Button
    private lateinit var accessButton: Button
    private lateinit var diagnostic: TextView

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(30), dp(20), dp(30))
            setBackgroundColor(Color.rgb(10, 13, 24))
        }
        root.addView(TextView(this).apply {
            text = "SOFIA"
            textSize = 32f
            setTextColor(Color.WHITE)
            setTypeface(typeface, Typeface.BOLD)
        })
        root.addView(TextView(this).apply {
            text = "Build 52 · Samsung Text Call automático"
            textSize = 14f
            setTextColor(Color.rgb(151, 164, 207))
            setPadding(0, dp(4), 0, dp(18))
        })
        status = TextView(this).apply {
            textSize = 17f
            setPadding(dp(16), dp(16), dp(16), dp(16))
            setBackgroundColor(Color.rgb(25, 29, 46))
        }
        root.addView(status)
        root.addView(TextView(this).apply {
            text = "Ativa a Sofia e abre o Telefone Samsung. Na Chamada de texto ela lê apenas as mensagens do cliente, responde com o Qwen local e envia pela voz Samsung."
            textSize = 13f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(14), 0, dp(14))
        })
        primary = Button(this).apply { isAllCaps = false; textSize = 18f; setPadding(dp(14), dp(14), dp(14), dp(14)) }
        root.addView(primary)
        val phone = Button(this).apply { text = "📞 Abrir Telefone Samsung"; isAllCaps = false; textSize = 17f }
        root.addView(phone)
        accessButton = Button(this).apply { text = "Permitir acessibilidade"; isAllCaps = false; visibility = View.GONE }
        root.addView(accessButton)
        modelButton = Button(this).apply { text = "🧠 Preparar Qwen local"; isAllCaps = false; visibility = View.GONE }
        root.addView(modelButton)
        val diagnosticButton = Button(this).apply { text = "Diagnóstico técnico"; isAllCaps = false }
        root.addView(diagnosticButton)
        diagnostic = TextView(this).apply {
            textSize = 11f
            setTextColor(Color.rgb(220, 225, 240))
            setTextIsSelectable(true)
            setPadding(dp(12), dp(12), dp(12), dp(12))
            setBackgroundColor(Color.rgb(18, 22, 36))
            visibility = View.GONE
        }
        root.addView(diagnostic)

        primary.setOnClickListener {
            val enabled = isBridgeEnabled()
            val model = findLocalModel()
            val running = prefs.getBoolean("bridge_ai_enabled", false) && prefs.getBoolean("bridge_auto_send", false)
            when {
                running -> prefs.edit().putBoolean("bridge_ai_enabled", false).putBoolean("bridge_auto_send", false).apply()
                !enabled -> startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                model == null -> startActivity(Intent(this, OfflineSofiaActivity::class.java))
                else -> prefs.edit()
                    .putString("offline_model_path", model.absolutePath)
                    .putBoolean("bridge_ai_enabled", true)
                    .putBoolean("bridge_auto_send", true)
                    .putBoolean("bridge_arm_once", false)
                    .putString("bridge_ai_status", "Sofia automática pronta · à espera da Chamada de texto")
                    .apply()
            }
            updateUi()
        }
        phone.setOnClickListener {
            val launch = packageManager.getLaunchIntentForPackage("com.samsung.android.dialer")
            if (launch != null) startActivity(launch) else startActivity(Intent(Intent.ACTION_DIAL))
        }
        accessButton.setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        modelButton.setOnClickListener { startActivity(Intent(this, OfflineSofiaActivity::class.java)) }
        diagnosticButton.setOnClickListener {
            diagnostic.visibility = if (diagnostic.visibility == View.VISIBLE) View.GONE else View.VISIBLE
            updateUi()
        }
        setContentView(ScrollView(this).apply { addView(root) })
        updateUi()
    }

    override fun onResume() { super.onResume(); updateUi() }

    private fun findLocalModel(): File? {
        val saved = prefs.getString("offline_model_path", "").orEmpty().takeIf { it.isNotBlank() }?.let(::File)
        if (saved?.exists() == true && saved.length() > 100_000_000L) return saved
        return File(filesDir, "models").listFiles()
            ?.filter { it.isFile && it.extension.equals("gguf", true) && it.length() > 100_000_000L }
            ?.maxByOrNull { it.length() }
    }

    private fun updateUi() {
        val access = isBridgeEnabled()
        val model = findLocalModel()
        val running = access && model != null && prefs.getBoolean("bridge_ai_enabled", false) && prefs.getBoolean("bridge_auto_send", false)
        val aiStatus = prefs.getString("bridge_ai_status", "").orEmpty()
        val customer = prefs.getString("bridge_customer_candidate", "").orEmpty()
        val reply = prefs.getString("bridge_last_ai_reply", "").orEmpty()
        status.text = buildString {
            append(if (access) "✓ Ponte Samsung ativa" else "⚠ Falta permitir acessibilidade")
            append("\n")
            append(if (model != null) "✓ Qwen local pronto" else "⚠ Falta preparar o Qwen")
            append("\n")
            append(if (running) "● SOFIA AUTOMÁTICA ATIVA" else "Sofia parada")
            if (aiStatus.isNotBlank()) append("\n\n$aiStatus")
            if (customer.isNotBlank()) append("\nCliente: $customer")
            if (reply.isNotBlank()) append("\nSofia: $reply")
        }
        status.setTextColor(if (running) Color.rgb(117, 235, 180) else Color.rgb(255, 196, 96))
        primary.text = when {
            running -> "■ PARAR SOFIA"
            !access -> "ATIVAR SOFIA · permitir acessibilidade"
            model == null -> "ATIVAR SOFIA · preparar Qwen"
            else -> "▶ ATIVAR SOFIA AUTOMÁTICA"
        }
        accessButton.visibility = if (!access) View.VISIBLE else View.GONE
        modelButton.visibility = if (model == null) View.VISIBLE else View.GONE
        diagnostic.text = prefs.getString("diag_TEXT_CALL_BRIDGE", "Sem diagnóstico de Chamada de texto ainda.")
    }

    private fun isBridgeEnabled(): Boolean {
        val expected = ComponentName(this, SofiaTextCallBridgeService::class.java).flattenToString()
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        return enabled.split(':').any { it.equals(expected, ignoreCase = true) }
    }
}
