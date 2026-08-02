import com.android.build.api.dsl.ApplicationBuildType
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Push notifications (see app/src/main/java/com/qlass/tutor/push/) init
// Firebase manually via FirebaseOptions instead of the google-services
// Gradle plugin + google-services.json — that plugin FAILS THE BUILD if the
// json file is missing, and this repo has no Firebase project yet. Reading
// these four values from local.properties (gitignored, never committed)
// means the build always succeeds; push just stays inert (see
// QlassMessagingService) until someone creates a Firebase project and adds
// real values there:
//   fcm.apiKey=...
//   fcm.appId=...
//   fcm.projectId=...
//   fcm.senderId=...
val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

fun addFcmBuildConfigFields(buildType: ApplicationBuildType) {
    buildType.buildConfigField("String", "FCM_API_KEY", "\"${localProperties.getProperty("fcm.apiKey", "")}\"")
    buildType.buildConfigField("String", "FCM_APP_ID", "\"${localProperties.getProperty("fcm.appId", "")}\"")
    buildType.buildConfigField("String", "FCM_PROJECT_ID", "\"${localProperties.getProperty("fcm.projectId", "")}\"")
    buildType.buildConfigField("String", "FCM_SENDER_ID", "\"${localProperties.getProperty("fcm.senderId", "")}\"")
}

android {
    namespace = "com.qlass.tutor"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.qlass.tutor"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        // Emulator reaches the host machine's backend via the special
        // 10.0.2.2 alias — a real device build would point this at the
        // deployed API domain instead.
        debug {
            buildConfigField("String", "API_BASE_URL", "\"http://10.0.2.2:8000\"")
            addFcmBuildConfigFields(this)
        }
        release {
            isMinifyEnabled = false
            buildConfigField("String", "API_BASE_URL", "\"https://api.qlass.example.com\"")
            addFcmBuildConfigFields(this)
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // Manually initialized (see FirebaseInit.kt) — no google-services plugin,
    // so this dependency alone can never break the build.
    implementation("com.google.firebase:firebase-messaging-ktx:24.1.0")
}
