import FirebaseAnalytics
import FirebaseCrashlytics
import FirebaseRemoteConfig

/// The one way app code reads feature flags. Honors `killswitch_<name>` and tags
/// crashes + sessions with the variant so Flag Guard can compare crash rates.
enum Flags {
    /// Watched flags -> control (safe) value. Keep in sync with job/flags.json.
    /// Names must be <= 21 chars ("ff_" + name hits Analytics' 24-char limit).
    static let watched: [String: String] = ["new_checkout": "off"]

    static func variant(_ name: String, rc: RemoteConfig = .remoteConfig()) -> String {
        resolve(name, value: rc[name].stringValue, killed: rc["killswitch_\(name)"].boolValue)
    }

    static func resolve(_ name: String, value: String, killed: Bool) -> String {
        killed ? (watched[name] ?? value) : value
    }

    /// Call after every successful Remote Config activate().
    static func tag(rc: RemoteConfig = .remoteConfig()) {
        for name in watched.keys {
            let v = variant(name, rc: rc)
            Crashlytics.crashlytics().setCustomValue(v, forKey: "ff_\(name)")
            Analytics.setUserProperty(v, forName: "ff_\(name)")
        }
    }
}
