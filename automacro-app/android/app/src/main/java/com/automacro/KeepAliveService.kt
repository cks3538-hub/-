package com.automacro

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.bluetooth.BluetoothDevice
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.AudioManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat

/** Foreground service that keeps the engine alive and listens for device-state broadcasts. */
class KeepAliveService : Service() {
  companion object {
    const val CHANNEL = "automacro_service"
    fun start(ctx: Context) {
      try { ContextCompat.startForegroundService(ctx, Intent(ctx, KeepAliveService::class.java)) } catch (e: Exception) {}
    }
    fun stop(ctx: Context) { ctx.stopService(Intent(ctx, KeepAliveService::class.java)) }
    var running = false
    var instance: KeepAliveService? = null
  }

  private var sensors: SensorManager? = null
  private var lastShake = 0L
  private val shakeListener = object : SensorEventListener {
    override fun onSensorChanged(e: SensorEvent) {
      val g = Math.sqrt((e.values[0] * e.values[0] + e.values[1] * e.values[1] + e.values[2] * e.values[2]).toDouble()) - SensorManager.GRAVITY_EARTH
      val now = System.currentTimeMillis()
      if (g > 12 && now - lastShake > 1500) {
        lastShake = now
        Events.send(this@KeepAliveService, "shake") { putDouble("force", g) }
      }
    }
    override fun onAccuracyChanged(s: Sensor?, a: Int) {}
  }

  /** Shake detection costs battery, so it only runs while a shake macro is enabled. */
  fun setShake(on: Boolean) {
    val sm = sensors ?: getSystemService(SensorManager::class.java).also { sensors = it }
    sm.unregisterListener(shakeListener)
    if (on) sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let { sm.registerListener(shakeListener, it, SensorManager.SENSOR_DELAY_UI) }
  }

  private var lastLevel = -1
  private var netCb: ConnectivityManager.NetworkCallback? = null
  private var netReady = false

