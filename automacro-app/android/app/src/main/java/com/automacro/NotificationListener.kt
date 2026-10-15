package com.automacro

import android.app.Notification
import android.app.RemoteInput
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

class NotificationListener : NotificationListenerService() {
  companion object {
    var instance: NotificationListener? = null
    private val replyActions = object : LinkedHashMap<String, Notification.Action>() {
      override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Notification.Action>?) = size > 60
    }
    private var lastSig = ""
    private var lastAt = 0L

    /** Sends [text] through the notification's own reply box (e.g. a KakaoTalk chat). */
    fun reply(ctx: Context, key: String, text: String): Boolean {
      val action = synchronized(replyActions) { replyActions[key] } ?: return false
      val inputs = action.remoteInputs ?: return false
      val intent = Intent()
      val results = Bundle()
      inputs.forEach { results.putCharSequence(it.resultKey, text) }
      RemoteInput.addResultsToIntent(inputs, intent, results)
      return try { action.actionIntent.send(ctx, 0, intent); true } catch (e: Exception) { false }
    }

    fun dismiss(key: String): Boolean {
      val l = instance ?: return false
      return try { l.cancelNotification(key); true } catch (e: Exception) { false }
    }
  }

  override fun onListenerConnected() { instance = this }
  override fun onListenerDisconnected() { if (instance === this) instance = null }

  override fun onNotificationPosted(sbn: StatusBarNotification) {
    if (sbn.packageName == packageName) return
    val n = sbn.notification ?: return
    if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
    if (n.flags and Notification.FLAG_ONGOING_EVENT != 0) return
    val ex = n.extras
    val title = ex.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
    val text = (ex.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: ex.getCharSequence(Notification.EXTRA_TEXT))?.toString() ?: ""
    if (title.isEmpty() && text.isEmpty()) return

    // Messengers re-post the same notification on every update; skip quick duplicates.
    val sig = sbn.packageName + "|" + title + "|" + text
    val now = System.currentTimeMillis()
    if (sig == lastSig && now - lastAt < 3000) return
    lastSig = sig; lastAt = now

    val replyAction = n.actions?.firstOrNull { a -> a.remoteInputs?.isNotEmpty() == true }
    if (replyAction != null) synchronized(replyActions) { replyActions[sbn.key] = replyAction }

    val appName = try {
      packageManager.getApplicationLabel(packageManager.getApplicationInfo(sbn.packageName, 0)).toString()
    } catch (e: Exception) { sbn.packageName }

    Events.send(this, "notification") {
      putString("pkg", sbn.packageName)
      putString("app", appName)
      putString("title", title)
      putString("text", text)
      putString("key", sbn.key)
      putBoolean("canReply", replyAction != null)
    }
  }
}
