package com.qlass.tutor.network

data class CheckPhoneRequest(val phone: String)
data class CheckPhoneResponse(val login_type: String)

data class VerifyOtpRequest(val phone: String, val otp: String, val name: String? = null)
data class StudentSummary(
    val id: Int,
    val name: String,
    val `class`: String?,
    val board: String?,
    val focus_topic: String?,
    val credit_balance: Double,
    val referral_code: String?,
    val tutor_level: Int = 4,
)
data class VerifyOtpResponse(val access_token: String, val student: StudentSummary)

data class ChatHistoryEntry(val role: String, val message: String, val created_at: String)

data class SendMessageRequest(val message: String)
data class SendMessageResponse(val reply: String, val credit_balance: Double)

data class ProgressResponse(
    val total_evaluated: Int,
    val correct: Int,
    val incorrect: Int,
    val accuracy_pct: Int?,
    val weak_topics: List<String>,
    val messages_sent: Int,
    val streak_days: Int,
    val active_days: Int,
    val chapters_covered: Int?,
    val chapters_total: Int?,
    val chapters_not_covered: List<String>,
)

data class CreditHistoryEntry(val amount: Double, val service: String?, val note: String?, val created_at: String)

data class DeviceTokenRequest(val token: String)

data class SetTutorLevelRequest(val level: Int)
