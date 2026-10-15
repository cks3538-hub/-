package com.automacro

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

class AlarmReceiver : BroadcastReceiver() {
  companion object {
    private fun pending(ctx: Context, id: String): PendingIntent {
      val i = Intent(ctx, AlarmReceiver::class.java).setAction("com.automacro.ALARM.$id").putExtra("id", id)
      return PendingIntent.getBroadcast(ctx, id.hashCode(), i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    fun schedule(ctx: Context, id: String, atMs: Long) {
      val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
      val pi = pending(ctx, id)
      val exact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()
      if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
      else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
    }

    fun cancel(ctx: Context, id: String) {
      val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
      am.cancel(pending(ctx, id))
    }
  }

  override fun onReceive(ctx: Context, intent: Intent) {
    val id = intent.getStringExtra("id") ?: return
    Events.send(ctx, "alarm") { putString("id", id) }
  }
}
