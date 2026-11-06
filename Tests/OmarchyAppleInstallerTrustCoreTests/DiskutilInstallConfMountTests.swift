#if os(macOS)
  import Foundation
  import XCTest

  @testable import OmarchyAppleInstallerTrustCore

  final class DiskutilInstallConfMountTests: XCTestCase {
    private let destination = URL(
      fileURLWithPath: "/private/var/run/omarchy-mount-test", isDirectory: true)

    func testAlreadyMountedESPIsVerifiedBeforeReuse() throws {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC")
      let operatorUnderTest = DiskutilInstallConfESPOperator(
        commands: disk.run, openMount: disk.openMount)
      let descriptor = try operatorUnderTest.mount("disk5s1", at: destination)
      defer { Darwin.close(descriptor) }
      XCTAssertEqual(disk.openedMounts, ["disk5s1:/Volumes/EFI - OMARC"])
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info"])
    }

    func testUnmountedESPIsMountedAtThePrivateDestination() throws {
      let disk = MountFixture()
      let operatorUnderTest = DiskutilInstallConfESPOperator(
        commands: disk.run, openMount: disk.openMount)
      let descriptor = try operatorUnderTest.mount("disk5s1", at: destination)
      defer { Darwin.close(descriptor) }
      XCTAssertEqual(disk.mountPoint, destination.path)
      XCTAssertEqual(disk.openedMounts, ["disk5s1:\(destination.path)"])
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info", "mount", "info"])
    }

    func testSuccessfulCommandAtWrongMountPointIsRejected() {
      let disk = MountFixture(forcedMountPoint: "/Volumes/EFI - OMARC")
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination)) {
        XCTAssertEqual($0 as? InstallConfESPError, .mountFailed)
      }
      XCTAssertNil(disk.mountPoint)
      XCTAssertEqual(disk.calls.last, ["unmount", "disk5s1"])
    }

    func testMountThatAttachesThenFailsIsUnmounted() {
      let disk = MountFixture(failsAfterMount: true)
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination))
      XCTAssertNil(disk.mountPoint)
      XCTAssertEqual(disk.calls.last, ["unmount", "disk5s1"])
    }

    func testReadOnlyMountIsRejectedAndUnmounted() {
      let disk = MountFixture(writable: false)
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination))
      XCTAssertNil(disk.mountPoint)
      XCTAssertEqual(disk.calls.last, ["unmount", "disk5s1"])
    }

    func testReadOnlyExistingMountIsRejectedWithoutUnmounting() {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC", writable: false)
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination))
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info"])
      XCTAssertEqual(disk.mountPoint, "/Volumes/EFI - OMARC")
    }

    func testBusyExistingMountDoesNotBlockSavingTheChoice() throws {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC", refusesUnmount: true)
      let operatorUnderTest = DiskutilInstallConfESPOperator(
        commands: disk.run, openMount: disk.openMount)
      let descriptor = try operatorUnderTest.mount("disk5s1", at: destination)
      defer { Darwin.close(descriptor) }
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info"])
      XCTAssertEqual(disk.mountPoint, "/Volumes/EFI - OMARC")
    }

    func testRefusedUnmountRestartsSpotlightOnceThenUnmountsNormally() throws {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC", unmountRefusals: 1)
      let operatorUnderTest = DiskutilInstallConfESPOperator(
        commands: disk.run, openMount: disk.openMount,
        restartSpotlightServer: disk.restartSpotlightServer)
      try operatorUnderTest.unmount("disk5s1")
      XCTAssertEqual(disk.spotlightRestarts, 1)
      XCTAssertEqual(disk.calls, [["unmount", "disk5s1"], ["unmount", "disk5s1"]])
      XCTAssertNil(disk.mountPoint)
    }

    func testUnmountThatSucceedsLeavesSpotlightAlone() throws {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC")
      let operatorUnderTest = DiskutilInstallConfESPOperator(
        commands: disk.run, openMount: disk.openMount,
        restartSpotlightServer: disk.restartSpotlightServer)
      try operatorUnderTest.unmount("disk5s1")
      XCTAssertEqual(disk.spotlightRestarts, 0)
      XCTAssertEqual(disk.calls, [["unmount", "disk5s1"]])
    }

    func testUnmountStillRefusedAfterTheRestartIsNeverForced() {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC", refusesUnmount: true)
      let operatorUnderTest = DiskutilInstallConfESPOperator(
        commands: disk.run, openMount: disk.openMount,
        restartSpotlightServer: disk.restartSpotlightServer)
      XCTAssertThrowsError(try operatorUnderTest.unmount("disk5s1"))
      XCTAssertEqual(disk.spotlightRestarts, 1)
      XCTAssertEqual(disk.calls, [["unmount", "disk5s1"], ["unmount", "disk5s1"]])
      XCTAssertEqual(disk.mountPoint, "/Volumes/EFI - OMARC")
    }

    func testExistingPathOnAnotherFilesystemIsRejected() {
      let disk = MountFixture(mountPoint: FileManager.default.temporaryDirectory.path)
      let operatorUnderTest = DiskutilInstallConfESPOperator(commands: disk.run)
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination)) {
        XCTAssertEqual($0 as? InstallConfESPError, .mountFailed)
      }
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info"])
    }

    func testUnverifiedExistingMountIsRejected() {
      let disk = MountFixture(mountPoint: "/Volumes/EFI - OMARC")
      let operatorUnderTest = DiskutilInstallConfESPOperator(
        commands: disk.run,
        openMount: { _, _ in throw InstallConfESPError.mountFailed })
      XCTAssertThrowsError(try operatorUnderTest.mount("disk5s1", at: destination))
      XCTAssertEqual(disk.calls.map { $0[0] }, ["info"])
    }
  }

  /// Models diskutil's successful no-op when mounting an already mounted volume.
  private final class MountFixture: @unchecked Sendable {
    var mountPoint: String?
    let forcedMountPoint: String?
    let writable: Bool
    var unmountRefusalsRemaining: Int
    let failsAfterMount: Bool
    var calls: [[String]] = []
    var openedMounts: [String] = []
    var spotlightRestarts = 0

    init(
      mountPoint: String? = nil, forcedMountPoint: String? = nil,
      writable: Bool = true, refusesUnmount: Bool = false, unmountRefusals: Int = 0,
      failsAfterMount: Bool = false
    ) {
      self.mountPoint = mountPoint
      self.forcedMountPoint = forcedMountPoint
      self.writable = writable
      self.unmountRefusalsRemaining = refusesUnmount ? Int.max : unmountRefusals
      self.failsAfterMount = failsAfterMount
    }

    func restartSpotlightServer() {
      spotlightRestarts += 1
    }

    func openMount(_ identifier: String, at mountPoint: URL) throws -> Int32 {
      openedMounts.append("\(identifier):\(mountPoint.path)")
      let descriptor = Darwin.open(
        FileManager.default.temporaryDirectory.path, O_RDONLY | O_DIRECTORY)
      guard descriptor >= 0 else { throw InstallConfESPError.mountFailed }
      return descriptor
    }

    func run(_ arguments: [String]) throws -> Data {
      calls.append(arguments)
      switch arguments[0] {
      case "info":
        var info: [String: Any] = ["DeviceIdentifier": "disk5s1", "WritableVolume": writable]
        if let mountPoint { info["MountPoint"] = mountPoint }
        return try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
      case "mount":
        if mountPoint == nil { mountPoint = forcedMountPoint ?? arguments[2] }
        if failsAfterMount { throw InstallConfESPError.mountFailed }
      case "unmount":
        if unmountRefusalsRemaining > 0 {
          unmountRefusalsRemaining -= 1
          throw InstallConfESPError.mountFailed
        }
        mountPoint = nil
      default:
        XCTFail("Unexpected disk command: \(arguments)")
      }
      return Data()
    }
  }
#endif
