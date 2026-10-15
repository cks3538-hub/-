package com.automacro

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony

class SmsReceiver : BroadcastReceiver() {
  override fun onReceive(ctx: Context, intent: Intent) {
    if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
    val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
    if (parts.isEmpty()) return
    val from = parts[0].originatingAddress ?: ""
    val body = parts.joinToString("") { it.messageBody ?: "" }
    Events.send(ctx, "sms") {
      putString("from", from)
      putString("body", body)
    }
  }
}
