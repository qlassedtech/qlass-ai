package com.qlass.tutor.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.qlass.tutor.ChatMessage
import com.qlass.tutor.UiState

// The tutor writes replies using WhatsApp's own *bold* convention (see
// backend app.agents.tutor_agent's system prompt). Rendered natively there;
// a plain Text composable here would just show the raw asterisks, so parse
// *...* spans into a bold AnnotatedString instead of pulling in a full
// markdown library for what's really just one convention.
private val boldSpanRegex = Regex("\\*([^*\\n]+)\\*")

private fun formatMessage(text: String) = buildAnnotatedString {
    var lastEnd = 0
    for (match in boldSpanRegex.findAll(text)) {
        append(text.substring(lastEnd, match.range.first))
        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(match.groupValues[1]) }
        lastEnd = match.range.last + 1
    }
    append(text.substring(lastEnd))
}

// Mirrors backend TUTOR_LEVEL_LABELS (app/services/chat_core.py) — kept in
// sync manually, same as the web app's LevelSwitcher component.
private val LEVEL_LABELS = mapOf(
    1 to "Level 1 — fastest, lightest on credits",
    2 to "Level 2 — quick and well-formatted",
    3 to "Level 3 — more detailed explanations",
    4 to "Level 4 — most thorough, uses the most credits",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    state: UiState,
    onSend: (String) -> Unit,
    onLogout: () -> Unit,
    onChangeLevel: (Int) -> Unit,
    onPickImage: () -> Unit,
    onPickDocument: () -> Unit,
    onToggleRecording: () -> Unit,
) {
    var draft by remember { mutableStateOf("") }
    var showLevelDialog by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    LaunchedEffect(state.messages.size, state.sending) {
        val lastIndex = state.messages.size - 1 + if (state.sending) 1 else 0
        if (lastIndex >= 0) listState.animateScrollToItem(lastIndex)
    }

    if (showLevelDialog) {
        LevelPickerDialog(
            currentLevel = state.tutorLevel,
            onSelect = { level -> onChangeLevel(level); showLevelDialog = false },
            onDismiss = { showLevelDialog = false },
        )
    }

    Column(modifier = Modifier.fillMaxSize()) {
        TopAppBar(
            title = {
                Column {
                    Text("AI Tutor", fontWeight = FontWeight.SemiBold)
                    state.creditBalance?.let {
                        Text("₹%.2f credits".format(it), style = MaterialTheme.typography.labelSmall)
                    }
                }
            },
            actions = {
                // Persistent header control — the app equivalent of
                // WhatsApp's "🎓 Change Level" menu button and typed
                // "level N" command, both landing on the same
                // student.tutor_level write server-side.
                IconButton(onClick = { showLevelDialog = true }) {
                    Icon(Icons.Filled.Tune, contentDescription = "Change tutor level")
                }
                TextButton(onClick = onLogout) { Text("Log out") }
            },
            colors = androidx.compose.material3.TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.surface,
            ),
        )

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 8.dp),
        ) {
            itemsIndexed(state.messages) { _, message -> ChatBubble(message) }
            if (state.sending) {
                item {
                    Row(
                        modifier = Modifier.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically,
                    ) {
                        CircularProgressIndicator(modifier = Modifier.size(16.dp).padding(end = 8.dp), strokeWidth = 2.dp)
                        Text(state.sendingLabel, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }

        state.error?.let {
            Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp))
        }

        if (state.recording) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("🔴 Recording…", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
            }
        }

        // Quizzes have no dedicated endpoint — student_chat.py classifies
        // plain chat text and starts a real scored quiz whenever it detects
        // quiz intent, so this chip is just a shortcut into that same path,
        // not a separate feature.
        Row(modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            AssistChip(onClick = { onSend("Quiz me on what we've covered so far") }, label = { Text("Quiz me") })
            AssistChip(onClick = { onSend("Give me a mock test") }, label = { Text("Mock test") })
        }

        Surface(shadowElevation = 4.dp, color = MaterialTheme.colorScheme.surface) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = onPickImage, enabled = !state.sending && !state.recording) {
                    Icon(Icons.Filled.PhotoCamera, contentDescription = "Attach a photo of your question")
                }
                IconButton(onClick = onPickDocument, enabled = !state.sending && !state.recording) {
                    Icon(Icons.Filled.AttachFile, contentDescription = "Attach a PDF or Word worksheet")
                }
                IconButton(onClick = onToggleRecording, enabled = !state.sending) {
                    Icon(
                        if (state.recording) Icons.Filled.Stop else Icons.Filled.Mic,
                        contentDescription = if (state.recording) "Stop recording" else "Record a voice question",
                        tint = if (state.recording) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it },
                    placeholder = { Text("Ask anything…") },
                    modifier = Modifier.weight(1f).padding(horizontal = 4.dp),
                    shape = RoundedCornerShape(24.dp),
                    enabled = !state.recording,
                )
                IconButton(onClick = {
                    if (draft.isNotBlank()) {
                        onSend(draft)
                        draft = ""
                    }
                }, enabled = !state.sending && !state.recording && draft.isNotBlank()) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}

@Composable
private fun LevelPickerDialog(currentLevel: Int?, onSelect: (Int) -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Change tutor level") },
        text = {
            Column {
                LEVEL_LABELS.forEach { (level, label) ->
                    Row(
                        modifier = Modifier.fillMaxWidth().clickable { onSelect(level) }.padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = level == currentLevel, onClick = { onSelect(level) })
                        Text(label, modifier = Modifier.padding(start = 4.dp))
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}

@Composable
private fun ChatBubble(message: ChatMessage) {
    val isUser = message.role == "user"
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
        Surface(
            color = if (isUser) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,
            shape = RoundedCornerShape(
                topStart = 16.dp, topEnd = 16.dp,
                bottomStart = if (isUser) 16.dp else 4.dp, bottomEnd = if (isUser) 4.dp else 16.dp,
            ),
            shadowElevation = 1.dp,
            modifier = Modifier.widthIn(max = 300.dp).padding(vertical = 2.dp),
        ) {
            Text(
                text = formatMessage(message.text),
                color = if (isUser) Color.White else MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            )
        }
    }
}
