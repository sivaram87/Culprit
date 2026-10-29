/** The one way app code reads feature flags. Honors `killswitch_<name>` and tags
 *  crashes + sessions with the variant so Culprit can compare crash rates.
 *  Vendor-neutral: wire `value`, `isKilled` and `tagSinks` once at startup
 *  (Firebase / Statsig / Crashlytics / Sentry snippets are in the README). */
object Flags {
    /** Watched flags -> control (safe) value. Keep in sync with job/flags.json.
     *  Names: lowercase a-z, 0-9, _ and <= 21 chars (Analytics' 24-char limit with "ff_"). */
    val watched = mapOf("new_checkout" to "off")

    var value: (String) -> String = { "" }
    var isKilled: (String) -> Boolean = { false }
    var tagSinks: List<(key: String, value: String) -> Unit> = emptyList()

    fun variant(name: String): String = resolve(name, value(name), isKilled(name))

    fun resolve(name: String, value: String, killed: Boolean): String =
        if (killed) watched[name] ?: value else value

    /** Call after every flag refresh (e.g. Remote Config activate, Statsig update). */
    fun tag() {
        for (name in watched.keys) {
            val v = variant(name)
            tagSinks.forEach { it("ff_$name", v) }
        }
    }
}
