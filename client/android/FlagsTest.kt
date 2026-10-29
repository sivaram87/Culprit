import org.junit.Assert.assertEquals
import org.junit.Test

class FlagsTest {
    @Test fun killSwitchServesControl() {
        assertEquals("off", Flags.resolve("new_checkout", "on", killed = true))
        assertEquals("on", Flags.resolve("new_checkout", "on", killed = false))
    }
}
