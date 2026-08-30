package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import net.amazingapps.llama.android.core.AiChat
import net.amazingapps.llama.android.core.InferenceEngine
import org.json.JSONObject

object SofiaLocalEngine {
    @Volatile private var loadedPath: String? = null

    private const val SYSTEM_PROMPT = """És a Sofia, assistente comercial virtual da Soluções Diferentes / SD Voice.
Fala sempre em português de Portugal.
A tua resposta será dita em voz numa chamada telefónica, por isso responde de forma natural, curta e clara.
Nunca repitas simplesmente a frase do cliente.
Faz no máximo uma pergunta de cada vez.
O objetivo é perceber a situação atual do cliente antes de sugerir qualquer alternativa.
Nunca inventes preços, campanhas, cobertura, fidelização, poupanças ou condições.
Se o cliente disser quanto paga, pergunta de forma natural o que está incluído nesse valor.
Se disser o operador, usa essa informação e avança para uma pergunta útil seguinte.
Se pedir para não ser contactado, pede desculpa de forma breve e confirma que o contacto termina.
Não uses listas, JSON, markdown, etiquetas, raciocínio ou comentários internos.
Nunca escrevas <think>, </think>, 'Sofia:' ou raciocínio interno.
/no_think"""

    private fun engine(context: Context): InferenceEngine = AiChat.getInferenceEngine(context.applicationContext)
    fun loadedModelPath(): String? = loadedPath
    fun isReady(context: Context): Boolean = loadedPath != null && engine(context).state.value is InferenceEngine.State.ModelReady

    suspend fun load(context: Context, modelPath: String) {
        val e = engine(context)
        val firstState = e.state.first {
            it is InferenceEngine.State.Initialized || it is InferenceEngine.State.ModelReady || it is InferenceEngine.State.Error
        }
        if (firstState is InferenceEngine.State.Error) try { e.cleanUp() } catch (_: Throwable) {}
        if (loadedPath == modelPath && e.state.value is InferenceEngine.State.ModelReady) return
        if (e.state.value is InferenceEngine.State.ModelReady || e.state.value is InferenceEngine.State.Error) {
            try { e.cleanUp() } catch (_: Throwable) {}
            loadedPath = null
        }
        e.loadModel(modelPath)
        e.setSystemPrompt(SYSTEM_PROMPT)
        loadedPath = modelPath
    }

    suspend fun respond(context: Context, customerText: String, memory: JSONObject): JSONObject {
        val e = engine(context)
        check(e.state.value is InferenceEngine.State.ModelReady) { "Modelo local da Sofia ainda não está carregado." }
        val customer = customerText.trim()
        require(customer.isNotBlank()) { "O texto do cliente está vazio." }

        val prompt = buildString {
            append("/no_think\n")
            append("Contexto conhecido do cliente: ")
            append(if (memory.length() == 0) "ainda sem dados" else memory.toString())
            append("\nCliente: ")
            append(customer)
            append("\nResponde apenas com a próxima frase curta que a Sofia deve dizer ao cliente. Sem raciocínio interno.")
        }

        val out = StringBuilder()
        e.sendUserPrompt(prompt, 90).collect { token -> out.append(token) }
        var reply = cleanReply(out.toString())
        if (reply.isBlank() || isEcho(reply, customer)) reply = discoveryFallback(customer)

        return JSONObject()
            .put("ok", true)
            .put("reply", reply)
            .put("outcome", classifyOutcome(customer))
            .put("memory", updateMemory(memory, customer))
            .put("model", "LOCAL_GGUF_LLAMA_CPP")
            .put("offline", true)
    }

    private fun cleanReply(raw: String): String {
        var text = raw.replace("\u0000", "").trim()

        // Remove complete thinking blocks.
        text = text.replace(Regex("(?is)<think>.*?</think>"), "").trim()

        // Some Qwen builds occasionally emit an opening <think> without closing it.
        // Never expose that internal text to the customer.
        if (text.contains("<think>", ignoreCase = true)) {
            val afterClose = text.substringAfterLast("</think>", "").trim()
            text = if (afterClose.isNotBlank()) afterClose else ""
        }

        text = text
            .replace(Regex("(?i)</?think>"), "")
            .replace(Regex("^```(?:text)?\\s*", RegexOption.IGNORE_CASE), "")
            .replace(Regex("\\s*```$"), "")
            .replace(Regex("^(Sofia|Assistente)\\s*:\\s*", RegexOption.IGNORE_CASE), "")
            .trim()

        return text.lineSequence()
            .map { it.trim() }
            .firstOrNull { it.isNotBlank() && !it.startsWith("<") }
            .orEmpty()
            .take(320)
    }

    private fun normalize(text: String): String = text.lowercase().replace(Regex("[^a-záàâãéêíóôõúç0-9]+"), " ").trim()

    private fun isEcho(reply: String, customer: String): Boolean {
        val r = normalize(reply); val c = normalize(customer)
        if (r.isBlank() || c.isBlank()) return false
        return r == c || (r.length >= 8 && (r.contains(c) || c.contains(r)))
    }

    private fun discoveryFallback(customer: String): String {
        val t = customer.lowercase()
        val hasPrice = Regex("\\b\\d{1,3}(?:[,.]\\d{1,2})?\\s*(?:€|euros?)?\\b").containsMatchIn(t)
        return when {
            hasPrice -> "Perfeito. O que é que está incluído nesse valor: televisão, internet e quantos telemóveis?"
            listOf("digi", "meo", "nos", "vodafone", "uzo", "woo", "amigo", "nowo").any { it in t } -> "Perfeito. E atualmente que serviços tem nesse operador?"
            else -> "Percebi. Pode dizer-me quanto paga atualmente e que serviços tem incluídos?"
        }
    }

    private fun classifyOutcome(customer: String): String {
        val t = normalize(customer)
        if (listOf("não me liguem", "nao me liguem", "não quero ser contactado", "nao quero ser contactado", "não me contacte", "nao me contacte").any { it in t }) return "DO_NOT_CALL"
        if (listOf("ligue mais tarde", "liga mais tarde", "contacte mais tarde", "amanhã", "amanha", "depois falamos").any { it in t }) return "CALLBACK"
        if (listOf("não estou interessado", "nao estou interessado", "não quero", "nao quero", "sem interesse").any { it in t }) return "NOT_INTERESTED"
        if (listOf("quero aderir", "tenho interesse", "estou interessado", "pode avançar", "pode avancar", "vamos avançar", "vamos avancar").any { it in t }) return "INTERESTED"
        return "CONTINUE"
    }

    private fun updateMemory(memory: JSONObject, customer: String): JSONObject {
        val next = JSONObject(memory.toString())
        val t = customer.lowercase()
        val operators = listOf("DIGI", "MEO", "NOS", "Vodafone", "UZO", "WOO", "Amigo", "NOWO")
        operators.firstOrNull { t.contains(it.lowercase()) }?.let { next.put("current_operator", it) }
        Regex("\\b(\\d{1,3}(?:[,.]\\d{1,2})?)\\s*(?:€|euros?)\\b", RegexOption.IGNORE_CASE)
            .find(customer)?.groupValues?.getOrNull(1)?.let { next.put("monthly_price", it.replace(',', '.')) }
        return next
    }
}
