package pt.solucoesdiferentes.sdvoicegateway

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import android.telecom.TelecomManager
import org.json.JSONArray
import org.json.JSONObject

object SamsungCallAssistDiagnostics {
    private val candidatePackages = listOf(
        "com.samsung.android.callassistant",
        "com.samsung.android.incallui",
        "com.samsung.android.app.telephonyui",
        "com.samsung.android.dialer",
        "com.samsung.android.aicore",
        "com.google.android.aicore",
        "com.samsung.android.app.interpreter",
        "com.samsung.android.bixby.agent"
    )

    fun snapshot(context: Context): JSONObject {
        val pm = context.packageManager
        val telecom = context.getSystemService(TelecomManager::class.java)
        val arr = JSONArray()
        candidatePackages.forEach { pkg ->
            val info = packageInfo(pm, pkg)
            val row = JSONObject()
                .put("package", pkg)
                .put("installed", info != null)
            if (info != null) {
                val app = info.applicationInfo
                row.put("enabled", app?.enabled == true)
                row.put("system_app", app?.let { (it.flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM) != 0 } == true)
                row.put("version_name", info.versionName ?: "")
                row.put("version_code", if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong())
                row.put("launchable", pm.getLaunchIntentForPackage(pkg) != null)
            }
            arr.put(row)
        }
        return JSONObject()
            .put("mode", "SAMSUNG_CALL_ASSIST_PROBE")
            .put("default_dialer", telecom?.defaultDialerPackage ?: "")
            .put("sofia_is_default_dialer", telecom?.defaultDialerPackage == context.packageName)
            .put("packages", arr)
            .put("strategy", "Keep Samsung Phone as default so privileged Call Assist / Text call can own cellular RX/TX; Sofia supplies the AI brain separately.")
    }

    fun hasCallAssistant(context: Context): Boolean = packageInfo(context.packageManager, "com.samsung.android.callassistant") != null

    private fun packageInfo(pm: PackageManager, pkg: String): PackageInfo? = try {
        if (Build.VERSION.SDK_INT >= 33) {
            pm.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            pm.getPackageInfo(pkg, 0)
        }
    } catch (_: Throwable) {
        null
    }
}
