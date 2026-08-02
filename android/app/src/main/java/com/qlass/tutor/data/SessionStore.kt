package com.qlass.tutor.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "qlass_session")
private val TOKEN_KEY = stringPreferencesKey("access_token")

// Persists the student's JWT across app restarts so they aren't asked to
// re-enter an OTP every time they open the app — mirrors the web app's use
// of localStorage for the same token (see frontend/src/api.ts).
class SessionStore(private val context: Context) {
    val tokenFlow: Flow<String?> = context.dataStore.data.map { it[TOKEN_KEY] }

    suspend fun saveToken(token: String) {
        context.dataStore.edit { it[TOKEN_KEY] = token }
    }

    suspend fun clear() {
        context.dataStore.edit { it.remove(TOKEN_KEY) }
    }
}