  private val pkgReceiver = object : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
      if (i.getBooleanExtra(Intent.EXTRA_REPLACING, false)) return
      val pkg = i.data?.schemeSpecificPart ?: return
      val label = try { packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString() } catch (e: Exception) { pkg }
      Events.send(ctx, "app_install") { putString("state", if (i.action == Intent.ACTION_PACKAGE_ADDED) "installed" else "removed"); putString("pkg", pkg); putString("app", label) }
    }
  }
  private var onWifi: Boolean? = null

  private val receiver = object : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
      when (i.action) {
        Intent.ACTION_POWER_CONNECTED -> Events.send(ctx, "power") { putString("state", "connected") }
        Intent.ACTION_POWER_DISCONNECTED -> Events.send(ctx, "power") { putString("state", "disconnected") }
        Intent.ACTION_SCREEN_ON -> Events.send(ctx, "screen") { putString("state", "on") }
        Intent.ACTION_SCREEN_OFF -> Events.send(ctx, "screen") { putString("state", "off") }
        Intent.ACTION_USER_PRESENT -> Events.send(ctx, "screen") { putString("state", "unlock") }
        Intent.ACTION_HEADSET_PLUG -> if (!isInitialStickyBroadcast) {
          val st = if (i.getIntExtra("state", 0) == 1) "plugged" else "unplugged"
          Events.send(ctx, "headset") { putString("state", st) }
        }
        Intent.ACTION_AIRPLANE_MODE_CHANGED -> Events.send(ctx, "airplane") { putString("state", if (i.getBooleanExtra("state", false)) "on" else "off") }
        AudioManager.RINGER_MODE_CHANGED_ACTION -> if (!isInitialStickyBroadcast) {
          val m = when (i.getIntExtra(AudioManager.EXTRA_RINGER_MODE, -1)) { AudioManager.RINGER_MODE_SILENT -> "silent"; AudioManager.RINGER_MODE_VIBRATE -> "vibrate"; else -> "normal" }
          Events.send(ctx, "ringer") { putString("state", m) }
        }
        BluetoothDevice.ACTION_ACL_CONNECTED, BluetoothDevice.ACTION_ACL_DISCONNECTED -> {
          @Suppress("DEPRECATION") val d: BluetoothDevice? = i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
          val name = try { d?.name ?: "" } catch (e: SecurityException) { "" }
          Events.send(ctx, "bluetooth") { putString("state", if (i.action == BluetoothDevice.ACTION_ACL_CONNECTED) "connected" else "disconnected"); putString("device", name) }
        }
        Intent.ACTION_BATTERY_CHANGED -> {
          val lv = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) * 100 / i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
          val prev = lastLevel
          lastLevel = lv
          if (prev >= 0 && prev != lv) Events.send(ctx, "battery") { putInt("level", lv); putInt("prev", prev) }
        }
      }
    }
  }

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onCreate() {
    super.onCreate()
    val nm = getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= 26) nm.createNotificationChannel(NotificationChannel(CHANNEL, "매크로 엔진 실행 중", NotificationManager.IMPORTANCE_MIN))
    val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
    val n: Notification = NotificationCompat.Builder(this, CHANNEL)
      .setSmallIcon(R.mipmap.ic_launcher)
      .setContentTitle("오토매크로 실행 중")
      .setContentText("문자·알림·전원 이벤트를 감지하고 있습니다")
      .setOngoing(true)
      .setContentIntent(open)
      .build()
    val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
    ServiceCompat.startForeground(this, 4401, n, type)
    running = true

    val f = IntentFilter().apply {
      addAction(Intent.ACTION_POWER_CONNECTED); addAction(Intent.ACTION_POWER_DISCONNECTED)
      addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_USER_PRESENT)
      addAction(Intent.ACTION_HEADSET_PLUG); addAction(Intent.ACTION_BATTERY_CHANGED)
    }
    f.addAction(Intent.ACTION_AIRPLANE_MODE_CHANGED); f.addAction(AudioManager.RINGER_MODE_CHANGED_ACTION)
    f.addAction(BluetoothDevice.ACTION_ACL_CONNECTED); f.addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED)
    ContextCompat.registerReceiver(this, receiver, f, ContextCompat.RECEIVER_NOT_EXPORTED)
    val pf = IntentFilter().apply { addAction(Intent.ACTION_PACKAGE_ADDED); addAction(Intent.ACTION_PACKAGE_FULLY_REMOVED); addDataScheme("package") }
    ContextCompat.registerReceiver(this, pkgReceiver, pf, ContextCompat.RECEIVER_NOT_EXPORTED)
    instance = this
    setShake(Prefs.shake(this))

    val cm = getSystemService(ConnectivityManager::class.java)
    netCb = object : ConnectivityManager.NetworkCallback() {
      override fun onAvailable(network: Network) { if (netReady) Events.send(this@KeepAliveService, "network") { putString("state", "online") } }
      override fun onLost(network: Network) {
        Events.send(this@KeepAliveService, "network") { putString("state", "offline") }
        if (onWifi == true) { onWifi = false; Events.send(this@KeepAliveService, "wifi") { putString("state", "disconnected") } }
      }
      override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
        val w = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
        val prev = onWifi
        onWifi = w
        if (prev != null && prev != w) Events.send(this@KeepAliveService, "wifi") { putString("state", if (w) "connected" else "disconnected") }
      }
    }
    try { cm.registerDefaultNetworkCallback(netCb!!) } catch (e: Exception) {}
    // The first onAvailable is the current network, not a change.
    android.os.Handler(mainLooper).postDelayed({ netReady = true }, 3000)
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

  override fun onDestroy() {
    running = false
    instance = null
    sensors?.unregisterListener(shakeListener)
    try { unregisterReceiver(receiver) } catch (e: Exception) {}
    try { unregisterReceiver(pkgReceiver) } catch (e: Exception) {}
    try { netCb?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) } } catch (e: Exception) {}
    super.onDestroy()
  }
}
