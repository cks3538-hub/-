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
  }

  private var lastLevel = -1
  private var netCb: ConnectivityManager.NetworkCallback? = null
  private var netReady = false

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
    ContextCompat.registerReceiver(this, receiver, f, ContextCompat.RECEIVER_NOT_EXPORTED)

    val cm = getSystemService(ConnectivityManager::class.java)
    netCb = object : ConnectivityManager.NetworkCallback() {
      override fun onAvailable(network: Network) { if (netReady) Events.send(this@KeepAliveService, "network") { putString("state", "online") } }
      override fun onLost(network: Network) { Events.send(this@KeepAliveService, "network") { putString("state", "offline") } }
    }
    try { cm.registerDefaultNetworkCallback(netCb!!) } catch (e: Exception) {}
    // The first onAvailable is the current network, not a change.
    android.os.Handler(mainLooper).postDelayed({ netReady = true }, 3000)
  }

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

  override fun onDestroy() {
    running = false
    try { unregisterReceiver(receiver) } catch (e: Exception) {}
    try { netCb?.let { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it) } } catch (e: Exception) {}
    super.onDestroy()
  }
}
