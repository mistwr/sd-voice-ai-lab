package pt.solucoesdiferentes.sdvoicegateway

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.provider.Settings
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import java.io.File

class TextCallBridgeLabActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private lateinit var state: TextView
    private lateinit var diagnostic: TextView
    private lateinit var draft: EditText
    private lateinit var aiSwitch: Switch
    private lateinit var autoSendSwitch: Switch

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(28), dp(20), dp(28))
            setBackgroundColor(Color.rgb(10, 13, 24))
        }

        root.addView(TextView(this).apply {
            text = "SOFIA · TEXT CALL BRIDGE"
            textSize = 25f
            setTextColor(Color.WHITE)
            setTypeface(typeface, Typeface.BOLD)
        })
        root.addView(TextView(this).apply {
            text = "Build 50 · Samsung Text Call + Qwen + auto-envio"
            textSize = 13f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(4), 0, dp(12))
        })

        val mainPanel = Button(this).apply { text = "← Abrir painel principal da Sofia"; isAllCaps = false }
        root.addView(mainPanel)

        state = TextView(this).apply {
            textSize = 15f
            setTextColor(Color.WHITE)
            setPadding(dp(14), dp(14), dp(14), dp(14))
            setBackgroundColor(Color.rgb(25, 29, 46))
        }
        root.addView(state)

        val access = Button(this).apply { text = "1 · Ativar ponte de acessibilidade"; isAllCaps = false }
        root.addView(access)

        val prepareQwen = Button(this).apply { text = "🧠 Preparar / carregar Qwen local"; isAllCaps = false }
        root.addView(prepareQwen)

        root.addView(TextView(this).apply {
            text = "Frase de teste a preencher na caixa ‘Escrever resposta’"
            textSize = 13f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(14), 0, dp(6))
        })

        draft = EditText(this).apply {
            setText(prefs.getString("bridge_test_draft", "Olá, sou a Sofia, assistente virtual."))
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            setBackgroundColor(Color.rgb(25, 29, 46))
            setPadding(dp(14), dp(14), dp(14), dp(14))
            minLines = 2
        }
        root.addView(draft, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val arm = Button(this).apply { text = "2 · Armar preenchimento único"; isAllCaps = false }
        root.addView(arm)

        aiSwitch = Switch(this).apply {
            text = "3 · Sofia offline automática"
            setTextColor(Color.WHITE)
            isChecked = prefs.getBoolean("bridge_ai_enabled", false)
            setPadding(0, dp(12), 0, dp(6))
        }
        root.addView(aiSwitch)

        autoSendSwitch = Switch(this).apply {
            text = "4 · ENVIAR automaticamente pela voz Samsung"
            setTextColor(Color.WHITE)
            isChecked = prefs.getBoolean("bridge_auto_send", false)
            setPadding(0, dp(6), 0, dp(8))
        }
        root.addView(autoSendSwitch)

        root.addView(TextView(this).apply {
            text = "Build 50 só aceita texto quando deteta a interface real da Chamada de texto. O ecrã Recentes/Contactos já não substitui o diagnóstico útil da chamada. O auto-envio continua protegido: só envia se a caixa contiver exatamente a resposta da Sofia e existir um único botão Enviar/Send identificável."
            textSize = 12f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(8), 0, dp(12))
        })

        val refresh = Button(this).apply { text = "Atualizar diagnóstico da ponte"; isAllCaps = false }
        root.addView(refresh)

        diagnostic = TextView(this).apply {
            textSize = 12f
            setTextColor(Color.rgb(230, 235, 250))
            setTextIsSelectable(true)
            setPadding(dp(12), dp(12), dp(12), dp(12))
            setBackgroundColor(Color.rgb(18, 22, 36))
        }
        root.addView(diagnostic)

        mainPanel.setOnClickListener { startActivity(Intent(this, MainActivity::class.java)) }
        access.setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        prepareQwen.setOnClickListener { startActivity(Intent(this, OfflineSofiaActivity::class.java)) }

        arm.setOnClickListener {
            val phrase = draft.text.toString().trim()
            if (phrase.isBlank()) {
                Toast.makeText(this, "Escreve uma frase de teste", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit().putString("bridge_test_draft", phrase).putBoolean("bridge_arm_once", true).apply()
            Toast.makeText(this, "Armado. Abre Samsung Chamada de texto.", Toast.LENGTH_SHORT).show()
            updateUi()
        }

        aiSwitch.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean("bridge_ai_enabled", checked).apply()
            if (!checked && autoSendSwitch.isChecked) autoSendSwitch.isChecked = false
            updateUi()
        }

        autoSendSwitch.setOnCheckedChangeListener { _, checked ->
            if (checked && !aiSwitch.isChecked) aiSwitch.isChecked = true
            prefs.edit().putBoolean("bridge_auto_send", checked).apply()
            updateUi()
        }

        refresh.setOnClickListener { updateUi() }
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
        val enabled = isBridgeEnabled()
        val armed = prefs.getBoolean("bridge_arm_once", false)
        val aiEnabled = prefs.getBoolean("bridge_ai_enabled", false)
        val autoSend = prefs.getBoolean("bridge_auto_send", false)
        val editableCount = prefs.getInt("bridge_editable_count", 0)
        val candidate = prefs.getString("bridge_customer_candidate", "").orEmpty()
        val aiStatus = prefs.getString("bridge_ai_status", "").orEmpty()
        val reply = prefs.getString("bridge_last_ai_reply", "").orEmpty()
        val model = findLocalModel()

        if (::aiSwitch.isInitialized && aiSwitch.isChecked != aiEnabled) aiSwitch.isChecked = aiEnabled
        if (::autoSendSwitch.isInitialized && autoSendSwitch.isChecked != autoSend) autoSendSwitch.isChecked = autoSend

        state.text = buildString {
            append(if (enabled) "✓ Ponte de acessibilidade ATIVA" else "⚠ Ponte de acessibilidade DESLIGADA")
            append("\n")
            append(if (model != null) "✓ Qwen/GGUF encontrado: ${model.name}" else "⚠ Qwen/GGUF não encontrado")
            append("\n")
            append(if (armed) "● Preenchimento único ARMADO" else "Preenchimento não armado")
            append("\n")
            append(if (aiEnabled) "🤖 Qwen → Text Call ATIVO" else "Qwen → Text Call desligado")
            append("\n")
            append(if (autoSend) "📤 AUTO-ENVIO ATIVO" else "Envio manual")
            append("\nCampos editáveis da última Chamada de texto: $editableCount")
            if (candidate.isNotBlank()) append("\nTexto candidato do cliente: $candidate")
            if (aiStatus.isNotBlank()) append("\nEstado IA: $aiStatus")
            if (reply.isNotBlank()) append("\nÚltima resposta Sofia: $reply")
        }
        state.setTextColor(if (enabled && model != null) Color.rgb(117, 235, 180) else Color.rgb(255, 196, 96))
        diagnostic.text = prefs.getString("diag_TEXT_CALL_BRIDGE", "Sem eventos válidos da interface Chamada de texto Samsung ainda.")
    }

    private fun isBridgeEnabled(): Boolean {
        val expected = ComponentName(this, SofiaTextCallBridgeService::class.java).flattenToString()
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        return enabled.split(':').any { it.equals(expected, ignoreCase = true) }
    }
}
