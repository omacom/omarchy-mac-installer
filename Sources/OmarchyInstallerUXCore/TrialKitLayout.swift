#if os(macOS)
  import Foundation
  import TryOmarchyKitClient

  /// Where a kit's parts sit inside the folder it unpacks into: the embedded
  /// kit's `Contents/Helpers`, or a fetched kit's own folder. Every question
  /// about the kit's layout goes through here.
  public struct TrialKitLayout: Equatable, Sendable {
    /// The one folder at the top of `TryOmarchyKit.zip`.
    public static let archiveFolder = TryOmarchyKit.bundleName

    public let root: URL

    public init(root: URL) {
      self.root = root
    }

    public static func embedded(in bundle: Bundle = .main) -> Self {
      Self(root: bundle.bundleURL.appendingPathComponent("Contents/Helpers", isDirectory: true))
    }

    /// `TryOmarchyKit.bundle`, a helper bundle with no bundle identity.
    public var kit: TryOmarchyKit { TryOmarchyKit.unpacked(in: root) }

    public var manifestURL: URL { kit.manifestURL }

    public var launcherURL: URL {
      kit.bundleURL.appendingPathComponent(TryOmarchyKit.helperPath, isDirectory: false)
    }

    public var hasLauncher: Bool { (try? kit.helperURL()) != nil }

    public func manifest() throws -> TryOmarchyKitManifest { try kit.manifest() }
  }
#endif
