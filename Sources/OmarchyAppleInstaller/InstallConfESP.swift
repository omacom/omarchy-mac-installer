#if os(macOS)
  import Darwin
  import Foundation

  public enum InstallConfESPError: Error, Equatable, Sendable {
    case invalidStoreIdentifier
    case notFound
    case ambiguous
    case mountFailed
    case writeFailed
    case readbackMismatch
    case unmountFailed

    /// True after the atomic replacement has landed, even if a later step failed.
    var followedConfirmedWrite: Bool {
      switch self {
      case .readbackMismatch, .unmountFailed:
        return true
      case .invalidStoreIdentifier, .notFound, .ambiguous, .mountFailed, .writeFailed:
        return false
      }
    }
  }

  public enum InstallConfHandoff: Equatable, Sendable, Codable {
    case recorded
    /// The document was written, but a later readback or unmount was not confirmed.
    case unconfirmed(encrypt: Bool)
    case notRecorded

    private enum CodingKeys: String, CodingKey {
      case code
      case encrypt
    }

    private enum Code: Int, Codable {
      case recorded = 0
      case unconfirmed = 1
      case notRecorded = 2
    }

    public init(from decoder: Decoder) throws {
      let container = try decoder.container(keyedBy: CodingKeys.self)
      switch try container.decode(Code.self, forKey: .code) {
      case .recorded:
        self = .recorded
      case .unconfirmed:
        self = .unconfirmed(encrypt: try container.decode(Bool.self, forKey: .encrypt))
      case .notRecorded:
        self = .notRecorded
      }
    }

    public func encode(to encoder: Encoder) throws {
      var container = encoder.container(keyedBy: CodingKeys.self)
      switch self {
      case .recorded:
        try container.encode(Code.recorded, forKey: .code)
      case .unconfirmed(let encrypt):
        try container.encode(Code.unconfirmed, forKey: .code)
        try container.encode(encrypt, forKey: .encrypt)
      case .notRecorded:
        try container.encode(Code.notRecorded, forKey: .code)
      }
    }
  }

  public enum InstallConfRecordPolicy {
    /// Handover is written after a completed engine run that still needs Recovery.
    public static func shouldRecord(
      operation: EngineHandoffOperation,
      nextAction: InstallerNextAction
    ) -> Bool {
      switch operation {
      case .install, .retryRecoveryAuthorization:
        return nextAction == .enterRecovery
      }
    }
  }

  /// One GPT row considered when locating the ESP the engine just created.
  public struct InstallConfESPPartition: Equatable, Sendable {
    public let identifier: String
    public let storeIdentifier: String
    public let type: String
    public let name: String
    public let offsetBytes: UInt64
    public let lengthBytes: UInt64

    public init(
      identifier: String,
      storeIdentifier: String,
      type: String,
      name: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64
    ) {
      self.identifier = identifier
      self.storeIdentifier = storeIdentifier
      self.type = type
      self.name = name
      self.offsetBytes = offsetBytes
      self.lengthBytes = lengthBytes
    }
  }

  public struct InstallConfESPIdentity: Equatable, Sendable {
    public let partitionIdentifier: String
    public let storeIdentifier: String

    public init(partitionIdentifier: String, storeIdentifier: String) {
      self.partitionIdentifier = partitionIdentifier
      self.storeIdentifier = storeIdentifier
    }
  }

  /// Picks the new Omarchy ESP from the engine plan's store and extent.
  ///
  /// The engine allocates `[offset, offset+length)` on `storeIdentifier` and
  /// creates the stub APFS, then `EFI - OMARC`, then the Linux boot and root
  /// partitions. Exactly one EFI volume named `EFI - OMARC` inside that extent
  /// is the handover target.
  public enum InstallConfESPLocator {
    public static let volumeName = "EFI - OMARC"
    private static let efiTypes: Set<String> = ["EFI", "EFI System Partition"]
    private static let storePattern = #"^disk[0-9]+$"#
    private static let partitionPattern = #"^disk[0-9]+s[0-9]+$"#

    public static func identify(
      storeIdentifier: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64,
      partitions: [InstallConfESPPartition]
    ) throws -> InstallConfESPIdentity {
      guard storeIdentifier.range(of: storePattern, options: .regularExpression) != nil
      else {
        throw InstallConfESPError.invalidStoreIdentifier
      }
      guard lengthBytes > 0,
        offsetBytes.addingReportingOverflow(lengthBytes).overflow == false
      else {
        throw InstallConfESPError.notFound
      }
      let end = offsetBytes + lengthBytes
      let matches = partitions.filter { partition in
        let partitionEnd = partition.offsetBytes.addingReportingOverflow(partition.lengthBytes)
        return partition.storeIdentifier == storeIdentifier
          && Self.efiTypes.contains(partition.type)
          && partition.name == volumeName
          && partition.lengthBytes > 0
          && !partitionEnd.overflow
          && partition.offsetBytes >= offsetBytes
          && partition.offsetBytes < end
          && partitionEnd.partialValue <= end
          && partition.identifier.range(
            of: partitionPattern, options: .regularExpression) != nil
          && partition.identifier.hasPrefix(storeIdentifier)
      }
      guard matches.count == 1, let match = matches.first else {
        throw matches.isEmpty
          ? InstallConfESPError.notFound : InstallConfESPError.ambiguous
      }
      return InstallConfESPIdentity(
        partitionIdentifier: match.identifier,
        storeIdentifier: storeIdentifier
      )
    }
  }

  public protocol InstallConfESPHelping: Sendable {
    func write(
      _ conf: InstallConf,
      storeIdentifier: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64
    ) async throws -> InstallConfHandoff
  }

  /// App-side recorder: any helper failure becomes the default-on summary,
  /// never an install failure.
  public struct InstallConfESPWriter: Sendable {
    private let helper: any InstallConfESPHelping

    public init(helper: any InstallConfESPHelping) {
      self.helper = helper
    }

    public func record(
      _ conf: InstallConf,
      storeIdentifier: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64
    ) async -> InstallConfHandoff {
      do {
        return try await helper.write(
          conf,
          storeIdentifier: storeIdentifier,
          offsetBytes: offsetBytes,
          lengthBytes: lengthBytes
        )
      } catch let error as InstallConfESPError where error.followedConfirmedWrite {
        return .unconfirmed(encrypt: conf.encrypt)
      } catch {
        return .notRecorded
      }
    }
  }

  public struct AuthorizedInstallConfESPHelper: InstallConfESPHelping {
    public let submitter: AuthenticatedEngineXPCSubmitter
    public let authorization: MachineOwnerAuthorization

    public init(
      submitter: AuthenticatedEngineXPCSubmitter,
      authorization: MachineOwnerAuthorization
    ) {
      self.submitter = submitter
      self.authorization = authorization
    }

    public func write(
      _ conf: InstallConf,
      storeIdentifier: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64
    ) async throws -> InstallConfHandoff {
      try await submitter.writeInstallConf(
        conf,
        storeIdentifier: storeIdentifier,
        offsetBytes: offsetBytes,
        lengthBytes: lengthBytes,
        authorization: authorization
      )
    }
  }

  protocol InstallConfESPDiskOperating: Sendable {
    func partitions(on storeIdentifier: String) throws -> [InstallConfESPPartition]
    /// Returns a verified mount-root descriptor, owned by the caller.
    func mount(_ identifier: String, at mountPoint: URL) throws -> Int32
    func unmount(_ identifier: String) throws
  }

  /// Reuse the identified ESP's verified mount, or mount it privately, then
  /// write and read back through pinned descriptors before unmounting.
  struct InstallConfESPMountWriter: @unchecked Sendable {
    private let disks: any InstallConfESPDiskOperating
    private let workingDirectory: URL
    private let fileManager: FileManager
    private let contentsOf: @Sendable (Int32) throws -> Data
    private let synchronizeDirectory: @Sendable (Int32) -> Void

    init(
      disks: any InstallConfESPDiskOperating,
      workingDirectory: URL,
      fileManager: FileManager = .default,
      contentsOf: @escaping @Sendable (Int32) throws -> Data = {
        try FileHandle(fileDescriptor: $0, closeOnDealloc: false).read(upToCount: 1024) ?? Data()
      },
      synchronizeDirectory: @escaping @Sendable (Int32) -> Void = Self.bestEffortDirectorySync
    ) {
      self.disks = disks
      self.workingDirectory = workingDirectory
      self.fileManager = fileManager
      self.contentsOf = contentsOf
      self.synchronizeDirectory = synchronizeDirectory
    }

    func write(
      _ conf: InstallConf,
      storeIdentifier: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64
    ) throws {
      let identity = try InstallConfESPLocator.identify(
        storeIdentifier: storeIdentifier,
        offsetBytes: offsetBytes,
        lengthBytes: lengthBytes,
        partitions: try disks.partitions(on: storeIdentifier)
      )
      let mountPoint = workingDirectory.appendingPathComponent(
        "esp-handoff-\(UUID().uuidString.lowercased())",
        isDirectory: true
      )
      try fileManager.createDirectory(
        at: mountPoint,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
      )
      // Even a failed verification can leave a filesystem attached here.
      // Never recursively remove a mount's contents during cleanup.
      defer { _ = Darwin.rmdir(mountPoint.path) }
      let descriptor: Int32
      var mounted = false
      do {
        descriptor = try disks.mount(identity.partitionIdentifier, at: mountPoint)
        mounted = true
      } catch {
        throw InstallConfESPError.mountFailed
      }
      defer {
        if mounted {
          try? disks.unmount(identity.partitionIdentifier)
        }
      }
      // This closes every descriptor before diskutil unmount, including errors.
      try writeAndVerify(conf, on: descriptor)
      do {
        try disks.unmount(identity.partitionIdentifier)
        mounted = false
      } catch {
        throw InstallConfESPError.unmountFailed
      }
    }

    private func writeAndVerify(_ conf: InstallConf, on root: Int32) throws {
      defer { Darwin.close(root) }
      var rootInfo = stat()
      guard fstat(root, &rootInfo) == 0 else { throw InstallConfESPError.writeFailed }
      let createdDirectory = mkdirat(root, InstallConf.directoryName, 0o755) == 0
      guard createdDirectory || errno == EEXIST else { throw InstallConfESPError.writeFailed }
      let directory = openat(
        root, InstallConf.directoryName, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
      guard directory >= 0 else { throw InstallConfESPError.writeFailed }
      defer { Darwin.close(directory) }
      var directoryInfo = stat()
      guard fstat(directory, &directoryInfo) == 0, directoryInfo.st_dev == rootInfo.st_dev else {
        throw InstallConfESPError.writeFailed
      }
      let temporary = ".\(InstallConf.fileName).\(UUID().uuidString.lowercased()).tmp"
      let pending = openat(
        directory, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o644)
      guard pending >= 0 else { throw InstallConfESPError.writeFailed }
      defer { _ = unlinkat(directory, temporary, 0) }
      let handle = FileHandle(fileDescriptor: pending, closeOnDealloc: true)
      defer { try? handle.close() }
      do {
        try handle.write(contentsOf: conf.serializedData)
        try handle.synchronize()
        try handle.close()
      } catch {
        throw InstallConfESPError.writeFailed
      }
      guard renameat(directory, temporary, directory, InstallConf.fileName) == 0 else {
        throw InstallConfESPError.writeFailed
      }
      // A busy volume may stay mounted. Flush the rename and any new parent
      // entry before readback, even when a later unmount cannot flush them.
      synchronizeDirectory(directory)
      if createdDirectory { synchronizeDirectory(root) }
      let readback = openat(
        directory, InstallConf.fileName, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
      guard readback >= 0 else { throw InstallConfESPError.readbackMismatch }
      defer { Darwin.close(readback) }
      var observedInfo = stat()
      guard fstat(readback, &observedInfo) == 0,
        (observedInfo.st_mode & S_IFMT) == S_IFREG,
        observedInfo.st_dev == rootInfo.st_dev,
        observedInfo.st_size == conf.serializedData.count
      else { throw InstallConfESPError.readbackMismatch }
      let observed: Data
      do {
        observed = try contentsOf(readback)
      } catch {
        throw InstallConfESPError.readbackMismatch
      }
      guard observed == conf.serializedData,
        let parsed = try? InstallConf.parse(observed), parsed == conf
      else { throw InstallConfESPError.readbackMismatch }
    }

    private static func bestEffortDirectorySync(_ descriptor: Int32) {
      // Some filesystem implementations do not support directory syncs.
      _ = fsync(descriptor)
      _ = fcntl(descriptor, F_FULLFSYNC)
    }
  }

  struct DiskutilInstallConfESPOperator: InstallConfESPDiskOperating {
    var commands: @Sendable ([String]) throws -> Data = Self.systemRun
    var openMount: @Sendable (String, URL) throws -> Int32 = Self.openVerifiedMount
    var restartSpotlightServer: @Sendable () -> Void = Self.restartSystemSpotlightServer

    func partitions(on storeIdentifier: String) throws -> [InstallConfESPPartition] {
      let listing = try plist(["list", "-plist", storeIdentifier])
      guard let disks = listing["AllDisksAndPartitions"] as? [[String: Any]],
        let entry = disks.first(where: {
          $0["DeviceIdentifier"] as? String == storeIdentifier
        }),
        let records = entry["Partitions"] as? [[String: Any]]
      else {
        throw InstallConfESPError.notFound
      }
      return try records.compactMap { item -> InstallConfESPPartition? in
        guard let identifier = item["DeviceIdentifier"] as? String else {
          return nil
        }
        let info = try plist(["info", "-plist", identifier])
        guard info["ParentWholeDisk"] as? String == storeIdentifier else {
          return nil
        }
        let offset = (info["PartitionMapPartitionOffset"] as? NSNumber)?.uint64Value ?? 0
        let size = (info["Size"] as? NSNumber)?.uint64Value ?? 0
        return InstallConfESPPartition(
          identifier: identifier,
          storeIdentifier: storeIdentifier,
          type: info["Content"] as? String ?? "",
          name: info["VolumeName"] as? String ?? "",
          offsetBytes: offset,
          lengthBytes: size
        )
      }
    }

    func mount(_ identifier: String, at mountPoint: URL) throws -> Int32 {
      let before = try plist(["info", "-plist", identifier])
      guard before["DeviceIdentifier"] as? String == identifier else {
        throw InstallConfESPError.mountFailed
      }
      // Spotlight can keep the engine's existing mount busy. Save the choice
      // there first, after proving the opened directory belongs to this ESP.
      if let existing = before["MountPoint"] as? String, !existing.isEmpty {
        guard before["WritableVolume"] as? Bool == true else {
          throw InstallConfESPError.mountFailed
        }
        return try openMount(identifier, URL(fileURLWithPath: existing, isDirectory: true))
      }
      do {
        _ = try run(["mount", "-mountPoint", mountPoint.path, identifier])
        let mounted = try plist(["info", "-plist", identifier])
        guard mounted["DeviceIdentifier"] as? String == identifier,
          mounted["WritableVolume"] as? Bool == true,
          let actual = mounted["MountPoint"] as? String,
          URL(fileURLWithPath: actual).resolvingSymlinksInPath().standardizedFileURL.path
            == mountPoint.resolvingSymlinksInPath().standardizedFileURL.path
        else { throw InstallConfESPError.mountFailed }
        return try openMount(identifier, mountPoint)
      } catch {
        try? unmount(identifier)
        throw InstallConfESPError.mountFailed
      }
    }

    static func openVerifiedMount(_ identifier: String, at mountPoint: URL) throws -> Int32 {
      guard let resolved = realpath(mountPoint.path, nil) else {
        throw InstallConfESPError.mountFailed
      }
      let path = String(cString: resolved)
      free(resolved)
      let descriptor = open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
      guard descriptor >= 0 else { throw InstallConfESPError.mountFailed }
      var filesystem = statfs()
      var opened = stat()
      var named = stat()
      func text<T>(_ value: inout T) -> String {
        withUnsafeBytes(of: &value) { bytes in
          String(decoding: bytes.prefix { $0 != 0 }, as: UTF8.self)
        }
      }
      guard fstatfs(descriptor, &filesystem) == 0, fstat(descriptor, &opened) == 0,
        text(&filesystem.f_mntfromname) == "/dev/\(identifier)",
        text(&filesystem.f_mntonname) == path,
        filesystem.f_flags & UInt32(MNT_RDONLY) == 0,
        lstat(path, &named) == 0, named.st_dev == opened.st_dev, named.st_ino == opened.st_ino
      else {
        Darwin.close(descriptor)
        throw InstallConfESPError.mountFailed
      }
      return descriptor
    }

    /// A normal unmount, retried once after restarting Spotlight's server.
    ///
    /// On macOS 27 the Spotlight server that watched the engine create and
    /// remount the new EFI can keep descriptors on its root after approving
    /// the unmount and closing its index, so the kernel refuses every normal
    /// unmount. A restarted server releases the volume when asked. The retry
    /// is the same normal unmount; nothing is forced.
    func unmount(_ identifier: String) throws {
      do {
        _ = try run(["unmount", identifier])
      } catch {
        restartSpotlightServer()
        _ = try run(["unmount", identifier])
      }
    }

    /// launchd starts the server again at once; its indexes are kept.
    static func restartSystemSpotlightServer() {
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
      process.arguments = ["kickstart", "-k", "system/com.apple.metadata.mds"]
      process.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"]
      process.standardOutput = FileHandle.nullDevice
      process.standardError = FileHandle.nullDevice
      process.standardInput = FileHandle.nullDevice
      do {
        try process.run()
        process.waitUntilExit()
      } catch {}
    }

    private func plist(_ arguments: [String]) throws -> [String: Any] {
      guard
        let value = try PropertyListSerialization.propertyList(from: run(arguments), format: nil)
          as? [String: Any]
      else {
        throw InstallConfESPError.notFound
      }
      return value
    }

    private func run(_ arguments: [String]) throws -> Data {
      try commands(arguments)
    }

    private static func systemRun(_ arguments: [String]) throws -> Data {
      let process = Process()
      process.executableURL = URL(fileURLWithPath: "/usr/sbin/diskutil")
      process.arguments = arguments
      process.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"]
      let pipe = Pipe()
      process.standardOutput = pipe
      process.standardError = FileHandle.nullDevice
      process.standardInput = FileHandle.nullDevice
      try process.run()
      let result = pipe.fileHandleForReading.readDataToEndOfFile()
      process.waitUntilExit()
      guard process.terminationReason == .exit, process.terminationStatus == 0 else {
        throw InstallConfESPError.mountFailed
      }
      return result
    }
  }
#endif
