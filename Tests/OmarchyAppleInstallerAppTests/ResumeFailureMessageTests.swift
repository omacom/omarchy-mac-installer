#if os(macOS)
  import XCTest
  import OmarchyAppleInstallerTrustCore
  @testable import OmarchyAppleInstallerApp

  final class ResumeFailureMessageTests: XCTestCase {
    func testEngineSummarySurvivesXPCErrorPresentation() {
      let notice = EngineFailureNotice(
        reason: .unclassified, exitStatus: 1, diskUnchanged: false,
        summary: "omarchy_resume.ResumeError: resume input changed")
      XCTAssertEqual(
        OmarchyResumeView.failureDetail(EngineXPCSubmissionError.engineFailed(notice)),
        notice.summary)
    }

    func testHelperRefusalRetainsItsDomainAndCode() {
      XCTAssertEqual(
        OmarchyResumeView.failureDetail(
          EngineXPCSubmissionError.helperRejected(domain: "test.helper", code: 7)),
        "The installation service refused the request (test.helper, code 7).")
    }
  }
#endif
