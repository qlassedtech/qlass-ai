package com.qlass.tutor.push

import android.content.Context
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.qlass.tutor.BuildConfig

/**
 * Manual FirebaseOptions init instead of the google-services Gradle plugin
 * (which requires a committed google-services.json and fails the build
 * without one). Until a real Firebase project's values are dropped into
 * local.properties (see app/build.gradle.kts), FCM_API_KEY etc. are empty
 * and this is a deliberate no-op — push notifications simply don't fire,
 * nothing crashes.
 */
object FirebaseInit {
    fun initIfConfigured(context: Context) {
        if (BuildConfig.FCM_API_KEY.isBlank() || BuildConfig.FCM_APP_ID.isBlank() || BuildConfig.FCM_PROJECT_ID.isBlank()) {
            return
        }
        if (FirebaseApp.getApps(context).isNotEmpty()) return
        try {
            val options = FirebaseOptions.Builder()
                .setApiKey(BuildConfig.FCM_API_KEY)
                .setApplicationId(BuildConfig.FCM_APP_ID)
                .setProjectId(BuildConfig.FCM_PROJECT_ID)
                .setGcmSenderId(BuildConfig.FCM_SENDER_ID)
                .build()
            FirebaseApp.initializeApp(context, options)
        } catch (_: Exception) {
            // Push is a nice-to-have, never worth crashing app startup over.
        }
    }

    fun isConfigured(): Boolean =
        BuildConfig.FCM_API_KEY.isNotBlank() && BuildConfig.FCM_APP_ID.isNotBlank() && BuildConfig.FCM_PROJECT_ID.isNotBlank()
}
