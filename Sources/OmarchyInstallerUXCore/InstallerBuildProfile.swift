#if os(macOS)
  import Foundation

  /// Private capability and state isolation are separate from encryption choice.
  public enum InstallerBuildProfile: Equatable, Sendable {
    case standard
    case privatePlain
    case privateLimine

    public static var current: Self {
      resolve(infoDictionary: Bundle.main.infoDictionary ?? [:])
    }

    public static func resolve(infoDictionary: [String: Any]) -> Self {
      // Preserve the restrictive legacy flag if an invalid bundle contains both.
      if infoDictionary["OmarchyPrivatePlainTest"] as? Bool == true {
        return .privatePlain
      }
      if infoDictionary["OmarchyPrivateLimineTest"] as? Bool == true {
        return .privateLimine
      }
      return .standard
    }

    public var allowsEncryption: Bool { self != .privatePlain }
    public var showsReleaseChannels: Bool { self == .standard }

    public var workspaceName: String {
      switch self {
      case .standard: "com.omarchy.mx.installer"
      case .privatePlain: "com.omarchy.mx.installer.private-m3-20260922"
      case .privateLimine: "com.omarchy.mx.installer.private-limine-20260922"
      }
    }

    public var startupSequence: String {
      switch self {
      case .privateLimine: "m1n1 → U-Boot → Limine → Omarchy"
      case .standard, .privatePlain: "m1n1 → U-Boot → GRUB → Omarchy"
      }
    }
  }
#endif
