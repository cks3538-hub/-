package com.automacro

import android.app.AlarmManager
import android.app.KeyguardManager
import android.bluetooth.BluetoothManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioDeviceInfo
import android.provider.AlarmClock
import android.telecom.TelecomManager
import android.telephony.TelephonyManager
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
    m.putBoolean("writeSettings", Settings.System.canWrite(ctx))
    m.putBoolean("dndAccess", (ctx.getSystemService(NotificationManager::class.java)).isNotificationPolicyAccessGranted)
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
    val audio = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    m.putBoolean("musicActive", audio.isMusicActive)
    m.putString("ringer", when (audio.ringerMode) { AudioManager.RINGER_MODE_SILENT -> "silent"; AudioManager.RINGER_MODE_VIBRATE -> "vibrate"; else -> "normal" })
    m.putBoolean("headphones", audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any {
      it.type in listOf(AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP, AudioDeviceInfo.TYPE_USB_HEADSET)
    })
    m.putBoolean("airplane", Settings.Global.getInt(ctx.contentResolver, Settings.Global.AIRPLANE_MODE_ON, 0) == 1)
    m.putBoolean("locked", (ctx.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked)
    m.putBoolean("bluetoothOn", try { (ctx.getSystemService(BluetoothManager::class.java)).adapter?.isEnabled == true } catch (e: Exception) { false })
    m.putString("call", try {
      @Suppress("DEPRECATION")
      when ((ctx.getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager).callState) { TelephonyManager.CALL_STATE_RINGING -> "ringing"; TelephonyManager.CALL_STATE_OFFHOOK -> "offhook"; else -> "idle" }
    } catch (e: Exception) { "unknown" })
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

  @ReactMethod fun openWriteSettings() = launch(Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:" + ctx.packageName)))
  @ReactMethod fun openDndAccess() = launch(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
  @ReactMethod fun setShake(on: Boolean) {
    Prefs.setShake(ctx, on)
    main.post { KeepAliveService.instance?.setShake(on) }
  }

  // ---- Slack forwarding ----
  @ReactMethod fun getSlackUrl(p: Promise) = p.resolve(Prefs.slackUrl(ctx))
  @ReactMethod fun setSlackUrl(url: String, p: Promise) { Prefs.setSlackUrl(ctx, url); p.resolve(true) }
  @ReactMethod fun testSlack(p: Promise) {
    if (Prefs.slackUrl(ctx).isBlank()) { p.resolve(-1); return }
    Slack.send(ctx, "[오토매크로] Slack 연결 테스트입니다.") { _, code -> p.resolve(code) }
  }

  // ---- device control ----
  private var torchOn = false
  @ReactMethod fun torch(mode: String, p: Promise) {
    try {
      val cm = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
      val id = cm.cameraIdList.firstOrNull { cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
      if (id == null) { p.resolve(false); return }
      torchOn = when (mode) { "on" -> true; "off" -> false; else -> !torchOn }
      cm.setTorchMode(id, torchOn)
      p.resolve(true)
    } catch (e: Exception) { p.resolve(false) }
  }
  @ReactMethod fun makeCall(number: String, p: Promise) {
    try { launch(Intent(Intent.ACTION_CALL, Uri.parse("tel:" + Uri.encode(number)))); p.resolve(true) } catch (e: Exception) { p.reject("call", e.message) }
  }
  @ReactMethod fun endCall(p: Promise) {
    try { @Suppress("DEPRECATION") p.resolve((ctx.getSystemService(Context.TELECOM_SERVICE) as TelecomManager).endCall()) } catch (e: Exception) { p.reject("call", e.message) }
  }
  @ReactMethod fun answerCall(p: Promise) {
    try { @Suppress("DEPRECATION") (ctx.getSystemService(Context.TELECOM_SERVICE) as TelecomManager).acceptRingingCall(); p.resolve(true) } catch (e: Exception) { p.reject("call", e.message) }
  }
  @ReactMethod fun setVolume(stream: String, percent: Double, p: Promise) {
    try {
      val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
      val st = when (stream) { "ring" -> AudioManager.STREAM_RING; "notification" -> AudioManager.STREAM_NOTIFICATION; "alarm" -> AudioManager.STREAM_ALARM; else -> AudioManager.STREAM_MUSIC }
      val max = am.getStreamMaxVolume(st)
      am.setStreamVolume(st, Math.round(max * percent.coerceIn(0.0, 100.0) / 100.0).toInt(), 0)
      p.resolve(true)
    } catch (e: Exception) { p.reject("volume", e.message) }
  }
  @ReactMethod fun setRinger(mode: String, p: Promise) {
    try {
      val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
      am.ringerMode = when (mode) { "silent" -> AudioManager.RINGER_MODE_SILENT; "vibrate" -> AudioManager.RINGER_MODE_VIBRATE; else -> AudioManager.RINGER_MODE_NORMAL }
      p.resolve(true)
    } catch (e: Exception) { p.reject("ringer", e.message) }
  }
  @ReactMethod fun setBrightness(percent: Double, p: Promise) {
    if (!Settings.System.canWrite(ctx)) { p.reject("settings", "WRITE_SETTINGS not granted"); return }
    Settings.System.putInt(ctx.contentResolver, Settings.System.SCREEN_BRIGHTNESS_MODE, Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL)
    Settings.System.putInt(ctx.contentResolver, Settings.System.SCREEN_BRIGHTNESS, Math.round(255 * percent.coerceIn(1.0, 100.0) / 100.0).toInt())
    p.resolve(true)
  }
  @ReactMethod fun setAlarmClock(hour: Int, minute: Int, label: String) {
    launch(Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, hour).putExtra(AlarmClock.EXTRA_MINUTES, minute)
      .putExtra(AlarmClock.EXTRA_MESSAGE, label).putExtra(AlarmClock.EXTRA_SKIP_UI, true))
  }
  @ReactMethod fun pressNotificationButton(key: String, label: String, p: Promise) = p.resolve(NotificationListener.press(ctx, key, label))
  @ReactMethod fun clearNotifications(pkg: String, p: Promise) = p.resolve(NotificationListener.clear(pkg))
  @ReactMethod fun hasNotification(pkg: String, text: String, p: Promise) = p.resolve(NotificationListener.present(pkg, text))

  // ---- alarms (time / interval triggers) ----
  @ReactMethod fun scheduleAlarm(id: String, atMs: Double) = AlarmReceiver.schedule(ctx, id, atMs.toLong())
  @ReactMethod fun cancelAlarm(id: String) = AlarmReceiver.cancel(ctx, id)

  override fun invalidate() { tts?.shutdown(); tts = null; super.invalidate() }
}
