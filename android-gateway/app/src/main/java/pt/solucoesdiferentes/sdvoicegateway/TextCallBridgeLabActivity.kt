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
import android.widget.TextView
import android.widget.Toast

class TextCallBridgeLabActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private lateinit var state: TextView
    private lateinit var diagnostic: TextView
    private lateinit var draft: EditText

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
            text = "Build 45 · Samsung RX/TX no-root · laboratório"
            textSize = 13f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(4), 0, dp(16))
        })

        state = TextView(this).apply {
            textSize = 15f
            setTextColor(Color.WHITE)
            setPadding(dp(14), dp(14), dp(14), dp(14))
            setBackgroundColor(Color.rgb(25, 29, 46))
        }
        root.addView(state)

        val access = Button(this).apply {
            text = "1 · Ativar ponte de acessibilidade"
            isAllCaps = false
        }
        root.addView(access)

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

        val arm = Button(this).apply {
            text = "2 · Armar preenchimento único"
            isAllCaps = false
        }
        root.addView(arm)

        root.addView(TextView(this).apply {
            text = "Este Build 45 NÃO carrega no botão de enviar. Só tenta ler a transcrição Samsung e preencher a caixa de resposta. Depois confirmamos manualmente antes de ligar o autopilot."
            textSize = 12f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(10), 0, dp(12))
        })

        val refresh = Button(this).apply {
            text = "Atualizar diagnóstico da ponte"
            isAllCaps = false
        }
        root.addView(refresh)

        diagnostic = TextView(this).apply {
            textSize = 12f
            setTextColor(Color.rgb(230, 235, 250))
            setTextIsSelectable(true)
            setPadding(dp(12), dp(12), dp(12), dp(12))
            setBackgroundColor(Color.rgb(18, 22, 36))
        }
        root.addView(diagnostic)

        access.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        arm.setOnClickListener {
            val phrase = draft.text.toString().trim()
            if (phrase.isBlank()) {
                Toast.makeText(this, "Escreve uma frase de teste", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit()
                .putString("bridge_test_draft", phrase)
                .putBoolean("bridge_arm_once", true)
                .apply()
            Toast.makeText(
                this,
                "Armado. Faz a chamada Samsung, abre Chamada de texto e espera a caixa de resposta aparecer.",
                Toast.LENGTH_LONG
            ).show()
            updateUi()
        }

        refresh.setOnClickListener { updateUi() }

        setContentView(ScrollView(this).apply { addView(root) })
        updateUi()
    }

    override fun onResume() {
        super.onResume()
        updateUi()
    }

    private fun updateUi() {
        val enabled = isBridgeEnabled()
        val armed = prefs.getBoolean("bridge_arm_once", false)
        val editableCount = prefs.getInt("bridge_editable_count", 0)
        val candidate = prefs.getString("bridge_customer_candidate", "").orEmpty()
        state.text = buildString {
            append(if (enabled) "✓ Ponte de acessibilidade ATIVA" else "⚠ Ponte de acessibilidade DESLIGADA")
            append("\n")
            append(if (armed) "● Preenchimento único ARMADO" else "Preenchimento não armado")
            append("\nCampos editáveis detetados: $editableCount")
            if (candidate.isNotBlank()) append("\nTexto candidato do cliente: $candidate")
        }
        state.setTextColor(if (enabled) Color.rgb(117, 235, 180) else Color.rgb(255, 196, 96))
        diagnostic.text = prefs.getString("diag_TEXT_CALL_BRIDGE", "Sem eventos da interface Samsung ainda.")
    }

    private fun isBridgeEnabled(): Boolean {
        val expected = ComponentName(this, SofiaTextCallBridgeService::class.java).flattenToString()
        val enabled = Settings.Secure.getString(contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
        return enabled.split(':').any { it.equals(expected, ignoreCase = true) }
    }
}
