#if os(macOS)
  import Foundation
  import OmarchyAppleInstallerTrustCore

  /// A Try Omarchy export the installer found, as the page shows it. Render
  /// only: the environment keeps the checked export and acts on its id.
  public struct TryOmarchyExportDisplay: Equatable, Identifiable, Sendable {
    /// The export id from its receipt.
    public let id: String
    public let publishedAt: Date
    public let entries: Int64
    public let expandedBytes: UInt64
    public let bundleBytes: UInt64

    public init(_ export: TryOmarchyExport) {
      id = export.receipt.exportID
      publishedAt = export.publishedAt
      entries = export.receipt.entries
      expandedBytes = UInt64(export.receipt.expandedBytes)
      bundleBytes = UInt64(export.receipt.bundleBytes)
    }

    public init(
      id: String,
      publishedAt: Date,
      entries: Int64,
      expandedBytes: UInt64,
      bundleBytes: UInt64
    ) {
      self.id = id
      self.publishedAt = publishedAt
      self.entries = entries
      self.expandedBytes = expandedBytes
      self.bundleBytes = bundleBytes
    }

    /// "862 items · 29 MB".
    public var summary: String {
      let items = entries == 1 ? "1 item" : "\(entries) items"
      return "\(items) · \(PlainLanguage.bytes(bundleBytes))"
    }
  }

  /// What the installer found in Try Omarchy's Exports folder.
  public enum TryOmarchyExportsDisplay: Equatable, Sendable {
    case notChecked
    /// `unusable` counts folders that looked like exports but failed a check.
    case found([TryOmarchyExportDisplay], unusable: Int)
    /// The Exports folder exists but is not private to this user.
    case unavailable

    public static let none = TryOmarchyExportsDisplay.found([], unusable: 0)

    public var exports: [TryOmarchyExportDisplay] {
      if case .found(let exports, _) = self { return exports }
      return []
    }
  }

  /// Where the chosen export's ciphertext check stands.
  public enum TryOmarchyExportCheck: Equatable, Sendable {
    case idle
    case checking
    case verified
    case failed(String)
  }
#endif
