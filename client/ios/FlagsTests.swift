import XCTest
@testable import App  // your app module

final class FlagsTests: XCTestCase {
    func testKillSwitchServesControl() {
        XCTAssertEqual(Flags.resolve("new_checkout", value: "on", killed: true), "off")
        XCTAssertEqual(Flags.resolve("new_checkout", value: "on", killed: false), "on")
    }
}
