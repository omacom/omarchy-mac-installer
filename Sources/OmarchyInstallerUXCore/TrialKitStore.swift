#if os(macOS)
  import Darwin
  import Foundation
  import TryOmarchyKitClient

  /// Holds a lock on a fetched kit's or a download's folder: shared while a
  /// start runs the kit, exclusive while a check downloads into it. A folder
  /// whose lock is held is never removed, whichever installer holds it.
  public final class TrialKitLease: @unchecked Sendable {
    private let descriptor: Int32

    init?(folder: URL, exclusive: Bool) {
      let path = folder.appendingPathComponent(TrialKitStore.lockName).path
      let descriptor = open(path, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600)
      guard descriptor >= 0 else { return nil }
      guard flock(descriptor, (exclusive ? LOCK_EX : LOCK_SH) | LOCK_NB) == 0 else {
        close(descriptor)
        return nil
      }
      self.descriptor = descriptor
    }

    deinit { close(descriptor) }
  }

  /// The kits this installer fetched, in its own folder under Application
  /// Support. Each kit unpacks into a folder of its own, and the `current`
  /// link names the newest; switching kits replaces the link in one rename.
  /// Each check downloads into an `incoming-<id>` folder of its own, and
  /// `last-check.json` records when the releases were last asked. Installers
  /// that share the folder change it under `.lock`.
  public struct TrialKitStore: Sendable {
    public static let folderName = "Try kits"
    public static let pointerName = "current"
    static let incomingPrefix = "incoming-"
    static let checkName = "last-check.json"
    static let kitPrefix = "kit-"
    static let lockName = ".lock"

    public let directory: URL

    public init(directory: URL) {
      self.directory = directory
    }

    /// `<Application Support>/<workspace>/Try kits`, beside the installer's
    /// own state.
    public static func under(applicationSupport: URL, workspace: String) -> Self {
      Self(
        directory:
          applicationSupport
          .appendingPathComponent(workspace, isDirectory: true)
          .appendingPathComponent(folderName, isDirectory: true))
    }

    private var pointer: URL { directory.appendingPathComponent(Self.pointerName) }
    private var checkRecord: URL { directory.appendingPathComponent(Self.checkName) }

    public func current() -> TrialKitLayout? {
      guard let name = currentName() else { return nil }
      return TrialKitLayout(root: directory.appendingPathComponent(name, isDirectory: true))
    }

    func currentName() -> String? {
      guard isPlainFolder(directory),
        let name = try? FileManager.default.destinationOfSymbolicLink(atPath: pointer.path),
        Self.isKitName(name), isPlainFolder(directory.appendingPathComponent(name))
      else { return nil }
      return name
    }

    static func isKitName(_ name: String) -> Bool {
      name.hasPrefix(kitPrefix) && isSafeName(name)
    }

    private static func isSafeName(_ name: String) -> Bool {
      name.count <= 128 && !name.contains("..")
        && name.utf8.allSatisfy {
          $0.isASCIILetterOrDigit || $0 == UInt8(ascii: "-") || $0 == UInt8(ascii: ".")
        }
    }

    /// The kit's folder, locked for a start until the lease is released.
    func lease(_ kit: TrialKitLayout) -> TrialKitLease? {
      TrialKitLease(folder: kit.root, exclusive: false)
    }

    /// A new, empty folder for one check's download, locked until the lease
    /// is released. The store's lock keeps `removeUnused` in another
    /// installer from taking the folder before its lease does.
    func freshIncoming() throws -> (URL, TrialKitLease) {
      try withLock {
        let folder = directory.appendingPathComponent(
          Self.incomingPrefix + UUID().uuidString.lowercased(), isDirectory: true)
        try FileManager.default.createDirectory(
          at: folder, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        guard let lease = TrialKitLease(folder: folder, exclusive: true) else {
          try? FileManager.default.removeItem(at: folder)
          throw CocoaError(.fileLocking)
        }
        return (folder, lease)
      }
    }

    /// Moves a checked kit's folder in and points `current` at it, unless
    /// another installer switched to a kit at least as new meanwhile. Returns
    /// the kit `current` names afterwards.
    func adopt(_ unpacked: URL, version: TryOmarchyKitVersion) throws -> TrialKitLayout {
      try withLock {
        if let current = current(), let have = try? current.manifest().version, have >= version {
          try? FileManager.default.removeItem(at: unpacked)
          return current
        }
        let suffix = UUID().uuidString.prefix(8).lowercased()
        let name = "\(Self.kitPrefix)\(version)-\(suffix)"
        guard Self.isKitName(name) else { throw CocoaError(.fileWriteInvalidFileName) }
        let destination = directory.appendingPathComponent(name, isDirectory: true)
        try FileManager.default.moveItem(at: unpacked, to: destination)
        let next = directory.appendingPathComponent("\(Self.pointerName).next-\(suffix)")
        guard symlink(name, next.path) == 0, rename(next.path, pointer.path) == 0 else {
          let failure = POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
          try? FileManager.default.removeItem(at: next)
          throw failure
        }
        return TrialKitLayout(root: destination)
      }
    }

    /// Removes every fetched kit but the one `current` names, unless it is
    /// no longer worth keeping, and every download, unless a start or a
    /// check, in this installer or another, holds its lock.
    func removeUnused(keepingCurrent: Bool = true) {
      try? withLock {
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: directory.path)
        else { return }
        let kept = keepingCurrent ? currentName() : nil
        for name in names
        where (name.hasPrefix(Self.kitPrefix) || name.hasPrefix(Self.incomingPrefix))
          && name != kept && Self.isSafeName(name)
        {
          let folder = directory.appendingPathComponent(name, isDirectory: true)
          guard isPlainFolder(folder), let lease = TrialKitLease(folder: folder, exclusive: true)
          else { continue }
          try? FileManager.default.removeItem(at: folder)
          withExtendedLifetime(lease) {}
        }
      }
    }

    public func lastCheck() -> Date? {
      var information = stat()
      guard lstat(checkRecord.path, &information) == 0, information.st_mode & S_IFMT == S_IFREG,
        information.st_size <= 1_024, let data = try? Data(contentsOf: checkRecord),
        let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let text = object["checkedAt"] as? String
      else { return nil }
      return ISO8601DateFormatter().date(from: text)
    }

    func recordCheck(at date: Date) throws {
      try createFolder()
      let data = try JSONSerialization.data(
        withJSONObject: ["format": 1, "checkedAt": ISO8601DateFormatter().string(from: date)],
        options: [.sortedKeys])
      try data.write(to: checkRecord, options: .atomic)
    }

    private func withLock<Value>(_ body: () throws -> Value) throws -> Value {
      try createFolder()
      let descriptor = open(
        directory.appendingPathComponent(Self.lockName).path,
        O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0o600)
      guard descriptor >= 0 else { throw CocoaError(.fileLocking) }
      defer { close(descriptor) }
      guard flock(descriptor, LOCK_EX) == 0 else { throw CocoaError(.fileLocking) }
      return try body()
    }

    /// The folder and the workspace above it, private to this user, and
    /// never a link.
    func createFolder() throws {
      let workspace = directory.deletingLastPathComponent()
      for folder in [workspace, directory] {
        if !FileManager.default.fileExists(atPath: folder.path) {
          try FileManager.default.createDirectory(
            at: folder, withIntermediateDirectories: folder == workspace,
            attributes: [.posixPermissions: 0o700])
        }
        guard isPlainFolder(folder) else { throw CocoaError(.fileWriteNoPermission) }
      }
    }

    private func isPlainFolder(_ url: URL) -> Bool {
      var information = stat()
      return lstat(url.path, &information) == 0 && information.st_mode & S_IFMT == S_IFDIR
    }
  }

  extension UInt8 {
    fileprivate var isASCIILetterOrDigit: Bool {
      (0x30...0x39).contains(self) || (0x41...0x5A).contains(self) || (0x61...0x7A).contains(self)
    }
  }
#endif
