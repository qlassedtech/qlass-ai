package com.qlass.tutor.network

import okhttp3.MultipartBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part

interface ApiService {
    @POST("/student-app/auth/check-phone")
    suspend fun checkPhone(@Body body: CheckPhoneRequest): CheckPhoneResponse

    @POST("/student-app/auth/request-otp")
    suspend fun requestOtp(@Body body: CheckPhoneRequest)

    @POST("/student-app/auth/verify-otp")
    suspend fun verifyOtp(@Body body: VerifyOtpRequest): VerifyOtpResponse

    @GET("/student-app/me")
    suspend fun me(@Header("Authorization") auth: String): StudentSummary

    @GET("/student-app/chat/history")
    suspend fun chatHistory(@Header("Authorization") auth: String): List<ChatHistoryEntry>

    @POST("/student-app/chat/send")
    suspend fun sendMessage(@Header("Authorization") auth: String, @Body body: SendMessageRequest): SendMessageResponse

    @GET("/student-app/progress")
    suspend fun progress(@Header("Authorization") auth: String): ProgressResponse

    @GET("/student-app/credits/history")
    suspend fun creditHistory(@Header("Authorization") auth: String): List<CreditHistoryEntry>

    @POST("/student-app/device-token")
    suspend fun registerDeviceToken(@Header("Authorization") auth: String, @Body body: DeviceTokenRequest)

    // Same OCR/STT/document pipeline WhatsApp uses server-side (see backend
    // app.routers.student_app) — response shape matches sendMessage exactly.
    @Multipart
    @POST("/student-app/chat/send-image")
    suspend fun sendImage(@Header("Authorization") auth: String, @Part file: MultipartBody.Part): SendMessageResponse

    @Multipart
    @POST("/student-app/chat/send-voice")
    suspend fun sendVoice(@Header("Authorization") auth: String, @Part file: MultipartBody.Part): SendMessageResponse

    @Multipart
    @POST("/student-app/chat/send-document")
    suspend fun sendDocument(@Header("Authorization") auth: String, @Part file: MultipartBody.Part): SendMessageResponse
}
