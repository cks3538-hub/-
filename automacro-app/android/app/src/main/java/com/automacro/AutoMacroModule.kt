package com.automacro

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.media.ToneGenerator
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.telephony.SmsManager
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.ReadableArray
import java.util.Locale
import java.util.UUID

class AutoMacroModule(private val ctx: ReactApplicationContext) : ReactContextBaseJavaModule(ctx) {
  override fun getName() = "AutoMacro"

  private val main = Handler(Looper.getMainLooper())
  private var tts: TextToSpeech? = null
  private var ttsReady = false
  private val ttsWaiting = HashMap<String, Promise>()

  private fun launch(i: Intent) { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }

  // ---- storage ----
  @ReactMethod fun loadState(p: Promise) = p.resolve(Prefs.state(ctx))
  @ReactMethod fun saveState(json: String, p: Promise) { Prefs.setState(ctx, json); p.resolve(true) }

  // ---- permissions & settings ----
  @ReactMethod fun status(p: Promise) {
    val m = Arguments.createMap()
    m.putBoolean("notificationAccess", NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(ctx.packageName))
    m.putBoolean("keepAlive", Prefs.keepAlive(ctx))
    m.putBoolean("keepAliveRunning", KeepAliveService.running)
    val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
    m.putBoolean("batteryUnrestricted", pm.isIgnoringBatteryOptimizations(ctx.packageName))
    val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
    m.putBoolean("exactAlarms", Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms())
    m.putInt("sdk", Build.VERSION.SDK_INT)
    p.resolve(m)
  }
  @ReactMethod fun openNotificationAccess() = launch(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
  @ReactMethod fun openAppSettings() = launch(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + ctx.packageName)))
  @ReactMethod fun requestBatteryUnrestricted() {
    try { launch(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + ctx.packageName))) }
    catch (e: Exception) { launch(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
  }
  @ReactMethod fun setKeepAlive(on: Boolean) {
    Prefs.setKeepAlive(ctx, on)
    if (on) KeepAliveService.start(ctx) else KeepAliveService.stop(ctx)
  }

  // ---- device info ----
  @ReactMethod fun getDevice(p: Promise) {
    val m = Arguments.createMap()
    val b = ctx.registerReceiver(null, IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED))
    if (b != null) {
      m.putInt("battery", b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) * 100 / b.getIntExtra(BatteryManager.EXTRA_SCALE, 100))
      val st = b.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
      m.putBoolean("charging", st == BatteryManager.BATTERY_STATUS_CHARGING || st == BatteryManager.BATTERY_STATUS_FULL)
    }
    val cm = ctx.getSystemService(ConnectivityManager::class.java)
    val caps = cm.getNetworkCapabilities(cm.activeNetwork)
    m.putBoolean("online", caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true)
    m.putBoolean("wifi", caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true)
    val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
    m.putBoolean("screenOn", pm.isInteractive)
    p.resolve(m)
  }
  @ReactMethod fun listApps(p: Promise) {
    val pm = ctx.packageManager
    val i = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
    val arr = Arguments.createArray()
    pm.queryIntentActivities(i, 0)
      .map { it.activityInfo.packageName to it.loadLabel(pm).toString() }
      .distinctBy { it.first }
      .sortedBy { it.second }
      .forEach { (pkg, label) -> arr.pushMap(Arguments.createMap().apply { putString("pkg", pkg); putString("label", label) }) }
    p.resolve(arr)
  }

  // ---- actions ----
  @ReactMethod fun notify(title: String, text: String) {
    val nm = ctx.getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(NotificationChannel("automacro_alerts", "매크로 알림", NotificationManager.IMPORTANCE_HIGH))
    val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
    val n = NotificationCompat.Builder(ctx, "automacro_alerts")
      .setSmallIcon(R.mipmap.ic_launcher).setContentTitle(title).setContentText(text)
      .setStyle(NotificationCompat.BigTextStyle().bigText(text))
      .setAutoCancel(true).setContentIntent(open).build()
    try { nm.notify((System.currentTimeMillis() % 100000).toInt(), n) } catch (e: SecurityException) {}
  }
  @ReactMethod fun toast(text: String, long: Boolean) {
    main.post { Toast.makeText(ctx, text, if (long) Toast.LENGTH_LONG else Toast.LENGTH_SHORT).show() }
  }
  @ReactMethod fun vibrate(pattern: ReadableArray) {
    val v = ctx.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
    val ms = LongArray(pattern.size() + 1)
    ms[0] = 0
    for (i in 0 until pattern.size()) ms[i + 1] = pattern.getDouble(i).toLong()
    if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createWaveform(ms, -1)) else @Suppress("DEPRECATION") v.vibrate(ms, -1)
  }
  @ReactMethod fun beep(ms: Int, times: Int, p: Promise) {
    Thread {
      try {
        val tg = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 90)
        repeat(times) { tg.startTone(ToneGenerator.TONE_PROP_BEEP2, ms); Thread.sleep(ms + 150L) }
        tg.release()
      } catch (e: Exception) {}
      p.resolve(true)
    }.start()
  }
  @ReactMethod fun speak(text: String, rate: Double, p: Promise) {
    main.post {
      val go = {
        val id = UUID.randomUUID().toString()
        ttsWaiting[id] = p
        tts?.setSpeechRate(rate.toFloat())
        tts?.speak(text, TextToSpeech.QUEUE_ADD, null, id)
      }
      if (tts != null && ttsReady) go()
      else {
        tts = TextToSpeech(ctx) { st ->
          ttsReady = st == TextToSpeech.SUCCESS
          if (!ttsReady) { p.resolve(false); return@TextToSpeech }
          tts?.language = Locale.KOREAN
          tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) { ttsWaiting.remove(id)?.resolve(true) }
            @Deprecated("") override fun onError(id: String?) { ttsWaiting.remove(id)?.resolve(false) }
          })
          go()
        }
      }
    }
  }
  @ReactMethod fun sendSms(number: String, text: String, p: Promise) {
    try {
      val sm = if (Build.VERSION.SDK_INT >= 31) ctx.getSystemService(SmsManager::class.java) else @Suppress("DEPRECATION") SmsManager.getDefault()
      val parts = sm.divideMessage(text)
      sm.sendMultipartTextMessage(number, null, parts, null, null)
      p.resolve(true)
    } catch (e: Exception) { p.reject("sms", e.message) }
  }
  @ReactMethod fun replyNotification(key: String, text: String, p: Promise) = p.resolve(NotificationListener.reply(ctx, key, text))
  @ReactMethod fun dismissNotification(key: String, p: Promise) = p.resolve(NotificationListener.dismiss(key))
  @ReactMethod fun launchApp(pkg: String, p: Promise) {
    val i = ctx.packageManager.getLaunchIntentForPackage(pkg)
    if (i == null) { p.resolve(false); return }
    try { launch(i); p.resolve(true) } catch (e: Exception) { p.resolve(false) }
  }
  @ReactMethod fun openUrl(url: String) { try { launch(Intent(Intent.ACTION_VIEW, Uri.parse(url))) } catch (e: Exception) {} }
  @ReactMethod fun copy(text: String) {
    main.post { (ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("AutoMacro", text)) }
  }

  // ---- alarms (time / interval triggers) ----
  @ReactMethod fun scheduleAlarm(id: String, atMs: Double) = AlarmReceiver.schedule(ctx, id, atMs.toLong())
  @ReactMethod fun cancelAlarm(id: String) = AlarmReceiver.cancel(ctx, id)

  override fun invalidate() { tts?.shutdown(); tts = null; super.invalidate() }
}
