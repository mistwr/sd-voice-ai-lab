package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.telecom.TelecomManager
import android.text.InputType
import android.view.View
import android.widget.*
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider

class MainActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private lateinit var status: TextView
    private lateinit var diagnostics: TextView
    private lateinit var techPanel: LinearLayout
    private lateinit var setupButton: Button
    private lateinit var roleState: TextView
    private lateinit var callAssistState: TextView
    private lateinit var recordingInfo: TextView
    private var player: MediaPlayer? = null

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun text(s: String, size: Float, bold: Boolean = false) = TextView(this).apply {
        text = s
        textSize = size
        setTextColor(Color.rgb(236, 240, 255))
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }
    private fun button(label: String) = Button(this).apply {
        text = label
        isAllCaps = false
        textSize = 16f
        setPadding(dp(14), dp(10), dp(14), dp(10))
    }
    private fun card() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(18), dp(16), dp(18), dp(16))
        setBackgroundColor(Color.rgb(25, 29, 46))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(28), dp(18), dp(30))
            setBackgroundColor(Color.rgb(10, 13, 24))
        }

        root.addView(text("SOFIA", 30f, true))
        root.addView(text("Assistente Comercial IA · SD Voice", 14f).apply { setTextColor(Color.rgb(151, 164, 207)) })
        root.addView(Space(this).apply { minimumHeight = dp(18) })

        val hero = card()
        hero.addView(text("●  Sofia", 20f, true).apply { setTextColor(Color.rgb(117, 235, 180)) })
        hero.addView(text("Samsung Gateway · Build 44 · Call Assist", 13f).apply { setTextColor(Color.LTGRAY) })
        roleState = text("A verificar modo de chamadas…", 14f).apply { setPadding(0, dp(10), 0, dp(8)) }
        hero.addView(roleState)
        setupButton = button("Usar modo Gateway direto")
        hero.addView(setupButton)
        root.addView(hero)

        root.addView(Space(this).apply { minimumHeight = dp(14) })
        val samsungCard = card()
        samsungCard.addView(text("🍒 Samsung Call Assist · rota no-root", 19f, true))
        callAssistState = text("A detetar Call Assist…", 13f).apply {
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(8), 0, dp(8))
        }
        samsungCard.addView(callAssistState)
        samsungCard.addView(text(
            "O Galaxy S26 tem Chamada de texto: o sistema Samsung ouve o cliente, mostra a transcrição e envia voz sintetizada para a chamada. Este modo testa essa via privilegiada mantendo o Telefone Samsung como aplicação de chamadas.",
            12f
        ).apply { setTextColor(Color.LTGRAY) })
        val defaultApps = button("📱  Escolher app de telefone predefinida")
        val callAssistSettings = button("⚙  Ver Samsung Call Assist")
        samsungCard.addView(defaultApps)
        samsungCard.addView(callAssistSettings)
        root.addView(samsungCard)

        root.addView(Space(this).apply { minimumHeight = dp(14) })
        val aiCard = card()
        aiCard.addView(text("IA durante a chamada", 19f, true))
        val brainButton = button("🧠  Testar Sofia · LLM + voz / Offline")
        aiCard.addView(brainButton)
        val recordSwitch = Switch(this).apply {
            text = "Gravar chamada"
            setTextColor(Color.WHITE)
            isChecked = prefs.getBoolean("record_calls", true)
        }
        val transcribeSwitch = Switch(this).apply {
            text = "Transcrever com IA"
            setTextColor(Color.WHITE)
            isChecked = prefs.getBoolean("transcribe_calls", true)
        }
        val uploadSwitch = Switch(this).apply {
            text = "Enviar gravação para Sofia"
            setTextColor(Color.WHITE)
            isChecked = prefs.getBoolean("upload_recordings", true)
        }
        val consent = text(
            "A gravação fica local e, se o envio estiver ativo, é enviada de forma autenticada para o armazenamento privado da Sofia. Use apenas quando houver base legal e informação/consentimento aplicável.",
            12f
        ).apply {
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(8), 0, 0)
        }
        aiCard.addView(recordSwitch)
        aiCard.addView(transcribeSwitch)
        aiCard.addView(uploadSwitch)
        aiCard.addView(consent)
        root.addView(aiCard)

        recordSwitch.setOnCheckedChangeListener { _, v -> prefs.edit().putBoolean("record_calls", v).apply() }
        transcribeSwitch.setOnCheckedChangeListener { _, v -> prefs.edit().putBoolean("transcribe_calls", v).apply() }
        uploadSwitch.setOnCheckedChangeListener { _, v -> prefs.edit().putBoolean("upload_recordings", v).apply() }
        brainButton.setOnClickListener { startActivity(Intent(this, SofiaBrainActivity::class.java)) }

        root.addView(Space(this).apply { minimumHeight = dp(14) })
        val recordingCard = card()
        recordingCard.addView(text("Última gravação", 19f, true))
        recordingInfo = text("Ainda não existe gravação local.", 13f).apply {
            setTextColor(Color.LTGRAY)
            setPadding(0, dp(8), 0, dp(10))
        }
        recordingCard.addView(recordingInfo)
        val listen = button("▶  Ouvir última gravação")
        val share = button("↗  Partilhar / exportar gravação")
        val resend = button("☁  Enviar novamente para Sofia")
        recordingCard.addView(listen)
        recordingCard.addView(share)
        recordingCard.addView(resend)
        root.addView(recordingCard)

        root.addView(Space(this).apply { minimumHeight = dp(14) })
        val callCard = card()
        callCard.addView(text("Nova chamada com Sofia", 19f, true))
        val testNumber = EditText(this).apply {
            hint = "Número do cliente"
            setHintTextColor(Color.GRAY)
            setTextColor(Color.WHITE)
            textSize = 20f
            inputType = InputType.TYPE_CLASS_PHONE
        }
        callCard.addView(testNumber)
        val testCall = button("📞  Ligar · modo Gateway direto")
        val samsungCall = button("🍒  Ligar · Samsung Call Assist")
        callCard.addView(testCall)
        callCard.addView(samsungCall)
        callCard.addView(text(
            "Teste Samsung: seleciona primeiro o Telefone Samsung como predefinido. Depois da chamada atender, abre Assistente de chamadas → Chamada de texto e escreve uma frase. Se a outra pessoa ouvir a voz IA e a resposta dela aparecer em texto, encontrámos RX + TX no-root pelo sistema Samsung.",
            12f
        ).apply { setTextColor(Color.LTGRAY) })
        status = text("Gateway parado", 13f).apply {
            setPadding(0, dp(10), 0, 0)
            setTextColor(Color.LTGRAY)
        }
        callCard.addView(status)
        root.addView(callCard)

        root.addView(Space(this).apply { minimumHeight = dp(14) })
        val techToggle = button("⚙  Laboratório técnico / diagnóstico")
        root.addView(techToggle)
        techPanel = card().apply { visibility = View.GONE }

        val api = EditText(this).apply {
            hint = "API"
            setText(prefs.getString("api_url", ""))
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
        }
        val key = EditText(this).apply {
            hint = "Device key"
            setText(prefs.getString("device_key", "samsung-01"))
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
        }
        val token = EditText(this).apply {
            hint = "Device token"
            setText(prefs.getString("device_token", ""))
            setTextColor(Color.WHITE)
            setHintTextColor(Color.GRAY)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        val save = button("Guardar e iniciar gateway")
        val settings = button("Abrir definições da aplicação")
        val refresh = button("Ver diagnóstico Build 44 — Call Assist / IA / TX")
        diagnostics = text("Diagnóstico: ainda sem dados", 12f).apply {
            setTextIsSelectable(true)
            setPadding(0, dp(12), 0, 0)
        }
        listOf(text("Configuração técnica", 18f, true), api, key, token, save, settings, refresh, diagnostics).forEach { techPanel.addView(it) }
        root.addView(techPanel)

        setupButton.setOnClickListener { requestDialerRole() }
        defaultApps.setOnClickListener { openDefaultApps() }
        callAssistSettings.setOnClickListener { openCallAssistSettings() }
        techToggle.setOnClickListener { techPanel.visibility = if (techPanel.visibility == View.VISIBLE) View.GONE else View.VISIBLE }
        settings.setOnClickListener { startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))) }
        save.setOnClickListener {
            prefs.edit()
                .putString("api_url", api.text.toString().trimEnd('/'))
                .putString("device_key", key.text.toString())
                .putString("device_token", token.text.toString())
                .apply()
            startGatewayWhenPermitted()
        }
        testCall.setOnClickListener { placeTestCall(testNumber.text.toString()) }
        samsungCall.setOnClickListener { placeSamsungCall(testNumber.text.toString()) }
        refresh.setOnClickListener { showDiagnostics(); updateRecordingUi(); updateSamsungCallAssistUi() }
        listen.setOnClickListener { playLatestRecording() }
        share.setOnClickListener { shareLatestRecording() }
        resend.setOnClickListener { uploadLatestRecording() }

        setContentView(ScrollView(this).apply { addView(root) })
        updateRoleUi()
        updateRecordingUi()
        updateSamsungCallAssistUi()
    }

    override fun onResume() {
        super.onResume()
        updateRoleUi()
        updateSamsungCallAssistUi()
        showDiagnostics()
        updateRecordingUi()
    }

    override fun onDestroy() {
        try { player?.release() } catch (_: Throwable) {}
        player = null
        super.onDestroy()
    }

    private fun hasDialerRole(): Boolean = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
        val rm = getSystemService(RoleManager::class.java)
        rm?.isRoleHeld(RoleManager.ROLE_DIALER) == true
    } else getSystemService(TelecomManager::class.java).defaultDialerPackage == packageName

    private fun updateRoleUi() {
        val held = hasDialerRole()
        roleState.text = if (held) {
            "✓ Modo Gateway direto ativo · Sofia é o dialer predefinido"
        } else {
            "Modo Gateway direto desligado · podes usar a rota Samsung Call Assist"
        }
        roleState.setTextColor(if (held) Color.rgb(117, 235, 180) else Color.rgb(255, 196, 96))
        setupButton.text = if (held) "✓ Modo Gateway direto ativo" else "Usar modo Gateway direto"
        setupButton.isEnabled = !held
    }

    private fun updateSamsungCallAssistUi() {
        try {
            val snapshot = SamsungCallAssistDiagnostics.snapshot(this)
            prefs.edit().putString("diag_SAMSUNG_CALL_ASSIST", snapshot.toString()).apply()
            val defaultDialer = snapshot.optString("default_dialer", "desconhecido")
            val installed = SamsungCallAssistDiagnostics.hasCallAssistant(this)
            callAssistState.text = if (installed) {
                "✓ Samsung Call Assist encontrado\nDialer atual: $defaultDialer"
            } else {
                "⚠ Pacote Samsung Call Assist não foi visível/detetado\nDialer atual: $defaultDialer"
            }
            callAssistState.setTextColor(if (installed) Color.rgb(117, 235, 180) else Color.rgb(255, 196, 96))
        } catch (t: Throwable) {
            callAssistState.text = "Diagnóstico Call Assist falhou: ${t.message ?: t.javaClass.simpleName}"
        }
    }

    private fun openDefaultApps() {
        try {
            startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
        } catch (t: Throwable) {
            status.text = "Não consegui abrir apps predefinidas: ${t.message ?: t.javaClass.simpleName}"
        }
    }

    private fun openCallAssistSettings() {
        val pkg = "com.samsung.android.callassistant"
        if (!SamsungCallAssistDiagnostics.hasCallAssistant(this)) {
            status.text = "Samsung Call Assist não foi detetado como pacote visível"
            return
        }
        try {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$pkg")))
        } catch (t: Throwable) {
            status.text = "Não consegui abrir Call Assist: ${t.message ?: t.javaClass.simpleName}"
        }
    }

    private fun updateRecordingUi() {
        val file = CallSessionRecorder.latestFile(this)
        if (file == null) {
            recordingInfo.text = "Ainda não existe gravação local."
            return
        }
        val duration = prefs.getLong("latest_recording_duration_ms", 0L)
        val upload = prefs.getString("latest_recording_upload_status", "local") ?: "local"
        val remote = prefs.getString("latest_recording_remote_path", "") ?: ""
        val seconds = duration / 1000.0
        val uploadText = when (upload) {
            "uploaded" -> "✓ enviada para Sofia"
            "failed" -> "⚠ envio falhou — podes reenviar"
            else -> "guardada localmente"
        }
        recordingInfo.text = "${file.name} · ${file.length()} bytes · %.1f s\n%s%s".format(
            seconds,
            uploadText,
            if (remote.isNotBlank()) "\nPrivado: $remote" else ""
        )
    }

    private fun playLatestRecording() {
        val file = CallSessionRecorder.latestFile(this) ?: run {
            status.text = "Ainda não há gravação para ouvir"
            return
        }
        try {
            player?.release()
            player = MediaPlayer().apply {
                setDataSource(file.absolutePath)
                setOnCompletionListener {
                    status.text = "Reprodução concluída"
                    try { it.release() } catch (_: Throwable) {}
                    player = null
                }
                prepare()
                start()
            }
            status.text = "A reproduzir ${file.name}…"
        } catch (t: Throwable) {
            status.text = "Erro ao reproduzir: ${t.message ?: t.javaClass.simpleName}"
        }
    }

    private fun shareLatestRecording() {
        val file = CallSessionRecorder.latestFile(this) ?: run {
            status.text = "Ainda não há gravação para exportar"
            return
        }
        try {
            val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = "audio/mp4"
                putExtra(Intent.EXTRA_STREAM, uri)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(Intent.createChooser(intent, "Partilhar gravação da Sofia"))
        } catch (t: Throwable) {
            status.text = "Erro ao exportar: ${t.message ?: t.javaClass.simpleName}"
        }
    }

    private fun uploadLatestRecording() {
        val file = CallSessionRecorder.latestFile(this) ?: run {
            status.text = "Ainda não há gravação para enviar"
            return
        }
        status.text = "A enviar gravação para Sofia…"
        Thread {
            try {
                val result = GatewayApi.uploadRecording(
                    applicationContext,
                    file,
                    prefs.getString("latest_recording_call_id", null),
                    prefs.getString("latest_recording_command_id", null),
                    prefs.getLong("latest_recording_duration_ms", 0L)
                )
                prefs.edit()
                    .putString("latest_recording_upload_status", "uploaded")
                    .putString("latest_recording_remote_path", result.optString("path", ""))
                    .putString("diag_RECORDING_UPLOAD", result.toString())
                    .apply()
                runOnUiThread {
                    status.text = "Gravação enviada para Sofia ✓"
                    updateRecordingUi()
                    showDiagnostics()
                }
            } catch (t: Throwable) {
                prefs.edit().putString("latest_recording_upload_status", "failed").apply()
                runOnUiThread {
                    status.text = "Falha no envio: ${t.message ?: t.javaClass.simpleName}"
                    updateRecordingUi()
                }
            }
        }.start()
    }

    private fun showDiagnostics() {
        val names = listOf(
            "CALL", "STATE", "AI_SESSION_STARTED", "CALL_RECORDING_STARTED", "CALL_RECORDING_STOPPED",
            "RECORDING_UPLOAD", "RECORDING_UPLOADED", "TRANSCRIPTION_REQUESTED", "SAMSUNG_CALL_ASSIST", "DEVICE_CAPABILITY",
            "STOCK_AUDIO_ROUTE_CAPABILITY", "TELEPHONY_PCM_CAPABILITY", "TELEPHONY_TX_CAPABILITY", "AUDIO_CAPABILITY",
            "DEVICE_CAPABILITY_HTTP_ERROR", "STOCK_AUDIO_ROUTE_CAPABILITY_HTTP_ERROR", "TELEPHONY_PCM_CAPABILITY_HTTP_ERROR",
            "TELEPHONY_TX_CAPABILITY_HTTP_ERROR", "AUDIO_CAPABILITY_HTTP_ERROR"
        )
        val out = names.mapNotNull { n -> prefs.getString("diag_$n", null)?.let { "$n:\n$it" } }.joinToString("\n\n")
        diagnostics.text = if (out.isBlank()) "Diagnóstico: ainda sem dados" else "DIAGNÓSTICO GSM — BUILD 44 CALL ASSIST / IA / TX\n\n$out"
    }

    private fun cleanNumber(raw: String): String = raw.trim().replace(" ", "")

    private fun ensureCallPermission(): Boolean {
        if (ActivityCompat.checkSelfPermission(this, Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CALL_PHONE), 100)
            return false
        }
        return true
    }

    private fun placeTestCall(raw: String) {
        if (!hasDialerRole()) {
            status.text = "Para o modo Gateway direto, ativa primeiro a Sofia como app de telefone"
            requestDialerRole()
            return
        }
        val number = cleanNumber(raw)
        if (number.isBlank()) {
            status.text = "Introduz o número do cliente"
            return
        }
        if (!ensureCallPermission()) return
        try {
            status.text = "Sofia está a iniciar a chamada em modo Gateway…"
            getSystemService(TelecomManager::class.java).placeCall(Uri.fromParts("tel", number, null), Bundle())
        } catch (t: Throwable) {
            status.text = "Erro GSM: ${t.message ?: "sem detalhe"}"
        }
    }

    private fun placeSamsungCall(raw: String) {
        val number = cleanNumber(raw)
        if (number.isBlank()) {
            status.text = "Introduz o número do cliente"
            return
        }
        if (!ensureCallPermission()) return
        val telecom = getSystemService(TelecomManager::class.java)
        val defaultDialer = telecom.defaultDialerPackage ?: ""
        if (defaultDialer == packageName) {
            status.text = "Seleciona o Telefone Samsung como app de telefone predefinida para testar Call Assist / Chamada de texto"
            openDefaultApps()
            return
        }
        try {
            status.text = "A iniciar pelo dialer do sistema: $defaultDialer"
            telecom.placeCall(Uri.fromParts("tel", number, null), Bundle())
            Toast.makeText(this, "Quando atender: Assistente de chamadas → Chamada de texto", Toast.LENGTH_LONG).show()
        } catch (t: Throwable) {
            status.text = "Erro no modo Samsung: ${t.message ?: t.javaClass.simpleName}"
        }
    }

    private fun requestDialerRole() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val rm = getSystemService(RoleManager::class.java)
                if (rm != null && rm.isRoleAvailable(RoleManager.ROLE_DIALER)) {
                    if (!rm.isRoleHeld(RoleManager.ROLE_DIALER)) startActivityForResult(rm.createRequestRoleIntent(RoleManager.ROLE_DIALER), 200)
                    else updateRoleUi()
                } else startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
            } else startActivityForResult(
                Intent(TelecomManager.ACTION_CHANGE_DEFAULT_DIALER).apply {
                    putExtra(TelecomManager.EXTRA_CHANGE_DEFAULT_DIALER_PACKAGE_NAME, packageName)
                },
                200
            )
        } catch (t: Throwable) {
            status.text = "Não foi possível abrir a autorização: ${t.message ?: "sem detalhe"}"
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 200) {
            updateRoleUi()
            updateSamsungCallAssistUi()
            status.text = if (hasDialerRole()) "Modo Gateway direto ativo ✓" else "A Sofia não ficou como dialer predefinido"
        }
    }

    private fun requiredPermissions() = arrayOf(
        Manifest.permission.CALL_PHONE,
        Manifest.permission.READ_PHONE_STATE,
        Manifest.permission.RECORD_AUDIO
    )

    private fun startGatewayWhenPermitted() {
        val missing = requiredPermissions().filter { ActivityCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 100)
            return
        }
        startGatewaySafely()
    }

    private fun startGatewaySafely() {
        try {
            ContextCompat.startForegroundService(this, Intent(this, GatewayService::class.java))
            status.text = "Sofia online · Gateway Build 44 ✓"
        } catch (t: Throwable) {
            status.text = "Erro ao iniciar: ${t.message ?: "sem detalhe"}"
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 100 && grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) startGatewaySafely()
    }
}
