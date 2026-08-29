package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.telecom.TelecomManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(40, 60, 40, 40)
        }
        val title = TextView(this).apply { text = "SD Voice AI — Samsung Gateway"; textSize = 22f }
        val api = EditText(this).apply { hint = "https://...vercel.app"; setText(prefs.getString("api_url", "")) }
        val key = EditText(this).apply { hint = "Device key"; setText(prefs.getString("device_key", "samsung-01")) }
        val token = EditText(this).apply { hint = "Device token"; setText(prefs.getString("device_token", "")) }
        val save = Button(this).apply { text = "Guardar e iniciar gateway" }
        val dialer = Button(this).apply { text = "Definir como app de telefone" }
        status = TextView(this).apply { text = "Gateway parado" }

        save.setOnClickListener {
            prefs.edit()
                .putString("api_url", api.text.toString().trimEnd('/'))
                .putString("device_key", key.text.toString())
                .putString("device_token", token.text.toString())
                .apply()
            startGatewayWhenPermitted()
        }

        dialer.setOnClickListener { requestDialerRole() }

        layout.addView(title)
        layout.addView(api)
        layout.addView(key)
        layout.addView(token)
        layout.addView(save)
        layout.addView(dialer)
        layout.addView(status)
        setContentView(layout)
    }

    private fun requestDialerRole() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val roleManager = getSystemService(RoleManager::class.java)
                if (roleManager != null && roleManager.isRoleAvailable(RoleManager.ROLE_DIALER)) {
                    if (roleManager.isRoleHeld(RoleManager.ROLE_DIALER)) {
                        status.text = "SD Voice Gateway já é a app de telefone"
                    } else {
                        status.text = "A pedir função de app de telefone…"
                        startActivityForResult(roleManager.createRequestRoleIntent(RoleManager.ROLE_DIALER), 200)
                    }
                } else {
                    status.text = "Função Telefone indisponível; a abrir apps predefinidas…"
                    startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
                }
            } else {
                startActivityForResult(Intent(TelecomManager.ACTION_CHANGE_DEFAULT_DIALER).apply {
                    putExtra(TelecomManager.EXTRA_CHANGE_DEFAULT_DIALER_PACKAGE_NAME, packageName)
                }, 200)
            }
        } catch (t: Throwable) {
            status.text = "Erro ao pedir função Telefone: ${t.javaClass.simpleName}: ${t.message ?: "sem detalhe"}"
            try { startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)) } catch (_: Throwable) {}
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 200) {
            val roleManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) getSystemService(RoleManager::class.java) else null
            val held = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && roleManager?.isRoleHeld(RoleManager.ROLE_DIALER) == true
            status.text = if (held || resultCode == RESULT_OK) "SD Voice Gateway definido como app de telefone" else "Função de app de telefone não atribuída"
        }
    }

    private fun requiredPermissions() = arrayOf(
        Manifest.permission.CALL_PHONE,
        Manifest.permission.READ_PHONE_STATE,
        Manifest.permission.RECORD_AUDIO
    )

    private fun startGatewayWhenPermitted() {
        val missing = requiredPermissions().filter {
            ActivityCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            status.text = "A aguardar permissões…"
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 100)
            return
        }
        startGatewaySafely()
    }

    private fun startGatewaySafely() {
        try {
            ContextCompat.startForegroundService(this, Intent(this, GatewayService::class.java))
            status.text = "Gateway iniciado"
        } catch (t: Throwable) {
            status.text = "Erro ao iniciar: ${t.javaClass.simpleName}: ${t.message ?: "sem detalhe"}"
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 100) return
        val denied = grantResults.indices.filter { grantResults[it] != PackageManager.PERMISSION_GRANTED }
        if (denied.isEmpty()) {
            startGatewaySafely()
        } else {
            status.text = "Permissões necessárias recusadas. Autoriza Telefone e Microfone nas definições da app."
        }
    }
}
