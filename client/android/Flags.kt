import com.google.firebase.Firebase
import com.google.firebase.analytics.analytics
import com.google.firebase.crashlytics.crashlytics
import com.google.firebase.remoteconfig.FirebaseRemoteConfig
import com.google.firebase.remoteconfig.remoteConfig

/** The one way app code reads feature flags. Honors `killswitch_<name>` and tags
 *  crashes + sessions with the variant so Flag Guard can compare crash rates. */
object Flags {
    /** Watched flags -> control (safe) value. Keep in sync with job/flags.json.
     *  Names must be <= 21 chars ("ff_" + name hits Analytics' 24-char limit). */
    val watched = mapOf("new_checkout" to "off")

    fun variant(name: String, rc: FirebaseRemoteConfig = Firebase.remoteConfig): String =
        resolve(name, rc.getString(name), rc.getBoolean("killswitch_$name"))

    fun resolve(name: String, value: String, killed: Boolean): String =
        if (killed) watched[name] ?: value else value

    /** Call after every successful Remote Config activate(). */
    fun tag(rc: FirebaseRemoteConfig = Firebase.remoteConfig) {
        for (name in watched.keys) {
            val v = variant(name, rc)
            Firebase.crashlytics.setCustomKey("ff_$name", v)
            Firebase.analytics.setUserProperty("ff_$name", v)
        }
    }
}
