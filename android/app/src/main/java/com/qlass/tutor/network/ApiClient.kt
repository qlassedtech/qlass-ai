package com.qlass.tutor.network

import com.google.gson.Gson
import com.qlass.tutor.BuildConfig
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.HttpException
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {
    val service: ApiService by lazy {
        val logging = HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
        val client = OkHttpClient.Builder()
            .addInterceptor(logging)
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
        Retrofit.Builder()
            .baseUrl(BuildConfig.API_BASE_URL)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }
}

private data class ErrorBody(val detail: String?)

// The backend returns {"detail": "..."} on every 4xx (FastAPI's default shape) —
// Retrofit only exposes that as a raw error body string, so this pulls the
// human-readable message back out for display instead of a generic HTTP code.
fun HttpException.detailMessage(): String {
    val raw = response()?.errorBody()?.string()
    val parsed = raw?.let { runCatching { Gson().fromJson(it, ErrorBody::class.java) }.getOrNull() }
    return parsed?.detail ?: "Something went wrong (${code()})"
}
