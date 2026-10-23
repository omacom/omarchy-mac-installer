#if os(macOS)
  import Foundation
  import XCTest

  @testable import OmarchyAppleInstallerTrustCore

  final class RecoveryTemplateIdentityTests: XCTestCase {
    private let team = "ABCDE12345"
    private var developerIDClient: String {
      "anchor apple generic and identifier \"\(InstallerProductIdentity.appIdentifier)\""
        + " and certificate leaf[subject.OU] = \"\(team)\""
    }

    func testAnSMJobBlessHelperHoldsTheTemplateToItsClientsTeam() {
      // The job embedded in a blessed helper carries only the client requirement.
      XCTAssertEqual(
        RecoveryTemplateIdentity.requirement(environment: [
          InstallerProductIdentity.clientRequirementEnvironmentVariable: developerIDClient
        ]),
        "anchor apple generic and identifier \"org.aurorasilicon.omarchy.recovery.recovery\""
          + " and certificate 1[field.1.2.840.113635.100.6.2.6]"
          + " and certificate leaf[field.1.2.840.113635.100.6.1.13]"
          + " and certificate leaf[subject.OU] = \"\(team)\"")
    }

    func testAPinnedCodeHashWins() {
      let pinned = "cdhash H\"" + String(repeating: "c", count: 40) + "\""
      XCTAssertEqual(
        RecoveryTemplateIdentity.requirement(environment: [
          RecoveryTemplateIdentity.requirementEnvironmentVariable: pinned,
          InstallerProductIdentity.clientRequirementEnvironmentVariable: developerIDClient,
        ]), pinned)
    }

    func testAnInvalidPinIsNeverReplacedByTheClientsTeam() {
      for rejected in [
        "cdhash H\"" + String(repeating: "c", count: 39) + "\"",
        "cdhash H\"" + String(repeating: "c", count: 40) + "\" or anchor apple",
        "anchor apple generic",
        "",
      ] {
        XCTAssertNil(
          RecoveryTemplateIdentity.requirement(environment: [
            RecoveryTemplateIdentity.requirementEnvironmentVariable: rejected,
            InstallerProductIdentity.clientRequirementEnvironmentVariable: developerIDClient,
          ]), rejected)
      }
    }

    func testAnAdHocOrForeignClientGivesNoTemplateIdentity() {
      for client in [
        "identifier \"\(InstallerProductIdentity.appIdentifier)\"",
        "anchor apple generic and identifier \"other.app\" and certificate leaf[subject.OU] = \"\(team)\"",
        "anchor apple generic and identifier \"\(InstallerProductIdentity.appIdentifier)\""
          + " and certificate leaf[subject.OU] = \"short\"",
      ] {
        XCTAssertNil(
          RecoveryTemplateIdentity.requirement(environment: [
            InstallerProductIdentity.clientRequirementEnvironmentVariable: client
          ]), client)
      }
      XCTAssertNil(RecoveryTemplateIdentity.requirement(environment: [:]))
    }

    func testTheIdentifierIsTheOneTheRecoveryBuildSigns() throws {
      let script = try String(
        contentsOf: URL(fileURLWithPath: #filePath)
          .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
          .appendingPathComponent("Recovery/build-app.sh"), encoding: .utf8)
      XCTAssertTrue(script.contains("org.aurorasilicon.omarchy.recovery.$MODE"))
      XCTAssertEqual(
        RecoveryTemplateIdentity.identifier, "org.aurorasilicon.omarchy.recovery.recovery")
    }
  }
#endif
