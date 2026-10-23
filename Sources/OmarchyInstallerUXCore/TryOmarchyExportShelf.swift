#if os(macOS)
  import Foundation
  import OmarchyAppleInstallerTrustCore

  /// The checked Try Omarchy exports from the latest look, kept by export id
  /// so the page can act on an id without ever holding a trust object.
  public final class TryOmarchyExportShelf: @unchecked Sendable {
    private let lock = NSLock()
    private var exports: [String: TryOmarchyExport] = [:]
    /// Each check is numbered; a newer check or a cancel stops older ones.
    private var verification = 0
    private let discovery: TryOmarchyExportDiscovery

    public init(discovery: TryOmarchyExportDiscovery) {
      self.discovery = discovery
    }

    /// Try Omarchy's Exports folder in this user's home.
    public static func home() -> TryOmarchyExportShelf {
      TryOmarchyExportShelf(
        discovery: TryOmarchyExportDiscovery(
          directory: TryOmarchyExportDiscovery.exportsDirectory(
            home: FileManager.default.homeDirectoryForCurrentUser
          )
        )
      )
    }

    public func look() async -> TryOmarchyExportsDisplay {
      let discovery = discovery
      let result = await Self.offCooperativePool { discovery.discover() }
      switch result {
      case .success(let found):
        lock.withLock {
          exports = Dictionary(
            uniqueKeysWithValues: found.exports.map { ($0.receipt.exportID, $0) }
          )
        }
        return .found(
          found.exports.map(TryOmarchyExportDisplay.init),
          unusable: found.rejected.count
        )
      case .failure:
        lock.withLock { exports = [:] }
        return .unavailable
      }
    }

    /// Hashes the export the latest look saw; a replaced or changed file fails.
    public func verify(id: String) async -> TryOmarchyExportCheck {
      guard let export = lock.withLock({ exports[id] }) else {
        return .failed(PlainLanguage.tryExportMissing)
      }
      let current = lock.withLock {
        verification += 1
        return verification
      }
      let discovery = discovery
      let result = await Self.offCooperativePool {
        Result {
          try discovery.verify(export) { [self] in lock.withLock { verification != current } }
        }
      }
      switch result {
      case .success(let verified):
        lock.withLock {
          if exports[id] == export { exports[id] = verified }
        }
        return .verified
      case .failure(let problem as TryOmarchyExportProblem):
        return .failed(PlainLanguage.tryExportFailure(problem))
      case .failure:
        return .failed(PlainLanguage.tryExportMissing)
      }
    }

    /// Stops the check under way, if any; its answer is no longer wanted.
    public func cancelVerification() {
      lock.withLock { verification += 1 }
    }

    /// File reads and hashing can take a while; keep them off the shared pool.
    private static func offCooperativePool<T: Sendable>(
      _ work: @escaping @Sendable () -> T
    ) async -> T {
      await withCheckedContinuation { continuation in
        DispatchQueue.global(qos: .userInitiated).async {
          continuation.resume(returning: work())
        }
      }
    }
  }
#endif
