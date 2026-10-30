#if os(macOS)
  import Foundation
  import XCTest
  @testable import OmarchyAppleInstallerTrustCore

  /// A disk that carries an Omarchy-like stub the app did not make and cannot remove: a
  /// 5,999,996,928 B startup container with its four volumes, then a 2 GiB and a
  /// 271,186,919,424 B partition of type Linux Filesystem, and no partition of type EFI.
  /// macOS is 214,995,828,736 B. The sizes are those of a real Mac (`gpt -r show`, 4096-byte
  /// blocks). The tests run `RemovalLayout.recognize` on snapshots built from them and answer
  /// one question: can removal tell a new install from that stub?
  final class OmarchyRemovalForeignStubTests: XCTestCase {
    typealias F = RemovalFixtures

    static let macOSBytes: UInt64 = 214_995_828_736
    static let foreignStubBytes: UInt64 = 5_999_996_928
    static let bootBytes: UInt64 = 2_147_483_648
    static let rootBytes: UInt64 = 271_186_919_424
    static let gapAfterStub: UInt64 = 978_944
    static let gapBeforeRecovery: UInt64 = 1_159_168

    // Not this app's install: a stub and two Linux partitions, no EFI partition.
    static let foreign = F.Install(
      name: "Other Linux", stub: F.id(60), esp: F.id(61), linux: [F.id(62), F.id(63)],
      group: F.id(64))

    // A new install on a macOS 27 Mac: 6 GB stub, EFI, one root partition.
    static let test = F.Install(
      name: "Omarchy", stub: F.id(70), esp: F.id(71), linux: [F.id(72)], group: F.id(73))
    static let testStubBytes: UInt64 = 5_999_951_872
    static let testEspBytes: UInt64 = 524_288_000
    static let testRootBytes: UInt64 = 20_000_000_000
    static var testGroupBytes: UInt64 { testStubBytes + testEspBytes + testRootBytes }

    static var testRows: [F.Row] {
      [
        .part(test.stub, "Apple_APFS", testStubBytes),
        .part(test.esp, "EFI", testEspBytes, name: "EFI - OMARC"),
        .part(test.linux[0], "Linux Filesystem", testRootBytes),
      ]
    }

    static var foreignRows: [F.Row] {
      [
        .part(foreign.stub, "Apple_APFS", foreignStubBytes), .free(gapAfterStub),
        .part(foreign.linux[0], "Linux Filesystem", bootBytes),
        .part(foreign.linux[1], "Linux Filesystem", rootBytes), .free(gapBeforeRecovery),
      ]
    }

    private func refusal(_ snapshot: RemovalSnapshot) -> String? {
      do {
        _ = try RemovalLayout.recognize(snapshot)
        return nil
      } catch {
        return (error as? RemovalFailure)?.message ?? "\(error)"
      }
    }

    /// The disk as found: macOS, the foreign stub, two Linux partitions, Recovery.
    static var foreignStubAsFound: RemovalSnapshot {
      F.snapshot(macOSSize: macOSBytes, foreignRows, installs: [foreign])
    }

    private static let shapePrefix =
      "Found a startup container, but the partitions with it don’t match one installation"
    private static let severalPrefix = "Found more than one installation:"

    /// As found: the foreign stub is recognized as a stub, but the group has no EFI
    /// partition, so removal refuses and deletes nothing.
    func testTheDiskAsFoundIsRefusedAndNothingIsSelected() {
      let message = refusal(Self.foreignStubAsFound)
      XCTAssertNotNil(message, "the foreign stub must not be accepted as an installation")
      XCTAssertTrue(message?.hasPrefix(Self.shapePrefix) == true, "\(message ?? "nil")")
    }

    /// A new install made in the space after macOS, in front of the foreign stub: two stubs.
    func testANewInstallNextToTheForeignStubIsRefusedAsMoreThanOne() {
      let snapshot = F.snapshot(
        macOSSize: Self.macOSBytes - Self.testGroupBytes, Self.testRows + Self.foreignRows,
        installs: [Self.test, Self.foreign])
      let message = refusal(snapshot)
      XCTAssertTrue(message?.hasPrefix(Self.severalPrefix) == true, "\(message ?? "nil")")
    }

    /// Even with the foreign stub deleted by hand, its two Linux partitions make five members.
    func testANewInstallBesideTheForeignLinuxPartitionsIsRefusedByShape() {
      let snapshot = F.snapshot(
        macOSSize: Self.macOSBytes - Self.testGroupBytes,
        Self.testRows + Array(Self.foreignRows.dropFirst(2)), installs: [Self.test])
      let message = refusal(snapshot)
      XCTAssertTrue(message?.hasPrefix(Self.shapePrefix) == true, "\(message ?? "nil")")
    }

    /// The same disk with no other Omarchy-like layout: only the new partitions are named.
    func testANewInstallOnADiskWithoutTheForeignGroupIsRecognizedAlone() throws {
      let snapshot = F.snapshot(
        macOSSize: Self.macOSBytes - Self.testGroupBytes, Self.testRows, installs: [Self.test])
      let layout = try RemovalLayout.recognize(snapshot)
      guard case .installation(let found, macOS: let macOS, target: _) = layout else {
        return XCTFail("a lone new install must be recognized")
      }
      XCTAssertEqual(found.stub.uuid, Self.test.stub)
      XCTAssertEqual(found.esp.uuid, Self.test.esp)
      XCTAssertEqual(found.linux.map(\.uuid), Self.test.linux)
      XCTAssertEqual(macOS.uuid, F.mac)
      let named = Set(found.members.map(\.uuid))
      XCTAssertEqual(named, Set([Self.test.stub, Self.test.esp] + Self.test.linux))
      XCTAssertFalse(named.contains(Self.foreign.stub))
      XCTAssertFalse(named.contains(F.recovery))
    }
  }
#endif
