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
    // Keep the receiver alive until the Slack request finishes on OkHttp's thread.
    if (Prefs.slackUrl(ctx).isNotBlank()) {
      val pending = goAsync()
      Slack.send(ctx, "[SMS] $from\n$body") { _, _ -> pending.finish() }
    }
  }
}
