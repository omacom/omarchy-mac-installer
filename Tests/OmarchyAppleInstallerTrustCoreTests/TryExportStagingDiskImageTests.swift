#if os(macOS)
  import CryptoKit
  import Foundation
  import Testing

  @testable import OmarchyAppleInstallerTrustCore

  /// Stages onto a real partitioned disk image through diskutil and the raw
  /// device, as the helper does. Run with
  /// `OMARCHY_TRY_EXPORT_STAGING_DISK=diskN` naming an attached image laid out
  /// as Development/try-export-staging-disk.sh makes it, and
  /// `OMARCHY_TRY_EXPORT_STAGING_BUNDLE` and `_RECEIPT` naming an export. The
  /// install's extent starts at the APFS stub, or at the first partition of a
  /// disk without one, such as a VM's laid out as an install.
  struct TryExportStagingDiskImageTests {
    static let environment = ProcessInfo.processInfo.environment
    static let disk = environment["OMARCHY_TRY_EXPORT_STAGING_DISK"]

    @Test(.enabled(if: disk != nil)) func stagesOntoAnAttachedImage() throws {
      let disk = try #require(Self.disk)
      let bundleURL = URL(
        fileURLWithPath: try #require(Self.environment["OMARCHY_TRY_EXPORT_STAGING_BUNDLE"]))
      let receipt = try Data(
        contentsOf: URL(
          fileURLWithPath: try #require(Self.environment["OMARCHY_TRY_EXPORT_STAGING_RECEIPT"])))
      let disks = DiskutilTryExportStagingOperator()
      let partitions = try disks.stagingPartitions(on: disk)
      let first = try #require(
        partitions.first { $0.type == "Apple_APFS" }
          ?? partitions.min { $0.offsetBytes < $1.offsetBytes })
      let end = try #require(partitions.map { $0.offsetBytes + $0.lengthBytes }.max())
      let work = FileManager.default.temporaryDirectory
        .appendingPathComponent("try-export-disk-\(UUID().uuidString)", isDirectory: true)
      try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
      defer { try? FileManager.default.removeItem(at: work) }

      let bundle = try FileHandle(forReadingFrom: bundleURL)
      defer { try? bundle.close() }
      let marker = try TryExportStagingWriter(disks: disks, workingDirectory: work).stage(
        bundle: bundle, receiptDocument: receipt, storeIdentifier: disk,
        offsetBytes: first.offsetBytes, lengthBytes: end - first.offsetBytes)

      let root = try TryExportRootLocator.identify(
        storeIdentifier: disk, offsetBytes: first.offsetBytes,
        lengthBytes: end - first.offsetBytes, partitions: partitions)
      let descriptor = open("/dev/r" + root.identifier, O_RDONLY)
      #expect(descriptor >= 0)
      defer { close(descriptor) }
      let written = Int(TryExportStagingRegion.writtenBytes(marker.length))
      var buffer = [UInt8](repeating: 0, count: written)
      #expect(pread(descriptor, &buffer, written, off_t(marker.offset)) == written)
      let original = try Data(contentsOf: bundleURL)
      #expect(Data(buffer.prefix(original.count)) == original)
      #expect(
        SHA256.hash(data: original).map { String(format: "%02x", $0) }.joined() == marker.sha256)
      #expect(marker.partitionUUID == root.uuid.lowercased())

      let esp = try InstallConfESPLocator.identify(
        storeIdentifier: disk, offsetBytes: first.offsetBytes,
        lengthBytes: end - first.offsetBytes, partitions: try disks.partitions(on: disk))
      let mountPoint = work.appendingPathComponent("esp", isDirectory: true)
      try FileManager.default.createDirectory(at: mountPoint, withIntermediateDirectories: true)
      try disks.mount(esp.partitionIdentifier, at: mountPoint)
      defer { try? disks.unmount(esp.partitionIdentifier) }
      let onDisk = try Data(contentsOf: mountPoint.appendingPathComponent("omarchy/migration.json"))
      #expect(onDisk == (try marker.serializedData()))
    }
  }
#endif
