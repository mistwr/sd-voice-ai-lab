package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import net.amazingapps.llama.android.core.AiChat
import net.amazingapps.llama.android.core.InferenceEngine
import org.json.JSONObject

/**
 * Reusable on-device brain for Sofia.
 *
 * The model lives in app-private storage and inference runs locally through llama.cpp.
 * No API key or Internet connection is required after the GGUF has been downloaded/imported.
 */
object SofiaLocalEngine {
    private val lock = Any()
    @Volatile private var loadedPath: String? = null

    private const val SYSTEM_PROMPT = """És a Sofia, assistente comercial IA da Soluções Diferentes / SD Voice.
Falas sempre em português de Portugal, de forma natural, curta, educada e humana.
Identifica-te como assistente virtual quando for relevante. Não finjas ser uma pessoa.
O objetivo é perceber a necessidade do cliente e ajudar, nunca pressionar.
Faz apenas uma pergunta de cada vez e evita respostas longas.
Nunca inventes preços, campanhas, cobertura, fidelização, poupanças ou condições.
Se o cliente pedir para não ser contactado, respeita imediatamente e usa DO_NOT_CALL.
Se houver interesse sem dados suficientes usa INTERESTED; se pedir contacto posterior usa CALLBACK; se não houver interesse usa NOT_INTERESTED; caso contrário CONTINUE.
Mantém na memória apenas factos realmente ditos pelo cliente.
/no_think
Responde APENAS em JSON válido nesta forma, sem markdown:
{"reply":"frase curta a dizer ao cliente","outcome":"CONTINUE|INTERESTED|CALLBACK|NOT_INTERESTED|DO_NOT_CALL","memory":{}}"""

    private fun engine(context: Context): InferenceEngine =
        AiChat.getInferenceEngine(context.applicationContext)

    fun loadedModelPath(): String? = loadedPath

    fun isReady(context: Context): Boolean =
        loadedPath != null && engine(context).state.value is InferenceEngine.State.ModelReady

    suspend fun load(context: Context, modelPath: String) {
        val e = engine(context)
        val firstState = e.state.first {
            it is InferenceEngine.State.Initialized ||
                it is InferenceEngine.State.ModelReady ||
                it is InferenceEngine.State.Error
        }
        if (firstState is InferenceEngine.State.Error) {
            try { e.cleanUp() } catch (_: Throwable) {}
        }

        if (loadedPath == modelPath && e.state.value is InferenceEngine.State.ModelReady) return

        if (e.state.value is InferenceEngine.State.ModelReady) {
            e.cleanUp()
            loadedPath = null
        }
        if (e.state.value is InferenceEngine.State.Error) {
            e.cleanUp()
            loadedPath = null
        }

        e.loadModel(modelPath)
        e.setSystemPrompt(SYSTEM_PROMPT)
        loadedPath = modelPath
    }

    suspend fun respond(context: Context, customerText: String, memory: JSONObject): JSONObject {
        val e = engine(context)
        check(e.state.value is InferenceEngine.State.ModelReady) { "Modelo local da Sofia ainda não está carregado." }

        val prompt = buildString {
            append("/no_think\n")
            append("O cliente disse: ")
            append(customerText.trim())
            append("\nMemória atual (JSON): ")
            append(memory.toString())
            append("\nResponde agora apenas com o JSON pedido.")
        }

        val out = StringBuilder()
        e.sendUserPrompt(prompt, 220).collect { token -> out.append(token) }
        val raw = stripThinking(out.toString()).trim()
        val parsed = parseJsonObject(raw)

        val reply = parsed.optString("reply", "").trim().ifBlank {
            raw.trim().take(500)
        }
        require(reply.isNotBlank()) { "O modelo local não gerou uma resposta falada." }

        val allowed = setOf("CONTINUE", "INTERESTED", "CALLBACK", "NOT_INTERESTED", "DO_NOT_CALL")
        val requestedOutcome = parsed.optString("outcome", "CONTINUE").uppercase()
        val outcome = if (requestedOutcome in allowed) requestedOutcome else "CONTINUE"
        val nextMemory = parsed.optJSONObject("memory") ?: memory

        return JSONObject()
            .put("ok", true)
            .put("reply", reply)
            .put("outcome", outcome)
            .put("memory", nextMemory)
            .put("model", "LOCAL_GGUF_LLAMA_CPP")
            .put("offline", true)
    }

    private fun stripThinking(text: String): String =
        text.replace(Regex("(?s)<think>.*?</think>"), "")
            .replace(Regex("^```(?:json)?\\s*", RegexOption.IGNORE_CASE), "")
            .replace(Regex("\\s*```$"), "")
            .trim()

    private fun parseJsonObject(text: String): JSONObject {
        return try {
            JSONObject(text)
        } catch (_: Throwable) {
            val start = text.indexOf('{')
            val end = text.lastIndexOf('}')
            if (start >= 0 && end > start) {
                try { JSONObject(text.substring(start, end + 1)) } catch (_: Throwable) { JSONObject() }
            } else JSONObject()
        }
    }
}
