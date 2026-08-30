package pt.solucoesdiferentes.sdvoicegateway

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.speech.tts.TextToSpeech
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale

class OfflineSofiaActivity : Activity(), TextToSpeech.OnInitListener {
    companion object {
        private const val PICK_GGUF = 4301
        private const val QWEN_FILE = "Qwen3-1.7B-Q4_K_M.gguf"
        private const val QWEN_URL = "https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf?download=true"
    }

    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var status: TextView
    private lateinit var modelInfo: TextView
    private lateinit var input: EditText
    private lateinit var response: TextView
    private lateinit var fallbackSwitch: Switch
    private lateinit var profileSpinner: Spinner
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var memory = JSONObject()

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun label(value: String, size: Float, bold: Boolean = false) = TextView(this).apply {
        text = value
        textSize = size
        setTextColor(Color.rgb(236, 240, 255))
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }
    private fun button(value: String) = Button(this).apply {
        text = value
        isAllCaps = false
        textSize = 16f
    }
    private fun card() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(16), dp(14), dp(16), dp(14))
        setBackgroundColor(Color.rgb(25, 29, 46))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        tts = TextToSpeech(this, this)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(26), dp(18), dp(30))
            setBackgroundColor(Color.rgb(10, 13, 24))
        }

        root.addView(label("SOFIA · OFFLINE", 28f, true))
        root.addView(label("Build 43 · LLM local no Samsung · 0 €/pedido", 13f).apply {
            setTextColor(Color.rgb(151, 164, 207))
            setPadding(0, dp(4), 0, dp(16))
        })

        val modelCard = card()
        modelCard.addView(label("Cérebro local", 19f, true))
        profileSpinner = Spinner(this)
        val profiles = listOf(
            "Qwen3 1.7B Q4_K_M · recomendado",
            "DeepSeek / outro GGUF · importar",
            "Gemma / outro GGUF · importar"
        )
        profileSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, profiles)
        modelCard.addView(profileSpinner)

        modelInfo = label("A verificar modelo local…", 13f).apply {
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(8), 0, dp(8))
        }
        modelCard.addView(modelInfo)

        val download = button("⬇  Descarregar Qwen3 1.7B · ~1,3 GB")
        val importModel = button("📂  Importar ficheiro GGUF")
        val load = button("⚡  Carregar modelo local")
        modelCard.addView(download)
        modelCard.addView(importModel)
        modelCard.addView(load)
        root.addView(modelCard)

        root.addView(android.widget.Space(this).apply { minimumHeight = dp(14) })
        val chatCard = card()
        chatCard.addView(label("Teste da Sofia sem Internet", 19f, true))
        input = EditText(this).apply {
            hint = "Ex.: Pago 55 euros por TV, net e dois telemóveis."
            setHintTextColor(Color.GRAY)
            setTextColor(Color.WHITE)
            textSize = 17f
            minLines = 3
            setBackgroundColor(Color.rgb(17, 21, 36))
            setPadding(dp(12), dp(12), dp(12), dp(12))
        }
        chatCard.addView(input, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        fallbackSwitch = Switch(this).apply {
            text = "Fallback cloud se o modelo local falhar"
            setTextColor(Color.WHITE)
            isChecked = prefs.getBoolean("offline_cloud_fallback", true)
        }
        chatCard.addView(fallbackSwitch)
        val ask = button("🤖  Sofia responder offline e falar")
        chatCard.addView(ask)

        status = label("A iniciar voz pt-PT…", 13f).apply {
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(10), 0, dp(10))
        }
        chatCard.addView(status)
        response = label("A resposta local da Sofia aparece aqui.", 18f).apply {
            setBackgroundColor(Color.rgb(17, 21, 36))
            setPadding(dp(14), dp(14), dp(14), dp(14))
        }
        chatCard.addView(response)
        root.addView(chatCard)

        root.addView(label(
            "O Qwen é descarregado uma única vez e depois o LLM funciona no próprio telemóvel, sem chave de API. O fallback cloud é opcional. Este ecrã testa cérebro + voz; a injeção da voz no uplink GSM continua a depender da rota de áudio Samsung.",
            12f
        ).apply {
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(16), 0, 0)
        })

        download.setOnClickListener { downloadQwen() }
        importModel.setOnClickListener { chooseGguf() }
        load.setOnClickListener { loadCurrentModel() }
        ask.setOnClickListener { askSofia() }
        fallbackSwitch.setOnCheckedChangeListener { _, checked ->
            prefs.edit().putBoolean("offline_cloud_fallback", checked).apply()
        }

        setContentView(ScrollView(this).apply { addView(root) })
        refreshModelInfo()
    }

    private fun modelsDir(): File = File(filesDir, "models").apply { mkdirs() }

    private fun selectedModel(): File? {
        val saved = prefs.getString("offline_model_path", "")?.takeIf { it.isNotBlank() }?.let(::File)
        if (saved?.exists() == true) return saved
        val qwen = File(modelsDir(), QWEN_FILE)
        return qwen.takeIf { it.exists() }
    }

    private fun refreshModelInfo() {
        val file = selectedModel()
        modelInfo.text = if (file == null) {
            "Nenhum GGUF instalado. Recomendo Qwen3 1.7B Q4_K_M."
        } else {
            val mb = file.length().toDouble() / 1024.0 / 1024.0
            val engineState = if (SofiaLocalEngine.isReady(this)) " · ✓ carregado" else " · pronto a carregar"
            "${file.name} · %.0f MB%s".format(mb, engineState)
        }
    }

    private fun downloadQwen() {
        val dest = File(modelsDir(), QWEN_FILE)
        if (dest.exists() && dest.length() > 100_000_000L) {
            prefs.edit().putString("offline_model_path", dest.absolutePath).apply()
            status.text = "Qwen3 já está no telemóvel ✓"
            refreshModelInfo()
            return
        }
        status.text = "A iniciar download do Qwen3… usa Wi-Fi se possível."
        scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val tmp = File(dest.absolutePath + ".part")
                    val conn = (URL(QWEN_URL).openConnection() as HttpURLConnection).apply {
                        connectTimeout = 20_000
                        readTimeout = 60_000
                        instanceFollowRedirects = true
                        requestMethod = "GET"
                        setRequestProperty("User-Agent", "SD-Voice-Sofia/43")
                    }
                    conn.connect()
                    if (conn.responseCode !in 200..299) throw IllegalStateException("HTTP ${conn.responseCode}")
                    val total = conn.contentLengthLong
                    var done = 0L
                    var lastUi = 0L
                    conn.inputStream.use { inputStream ->
                        tmp.outputStream().buffered().use { outputStream ->
                            val buffer = ByteArray(256 * 1024)
                            while (true) {
                                val read = inputStream.read(buffer)
                                if (read <= 0) break
                                outputStream.write(buffer, 0, read)
                                done += read
                                if (done - lastUi >= 8L * 1024 * 1024) {
                                    lastUi = done
                                    val pct = if (total > 0) (done * 100 / total).toInt() else -1
                                    runOnUiThread {
                                        status.text = if (pct >= 0) "A descarregar Qwen3… $pct%" else "A descarregar Qwen3… ${done / 1024 / 1024} MB"
                                    }
                                }
                            }
                        }
                    }
                    conn.disconnect()
                    if (tmp.length() < 100_000_000L) throw IllegalStateException("Download incompleto (${tmp.length()} bytes)")
                    if (dest.exists()) dest.delete()
                    if (!tmp.renameTo(dest)) {
                        tmp.copyTo(dest, overwrite = true)
                        tmp.delete()
                    }
                    prefs.edit().putString("offline_model_path", dest.absolutePath).apply()
                }
                status.text = "Qwen3 descarregado ✓ Agora toca em ‘Carregar modelo local’."
                refreshModelInfo()
            } catch (t: Throwable) {
                status.text = "Falha no download: ${t.message ?: t.javaClass.simpleName}"
            }
        }
    }

    private fun chooseGguf() {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
        }
        startActivityForResult(intent, PICK_GGUF)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != PICK_GGUF || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        status.text = "A importar GGUF…"
        scope.launch {
            try {
                val copied = withContext(Dispatchers.IO) { copyImportedModel(uri) }
                prefs.edit().putString("offline_model_path", copied.absolutePath).apply()
                status.text = "GGUF importado ✓ ${copied.name}"
                refreshModelInfo()
            } catch (t: Throwable) {
                status.text = "Falha ao importar: ${t.message ?: t.javaClass.simpleName}"
            }
        }
    }

    private fun copyImportedModel(uri: Uri): File {
        var displayName = "sofia-imported.gguf"
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0) displayName = cursor.getString(idx) ?: displayName
            }
        }
        val safe = displayName.replace(Regex("[^A-Za-z0-9._-]"), "_").let {
            if (it.lowercase().endsWith(".gguf")) it else "$it.gguf"
        }
        val dest = File(modelsDir(), safe)
        contentResolver.openInputStream(uri).use { inputStream ->
            requireNotNull(inputStream) { "Não foi possível abrir o ficheiro." }
            dest.outputStream().buffered().use { outputStream -> inputStream.copyTo(outputStream, 256 * 1024) }
        }
        require(dest.length() > 1_000_000L) { "Ficheiro GGUF vazio ou inválido." }
        return dest
    }

    private fun loadCurrentModel() {
        val file = selectedModel() ?: run {
            status.text = "Primeiro descarrega ou importa um GGUF."
            return
        }
        status.text = "A carregar ${file.name} na RAM…"
        scope.launch {
            try {
                withContext(Dispatchers.IO) { SofiaLocalEngine.load(applicationContext, file.absolutePath) }
                status.text = "Sofia Offline pronta ✓ · ${file.name}"
                refreshModelInfo()
            } catch (t: Throwable) {
                status.text = "Erro ao carregar modelo: ${t.message ?: t.javaClass.simpleName}"
            }
        }
    }

    private fun askSofia() {
        val customerText = input.text.toString().trim()
        if (customerText.isBlank()) {
            status.text = "Escreve primeiro o que o cliente disse."
            return
        }
        response.text = "…"
        status.text = "Sofia está a pensar localmente…"
        scope.launch {
            var usedCloud = false
            try {
                val result = withContext(Dispatchers.IO) {
                    val file = selectedModel() ?: throw IllegalStateException("Nenhum GGUF instalado")
                    if (!SofiaLocalEngine.isReady(applicationContext)) {
                        SofiaLocalEngine.load(applicationContext, file.absolutePath)
                    }
                    SofiaLocalEngine.respond(applicationContext, customerText, memory)
                }
                showReply(result, false)
            } catch (localError: Throwable) {
                if (!fallbackSwitch.isChecked) {
                    status.text = "Falha local: ${localError.message ?: localError.javaClass.simpleName}"
                    response.text = ""
                    return@launch
                }
                try {
                    usedCloud = true
                    status.text = "Local falhou; a usar fallback cloud…"
                    val result = withContext(Dispatchers.IO) {
                        GatewayApi.sofiaRespond(applicationContext, customerText, memory)
                    }
                    showReply(result, true)
                } catch (cloudError: Throwable) {
                    response.text = ""
                    status.text = "Local + cloud falharam: ${cloudError.message ?: cloudError.javaClass.simpleName}"
                }
            }
            if (usedCloud) refreshModelInfo()
        }
    }

    private fun showReply(result: JSONObject, cloud: Boolean) {
        val reply = result.optString("reply", "").trim()
        val newMemory = result.optJSONObject("memory")
        if (newMemory != null) memory = newMemory
        val outcome = result.optString("outcome", "CONTINUE")
        response.text = reply
        status.text = if (cloud) "Sofia respondeu via cloud · $outcome" else "Sofia respondeu OFFLINE ✓ · $outcome"
        if (reply.isNotBlank() && ttsReady) {
            tts?.speak(reply, TextToSpeech.QUEUE_FLUSH, null, "sofia-offline-${System.currentTimeMillis()}")
        }
    }

    override fun onInit(result: Int) {
        if (result != TextToSpeech.SUCCESS) {
            status.text = "Falha ao iniciar Text-to-Speech"
            return
        }
        val available = tts?.setLanguage(Locale("pt", "PT")) ?: TextToSpeech.LANG_NOT_SUPPORTED
        ttsReady = available != TextToSpeech.LANG_MISSING_DATA && available != TextToSpeech.LANG_NOT_SUPPORTED
        if (ttsReady) {
            tts?.setSpeechRate(0.96f)
            tts?.setPitch(1.02f)
            status.text = "Voz pt-PT pronta · escolhe/carrega o modelo local."
        } else status.text = "Motor TTS sem voz pt-PT disponível."
    }

    override fun onDestroy() {
        scope.cancel()
        try { tts?.stop(); tts?.shutdown() } catch (_: Throwable) {}
        tts = null
        super.onDestroy()
    }
}
