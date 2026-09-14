import XCTest

@testable import OmarchyAppleInstallerTrustCore

final class InstallerAllocationRecommendationTests: XCTestCase {
  private let gib: UInt64 = 1_073_741_824

  func testPrefersFreeExtentAndBalancedTarget() throws {
    let resize = candidate(
      kind: "resize",
      source: "disk0s2",
      length: 600 * gib,
      minimumInstall: 64 * gib,
      minimumContainer: 200 * gib
    )
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 300 * gib,
      minimumInstall: 64 * gib
    )

    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([resize, free])
    )

    XCTAssertEqual(recommendation.candidate, free)
    XCTAssertEqual(recommendation.requestedLengthBytes, 128 * gib)
  }

  func testClampsToAlignedMaximumWithoutViolatingMinimum() throws {
    let unit = PinnedAsahiPlanRequest.allocationUnitBytes
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 90 * gib + 333,
      minimumInstall: 64 * gib + 1
    )

    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([free])
    )

    XCTAssertEqual(recommendation.requestedLengthBytes % unit, 0)
    XCTAssertLessThanOrEqual(
      recommendation.requestedLengthBytes,
      free.lengthBytes
    )
    XCTAssertGreaterThanOrEqual(
      recommendation.requestedLengthBytes,
      free.minimumInstallBytes
    )
  }

  func testFailsClosedWhenNoCandidateCanMeetMinimum() {
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 32 * gib,
      minimumInstall: 64 * gib
    )

    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(inventory: inventory([free]))
    ) {
      XCTAssertEqual(
        $0 as? InstallerAllocationRecommendationError,
        .noEligibleCandidate
      )
    }
  }

  func testResizeMaximumLeavesRoomForHandoffCopiesBeforeApproval() throws {
    let resize = candidate(
      kind: "resize",
      source: "disk0s2",
      length: 494_384_795_648,
      minimumInstall: 76_562_825_216,
      minimumContainer: 118_385_364_992
    )
    let oldMaximum: UInt64 = 375_999_430_656
    let handoffBytes: UInt64 = 8_492_104_840
    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([resize]),
      targetBytes: oldMaximum,
      reservedBytes: handoffBytes
    )

    XCTAssertEqual(recommendation.candidate, resize)
    XCTAssertEqual(recommendation.requestedLengthBytes, recommendation.maximumBytes)
    XCTAssertEqual(recommendation.maximumBytes % PinnedAsahiPlanRequest.allocationUnitBytes, 0)
    XCTAssertGreaterThanOrEqual(
      resize.lengthBytes - resize.minimumContainerBytes - recommendation.maximumBytes,
      handoffBytes
    )

    // The reported failure: helper import raised the minimum after review.
    let executionCandidate = candidate(
      kind: "resize",
      source: "disk0s2",
      length: resize.lengthBytes,
      minimumInstall: resize.minimumInstallBytes,
      minimumContainer: 122_248_232_960
    )
    XCTAssertThrowsError(
      try PinnedAsahiPlanRequest(
        inventory: inventory([executionCandidate]), candidate: executionCandidate,
        requestedLengthBytes: oldMaximum
      )
    )
    XCTAssertNoThrow(
      try PinnedAsahiPlanRequest(
        inventory: inventory([executionCandidate]), candidate: executionCandidate,
        requestedLengthBytes: recommendation.requestedLengthBytes
      )
    )
  }

  func testHandoffReserveDoesNotReduceAlreadyFreeExtent() throws {
    let free = candidate(
      kind: "free", source: "disk0s3", length: 100 * gib,
      minimumInstall: 64 * gib
    )
    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([free]), targetBytes: 100 * gib,
      reservedBytes: UInt64.max
    )

    XCTAssertEqual(recommendation.maximumBytes, 100 * gib)
    XCTAssertEqual(recommendation.requestedLengthBytes, 100 * gib)
  }

  func testResizeReserveCannotUnderflowOrViolateInstallMinimum() {
    let resize = candidate(
      kind: "resize", source: "disk0s2", length: 200 * gib,
      minimumInstall: 64 * gib, minimumContainer: 100 * gib
    )
    for reserve in [37 * gib, 100 * gib, UInt64.max] {
      XCTAssertThrowsError(
        try InstallerAllocationRecommendation(
          inventory: inventory([resize]), reservedBytes: reserve
        )
      ) {
        XCTAssertEqual(
          $0 as? InstallerAllocationRecommendationError, .noEligibleCandidate
        )
      }
    }
  }

  func testReplaceOnlyInventoryFailsClosed() {
    let replace = candidate(
      kind: "replace",
      source: "disk0s3",
      length: 300 * gib,
      minimumInstall: 64 * gib,
      identityDigest: "sha256:" + String(repeating: "9", count: 64)
    )

    XCTAssertThrowsError(
      try InstallerAllocationRecommendation(inventory: inventory([replace]))
    ) {
      XCTAssertEqual(
        $0 as? InstallerAllocationRecommendationError,
        .noEligibleCandidate
      )
    }
  }

  func testReplaceCandidateIsNeverAutoSelected() throws {
    let replace = candidate(
      kind: "replace",
      source: "disk0s2",
      length: 600 * gib,
      minimumInstall: 64 * gib,
      identityDigest: "sha256:" + String(repeating: "9", count: 64)
    )
    let free = candidate(
      kind: "free",
      source: "disk0s3",
      length: 300 * gib,
      minimumInstall: 64 * gib
    )

    let recommendation = try InstallerAllocationRecommendation(
      inventory: inventory([replace, free])
    )

    XCTAssertEqual(recommendation.candidate, free)
  }

  private func inventory(
    _ candidates: [ValidatedEngineCandidate]
  ) -> ValidatedEngineInventory {
    ValidatedEngineInventory(
      layoutDigest: "sha256:" + String(repeating: "a", count: 64),
      systemStoreIdentifier: "disk0",
      candidates: candidates
    )
  }

  private func candidate(
    kind: String,
    source: String,
    length: UInt64,
    minimumInstall: UInt64,
    minimumContainer: UInt64 = 0,
    identityDigest: String? = nil
  ) -> ValidatedEngineCandidate {
    ValidatedEngineCandidate(
      kind: kind,
      sourceIdentifier: source,
      offsetBytes: 0,
      lengthBytes: length,
      minimumInstallBytes: minimumInstall,
      minimumContainerBytes: minimumContainer,
      identityDigest: identityDigest
    )
  }
}
