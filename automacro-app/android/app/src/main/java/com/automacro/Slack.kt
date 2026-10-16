package com.automacro

import android.content.Context
import android.util.Log
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Forwards SMS / KakaoTalk text to the Slack Incoming Webhook saved in settings. */
object Slack {
  private val client = OkHttpClient.Builder()
    .connectTimeout(10, TimeUnit.SECONDS)
    .writeTimeout(10, TimeUnit.SECONDS)
    .readTimeout(15, TimeUnit.SECONDS)
    .build()
  private val JSON = "application/json; charset=utf-8".toMediaType()

  /**
   * Posts {"text": text} on OkHttp's background dispatcher. Skips when no URL is saved.
   * [done] runs after the request finishes (success or failure), or right away when skipped.
   */
  fun send(ctx: Context, text: String, done: ((ok: Boolean, code: Int) -> Unit)? = null) {
    val url = Prefs.slackUrl(ctx).trim()
    if (url.isEmpty()) { done?.invoke(false, 0); return }
    val body = JSONObject().put("text", text).toString().toRequestBody(JSON)
    val req = try { Request.Builder().url(url).post(body).build() } catch (e: IllegalArgumentException) {
      Log.w("AutoMacro", "invalid Slack webhook URL")
      done?.invoke(false, 0); return
    }
    client.newCall(req).enqueue(object : Callback {
      override fun onFailure(call: Call, e: IOException) {
        Log.w("AutoMacro", "Slack send failed: ${e.message}")
        done?.invoke(false, 0)
      }
      override fun onResponse(call: Call, response: Response) {
        response.use { done?.invoke(it.isSuccessful, it.code) }
      }
    })
  }
}
