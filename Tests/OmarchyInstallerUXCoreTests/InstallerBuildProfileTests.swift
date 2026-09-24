#if os(macOS)
  import XCTest
  @testable import OmarchyInstallerUXCore

  final class InstallerBuildProfileTests: XCTestCase {
    func testStandardProfileRemainsUnchanged() {
      let profile = InstallerBuildProfile.resolve(infoDictionary: [:])
      XCTAssertEqual(profile, .standard)
      XCTAssertTrue(profile.allowsEncryption)
      XCTAssertTrue(profile.showsReleaseChannels)
      XCTAssertEqual(profile.workspaceName, "com.omarchy.mx.installer")
      XCTAssertTrue(profile.startupSequence.contains("GRUB"))
    }

    func testLegacyPlainProfileRemainsIsolatedAndCannotEncrypt() {
      let profile = InstallerBuildProfile.resolve(infoDictionary: ["OmarchyPrivatePlainTest": true])
      XCTAssertEqual(profile, .privatePlain)
      XCTAssertFalse(profile.allowsEncryption)
      XCTAssertFalse(profile.showsReleaseChannels)
      XCTAssertEqual(profile.workspaceName, "com.omarchy.mx.installer.private-m3-20260922")
      XCTAssertTrue(profile.startupSequence.contains("GRUB"))
    }

    func testPrivateLimineCanEncryptWithoutUsingPublicOrChrisState() {
      let profile = InstallerBuildProfile.resolve(infoDictionary: ["OmarchyPrivateLimineTest": true]
      )
      XCTAssertEqual(profile, .privateLimine)
      XCTAssertTrue(profile.allowsEncryption)
      XCTAssertFalse(profile.showsReleaseChannels)
      XCTAssertEqual(profile.workspaceName, "com.omarchy.mx.installer.private-limine-20260922")
      XCTAssertNotEqual(profile.workspaceName, InstallerBuildProfile.standard.workspaceName)
      XCTAssertNotEqual(profile.workspaceName, InstallerBuildProfile.privatePlain.workspaceName)
      XCTAssertTrue(profile.startupSequence.contains("Limine"))
      XCTAssertFalse(profile.startupSequence.contains("GRUB"))
    }

    func testConflictingFlagsRetainPlainRestriction() {
      let profile = InstallerBuildProfile.resolve(infoDictionary: [
        "OmarchyPrivatePlainTest": true, "OmarchyPrivateLimineTest": true,
      ])
      XCTAssertEqual(profile, .privatePlain)
      XCTAssertFalse(profile.allowsEncryption)
    }

  }
#endif
