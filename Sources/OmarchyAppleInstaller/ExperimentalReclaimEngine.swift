#if os(macOS)
  import Foundation

  public enum ExperimentalReclaimEngineError: Error, Equatable, Sendable {
    /// The signed catalog's installer record has no engine to stand in for.
    case catalogEngineHasNoDelivery
    /// The archive is not the engine this app was built with.
    case archiveIsNotThePinnedEngine
  }

  /// Experimental single-boot ships in the engine pinned into the app before
  /// a signed catalog publishes that same archive. A release whose engine has
  /// no reclaim candidate may run this pinned archive, and no other file, for
  /// that plan. The catalog still chooses the OS image and its metadata.
  public enum ExperimentalReclaimEngine {
    public static func isPinnedReclaimPlan(_ plan: ValidatedEnginePlan) -> Bool {
      plan.candidateKind == "reclaim"
        && plan.engineVersion == ValidationEngineArtifactLocator.version
        && plan.engineDigest == ValidationEngineArtifactLocator.expectedDigest
    }

    /// The catalog record with its engine identity replaced by the app pin.
    /// Metadata, payload and repair manifest stay the catalog's.
    public static func pinnedInstaller(
      from catalog: PinnedInstallerRecord
    ) throws -> PinnedInstallerRecord {
      guard let delivery = catalog.delivery else {
        throw ExperimentalReclaimEngineError.catalogEngineHasNoDelivery
      }
      let engine = try bundledArtifact(standingInFor: delivery.engine)
      return PinnedInstallerRecord(
        deviceIdentifier: catalog.deviceIdentifier,
        operation: catalog.operation,
        asahiInstallerTag: catalog.asahiInstallerTag,
        asahiInstallerRevision: catalog.asahiInstallerRevision,
        asahiInstallerDataRevision: catalog.asahiInstallerDataRevision,
        downstreamRevision: catalog.downstreamRevision,
        engineVersion: ValidationEngineArtifactLocator.version,
        engineDigest: ValidationEngineArtifactLocator.expectedDigest,
        metadataDigest: catalog.metadataDigest,
        payloadDigest: catalog.payloadDigest,
        repairManifestDigest: catalog.repairManifestDigest,
        evidenceRevision: catalog.evidenceRevision,
        delivery: PinnedInstallerDelivery(
          engine: engine,
          metadata: delivery.metadata,
          payload: delivery.payload,
          repairManifest: delivery.repairManifest
        )
      )
    }

    public static func assets(
      _ assets: PreparedInstallerAssets,
      bundled: PinnedAsahiEngineArchive
    ) throws -> PreparedInstallerAssets {
      guard bundled.expectedDigest == ValidationEngineArtifactLocator.expectedDigest,
        bundled.expectedSizeBytes == ValidationEngineArtifactLocator.expectedSizeBytes
      else {
        throw ExperimentalReclaimEngineError.archiveIsNotThePinnedEngine
      }
      let installer = try pinnedInstaller(from: assets.installer)
      guard let engine = installer.delivery?.engine else {
        throw ExperimentalReclaimEngineError.catalogEngineHasNoDelivery
      }
      return PreparedInstallerAssets(
        catalogIdentity: assets.catalogIdentity,
        installer: installer,
        engine: StagedInstallerArtifact(
          artifact: engine,
          fileURL: bundled.fileURL,
          reusedExistingFile: true
        ),
        metadata: assets.metadata,
        payload: assets.payload,
        repairManifest: assets.repairManifest,
        installerCompatibility: assets.installerCompatibility
      )
    }

    private static func bundledArtifact(
      standingInFor catalogEngine: PinnedInstallerArtifact
    ) throws -> PinnedInstallerArtifact {
      try PinnedInstallerArtifact(
        role: catalogEngine.role,
        sourceURL: catalogEngine.sourceURL,
        fileName: ValidationEngineArtifactLocator.fileName,
        expectedDigest: ValidationEngineArtifactLocator.expectedDigest,
        expectedSizeBytes: ValidationEngineArtifactLocator.expectedSizeBytes
      )
    }
  }

  extension PreparedInstallerRelease {
    /// Plan and run experimental single-boot with the app-pinned engine.
    public func usingBundledReclaimEngine(
      _ bundled: PinnedAsahiEngineArchive
    ) throws -> PreparedInstallerRelease {
      PreparedInstallerRelease(
        assets: try ExperimentalReclaimEngine.assets(assets, bundled: bundled),
        catalogDocuments: catalogDocuments
      )
    }
  }
#endif
