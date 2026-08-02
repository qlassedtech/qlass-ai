package com.qlass.tutor.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Matches the web frontend's own palette exactly (frontend/src/index.css
// --accent/--bg/--card-bg/etc.) so the brand looks identical whether a
// student is on the web app or this native app.
private val LightColors = lightColorScheme(
    primary = Color(0xFF2B3EC4),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFEEF0FD),
    onPrimaryContainer = Color(0xFF1C2A8F),
    secondary = Color(0xFFFBBF24),
    background = Color(0xFFF3F4FA),
    onBackground = Color(0xFF161829),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF161829),
    surfaceVariant = Color(0xFFF7F8FE),
    onSurfaceVariant = Color(0xFF161829),
    outline = Color(0xFFE8E9F3),
    error = Color(0xFFDC2626),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF7B8BF5),
    onPrimary = Color(0xFF0E101C),
    primaryContainer = Color(0xFF232853),
    onPrimaryContainer = Color(0xFF7B8BF5),
    secondary = Color(0xFFFBBF24),
    background = Color(0xFF0E101C),
    onBackground = Color(0xFFE8E9F3),
    surface = Color(0xFF171A2B),
    onSurface = Color(0xFFE8E9F3),
    surfaceVariant = Color(0xFF1A1E38),
    onSurfaceVariant = Color(0xFFE8E9F3),
    outline = Color(0xFF2C2F48),
    error = Color(0xFFF87171),
)

@Composable
fun QlassTutorTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) DarkColors else LightColors
    MaterialTheme(colorScheme = colors, content = content)
}
