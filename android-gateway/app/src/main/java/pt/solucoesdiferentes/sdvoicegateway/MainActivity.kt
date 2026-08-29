package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
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

        dialer.setOnClickListener {
            if (android.os.Build.VERSION.SDK_INT >= 29) {
                startActivity(Intent(TelecomManager.ACTION_CHANGE_DEFAULT_DIALER).apply {
                    putExtra(TelecomManager.EXTRA_CHANGE_DEFAULT_DIALER_PACKAGE_NAME, packageName)
                })
            }
        }

        layout.addView(title)
        layout.addView(api)
        layout.addView(key)
        layout.addView(token)
        layout.addView(save)
        layout.addView(dialer)
        layout.addView(status)
        setContentView(layout)
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
