package pt.solucoesdiferentes.sdvoicegateway

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import java.util.Locale

class SofiaBrainActivity : Activity(), TextToSpeech.OnInitListener {
    private lateinit var input: EditText
    private lateinit var response: TextView
    private lateinit var status: TextView
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var memory = JSONObject()

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        tts = TextToSpeech(this, this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(28), dp(20), dp(28))
            setBackgroundColor(Color.rgb(10, 13, 24))
        }

        root.addView(TextView(this).apply {
            text = "SOFIA · CÉREBRO + VOZ"
            textSize = 26f
            setTextColor(Color.WHITE)
            setTypeface(typeface, Typeface.BOLD)
        })
        root.addView(TextView(this).apply {
            text = "Build 43 · Offline primeiro · cloud como fallback"
            textSize = 13f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(4), 0, dp(14))
        })

        val offline = Button(this).apply {
            text = "📱  Sofia Offline · 0 €/pedido"
            isAllCaps = false
            textSize = 17f
        }
        root.addView(offline)
        root.addView(TextView(this).apply {
            text = "Qwen3 1.7B GGUF no próprio telemóvel; também permite importar outros modelos GGUF."
            textSize = 12f
            setTextColor(Color.rgb(151, 164, 207))
            setPadding(0, dp(6), 0, dp(18))
        })

        root.addView(TextView(this).apply {
            text = "Teste cloud / servidor"
            textSize = 18f
            setTextColor(Color.WHITE)
            setTypeface(typeface, Typeface.BOLD)
            setPadding(0, 0, 0, dp(8))
        })

        input = EditText(this).apply {
            hint = "Escreve aqui o que o cliente disse…"
            setHintTextColor(Color.GRAY)
            setTextColor(Color.WHITE)
            textSize = 17f
            minLines = 3
            setBackgroundColor(Color.rgb(25, 29, 46))
            setPadding(dp(14), dp(14), dp(14), dp(14))
        }
        root.addView(input, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val ask = Button(this).apply {
            text = "☁️  Sofia responder via LLM cloud e falar"
            isAllCaps = false
            textSize = 17f
        }
        root.addView(ask)

        status = TextView(this).apply {
            text = "A iniciar voz pt-PT…"
            textSize = 13f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(10), 0, dp(10))
        }
        root.addView(status)

        response = TextView(this).apply {
            text = "A resposta da Sofia aparece aqui."
            textSize = 18f
            setTextColor(Color.rgb(236, 240, 255))
            setBackgroundColor(Color.rgb(25, 29, 46))
            setPadding(dp(16), dp(16), dp(16), dp(16))
        }
        root.addView(response)

        root.addView(TextView(this).apply {
            text = "O cérebro e o TTS já podem funcionar localmente ou por cloud. A voz ainda não é injetada diretamente no uplink GSM porque o Android continua a redirecionar o TX para o auricular em vez de TELEPHONY."
            textSize = 12f
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(18), 0, 0)
        })

        offline.setOnClickListener { startActivity(Intent(this, OfflineSofiaActivity::class.java)) }
        ask.setOnClickListener { askSofia() }
        setContentView(ScrollView(this).apply { addView(root) })
    }

    override fun onInit(result: Int) {
        if (result == TextToSpeech.SUCCESS) {
            val ptPt = Locale("pt", "PT")
            val available = tts?.setLanguage(ptPt) ?: TextToSpeech.LANG_NOT_SUPPORTED
            ttsReady = available != TextToSpeech.LANG_MISSING_DATA && available != TextToSpeech.LANG_NOT_SUPPORTED
            if (ttsReady) {
                tts?.setSpeechRate(0.96f)
                tts?.setPitch(1.02f)
                status.text = "Voz pt-PT pronta ✓"
            } else status.text = "Motor TTS sem voz pt-PT disponível"
        } else status.text = "Falha ao iniciar Text-to-Speech"
    }

    private fun askSofia() {
        val customerText = input.text.toString().trim()
        if (customerText.isBlank()) {
            status.text = "Escreve primeiro o que o cliente disse"
            return
        }

        status.text = "Sofia está a pensar via cloud…"
        response.text = "…"
        Thread {
            try {
                val result = GatewayApi.sofiaRespond(applicationContext, customerText, memory)
                val reply = result.optString("reply", "").trim()
                val newMemory = result.optJSONObject("memory")
                if (newMemory != null) memory = newMemory
                val outcome = result.optString("outcome", "CONTINUE")
                runOnUiThread {
                    response.text = reply
                    status.text = "LLM cloud respondeu · $outcome"
                    if (reply.isNotBlank() && ttsReady) {
                        tts?.speak(reply, TextToSpeech.QUEUE_FLUSH, null, "sofia-reply-${System.currentTimeMillis()}")
                        status.text = "Sofia a falar… · $outcome"
                    } else if (!ttsReady) {
                        status.text = "LLM respondeu, mas o TTS pt-PT não está disponível"
                    }
                }
            } catch (t: Throwable) {
                runOnUiThread {
                    response.text = ""
                    status.text = "Erro Sofia cloud: ${t.message ?: t.javaClass.simpleName}"
                }
            }
        }.start()
    }

    override fun onDestroy() {
        try {
            tts?.stop()
            tts?.shutdown()
        } catch (_: Throwable) {}
        tts = null
        super.onDestroy()
    }
}
