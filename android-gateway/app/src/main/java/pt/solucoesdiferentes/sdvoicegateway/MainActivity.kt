package pt.solucoesdiferentes.sdvoicegateway

import android.Manifest
import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.telecom.TelecomManager
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat

class MainActivity : Activity() {
    private val prefs by lazy { getSharedPreferences("gateway", MODE_PRIVATE) }
    private lateinit var status: TextView; private lateinit var diagnostics: TextView; private lateinit var techPanel: LinearLayout
    private fun dp(v:Int)=(v*resources.displayMetrics.density).toInt()
    private fun text(s:String,size:Float,bold:Boolean=false)=TextView(this).apply{text=s;textSize=size;setTextColor(Color.rgb(236,240,255));if(bold)setTypeface(typeface,Typeface.BOLD)}
    private fun button(label:String)=Button(this).apply{text=label;isAllCaps=false;textSize=16f;setPadding(dp(14),dp(10),dp(14),dp(10))}
    private fun card()=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;setPadding(dp(18),dp(16),dp(18),dp(16));setBackgroundColor(Color.rgb(25,29,46))}
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;setPadding(dp(18),dp(28),dp(18),dp(30));setBackgroundColor(Color.rgb(10,13,24))}
        root.addView(text("SOFIA",30f,true));root.addView(text("Assistente Comercial IA · SD Voice",14f).apply{setTextColor(Color.rgb(151,164,207))})
        root.addView(Space(this).apply{minimumHeight=dp(18)})
        val hero=card();hero.addView(text("●  Sofia pronta",20f,true).apply{setTextColor(Color.rgb(117,235,180))});hero.addView(text("Samsung Gateway · Build 38 TX",13f).apply{setTextColor(Color.LTGRAY)});hero.addView(text("A Sofia liga pela SIM deste telemóvel. O laboratório técnico continua disponível em baixo.",14f).apply{setPadding(0,dp(10),0,0)});root.addView(hero)
        root.addView(Space(this).apply{minimumHeight=dp(14)})
        val callCard=card();callCard.addView(text("Nova chamada com Sofia",19f,true));val testNumber=EditText(this).apply{hint="Número do cliente";setHintTextColor(Color.GRAY);setTextColor(Color.WHITE);textSize=20f;inputType=InputType.TYPE_CLASS_PHONE};callCard.addView(testNumber)
        val testCall=button("📞  Ligar com Sofia");callCard.addView(testCall);status=text("Gateway parado",13f).apply{setPadding(0,dp(10),0,0);setTextColor(Color.LTGRAY)};callCard.addView(status);root.addView(callCard)
        root.addView(Space(this).apply{minimumHeight=dp(14)})
        val techToggle=button("⚙  Laboratório técnico / diagnóstico");root.addView(techToggle)
        techPanel=card().apply{visibility=View.GONE}
        val api=EditText(this).apply{hint="API";setText(prefs.getString("api_url",""));setTextColor(Color.WHITE);setHintTextColor(Color.GRAY)}
        val key=EditText(this).apply{hint="Device key";setText(prefs.getString("device_key","samsung-01"));setTextColor(Color.WHITE);setHintTextColor(Color.GRAY)}
        val token=EditText(this).apply{hint="Device token";setText(prefs.getString("device_token",""));setTextColor(Color.WHITE);setHintTextColor(Color.GRAY);inputType=InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD}
        val save=button("Guardar e iniciar gateway — Build 38");val dialer=button("Definir como app de telefone");val refresh=button("Ver diagnóstico Build 38 — TX")
        diagnostics=text("Diagnóstico Build 38: ainda sem dados",12f).apply{setTextIsSelectable(true);setPadding(0,dp(12),0,0)}
        listOf(text("Configuração técnica",18f,true),api,key,token,save,dialer,refresh,diagnostics).forEach{techPanel.addView(it)};root.addView(techPanel)
        techToggle.setOnClickListener{techPanel.visibility=if(techPanel.visibility==View.VISIBLE)View.GONE else View.VISIBLE}
        save.setOnClickListener{prefs.edit().putString("api_url",api.text.toString().trimEnd('/')).putString("device_key",key.text.toString()).putString("device_token",token.text.toString()).apply();startGatewayWhenPermitted()}
        dialer.setOnClickListener{requestDialerRole()};testCall.setOnClickListener{placeTestCall(testNumber.text.toString())};refresh.setOnClickListener{showDiagnostics()}
        setContentView(ScrollView(this).apply{addView(root)})
    }
    override fun onResume(){super.onResume();showDiagnostics()}
    private fun showDiagnostics(){val names=listOf("CALL","STATE","DEVICE_CAPABILITY","STOCK_AUDIO_ROUTE_CAPABILITY","TELEPHONY_PCM_CAPABILITY","TELEPHONY_TX_CAPABILITY","AUDIO_CAPABILITY","DEVICE_CAPABILITY_HTTP_ERROR","STOCK_AUDIO_ROUTE_CAPABILITY_HTTP_ERROR","TELEPHONY_PCM_CAPABILITY_HTTP_ERROR","TELEPHONY_TX_CAPABILITY_HTTP_ERROR","AUDIO_CAPABILITY_HTTP_ERROR");val out=names.mapNotNull{n->prefs.getString("diag_$n",null)?.let{"$n:\n$it"}}.joinToString("\n\n");diagnostics.text=if(out.isBlank())"Diagnóstico Build 38: ainda sem dados" else "DIAGNÓSTICO GSM — BUILD 38 TELEPHONY TX\n\n$out"}
    private fun placeTestCall(raw:String){val number=raw.trim().replace(" ","");if(number.isBlank()){status.text="Introduz o número do cliente";return};if(ActivityCompat.checkSelfPermission(this,Manifest.permission.CALL_PHONE)!=PackageManager.PERMISSION_GRANTED){status.text="Falta autorização Telefone";ActivityCompat.requestPermissions(this,arrayOf(Manifest.permission.CALL_PHONE),100);return};try{status.text="Sofia está a iniciar a chamada…";getSystemService(TelecomManager::class.java).placeCall(Uri.fromParts("tel",number,null),Bundle())}catch(t:Throwable){status.text="Erro GSM: ${t.javaClass.simpleName}: ${t.message?:"sem detalhe"}"}}
    private fun requestDialerRole(){try{if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.Q){val rm=getSystemService(RoleManager::class.java);if(rm!=null&&rm.isRoleAvailable(RoleManager.ROLE_DIALER)){if(rm.isRoleHeld(RoleManager.ROLE_DIALER))status.text="Gateway já controla as chamadas" else startActivityForResult(rm.createRequestRoleIntent(RoleManager.ROLE_DIALER),200)}else startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))}else startActivityForResult(Intent(TelecomManager.ACTION_CHANGE_DEFAULT_DIALER).apply{putExtra(TelecomManager.EXTRA_CHANGE_DEFAULT_DIALER_PACKAGE_NAME,packageName)},200)}catch(t:Throwable){status.text="Erro ao pedir função Telefone: ${t.message?:"sem detalhe"}"}}
    override fun onActivityResult(requestCode:Int,resultCode:Int,data:Intent?){super.onActivityResult(requestCode,resultCode,data);if(requestCode==200){val rm=if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.Q)getSystemService(RoleManager::class.java)else null;val held=Build.VERSION.SDK_INT>=Build.VERSION_CODES.Q&&rm?.isRoleHeld(RoleManager.ROLE_DIALER)==true;status.text=if(held||resultCode==RESULT_OK)"Sofia ligada ao sistema de chamadas ✓" else "Função de app de telefone não atribuída"}}
    private fun requiredPermissions()=arrayOf(Manifest.permission.CALL_PHONE,Manifest.permission.READ_PHONE_STATE,Manifest.permission.RECORD_AUDIO)
    private fun startGatewayWhenPermitted(){val missing=requiredPermissions().filter{ActivityCompat.checkSelfPermission(this,it)!=PackageManager.PERMISSION_GRANTED};if(missing.isNotEmpty()){status.text="A aguardar permissões…";ActivityCompat.requestPermissions(this,missing.toTypedArray(),100);return};startGatewaySafely()}
    private fun startGatewaySafely(){try{ContextCompat.startForegroundService(this,Intent(this,GatewayService::class.java));status.text="Sofia online · Gateway Build 38 TX ✓"}catch(t:Throwable){status.text="Erro ao iniciar: ${t.message?:"sem detalhe"}"}}
    override fun onRequestPermissionsResult(requestCode:Int,permissions:Array<out String>,grantResults:IntArray){super.onRequestPermissionsResult(requestCode,permissions,grantResults);if(requestCode==100&&grantResults.all{it==PackageManager.PERMISSION_GRANTED})startGatewaySafely()}
}
