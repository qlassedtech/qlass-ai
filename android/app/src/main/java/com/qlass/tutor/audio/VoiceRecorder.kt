package com.qlass.tutor.audio

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import java.io.File

/**
 * Thin wrapper around MediaRecorder — records to AAC/M4A (Sarvam's STT API
 * accepts audio/aac and audio/mp4 directly, see backend
 * app.services.sarvam_client), the same format MediaRecorder's most
 * broadly-compatible output preset already produces on every Android
 * version this app targets.
 */
class VoiceRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var outputFile: File? = null

    fun start(): Boolean {
        val file = File(context.cacheDir, "voice_note_${System.currentTimeMillis()}.m4a")
        val mediaRecorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(context)
        } else {
            @Suppress("DEPRECATION")
            MediaRecorder()
        }
        return try {
            mediaRecorder.apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setOutputFile(file.absolutePath)
                prepare()
                start()
            }
            recorder = mediaRecorder
            outputFile = file
            true
        } catch (_: Exception) {
            mediaRecorder.release()
            false
        }
    }

    /** Returns the recorded file, or null if nothing was captured (too short / never started). */
    fun stop(): File? {
        val current = recorder ?: return null
        return try {
            current.stop()
            outputFile
        } catch (_: Exception) {
            null
        } finally {
            current.release()
            recorder = null
        }
    }

    fun cancel() {
        try {
            recorder?.stop()
        } catch (_: Exception) {
        }
        recorder?.release()
        recorder = null
        outputFile?.delete()
        outputFile = null
    }
}
