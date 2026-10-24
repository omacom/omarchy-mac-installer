#if os(macOS)
  import CryptoKit
  import Foundation
  import OmarchyAppleInstallerTrustCore
  import TryOmarchyKitClient

  public enum TrialKitUpdateOutcome: Equatable, Sendable {
    case notDue
    /// Cellular or Low Data Mode: the next start asks again.
    case meteredNetwork
    /// The kit in use is the newest; a development kit always is.
    case upToDate(TryOmarchyKitVersion)
    /// The latest kit speaks a run-mode protocol this installer does not.
    case needsNewerInstaller(TryOmarchyKitVersion)
    case installed(TryOmarchyKitVersion)
    case failed(String)
  }

  /// Keeps Try's kit up to date between installer releases. Try starts with
  /// the newest kit it has: a fetched kit only while it is newer than the
  /// embedded one. Each start may ask Try Omarchy's latest release, once a
  /// day, for a newer kit, which is checked and switched to for the next
  /// start. Nothing here ever stops Try from starting.
  public final class TrialKitUpdater: @unchecked Sendable {
    public static let checkInterval: TimeInterval = 86_400

    /// Where kits come from and which signatures they need. Release builds
    /// always use the defaults; debug builds take a local server and accept
    /// ad hoc signatures through the environment, for checking a locally
    /// built kit.
    public struct Settings: Equatable, Sendable {
      public static let releasesKey = "OMARCHY_TRY_KIT_RELEASES_URL"
      public static let adHocKey = "OMARCHY_TRY_KIT_ACCEPT_AD_HOC"

      public var releases: URL
      public var policy: TrialKitSignaturePolicy

      public static let release = Self(releases: TrialKitReleases.defaultBase, policy: .release)

      public static func from(environment: [String: String]) -> Self {
        #if DEBUG
          var settings = release
          if let text = environment[releasesKey], let url = URL(string: text),
            url.scheme == "https" || TrialKitReleases(base: url).allowsLoopbackHTTP
          {
            settings.releases = url
          }
          if environment[adHocKey] == "1" { settings.policy = .anyValidSignature }
          return settings
        #else
          return release
        #endif
      }
    }

    private let store: TrialKitStore
    private let embedded: TrialKitLayout
    private let source: any TrialKitReleaseSource
    private let signatures: TrialKitSignatureCheck
    private let startSignatures: TrialKitSignatureCheck
    private let network: any InstallerNetworkPathObserving
    private let now: @Sendable () -> Date
    private let networkWait: Duration
    private let lock = NSLock()
    private var checking = false

    /// `signatures` checks a download; `startSignatures`, by default the
    /// same, checks the fetched kit again at every start.
    public init(
      store: TrialKitStore, embedded: TrialKitLayout, source: any TrialKitReleaseSource,
      signatures: TrialKitSignatureCheck, startSignatures: TrialKitSignatureCheck? = nil,
      network: any InstallerNetworkPathObserving,
      now: @escaping @Sendable () -> Date = { Date() }, networkWait: Duration = .seconds(10)
    ) {
      self.store = store
      self.embedded = embedded
      self.source = source
      self.signatures = signatures
      self.startSignatures = startSignatures ?? signatures
      self.network = network
      self.now = now
      self.networkWait = networkWait
    }

    /// Whether Try may run a fetched kit with this manifest, given the
    /// embedded kit's. A kit without a manifest is older than any with one.
    public static func prefers(
      _ fetched: TryOmarchyKitManifest, over embedded: TryOmarchyKitManifest?
    ) -> Bool {
      guard !fetched.version.isDevelopment, fetched.isSupported else { return false }
      guard let embedded else { return true }
      return fetched.version > embedded.version
    }

    /// The kit a start runs. A fetched kit is used only while it is newer
    /// than the embedded one and its signatures still pass, since its folder
    /// is writable by this user; its lease keeps it in place until released.
    /// Fetched kits nothing runs, and older downloads, are removed. This
    /// reads and hashes the kit, so it belongs off the main actor.
    public func kitForNextStart() -> TrialKitChoice {
      guard let fetched = store.current() else {
        store.removeUnused()
        return TrialKitChoice(layout: embedded)
      }
      guard let manifest = try? fetched.manifest(),
        Self.prefers(manifest, over: try? embedded.manifest())
      else {
        store.removeUnused(keepingCurrent: false)
        return TrialKitChoice(layout: embedded)
      }
      var lease = store.lease(fetched)
      guard lease != nil, fetched.hasLauncher else {
        store.removeUnused()
        return TrialKitChoice(layout: embedded, refusal: "the fetched kit is missing or in use")
      }
      do {
        try startSignatures.verify(fetched.root)
      } catch {
        lease = nil
        return refuse(Self.describe(error))
      }
      store.removeUnused()
      return TrialKitChoice(layout: fetched, version: manifest.version, lease: lease)
    }

    /// A fetched kit that fails its check at a start is removed, unless
    /// another start runs it, so the next check downloads it again.
    private func refuse(_ reason: String) -> TrialKitChoice {
      store.removeUnused(keepingCurrent: false)
      return TrialKitChoice(layout: embedded, refusal: reason)
    }

    public func checkIfDue() async -> TrialKitUpdateOutcome {
      let began = lock.withLock { () -> Bool in
        defer { checking = true }
        return !checking
      }
      guard began else { return .notDue }
      defer { lock.withLock { checking = false } }
      let date = now()
      if let last = store.lastCheck(), last <= date,
        date.timeIntervalSince(last) < Self.checkInterval
      {
        return .notDue
      }
      let embeddedManifest = try? embedded.manifest()
      if let embeddedManifest, embeddedManifest.version.isDevelopment {
        return .upToDate(embeddedManifest.version)
      }
      guard await networkAllows() else { return .meteredNetwork }
      do {
        try store.recordCheck(at: date)
        let (incoming, lease) = try store.freshIncoming()
        defer {
          try? FileManager.default.removeItem(at: incoming)
          withExtendedLifetime(lease) {}
        }
        return try await fetchNewer(than: newestKept(embedded: embeddedManifest), into: incoming)
      } catch {
        return .failed(Self.describe(error))
      }
    }

    private func newestKept(embedded: TryOmarchyKitManifest?) -> TryOmarchyKitVersion? {
      guard let fetched = try? store.current()?.manifest(), Self.prefers(fetched, over: embedded)
      else { return embedded?.version }
      return fetched.version
    }

    private func fetchNewer(than have: TryOmarchyKitVersion?, into incoming: URL) async throws
      -> TrialKitUpdateOutcome
    {
      let manifestFile = incoming.appendingPathComponent("TryOmarchyKit.json")
      try await source.fetch(.latestManifest, to: manifestFile)
      let manifest = try TryOmarchyKitManifest.decode(Data(contentsOf: manifestFile))
      guard !manifest.version.isDevelopment else {
        throw TrialKitUpdateError.developmentRelease
      }
      if let have, manifest.version <= have { return .upToDate(have) }
      guard manifest.isSupported else { return .needsNewerInstaller(manifest.version) }
      let checksumFile = incoming.appendingPathComponent("TryOmarchyKit.zip.sha256")
      try await source.fetch(.checksum(manifest.version), to: checksumFile)
      let expected = try Self.digest(inChecksumFile: Data(contentsOf: checksumFile))
      let archive = incoming.appendingPathComponent("TryOmarchyKit.zip")
      try await source.fetch(.archive(manifest.version), to: archive)
      guard try Self.digest(ofFile: archive) == expected else {
        throw TrialKitUpdateError.digestMismatch
      }
      let unpacked = incoming.appendingPathComponent("kit", isDirectory: true)
      try TrialKitArchive.unpack(archive, into: unpacked)
      let layout = TrialKitLayout(root: unpacked)
      guard let inside = try? layout.manifest(), inside == manifest else {
        throw TrialKitUpdateError.manifestMismatch
      }
      guard layout.hasLauncher else { throw TrialKitUpdateError.noLauncher }
      try signatures.verify(unpacked)
      let adopted = try store.adopt(unpacked, version: manifest.version)
      store.removeUnused()
      guard let version = try? adopted.manifest().version, version >= manifest.version else {
        return .failed("the fetched kit could not be switched to")
      }
      return version == manifest.version ? .installed(version) : .upToDate(version)
    }

    private func networkAllows() async -> Bool {
      if network.current().allowsPrefetch { return true }
      let network = network
      let wait = networkWait
      return await withTaskGroup(of: Bool.self) { group in
        group.addTask {
          for await path in network.updates() where path.allowsPrefetch { return true }
          return false
        }
        group.addTask {
          try? await Task.sleep(for: wait)
          return false
        }
        let first = await group.next() ?? false
        group.cancelAll()
        return first
      }
    }

    /// `TryOmarchyKit.zip.sha256`: one line, the digest and the file's name.
    static func digest(inChecksumFile data: Data) throws -> String {
      guard let text = String(data: data, encoding: .utf8) else {
        throw TrialKitUpdateError.badChecksum
      }
      var line = Substring(text)
      if line.hasSuffix("\n") { line = line.dropLast() }
      let parts = line.split(separator: " ", omittingEmptySubsequences: false)
      guard parts.count == 3, parts[1].isEmpty, parts[2] == "TryOmarchyKit.zip",
        parts[0].utf8.count == 64,
        parts[0].utf8.allSatisfy({ (0x30...0x39).contains($0) || (0x61...0x66).contains($0) })
      else { throw TrialKitUpdateError.badChecksum }
      return String(parts[0])
    }

    static func digest(ofFile url: URL) throws -> String {
      let handle = try FileHandle(forReadingFrom: url)
      defer { try? handle.close() }
      var hasher = SHA256()
      while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
        hasher.update(data: chunk)
      }
      return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func describe(_ error: Error) -> String {
      switch error {
      case let error as TrialKitUpdateError: "\(error)"
      case let error as TrialKitDownloadError: "download: \(error)"
      case let error as TrialKitArchive.Refusal: "archive: \(error)"
      case let error as TrialKitSignatureRefusal: "signature: \(error)"
      case is TryOmarchyKitManifestError: "the release's TryOmarchyKit.json is invalid"
      default: "\(type(of: error))"
      }
    }
  }

  /// The kit a start runs, and, for a fetched kit, the lease that keeps its
  /// folder in place while it runs.
  public struct TrialKitChoice: Sendable {
    public let layout: TrialKitLayout
    public let version: TryOmarchyKitVersion?
    public let lease: TrialKitLease?
    /// Why the fetched kit was passed over for the embedded one.
    public let refusal: String?

    public init(
      layout: TrialKitLayout, version: TryOmarchyKitVersion? = nil, lease: TrialKitLease? = nil,
      refusal: String? = nil
    ) {
      self.layout = layout
      self.version = version
      self.lease = lease
      self.refusal = refusal
    }

    public var isFetched: Bool { lease != nil }
  }

  public enum TrialKitUpdateError: Error, Equatable, Sendable {
    case developmentRelease
    case badChecksum
    case digestMismatch
    case manifestMismatch
    case noLauncher
  }
#endif
