#if os(macOS)
  import CryptoKit
  import Darwin
  import Foundation
  import OmarchyInstallerSystem

  /// Where a Try export goes in the new Root partition, before Linux first
  /// boots (docs/native-import.md#the-region). Byte offsets are relative to
  /// the partition's start.
  public enum TryExportStagingRegion {
    static let mebibyte: UInt64 = 1 << 20
    static let gibibyte: UInt64 = 1 << 30
    static let tail = 64 * mebibyte
    /// Above the largest `root.img` and the first boot's encryption shift.
    public static let floor = 33 * gibibyte
    /// Room below the region for the copy the first boot makes.
    public static let copyMargin = 4 * gibibyte
    /// Btrfs's superblock copy at 256 GiB, plain or 32 MiB into LUKS.
    static let mirror = (256 * gibibyte - mebibyte)..<(256 * gibibyte + 64 * mebibyte)

    public static func writtenBytes(_ length: UInt64) -> UInt64 {
      (length + 4_095) / 4_096 * 4_096
    }

    /// Nil when the partition has no room for the export.
    public static func offset(partitionBytes: UInt64, bundleBytes: UInt64) -> UInt64? {
      guard bundleBytes > 0, bundleBytes < partitionBytes, partitionBytes > tail else { return nil }
      let written = writtenBytes(bundleBytes)
      let end = alignedDown(partitionBytes - tail)
      guard end >= written else { return nil }
      var start = alignedDown(end - written)
      if start < mirror.upperBound, start + written > mirror.lowerBound {
        guard mirror.lowerBound >= written else { return nil }
        start = alignedDown(mirror.lowerBound - written)
      }
      let minimum = (floor + copyMargin).addingReportingOverflow(bundleBytes)
      guard !minimum.overflow, start >= minimum.partialValue else { return nil }
      return start
    }

    /// What a native install needs beyond Omarchy's own size, whose Root
    /// holds a 32 GiB image: the export staged and claimed, and its files
    /// imported and decrypted while the import runs.
    public static func additionalInstallBytes(bundleBytes: UInt64, expandedBytes: UInt64) -> UInt64
    {
      (floor - 32 * gibibyte) + copyMargin + tail + 2 * writtenBytes(bundleBytes) + 2
        * expandedBytes
    }

    private static func alignedDown(_ value: UInt64) -> UInt64 {
      value / mebibyte * mebibyte
    }
  }

  /// Whether the image a plan installs claims staged exports at its first
  /// boot: its metadata's Omarchy target names the marker schema it reads.
  public enum TryExportImageSupport {
    public static let metadataKey = "native_import"

    public static func claimsExports(metadataURL: URL) -> Bool {
      var status = stat()
      guard metadataURL.isFileURL, lstat(metadataURL.path, &status) == 0,
        status.st_mode & S_IFMT == S_IFREG, status.st_size > 0, status.st_size <= 1 << 20,
        let bytes = try? Data(contentsOf: metadataURL),
        let metadata = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
        let entries = metadata["os_list"] as? [[String: Any]]
      else { return false }
      let targets = entries.filter { $0["omarchy_target"] as? String == "apple-silicon-full-os" }
      return targets.count == 1
        && targets[0][metadataKey] as? String == TryExportStagingMarker.schema
    }
  }

  public enum TryExportStagingReason: String, Codable, Equatable, Sendable {
    case noRoom = "no-room"
    case rootNotFound = "root-not-found"
    case bundleChanged = "bundle-changed"
    case writeFailed = "write-failed"
    case readbackMismatch = "readback-mismatch"
    case markerFailed = "marker-failed"
    /// The image installed doesn't claim staged exports.
    case imageCannotImport = "image-cannot-import"
    /// The helper couldn't be asked or refused the request.
    case helperFailed = "helper-failed"
  }

  public enum TryExportStagingOutcome: Equatable, Sendable, Codable {
    case staged(exportID: String)
    case notStaged(TryExportStagingReason)
  }

  struct TryExportStagingError: Error, Equatable {
    let reason: TryExportStagingReason
    init(_ reason: TryExportStagingReason) { self.reason = reason }
  }

  /// One GPT row considered when locating the Root partition the engine
  /// just created.
  public struct TryExportStagingPartition: Equatable, Sendable {
    public let identifier: String
    public let storeIdentifier: String
    public let type: String
    public let uuid: String
    public let offsetBytes: UInt64
    public let lengthBytes: UInt64

    public init(
      identifier: String, storeIdentifier: String, type: String, uuid: String,
      offsetBytes: UInt64, lengthBytes: UInt64
    ) {
      self.identifier = identifier
      self.storeIdentifier = storeIdentifier
      self.type = type
      self.uuid = uuid
      self.offsetBytes = offsetBytes
      self.lengthBytes = lengthBytes
    }
  }

  /// The engine creates Boot, then Root, as the last Linux partitions of
  /// the plan's extent, and Root takes the rest of it.
  public enum TryExportRootLocator {
    static let linuxTypes: Set<String> = [
      "Linux Filesystem", "Linux", "0FC63DAF-8483-4772-8E79-3D69D8477DE4",
    ]
    private static let partitionPattern = #"^disk[0-9]+s[0-9]+$"#

    public static func identify(
      storeIdentifier: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64,
      partitions: [TryExportStagingPartition]
    ) throws -> TryExportStagingPartition {
      let extentEnd = offsetBytes.addingReportingOverflow(lengthBytes)
      guard lengthBytes > 0, !extentEnd.overflow else {
        throw TryExportStagingError(.rootNotFound)
      }
      let linux = partitions.filter { partition in
        let end = partition.offsetBytes.addingReportingOverflow(partition.lengthBytes)
        return partition.storeIdentifier == storeIdentifier
          && linuxTypes.contains(partition.type)
          && partition.lengthBytes > 0
          && !end.overflow
          && partition.offsetBytes >= offsetBytes
          && end.partialValue <= extentEnd.partialValue
          && partition.identifier.range(of: partitionPattern, options: .regularExpression) != nil
          && partition.identifier.hasPrefix(storeIdentifier + "s")
      }
      guard linux.count == 2,
        let root = linux.max(by: { $0.offsetBytes < $1.offsetBytes }),
        root.lengthBytes > TryExportStagingRegion.floor,
        TryOmarchyExportReceipt.isCanonicalUUID(root.uuid.lowercased())
      else {
        throw TryExportStagingError(.rootNotFound)
      }
      return root
    }
  }

  /// `omarchy/migration.json` on the ESP: the commit marker the native first
  /// boot claims (docs/native-import.md#the-marker).
  public struct TryExportStagingMarker: Equatable, Sendable {
    public static let schema = "omarchy-migration/staging/1"
    public static let fileName = "migration.json"
    public static let maximumBytes = 64 * 1024

    public let exportID: String
    public let partitionUUID: String
    public let offset: UInt64
    public let length: UInt64
    public let sha256: String
    /// The receipt exactly as Try published it.
    public let receipt: Data

    /// Keys in sorted order; the receipt is embedded as the object it is.
    public func serializedData() throws -> Data {
      let receiptText = String(decoding: receipt, as: UTF8.self)
        .trimmingCharacters(in: .whitespacesAndNewlines)
      guard receiptText.hasPrefix("{"), receiptText.hasSuffix("}") else {
        throw TryExportStagingError(.markerFailed)
      }
      let text =
        "{\"export_id\":\"\(exportID)\",\"length\":\(length),\"offset\":\(offset),"
        + "\"partition_uuid\":\"\(partitionUUID)\",\"receipt\":\(receiptText),"
        + "\"schema\":\"\(Self.schema)\",\"sha256\":\"\(sha256)\"}\n"
      let data = Data(text.utf8)
      guard data.count <= Self.maximumBytes,
        (try? JSONSerialization.jsonObject(with: data)) is [String: Any]
      else {
        throw TryExportStagingError(.markerFailed)
      }
      return data
    }
  }

  protocol TryExportStagingDiskOperating: InstallConfESPDiskOperating {
    func stagingPartitions(on storeIdentifier: String) throws -> [TryExportStagingPartition]
    /// The raw partition, open for reading and writing.
    func openRaw(_ identifier: String) throws -> Int32
    func synchronize(_ descriptor: Int32) throws
  }

  /// Privileged stager used by the helper, after a completed engine run.
  struct TryExportStagingWriter: @unchecked Sendable {
    static let chunkBytes = 4 * 1024 * 1024

    private let disks: any TryExportStagingDiskOperating
    private let workingDirectory: URL

    init(disks: any TryExportStagingDiskOperating, workingDirectory: URL) {
      self.disks = disks
      self.workingDirectory = workingDirectory
    }

    func stage(
      bundle: FileHandle,
      receiptDocument: Data,
      storeIdentifier: String,
      offsetBytes: UInt64,
      lengthBytes: UInt64
    ) throws -> TryExportStagingMarker {
      guard let receipt = try? TryOmarchyExportReceipt.decode(receiptDocument) else {
        throw TryExportStagingError(.bundleChanged)
      }
      let length = UInt64(receipt.bundleBytes)
      let source = bundle.fileDescriptor
      var information = stat()
      guard fstat(source, &information) == 0, information.st_mode & S_IFMT == S_IFREG,
        UInt64(information.st_size) == length
      else {
        throw TryExportStagingError(.bundleChanged)
      }
      let root: TryExportStagingPartition
      do {
        root = try TryExportRootLocator.identify(
          storeIdentifier: storeIdentifier, offsetBytes: offsetBytes, lengthBytes: lengthBytes,
          partitions: try disks.stagingPartitions(on: storeIdentifier))
      } catch {
        throw TryExportStagingError(.rootNotFound)
      }
      guard
        let offset = TryExportStagingRegion.offset(
          partitionBytes: root.lengthBytes, bundleBytes: length)
      else {
        throw TryExportStagingError(.noRoom)
      }
      let expected = String(receipt.bundleDigest.rawValue.dropFirst("sha256:".count))
      let target: Int32
      do {
        target = try disks.openRaw(root.identifier)
      } catch {
        throw TryExportStagingError(.writeFailed)
      }
      defer { close(target) }
      guard try copy(from: source, length: length, to: target, at: offset) == expected else {
        throw TryExportStagingError(.bundleChanged)
      }
      do {
        try disks.synchronize(target)
      } catch {
        throw TryExportStagingError(.writeFailed)
      }
      guard try digest(of: target, at: offset, length: length) == expected else {
        throw TryExportStagingError(.readbackMismatch)
      }
      let marker = TryExportStagingMarker(
        exportID: receipt.exportID, partitionUUID: root.uuid.lowercased(), offset: offset,
        length: length, sha256: expected, receipt: receiptDocument)
      do {
        try InstallConfESPMountWriter(disks: disks, workingDirectory: workingDirectory).write(
          try marker.serializedData(), named: TryExportStagingMarker.fileName,
          storeIdentifier: storeIdentifier, offsetBytes: offsetBytes, lengthBytes: lengthBytes)
      } catch let error as InstallConfESPError where error == .unmountFailed {
        return marker
      } catch {
        throw TryExportStagingError(.markerFailed)
      }
      return marker
    }

    /// Writes whole 4 KiB blocks, the last padded with zeros, and returns
    /// the digest of the bundle's own bytes.
    private func copy(from source: Int32, length: UInt64, to target: Int32, at offset: UInt64)
      throws -> String
    {
      var hasher = SHA256()
      var buffer = [UInt8](repeating: 0, count: Self.chunkBytes)
      var copied: UInt64 = 0
      while copied < length {
        let wanted = Int(min(UInt64(Self.chunkBytes), length - copied))
        let read = buffer.withUnsafeMutableBytes { pointer in
          pread(source, pointer.baseAddress, wanted, off_t(copied))
        }
        guard read == wanted else { throw TryExportStagingError(.bundleChanged) }
        hasher.update(data: buffer[0..<wanted])
        let block = Int(TryExportStagingRegion.writtenBytes(UInt64(wanted)))
        for index in wanted..<block { buffer[index] = 0 }
        var written = 0
        while written < block {
          let result = buffer.withUnsafeBytes { pointer in
            pwrite(
              target, pointer.baseAddress! + written, block - written,
              off_t(offset + copied) + off_t(written))
          }
          guard result > 0 else { throw TryExportStagingError(.writeFailed) }
          written += result
        }
        copied += UInt64(wanted)
      }
      var extra: UInt8 = 0
      guard pread(source, &extra, 1, off_t(length)) == 0 else {
        throw TryExportStagingError(.bundleChanged)
      }
      return Self.hexadecimal(hasher.finalize())
    }

    private func digest(of target: Int32, at offset: UInt64, length: UInt64) throws -> String {
      var hasher = SHA256()
      var buffer = [UInt8](repeating: 0, count: Self.chunkBytes)
      var done: UInt64 = 0
      while done < length {
        let wanted = Int(min(UInt64(Self.chunkBytes), length - done))
        let block = Int(TryExportStagingRegion.writtenBytes(UInt64(wanted)))
        let read = buffer.withUnsafeMutableBytes { pointer in
          pread(target, pointer.baseAddress, block, off_t(offset + done))
        }
        guard read == block else { throw TryExportStagingError(.readbackMismatch) }
        hasher.update(data: buffer[0..<wanted])
        done += UInt64(wanted)
      }
      return Self.hexadecimal(hasher.finalize())
    }

    static func hexadecimal(_ digest: SHA256.Digest) -> String {
      digest.map { String(format: "%02x", $0) }.joined()
    }
  }

  struct DiskutilTryExportStagingOperator: TryExportStagingDiskOperating {
    var esp = DiskutilInstallConfESPOperator()
    var commands: @Sendable ([String]) throws -> Data = DiskutilTryExportStagingOperator.systemRun

    func partitions(on storeIdentifier: String) throws -> [InstallConfESPPartition] {
      try esp.partitions(on: storeIdentifier)
    }

    func mount(_ identifier: String, at mountPoint: URL) throws {
      try esp.mount(identifier, at: mountPoint)
    }

    func unmount(_ identifier: String) throws {
      try esp.unmount(identifier)
    }

    func stagingPartitions(on storeIdentifier: String) throws -> [TryExportStagingPartition] {
      let listing = try plist(["list", "-plist", storeIdentifier])
      guard let disks = listing["AllDisksAndPartitions"] as? [[String: Any]],
        let entry = disks.first(where: { $0["DeviceIdentifier"] as? String == storeIdentifier }),
        let records = entry["Partitions"] as? [[String: Any]]
      else {
        throw TryExportStagingError(.rootNotFound)
      }
      return try records.compactMap { item -> TryExportStagingPartition? in
        guard let identifier = item["DeviceIdentifier"] as? String else { return nil }
        let info = try plist(["info", "-plist", identifier])
        guard info["ParentWholeDisk"] as? String == storeIdentifier else { return nil }
        return TryExportStagingPartition(
          identifier: identifier,
          storeIdentifier: storeIdentifier,
          type: info["Content"] as? String ?? "",
          uuid: info["DiskUUID"] as? String ?? "",
          offsetBytes: (info["PartitionMapPartitionOffset"] as? NSNumber)?.uint64Value ?? 0,
          lengthBytes: (info["Size"] as? NSNumber)?.uint64Value ?? 0
        )
      }
    }

    func openRaw(_ identifier: String) throws -> Int32 {
      guard identifier.range(of: #"^disk[0-9]+s[0-9]+$"#, options: .regularExpression) != nil
      else {
        throw TryExportStagingError(.writeFailed)
      }
      let descriptor = open("/dev/r" + identifier, O_RDWR | O_NOFOLLOW)
      guard descriptor >= 0 else { throw TryExportStagingError(.writeFailed) }
      var information = stat()
      guard fstat(descriptor, &information) == 0, information.st_mode & S_IFMT == S_IFCHR else {
        close(descriptor)
        throw TryExportStagingError(.writeFailed)
      }
      return descriptor
    }

    /// DKIOCSYNCHRONIZE, as the engine flushes the images it writes.
    func synchronize(_ descriptor: Int32) throws {
      guard omarchy_installer_synchronize_disk(descriptor) == 0 else {
        throw TryExportStagingError(.writeFailed)
      }
    }

    private func plist(_ arguments: [String]) throws -> [String: Any] {
      guard
        let value = try PropertyListSerialization.propertyList(
          from: commands(arguments), format: nil) as? [String: Any]
      else {
        throw TryExportStagingError(.rootNotFound)
      }
      return value
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
        throw TryExportStagingError(.rootNotFound)
      }
      return result
    }
  }
#endif
