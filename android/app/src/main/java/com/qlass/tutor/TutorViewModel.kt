package com.qlass.tutor

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.qlass.tutor.data.SessionStore
import com.qlass.tutor.network.ApiClient
import com.qlass.tutor.network.ChatHistoryEntry
import com.qlass.tutor.network.CheckPhoneRequest
import com.qlass.tutor.network.CreditHistoryEntry
import com.qlass.tutor.network.DeviceTokenRequest
import com.qlass.tutor.network.ProgressResponse
import com.qlass.tutor.network.SendMessageResponse
import com.qlass.tutor.network.VerifyOtpRequest
import com.qlass.tutor.network.detailMessage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import retrofit2.HttpException
import java.io.File

enum class AuthStage { PHONE_ENTRY, AWAITING_OTP, PASSWORD_ONLY, PARENT_ONLY, CHECKING }

enum class Tab { CHAT, PROGRESS }

data class ChatMessage(val role: String, val text: String)

data class UiState(
    val authStage: AuthStage = AuthStage.PHONE_ENTRY,
    val phone: String = "",
    val loading: Boolean = false,
    val error: String? = null,
    val loggedIn: Boolean = false,
    val studentName: String? = null,
    val creditBalance: Double? = null,
    val messages: List<ChatMessage> = emptyList(),
    val sending: Boolean = false,
    val sendingLabel: String = "Thinking…",
    val recording: Boolean = false,
    val selectedTab: Tab = Tab.CHAT,
    val progress: ProgressResponse? = null,
    val creditHistory: List<CreditHistoryEntry> = emptyList(),
    val progressLoading: Boolean = false,
)

class TutorViewModel(private val sessionStore: SessionStore) : ViewModel() {
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var token: String? = null

    init {
        viewModelScope.launch {
            val saved = sessionStore.tokenFlow.first()
            if (saved != null) {
                token = saved
                _state.value = _state.value.copy(loggedIn = true)
                loadHistory()
                loadMe()
            }
        }
    }

    fun onPhoneChanged(value: String) {
        _state.value = _state.value.copy(phone = value, error = null)
    }

    fun submitPhone() {
        val phone = _state.value.phone.trim()
        if (phone.length < 10) {
            _state.value = _state.value.copy(error = "Enter a valid phone number")
            return
        }
        _state.value = _state.value.copy(loading = true, error = null, authStage = AuthStage.CHECKING)
        viewModelScope.launch {
            try {
                val check = ApiClient.service.checkPhone(CheckPhoneRequest(phone))
                when (check.login_type) {
                    "otp" -> {
                        ApiClient.service.requestOtp(CheckPhoneRequest(phone))
                        _state.value = _state.value.copy(loading = false, authStage = AuthStage.AWAITING_OTP)
                    }
                    "password" -> _state.value = _state.value.copy(loading = false, authStage = AuthStage.PASSWORD_ONLY)
                    else -> _state.value = _state.value.copy(loading = false, authStage = AuthStage.PARENT_ONLY)
                }
            } catch (e: HttpException) {
                _state.value = _state.value.copy(loading = false, authStage = AuthStage.PHONE_ENTRY, error = e.detailMessage())
            } catch (e: Exception) {
                _state.value = _state.value.copy(loading = false, authStage = AuthStage.PHONE_ENTRY, error = "Couldn't reach the server — check your connection")
            }
        }
    }

    fun verifyOtp(otp: String) {
        _state.value = _state.value.copy(loading = true, error = null)
        viewModelScope.launch {
            try {
                val result = ApiClient.service.verifyOtp(VerifyOtpRequest(phone = _state.value.phone.trim(), otp = otp))
                token = result.access_token
                sessionStore.saveToken(result.access_token)
                _state.value = _state.value.copy(
                    loading = false, loggedIn = true,
                    studentName = result.student.name, creditBalance = result.student.credit_balance,
                )
                loadHistory()
            } catch (e: HttpException) {
                _state.value = _state.value.copy(loading = false, error = e.detailMessage())
            } catch (e: Exception) {
                _state.value = _state.value.copy(loading = false, error = "Couldn't reach the server — check your connection")
            }
        }
    }

    private fun loadMe() {
        val auth = token ?: return
        viewModelScope.launch {
            try {
                val me = ApiClient.service.me("Bearer $auth")
                _state.value = _state.value.copy(studentName = me.name, creditBalance = me.credit_balance)
            } catch (_: Exception) {
                // Chat/progress still work from cache; balance just stays unknown until a message succeeds.
            }
        }
    }

