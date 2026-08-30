plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "pt.solucoesdiferentes.sdvoicegateway"
    compileSdk = 35

    defaultConfig {
        applicationId = "pt.solucoesdiferentes.sdvoicegateway"
        minSdk = 29
        targetSdk = 35
        versionCode = 40
        versionName = "0.40.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
}
