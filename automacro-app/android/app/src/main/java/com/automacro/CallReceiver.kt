package com.automacro

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager

class CallReceiver : BroadcastReceiver() {
  companion object {
    private var last = TelephonyManager.EXTRA_STATE_IDLE
    private var number = ""
    private var sentRinging = false
  }

  @Suppress("DEPRECATION")
  override fun onReceive(ctx: Context, intent: Intent) {
    val state = intent.getStringExtra(TelephonyManager.EXTRA_STATE) ?: return
    // Android delivers PHONE_STATE twice (with and without the number); wait for the number.
    val num = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER)
    when (state) {
      TelephonyManager.EXTRA_STATE_RINGING -> {
        if (num != null) number = num
        if (!sentRinging && num != null) {
          sentRinging = true
          Events.send(ctx, "call") { putString("state", "ringing"); putString("number", number) }
        }
      }
      TelephonyManager.EXTRA_STATE_OFFHOOK -> if (last != state) {
        if (last == TelephonyManager.EXTRA_STATE_RINGING) Events.send(ctx, "call") { putString("state", "answered"); putString("number", number) }
      }
      TelephonyManager.EXTRA_STATE_IDLE -> if (last != state) {
        val missed = last == TelephonyManager.EXTRA_STATE_RINGING
        Events.send(ctx, "call") { putString("state", if (missed) "missed" else "ended"); putString("number", number) }
        number = ""; sentRinging = false
      }
    }
    last = state
  }
}
