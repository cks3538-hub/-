package com.automacro

import android.content.Context

object Prefs {
  private fun sp(ctx: Context) = ctx.getSharedPreferences("automacro", Context.MODE_PRIVATE)
  fun state(ctx: Context): String? = sp(ctx).getString("state", null)
  fun setState(ctx: Context, json: String) { sp(ctx).edit().putString("state", json).apply() }
  fun keepAlive(ctx: Context) = sp(ctx).getBoolean("keepAlive", false)
  fun setKeepAlive(ctx: Context, on: Boolean) { sp(ctx).edit().putBoolean("keepAlive", on).apply() }
}
