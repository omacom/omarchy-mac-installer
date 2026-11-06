#if os(macOS)
  import XCTest
  @testable import OmarchyAppleInstallerApp
  @testable import OmarchyAppleInstallerTrustCore

  @MainActor
  final class InterruptedResumeStartupTests: XCTestCase {
    func testOlderHelperIsNotAskedToReadRecordsOrInstalledAtStartup() async {
      let found = await InterruptedResumeStartup.available(
        helperIsCurrent: { false },
        discover: {
          XCTFail("Startup must not send the new request to an older helper")
          throw CocoaError(.featureUnsupported)
        })
      XCTAssertFalse(found)
    }

    func testOnlyAnIncompleteProtectedRecordOpensResume() async {
      let ticket = InterruptedInstallResumeTicket(
        id: UUID(), deviceIdentifier: "apple,j414s", allocationBytes: 100_000_000_000,
        metadataDigest: "sha256:" + String(repeating: "e", count: 64),
        payloadDigest: "sha256:" + String(repeating: "f", count: 64))
      for (available, record) in [(true, ticket), (false, ticket), (true, nil)] {
        let found = await InterruptedResumeStartup.available(
          helperIsCurrent: { true },
          discover: {
            InterruptedInstallResumeReply(
              ticket: record, automaticResumeAvailable: available, message: "Saved status")
          })
        XCTAssertEqual(found, available && record != nil)
      }
    }
  }
#endif
