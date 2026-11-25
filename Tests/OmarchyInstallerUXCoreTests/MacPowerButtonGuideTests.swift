#if os(macOS)
  import XCTest
  @testable import OmarchyInstallerUXCore

  final class MacPowerButtonGuideTests: XCTestCase {
    func testNotebookAndCompactDesktopSelection() {
      for id in ["apple,j293", "apple,j413", "apple,j414s", "apple,j700"] {
        XCTAssertEqual(MacPowerButtonGuide(deviceIdentifier: id, modelIdentifier: ""), .notebook)
      }
      for id in ["apple,j274", "apple,j473", "apple,j474s", "apple,j375c", "apple,j475d"] {
        XCTAssertEqual(MacPowerButtonGuide(deviceIdentifier: id, modelIdentifier: ""), .desktopRear)
      }
    }

    func testUndersideButtonDoesNotUseRearButtonArtwork() {
      for model in ["Mac16,10", "Mac16,11"] {
        let guide = MacPowerButtonGuide(deviceIdentifier: "unknown", modelIdentifier: model)
        XCTAssertEqual(guide, .desktopUnderside)
        XCTAssertNil(guide.assetName)
        XCTAssertTrue(guide.holdInstruction.contains("underside"))
      }
    }

    /// The 2024 minis are named Mac minis, so the family would otherwise send
    /// them to the rear button their enclosure does not have.
    func testThe2024MacMinisKeepTheUndersideButtonOnceTheyAreNamed() {
      for (id, model) in [("apple,j773g", "Mac16,10"), ("apple,j773s", "Mac16,11")] {
        XCTAssertEqual(MacModelNames.entry(for: id)?.family, "Mac mini", id)
        let guide = MacPowerButtonGuide(deviceIdentifier: id, modelIdentifier: model)
        XCTAssertEqual(guide, .desktopUnderside, id)
        XCTAssertNil(guide.assetName, id)
      }
    }

    /// Every other Mac mini keeps the rear button.
    func testOlderMacMinisStillUseTheRearButton() {
      for (id, model) in [("apple,j274", "Macmini9,1"), ("apple,j473", "Mac14,3")] {
        XCTAssertEqual(
          MacPowerButtonGuide(deviceIdentifier: id, modelIdentifier: model), .desktopRear, id)
      }
    }

    func testUnknownAndOtherEnclosuresUseGenericGuidance() {
      for id in ["unknown", "apple,j456", "apple,j180d"] {
        let guide = MacPowerButtonGuide(deviceIdentifier: id, modelIdentifier: "unknown")
        XCTAssertEqual(guide, .generic)
        XCTAssertNil(guide.assetName)
      }
    }
  }
#endif
