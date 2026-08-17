package com.qlass.tutor

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.CreationExtras
import com.google.firebase.messaging.FirebaseMessaging
import com.qlass.tutor.audio.VoiceRecorder
import com.qlass.tutor.data.SessionStore
import com.qlass.tutor.push.FirebaseInit
import com.qlass.tutor.ui.ChatScreen
import com.qlass.tutor.ui.LoginScreen
import com.qlass.tutor.ui.ProgressScreen
import com.qlass.tutor.ui.theme.QlassTutorTheme
import java.io.File

class TutorViewModelFactory(private val sessionStore: SessionStore) : ViewModelProvider.Factory {
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T {
        @Suppress("UNCHECKED_CAST")
        return TutorViewModel(sessionStore) as T
    }
}

private fun queryDisplayName(context: android.content.Context, uri: Uri): String {
    context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
        val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (nameIndex >= 0 && cursor.moveToFirst()) return cursor.getString(nameIndex)
    }
    return uri.lastPathSegment ?: "document"
}

class MainActivity : ComponentActivity() {
    private val viewModel: TutorViewModel by viewModels { TutorViewModelFactory(SessionStore(applicationContext)) }
    private val voiceRecorder by lazy { VoiceRecorder(applicationContext) }
    private var pendingCameraUri: Uri? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            QlassTutorTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    val state by viewModel.state.collectAsState()
                    val context = LocalContext.current

                    val notificationPermissionLauncher = rememberLauncherForActivityResult(
                        ActivityResultContracts.RequestPermission(),
                    ) { }

                    val cameraLauncher = rememberLauncherForActivityResult(
                        ActivityResultContracts.TakePicture(),
                    ) { success ->
                        val uri = pendingCameraUri
                        if (success && uri != null) {
                            val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                            if (bytes != null) viewModel.sendImage(bytes, "photo.jpg")
                        }
                    }
                    val cameraPermissionLauncher = rememberLauncherForActivityResult(
                        ActivityResultContracts.RequestPermission(),
                    ) { granted ->
                        if (granted) {
                            val file = File(context.cacheDir, "capture_${System.currentTimeMillis()}.jpg")
                            val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
                            pendingCameraUri = uri
                            cameraLauncher.launch(uri)
                        }
                    }

                    val documentLauncher = rememberLauncherForActivityResult(
                        ActivityResultContracts.OpenDocument(),
                    ) { uri ->
                        if (uri != null) {
                            val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                            val name = queryDisplayName(context, uri)
                            val mimeType = context.contentResolver.getType(uri) ?: "application/octet-stream"
                            if (bytes != null) viewModel.sendDocument(bytes, name, mimeType)
                        }
                    }

                    val micPermissionLauncher = rememberLauncherForActivityResult(
                        ActivityResultContracts.RequestPermission(),
                    ) { granted ->
                        if (granted && voiceRecorder.start()) viewModel.setRecording(true)
                    }

                    // Registers the current FCM token (and asks for the
                    // Android 13+ notification permission) once per login —
                    // both are silent no-ops when Firebase isn't configured
                    // (see FirebaseInit) or the permission is already denied.
                    LaunchedEffect(state.loggedIn) {
                        if (!state.loggedIn || !FirebaseInit.isConfigured()) return@LaunchedEffect
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
                            != PackageManager.PERMISSION_GRANTED
                        ) {
                            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                        }
                        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
                            viewModel.registerDeviceToken(token)
                        }
                    }

                    if (state.loggedIn) {
                        Column(modifier = Modifier.fillMaxSize()) {
                            Column(modifier = Modifier.weight(1f)) {
                                when (state.selectedTab) {
                                    Tab.CHAT -> ChatScreen(
                                        state = state,
                                        onSend = viewModel::sendMessage,
                                        onLogout = viewModel::logout,
                                        onChangeLevel = viewModel::changeLevel,
                                        onPickImage = {
                                            if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                                                val file = File(context.cacheDir, "capture_${System.currentTimeMillis()}.jpg")
                                                val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
                                                pendingCameraUri = uri
                                                cameraLauncher.launch(uri)
                                            } else {
                                                cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                                            }
                                        },
                                        onPickDocument = {
                                            documentLauncher.launch(arrayOf(
                                                "application/pdf",
                                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                            ))
                                        },
                                        onToggleRecording = {
                                            if (state.recording) {
                                                val file = voiceRecorder.stop()
                                                viewModel.setRecording(false)
                                                if (file != null) viewModel.sendVoice(file)
                                            } else if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                                                if (voiceRecorder.start()) viewModel.setRecording(true)
                                            } else {
                                                micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                                            }
                                        },
                                    )
                                    Tab.PROGRESS -> ProgressScreen(state = state)
                                }
                            }
                            NavigationBar {
                                NavigationBarItem(
                                    selected = state.selectedTab == Tab.CHAT,
                                    onClick = { viewModel.selectTab(Tab.CHAT) },
                                    icon = { Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = "Chat") },
                                    label = { Text("Chat") },
                                )
                                NavigationBarItem(
                                    selected = state.selectedTab == Tab.PROGRESS,
                                    onClick = { viewModel.selectTab(Tab.PROGRESS) },
                                    icon = { Icon(Icons.Filled.BarChart, contentDescription = "Progress") },
                                    label = { Text("Progress") },
                                )
                            }
                        }
                    } else {
                        LoginScreen(
                            state = state,
                            onPhoneChanged = viewModel::onPhoneChanged,
                            onSubmitPhone = viewModel::submitPhone,
                            onVerifyOtp = viewModel::verifyOtp,
                        )
                    }
                }
            }
        }
    }
}
