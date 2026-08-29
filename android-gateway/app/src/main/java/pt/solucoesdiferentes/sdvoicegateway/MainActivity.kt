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
        val status = TextView(this).apply { text = "Gateway parado" }

        save.setOnClickListener {
            prefs.edit()
                .putString("api_url", api.text.toString().trimEnd('/'))
                .putString("device_key", key.text.toString())
                .putString("device_token", token.text.toString())
                .apply()
            requestRuntimePermissions()
            ContextCompat.startForegroundService(this, Intent(this, GatewayService::class.java))
            status.text = "Gateway iniciado"
        }

        dialer.setOnClickListener {
            val telecom = getSystemService(TELECOM_SERVICE) as TelecomManager
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

    private fun requestRuntimePermissions() {
        val permissions = arrayOf(
            Manifest.permission.CALL_PHONE,
            Manifest.permission.READ_PHONE_STATE,
            Manifest.permission.RECORD_AUDIO
        )
        val missing = permissions.filter { ActivityCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) ActivityCompat.requestPermissions(this, missing.toTypedArray(), 100)
    }
}
