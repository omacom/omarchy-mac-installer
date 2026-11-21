#if os(macOS)
  import XCTest
  @testable import OmarchyAppleInstallerApp

  @MainActor
  final class ProMotionSelectionTests: XCTestCase {
    private func mode(
      _ id: Int32 = 1, proMotion: Bool? = false, hz: Double = 60,
      width: Int = 1728, pixels: Int = 3456, flags: UInt32 = 3,
      usable: Bool = true
    ) -> ProMotionMode {
      ProMotionMode(
        id: id, width: width, height: 1117, pixelWidth: pixels, pixelHeight: 2234,
        flags: flags, refreshRate: hz, usable: usable, isProMotion: proMotion)
    }

    private func snapshot(
      current: ProMotionMode? = nil, modes: [ProMotionMode]? = nil,
      builtIn: Bool = true, mirrored: Bool = false, displayID: UInt32 = 7
    ) -> ProMotionSnapshot {
      ProMotionSnapshot(
        displayID: displayID, builtIn: builtIn, mirrored: mirrored,
        current: current ?? mode(),
        modes: modes ?? [mode(), mode(2, proMotion: true, hz: 120)])
    }

    func testSelectsOnlyExplicitProMotionAtTheSameLayout() {
      XCTAssertEqual(snapshot().selection, .available(2))
      for hz in [0.0, 120.0] {
        XCTAssertEqual(
          snapshot(modes: [mode(2, proMotion: nil, hz: hz)]).selection,
          hz == 0 ? .hidden : .manual)
        XCTAssertNotEqual(
          snapshot(modes: [mode(2, proMotion: false, hz: hz)]).selection, .available(2))
      }
    }

    func testDoesNotChangeResolutionScalingOrFlags() {
      for candidate in [
        mode(2, proMotion: true, width: 1512),
        mode(2, proMotion: true, pixels: 1728),
        mode(2, proMotion: true, flags: 4),
        mode(2, proMotion: true, usable: false),
      ] {
        XCTAssertEqual(snapshot(modes: [candidate]).selection, .manual)
      }
    }

    func testAmbiguousModesRequireSettingsButDuplicateIDsDoNot() {
      let candidate = mode(2, proMotion: true)
      XCTAssertEqual(snapshot(modes: [candidate, candidate]).selection, .available(2))
      XCTAssertEqual(
        snapshot(modes: [candidate, mode(3, proMotion: true)]).selection, .manual)
    }

    func testExternalMirroredAndOrdinaryPanelsAreNotChanged() {
      XCTAssertEqual(snapshot(builtIn: false).selection, .hidden)
      XCTAssertEqual(snapshot(mirrored: true).selection, .manual)
      XCTAssertEqual(snapshot(modes: [mode()]).selection, .hidden)
      XCTAssertEqual(snapshot(modes: [mode()], mirrored: true).selection, .hidden)
    }

    func testAlreadyEnabledIsNotOfferedAgain() {
      XCTAssertEqual(snapshot(current: mode(2, proMotion: true)).selection, .enabled)
    }

    func testSimulationNeverReadsOrChangesTheHost() throws {
      let access = FakeDisplay(snapshot: snapshot())
      let action = ProMotionAction(access: access, isSimulation: true)
      XCTAssertEqual(try action.inspect(), .hidden)
      XCTAssertThrowsError(try action.enable())
      XCTAssertEqual(access.reads, 0)
      XCTAssertEqual(access.writes, 0)
    }

    func testSuccessRequiresReinspectionOfTheTarget() throws {
      let access = FakeDisplay(snapshot: snapshot())
      access.after = snapshot(current: mode(2, proMotion: true, hz: 120))
      try ProMotionAction(access: access, isSimulation: false).enable()
      XCTAssertEqual(access.reads, 2)
      XCTAssertEqual(access.writes, 1)
      XCTAssertEqual(access.appliedDisplay, 7)
      XCTAssertEqual(access.appliedMode, 2)
    }

    func testSuccessfulAPICallWithoutModeChangeIsNotSuccess() {
      let access = FakeDisplay(snapshot: snapshot())
      XCTAssertThrowsError(try ProMotionAction(access: access, isSimulation: false).enable())
      XCTAssertEqual(access.writes, 1)
    }

    func testReplacementDisplayOrChangedGeometryCannotVerify() {
      for after in [
        snapshot(current: mode(2, proMotion: true), displayID: 8),
        snapshot(current: mode(2, proMotion: true, width: 1512)),
        snapshot(current: mode(2, proMotion: true), mirrored: true),
      ] {
        let access = FakeDisplay(snapshot: snapshot())
        access.after = after
        XCTAssertThrowsError(try ProMotionAction(access: access, isSimulation: false).enable())
      }
    }

    func testUnavailableAndAlreadyEnabledDoNotWrite() {
      for state in [
        snapshot(builtIn: false), snapshot(mirrored: true),
        snapshot(current: mode(2, proMotion: true)), snapshot(modes: [mode()]),
      ] {
        let access = FakeDisplay(snapshot: state)
        XCTAssertThrowsError(try ProMotionAction(access: access, isSimulation: false).enable())
        XCTAssertEqual(access.writes, 0)
      }
    }

    func testConfigurationFailureIsPropagatedWithoutRetry() {
      let access = FakeDisplay(snapshot: snapshot())
      access.failWrite = true
      XCTAssertThrowsError(try ProMotionAction(access: access, isSimulation: false).enable())
      XCTAssertEqual(access.writes, 1)
      XCTAssertEqual(access.reads, 1)
    }
  }

  @MainActor
  private final class FakeDisplay: ProMotionDisplayAccess {
    let before: ProMotionSnapshot
    var after: ProMotionSnapshot?
    var failWrite = false
    var reads = 0
    var writes = 0
    var appliedDisplay: UInt32?
    var appliedMode: Int32?

    init(snapshot: ProMotionSnapshot) { before = snapshot }

    func inspect() throws -> ProMotionSnapshot? {
      reads += 1
      return writes > 0 ? (after ?? before) : before
    }

    func apply(displayID: UInt32, modeID: Int32) throws {
      writes += 1
      appliedDisplay = displayID
      appliedMode = modeID
      if failWrite { throw ProMotionError.configurationFailed }
    }
  }
#endif
