package com.qlass.tutor.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.qlass.tutor.UiState
import java.time.Instant
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

@Composable
fun ProgressScreen(state: UiState) {
    if (state.progressLoading && state.progress == null) {
        Column(
            modifier = Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center,
        ) { CircularProgressIndicator(modifier = Modifier.padding(24.dp)) }
        return
    }

    val progress = state.progress
    LazyColumn(modifier = Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        item {
            Text("Your Progress", style = MaterialTheme.typography.headlineSmall)
        }
        item {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(
                    label = "Credit balance", value = state.creditBalance?.let { "₹%.2f".format(it) } ?: "—",
                    modifier = Modifier.weight(1f),
                )
                StatCard(
                    label = "Streak", value = progress?.let { "${it.streak_days} day${if (it.streak_days == 1) "" else "s"}" } ?: "—",
                    modifier = Modifier.weight(1f),
                )
            }
        }
        item {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(
                    label = "Accuracy", value = progress?.accuracy_pct?.let { "$it%" } ?: "No data yet",
                    modifier = Modifier.weight(1f),
                )
                StatCard(
                    label = "Chapters covered",
                    value = if (progress?.chapters_total != null) "${progress.chapters_covered}/${progress.chapters_total}" else "—",
                    modifier = Modifier.weight(1f),
                )
            }
        }
        if (!progress?.weak_topics.isNullOrEmpty()) {
            item {
                Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text("Topics to review", fontWeight = FontWeight.Bold)
                        androidx.compose.foundation.layout.Spacer(modifier = Modifier.padding(top = 4.dp))
                        Text(progress!!.weak_topics.joinToString(", "))
                    }
                }
            }
        }
        item {
            Text("Recent credit activity", style = MaterialTheme.typography.titleMedium)
        }
        if (state.creditHistory.isEmpty()) {
            item { Text("No transactions yet.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
        } else {
            items(state.creditHistory) { entry ->
                Column {
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(entry.note ?: entry.service ?: "AI usage", style = MaterialTheme.typography.bodyMedium)
                        Text(
                            (if (entry.amount >= 0) "+₹%.2f" else "−₹%.2f").format(kotlin.math.abs(entry.amount)),
                            color = if (entry.amount >= 0) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error,
                        )
                    }
                    Text(formatTimestamp(entry.created_at), style = MaterialTheme.typography.labelSmall)
                    HorizontalDivider(modifier = Modifier.padding(top = 8.dp))
                }
            }
        }
    }
}

@Composable
private fun StatCard(label: String, value: String, modifier: Modifier = Modifier) {
    Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceVariant, modifier = modifier) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium)
            Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        }
    }
}

private fun formatTimestamp(iso: String): String = try {
    DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM).format(
        Instant.parse(iso).atZone(java.time.ZoneId.systemDefault()),
    )
} catch (_: Exception) {
    iso
}
