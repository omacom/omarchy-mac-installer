import XCTest

@testable import OmarchyAppleInstallerTrustCore

final class ExperimentalReclaimEngineTests: XCTestCase {
  func testPinnedReclaimPlanKeepsTheCatalogImageAndReplacesTheEngine() throws {
    let catalogEngine = try artifact(
      role: "engine", name: "installer-published.tar.gz",
      digest: String(repeating: "a", count: 64))
    let metadata = try artifact(
      role: "metadata", name: "installer_data.json",
      digest: String(repeating: "b", count: 64))
    let payload = try artifact(
      role: "payload", name: "os.zip",
      digest: String(repeating: "c", count: 64))
    let record = PinnedInstallerRecord(
      deviceIdentifier: "apple,j700",
      asahiInstallerTag: "v0.9.2",
      asahiInstallerRevision: String(repeating: "1", count: 40),
      asahiInstallerDataRevision: String(repeating: "2", count: 40),
      downstreamRevision: String(repeating: "3", count: 40),
      engineVersion: "v0.9.2-omarchy.published",
      engineDigest: catalogEngine.expectedDigest,
      metadataDigest: metadata.expectedDigest,
      payloadDigest: payload.expectedDigest,
      evidenceRevision: "neo",
      delivery: PinnedInstallerDelivery(
        engine: catalogEngine, metadata: metadata, payload: payload)
    )

    let substituted = try ExperimentalReclaimEngine.pinnedInstaller(from: record)
    XCTAssertEqual(substituted.engineDigest, ValidationEngineArtifactLocator.expectedDigest)
    XCTAssertEqual(substituted.engineVersion, ValidationEngineArtifactLocator.version)
    XCTAssertEqual(
      substituted.delivery?.engine.fileName, ValidationEngineArtifactLocator.fileName)
    XCTAssertEqual(
      substituted.delivery?.engine.expectedSizeBytes,
      ValidationEngineArtifactLocator.expectedSizeBytes)
    XCTAssertEqual(substituted.delivery?.engine.sourceURL, catalogEngine.sourceURL)
    XCTAssertEqual(substituted.metadataDigest, record.metadataDigest)
    XCTAssertEqual(substituted.payloadDigest, record.payloadDigest)
    XCTAssertEqual(substituted.delivery?.metadata, metadata)
    XCTAssertEqual(substituted.delivery?.payload, payload)
    XCTAssertNotEqual(substituted.engineDigest, record.engineDigest)

    let plan = ValidatedEnginePlan(
      planDigest: String(repeating: "d", count: 64),
      deviceIdentifier: "apple,j700",
      storeIdentifier: "disk0",
      layoutDigest: "sha256:" + String(repeating: "e", count: 64),
      candidateKind: "reclaim",
      sourceIdentifier: "disk0s2",
      offsetBytes: 1,
      lengthBytes: 2,
      engineVersion: ValidationEngineArtifactLocator.version,
      engineDigest: ValidationEngineArtifactLocator.expectedDigest,
      metadataDigest: metadata.expectedDigest,
      payloadDigest: payload.expectedDigest,
      repairManifestDigest: nil,
      requiredHumanSteps: ["enterOneTrueRecovery", "authenticateMachineOwner", "createRecoveryAccount"]
    )
    XCTAssertTrue(ExperimentalReclaimEngine.isPinnedReclaimPlan(plan))

    let alongside = ValidatedEnginePlan(
      planDigest: plan.planDigest,
      deviceIdentifier: plan.deviceIdentifier,
      storeIdentifier: plan.storeIdentifier,
      layoutDigest: plan.layoutDigest,
      candidateKind: "resize",
      sourceIdentifier: plan.sourceIdentifier,
      offsetBytes: plan.offsetBytes,
      lengthBytes: plan.lengthBytes,
      engineVersion: plan.engineVersion,
      engineDigest: plan.engineDigest,
      metadataDigest: plan.metadataDigest,
      payloadDigest: plan.payloadDigest,
      repairManifestDigest: nil,
      requiredHumanSteps: plan.requiredHumanSteps
    )
    XCTAssertFalse(ExperimentalReclaimEngine.isPinnedReclaimPlan(alongside))
  }

  private func artifact(
    role: String, name: String, digest: String
  ) throws -> PinnedInstallerArtifact {
    try PinnedInstallerArtifact(
      role: role,
      sourceURL: URL(string: "https://mac.omarchy.org/v1/engine/\(name)")!,
      fileName: name,
      expectedDigest: "sha256:" + digest,
      expectedSizeBytes: 1024
    )
  }
}
