import Foundation
import UIKit

// Where flags come from and where crashes go. Two versions of the same five functions:
// - Firebase (Remote Config + Crashlytics + Analytics), compiled when the Firebase packages are added.
// - The local Culprit sandbox (sandbox/server.py), used otherwise. No accounts needed.

#if canImport(FirebaseCore) && canImport(FirebaseRemoteConfig) && canImport(FirebaseCrashlytics) && canImport(FirebaseAnalytics)
import FirebaseAnalytics
import FirebaseCore
import FirebaseCrashlytics
import FirebaseRemoteConfig

enum Backend {
    static let name = "Firebase"
    static let canSwitchUser = false
    static var user: String { Analytics.appInstanceID() ?? "" }

    static func start() {
        FirebaseApp.configure()
        let rc = RemoteConfig.remoteConfig()
        #if DEBUG
        let settings = RemoteConfigSettings()
        settings.minimumFetchInterval = 0  // staging: see kill switches right away
        rc.configSettings = settings
        #endif
        rc.setDefaults(["new_checkout": "off" as NSObject, "killswitch_new_checkout": false as NSObject])

        Flags.value = { rc[$0].stringValue }
        Flags.isKilled = { rc["killswitch_\($0)"].boolValue }
        Flags.tagSinks = [{ key, value in
            Crashlytics.crashlytics().setCustomValue(value, forKey: key)
            Analytics.setUserProperty(value, forName: key)
        }]
        Flags.tag()  // cached values, before anything can crash
    }

    static func refresh(_ done: @escaping () -> Void = {}) {
        RemoteConfig.remoteConfig().fetchAndActivate { status, _ in
            DispatchQueue.main.async {
                if status != .error { Flags.tag() }
                done()
            }
        }
    }

    static func newUser() {}

    static func crash() -> Never { fatalError("Demo crash in new checkout") }  // Crashlytics sends it on next launch

    // Crashlytics doesn't report iOS hangs (its ANR type is Android-only). Use Sentry's app-hang
    // tracking, or run the Android sample, to test the ANR path for real.
    static func freeze() { Thread.sleep(forTimeInterval: 6) }

    static func nonFatal() {
        Crashlytics.crashlytics().record(error: NSError(domain: "checkout.save_card", code: 1))
    }
}

#else

enum Backend {
    static let name = "Sandbox"
    static let canSwitchUser = true
    static let url = URL(string: ProcessInfo.processInfo.environment["SANDBOX_URL"] ?? "http://localhost:8787")!
    private static let defaults = UserDefaults.standard
    private static var config = defaults.dictionary(forKey: "config") ?? ["new_checkout": "off"]  // cached, like Remote Config
    private static var tags: [String: String] = [:]
    private static let pending = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("pending-crash.json")

    static var user: String {
        if let id = defaults.string(forKey: "user") { return id }
        newUser()
        return defaults.string(forKey: "user")!
    }

    static func start() {
        Flags.value = { config[$0] as? String ?? "" }
        Flags.isKilled = { config["killswitch_\($0)"] as? Bool ?? false }
        Flags.tagSinks = [{ key, value in tags[key] = value }]
        Flags.tag()
        // Like a crash SDK: a crash is written to disk as it happens and uploaded on the next launch.
        if let data = try? Data(contentsOf: pending) {
            post(data)
            try? FileManager.default.removeItem(at: pending)
        }
    }

    static func refresh(_ done: @escaping () -> Void = {}) {
        var get = URLComponents(url: url.appendingPathComponent("config"), resolvingAgainstBaseURL: false)!
        get.queryItems = [URLQueryItem(name: "user", value: user)]
        URLSession.shared.dataTask(with: get.url!) { data, _, _ in
            DispatchQueue.main.async {
                if let data, let fresh = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    config = fresh
                    defaults.set(fresh, forKey: "config")
                    Flags.tag()
                }
                send("session")  // counts this user for the variant they now get
                done()
            }
        }.resume()
    }

    static func newUser() {
        defaults.set(UUID().uuidString.lowercased(), forKey: "user")
        defaults.removeObject(forKey: "config")
        config = ["new_checkout": "off"]
    }

    static func crash() -> Never {
        try? body("FATAL").write(to: pending)
        fatalError("Demo crash in new checkout")
    }

    static func freeze() {
        Thread.sleep(forTimeInterval: 6)
        send("ANR")  // ponytail: reported after the freeze; real SDKs use a watchdog thread
    }

    static func nonFatal() { send("NON_FATAL") }

    private static func body(_ type: String) -> Data {
        let event: [String: Any] = [
            "user": user, "type": type, "tags": tags,
            "version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0",
            "os": "iOS \(UIDevice.current.systemVersion)",
            "device": ProcessInfo.processInfo.environment["SIMULATOR_DEVICE_NAME"] ?? UIDevice.current.model,
        ]
        return try! JSONSerialization.data(withJSONObject: event)
    }

    private static func send(_ type: String) { post(body(type)) }

    private static func post(_ data: Data) {
        var req = URLRequest(url: url.appendingPathComponent("events"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        URLSession.shared.dataTask(with: req).resume()
    }
}

#endif
