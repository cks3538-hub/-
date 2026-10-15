package com.automacro

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class BootReceiver : BroadcastReceiver() {
  override fun onReceive(ctx: Context, intent: Intent) {
    if (Prefs.keepAlive(ctx)) KeepAliveService.start(ctx)
    Events.send(ctx, "boot")
  }
}
