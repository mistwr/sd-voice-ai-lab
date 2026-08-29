package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.telecom.TelecomManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private lateinit var status: TextView
    private lateinit var diagnostics: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(40, 60, 40, 40) }
        val title = TextView(this).apply { text = "SD Voice AI — Samsung Gateway"; textSize = 22f }
        val api = EditText(this).apply { hint = "https://...vercel.app"; setText(prefs.getString("api_url", "")) }
        val key = EditText(this).apply { hint = "Device key"; setText(prefs.getString("device_key", "samsung-01")) }
        val token = EditText(this).apply { hint = "Device token"; setText(prefs.getString("device_token", "")) }
        val save = Button(this).apply { text = "Guardar e iniciar gateway" }
        val dialer = Button(this).apply { text = "Definir como app de telefone" }
        val testNumber = EditText(this).apply { hint = "Número para teste GSM"; inputType = android.text.InputType.TYPE_CLASS_PHONE }
        val testCall = Button(this).apply { text = "Fazer chamada GSM de teste" }
        val refresh = Button(this).apply { text = "Ver diagnóstico da chamada" }
        status = TextView(this).apply { text = "Gateway parado" }
        diagnostics = TextView(this).apply { text = "Diagnóstico: ainda sem dados"; setTextIsSelectable(true) }

        save.setOnClickListener {
            prefs.edit().putString("api_url", api.text.toString().trimEnd('/')).putString("device_key", key.text.toString())
                .putString("device_token", token.text.toString()).apply(); startGatewayWhenPermitted()
        }
        dialer.setOnClickListener { requestDialerRole() }
        testCall.setOnClickListener { placeTestCall(testNumber.text.toString()) }
        refresh.setOnClickListener { showDiagnostics() }

        listOf(title, api, key, token, save, dialer, testNumber, testCall, status, refresh, diagnostics).forEach { layout.addView(it) }
        setContentView(ScrollView(this).apply { addView(layout) })
    }

    override fun onResume() { super.onResume(); showDiagnostics() }

    private fun showDiagnostics() {
        val names = listOf("CALL","STATE","DEVICE_CAPABILITY","AUDIO_CAPABILITY","TX_AUDIO_CAPABILITY","DEVICE_CAPABILITY_HTTP_ERROR","AUDIO_CAPABILITY_HTTP_ERROR","TX_AUDIO_CAPABILITY_HTTP_ERROR")
        val text = names.mapNotNull { n -> prefs.getString("diag_$n", null)?.let { "$n:\n$it" } }.joinToString("\n\n")
        diagnostics.text = if (text.isBlank()) "Diagnóstico: ainda sem dados" else "DIAGNÓSTICO GSM\n\n$text"
    }

    private fun placeTestCall(raw: String) {
        val number = raw.trim().replace(" ", "")
        if (number.isBlank()) { status.text = "Introduz um número de teste"; return }
        if (ActivityCompat.checkSelfPermission(this, Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED) {
            status.text = "Falta autorização Telefone"; ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CALL_PHONE), 100); return
        }
        try { status.text = "A iniciar chamada GSM…"; getSystemService(TelecomManager::class.java).placeCall(Uri.fromParts("tel", number, null), Bundle()) }
        catch (t: Throwable) { status.text = "Erro GSM: ${t.javaClass.simpleName}: ${t.message ?: "sem detalhe"}" }
    }

    private fun requestDialerRole() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val rm = getSystemService(RoleManager::class.java)
                if (rm != null && rm.isRoleAvailable(RoleManager.ROLE_DIALER)) {
                    if (rm.isRoleHeld(RoleManager.ROLE_DIALER)) status.text = "SD Voice Gateway já é a app de telefone"
                    else { status.text = "A pedir função de app de telefone…"; startActivityForResult(rm.createRequestRoleIntent(RoleManager.ROLE_DIALER), 200) }
                } else startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
            } else startActivityForResult(Intent(TelecomManager.ACTION_CHANGE_DEFAULT_DIALER).apply { putExtra(TelecomManager.EXTRA_CHANGE_DEFAULT_DIALER_PACKAGE_NAME, packageName) }, 200)
        } catch (t: Throwable) { status.text = "Erro ao pedir função Telefone: ${t.javaClass.simpleName}: ${t.message ?: "sem detalhe"}" }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 200) {
            val rm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) getSystemService(RoleManager::class.java) else null
            val held = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && rm?.isRoleHeld(RoleManager.ROLE_DIALER) == true
            status.text = if (held || resultCode == RESULT_OK) "SD Voice Gateway definido como app de telefone" else "Função de app de telefone não atribuída"
        }
    }

    private fun requiredPermissions() = arrayOf(Manifest.permission.CALL_PHONE, Manifest.permission.READ_PHONE_STATE, Manifest.permission.RECORD_AUDIO)
    private fun startGatewayWhenPermitted() {
        val missing = requiredPermissions().filter { ActivityCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) { status.text = "A aguardar permissões…"; ActivityCompat.requestPermissions(this, missing.toTypedArray(), 100); return }
        startGatewaySafely()
    }
    private fun startGatewaySafely() {
        try { ContextCompat.startForegroundService(this, Intent(this, GatewayService::class.java)); status.text = "Gateway iniciado" }
        catch (t: Throwable) { status.text = "Erro ao iniciar: ${t.javaClass.simpleName}: ${t.message ?: "sem detalhe"}" }
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 100) return
        if (grantResults.all { it == PackageManager.PERMISSION_GRANTED }) startGatewaySafely()
        else status.text = "Permissões necessárias recusadas. Autoriza Telefone e Microfone nas definições da app."
    }
}
