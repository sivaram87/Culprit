/// The one way app code reads feature flags. Honors `killswitch_<name>` and tags
/// crashes + sessions with the variant so Culprit can compare crash rates.
/// Vendor-neutral: wire `value`, `isKilled` and `tagSinks` once at startup
/// (Firebase / Statsig / Crashlytics / Sentry snippets are in the README).
enum Flags {
    /// Watched flags -> control (safe) value. Keep in sync with job/flags.json.
    /// Names: lowercase a-z, 0-9, _ and <= 21 chars (Analytics' 24-char limit with "ff_").
    static let watched: [String: String] = ["new_checkout": "off"]

    static var value: (String) -> String = { _ in "" }
    static var isKilled: (String) -> Bool = { _ in false }
    static var tagSinks: [(_ key: String, _ value: String) -> Void] = []

    static func variant(_ name: String) -> String {
        resolve(name, value: value(name), killed: isKilled(name))
    }

    static func resolve(_ name: String, value: String, killed: Bool) -> String {
        killed ? (watched[name] ?? value) : value
    }

    /// Call after every flag refresh (e.g. Remote Config activate, Statsig update).
    static func tag() {
        for name in watched.keys {
            let v = variant(name)
            tagSinks.forEach { $0("ff_\(name)", v) }
        }
    }
}