    private fun loadHistory() {
        val auth = token ?: return
        viewModelScope.launch {
            try {
                val history: List<ChatHistoryEntry> = ApiClient.service.chatHistory("Bearer $auth")
                _state.value = _state.value.copy(messages = history.map { ChatMessage(it.role, it.message) })
            } catch (_: Exception) {
                // History is a nice-to-have on load; a failure here shouldn't block the chat screen.
            }
        }
    }

    fun sendMessage(text: String) {
        val auth = token ?: return
        if (text.isBlank()) return
        startSend("user", text, "Thinking…")
        viewModelScope.launch {
            runCatching { ApiClient.service.sendMessage("Bearer $auth", com.qlass.tutor.network.SendMessageRequest(text)) }
                .fold(::finishSend, ::failSend)
        }
    }

    // Photo of a homework question — same Azure OCR pipeline WhatsApp uses
    // (backend app.routers.student_app's send-image), just fed from a
    // multipart file instead of a Wati media URL.
    fun sendImage(bytes: ByteArray, filename: String) {
        val auth = token ?: return
        startSend("user", "📷 Photo", "Reading your photo…")
        viewModelScope.launch {
            val body = bytes.toRequestBody("image/*".toMediaTypeOrNull())
            val part = MultipartBody.Part.createFormData("file", filename, body)
            runCatching { ApiClient.service.sendImage("Bearer $auth", part) }.fold(::finishSend, ::failSend)
        }
    }

    // A recorded voice note — same Sarvam STT pipeline WhatsApp uses.
    fun sendVoice(file: File) {
        val auth = token ?: return
        startSend("user", "🎤 Voice message", "Listening to your question…")
        viewModelScope.launch {
            val body = file.asRequestBody("audio/mp4".toMediaTypeOrNull())
            val part = MultipartBody.Part.createFormData("file", file.name, body)
            runCatching { ApiClient.service.sendVoice("Bearer $auth", part) }.fold(::finishSend, ::failSend)
        }
    }

    // A PDF/Word worksheet — same extract_text_from_document pipeline
    // WhatsApp uses; the backend pins long extracted text as the student's
    // active document automatically (see app.services.document_client).
    fun sendDocument(bytes: ByteArray, filename: String, mimeType: String) {
        val auth = token ?: return
        startSend("user", "📎 $filename", "Reading your file…")
        viewModelScope.launch {
            val body = bytes.toRequestBody(mimeType.toMediaTypeOrNull())
            val part = MultipartBody.Part.createFormData("file", filename, body)
            runCatching { ApiClient.service.sendDocument("Bearer $auth", part) }.fold(::finishSend, ::failSend)
        }
    }

    private fun startSend(role: String, label: String, sendingLabel: String) {
        _state.value = _state.value.copy(
            messages = _state.value.messages + ChatMessage(role, label), sending = true,
            sendingLabel = sendingLabel, error = null,
        )
    }

    private fun finishSend(response: SendMessageResponse) {
        _state.value = _state.value.copy(
            messages = _state.value.messages + ChatMessage("assistant", response.reply),
            creditBalance = response.credit_balance, sending = false,
        )
    }

    private fun failSend(error: Throwable) {
        val message = if (error is HttpException) error.detailMessage() else "Couldn't reach the server — check your connection"
        _state.value = _state.value.copy(sending = false, error = message)
    }

    fun setRecording(value: Boolean) {
        _state.value = _state.value.copy(recording = value)
    }

    fun logout() {
        viewModelScope.launch {
            sessionStore.clear()
            token = null
            _state.value = UiState()
        }
    }

    fun selectTab(tab: Tab) {
        _state.value = _state.value.copy(selectedTab = tab)
        if (tab == Tab.PROGRESS && _state.value.progress == null) loadProgress()
    }

    fun loadProgress() {
        val auth = token ?: return
        _state.value = _state.value.copy(progressLoading = true)
        viewModelScope.launch {
            try {
                val progress = ApiClient.service.progress("Bearer $auth")
                val history = ApiClient.service.creditHistory("Bearer $auth")
                _state.value = _state.value.copy(progress = progress, creditHistory = history, progressLoading = false)
            } catch (e: HttpException) {
                _state.value = _state.value.copy(progressLoading = false, error = e.detailMessage())
            } catch (e: Exception) {
                _state.value = _state.value.copy(progressLoading = false, error = "Couldn't reach the server — check your connection")
            }
        }
    }

    // Called once after login (and again whenever FCM rotates the token,
    // from QlassMessagingService) — a no-op server-side no-op is fine here
    // too, so failures are swallowed rather than surfaced as a chat error.
    fun registerDeviceToken(fcmToken: String) {
        val auth = token ?: return
        viewModelScope.launch {
            try {
                ApiClient.service.registerDeviceToken("Bearer $auth", DeviceTokenRequest(fcmToken))
            } catch (_: Exception) {
            }
        }
    }
}
