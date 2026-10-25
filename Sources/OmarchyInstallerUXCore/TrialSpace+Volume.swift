#if os(macOS)
  import Foundation
  import TryOmarchyKitClient

  /// The free space the header names for macOS's disk and the Try page names
  /// for the VM's: what macOS can make available on the volume, read from
  /// the nearest folder that exists. One reading, so the same disk shows the
  /// same number on both.
  public enum VolumeFreeSpace {
    public static func bytes(at url: URL) -> Int64? { TryOmarchyStorage.freeBytes(at: url) }

    /// The volume macOS and the default VM folder live on.
    public static func macOS() -> UInt64? {
      bytes(at: FileManager.default.homeDirectoryForCurrentUser).map { UInt64(max(0, $0)) }
    }
  }

  extension TrialSpace {
    /// What a start needs in the VM's folder, and what is free on the volume
    /// that holds it or will hold it.
    public static func of(
      _ root: URL?,
      freeBytes: (URL) -> Int64? = { VolumeFreeSpace.bytes(at: $0) }
    ) -> TrialSpace {
      guard let root else {
        return TrialSpace(
          requiredBytes: TryOmarchyStorage.requiredFreeBytesForNewVM, freeBytes: nil, hasVM: false)
      }
      return TrialSpace(
        requiredBytes: TryOmarchyStorage.requiredFreeBytes(at: root),
        freeBytes: freeBytes(root), hasVM: TryOmarchyStorage.hasVM(at: root))
    }
  }
#endif
