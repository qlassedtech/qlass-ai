package com.qlass.tutor

import android.app.Application
import com.qlass.tutor.push.FirebaseInit

class QlassApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        FirebaseInit.initIfConfigured(this)
    }
}
