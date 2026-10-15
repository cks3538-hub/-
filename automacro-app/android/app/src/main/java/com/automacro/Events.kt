package com.automacro

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log
import com.facebook.react.HeadlessJsTaskService

/** Hands a device event to the JS macro engine through the headless task service. */
object Events {
  fun send(ctx: Context, type: String, fill: Bundle.() -> Unit = {}) {
    val extras = Bundle().apply {
      putString("type", type)
      putDouble("at", System.currentTimeMillis().toDouble())
      fill()
    }
    val intent = Intent(ctx, MacroTaskService::class.java).putExtras(extras)
    try {
      ctx.startService(intent)
      HeadlessJsTaskService.acquireWakeLockNow(ctx)
    } catch (e: Exception) {
      // Background start limits: the always-on service lifts them.
      Log.w("AutoMacro", "could not start macro task for $type", e)
    }
  }
}
