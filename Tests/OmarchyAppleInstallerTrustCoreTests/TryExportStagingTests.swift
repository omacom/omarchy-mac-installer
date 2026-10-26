#if os(macOS)
  import CryptoKit
  import Foundation
  import Testing

  @testable import OmarchyAppleInstallerTrustCore

  struct TryExportStagingTests {
    static let gibibyte: UInt64 = 1 << 30
    static let rootOffset: UInt64 = 806 * gibibyte
    static let rootLength: UInt64 = 40 * gibibyte
    static let rootUUID = "6A1F5C2E-0B7D-4D3E-9C1A-5E2F8B7D4C3A"

    static func partitions(rootLength: UInt64 = rootLength) -> [TryExportStagingPartition] {
      [
        .init(
          identifier: "disk0s4", storeIdentifier: "disk0", type: "Apple_APFS",
          uuid: "11111111-1111-4111-8111-111111111111", offsetBytes: 800 * gibibyte,
          lengthBytes: 3 * gibibyte),
        .init(
          identifier: "disk0s5", storeIdentifier: "disk0", type: "EFI",
          uuid: "22222222-2222-4222-8222-222222222222", offsetBytes: 803 * gibibyte,
          lengthBytes: gibibyte),
        .init(
          identifier: "disk0s6", storeIdentifier: "disk0", type: "Linux Filesystem",
          uuid: "33333333-3333-4333-8333-333333333333", offsetBytes: 804 * gibibyte,
          lengthBytes: 2 * gibibyte),
        .init(
          identifier: "disk0s7", storeIdentifier: "disk0", type: "Linux Filesystem",
          uuid: rootUUID, offsetBytes: rootOffset, lengthBytes: rootLength),
      ]
    }

    @Test func regionMatchesTheSharedCases() throws {
      let fixture = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appendingPathComponent("packages/omarchy-migration/test/fixtures/staging-regions.json")
      let document =
        try JSONSerialization.jsonObject(with: Data(contentsOf: fixture)) as! [String: Any]
      #expect(document["schema"] as? String == "omarchy-migration/staging-regions/1")
      let cases = try #require(document["cases"] as? [[String: Any]])
      #expect(cases.count > 10)
      for item in cases {
        let partition = (item["partition_bytes"] as! NSNumber).uint64Value
        let bundle = (item["bundle_bytes"] as! NSNumber).uint64Value
        let expected = (item["offset"] as? NSNumber)?.uint64Value
        #expect(
          TryExportStagingRegion.offset(partitionBytes: partition, bundleBytes: bundle) == expected,
          "\(partition) \(bundle)")
      }
      #expect(
        TryExportStagingRegion.offset(partitionBytes: 100 * Self.gibibyte, bundleBytes: 0) == nil)
      #expect(TryExportStagingRegion.offset(partitionBytes: 1, bundleBytes: 1) == nil)
      #expect(TryExportStagingRegion.offset(partitionBytes: .max, bundleBytes: .max) == nil)
    }

    @Test func onlyAnImageWhoseMetadataNamesTheMarkerClaimsExports() throws {
      let folder = FileManager.default.temporaryDirectory
        .appendingPathComponent("try-export-image-\(UUID().uuidString)", isDirectory: true)
      try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
      defer { try? FileManager.default.removeItem(at: folder) }
      func claims(_ entries: [[String: Any]]) throws -> Bool {
        let url = folder.appendingPathComponent("installer_data.json")
        try JSONSerialization.data(withJSONObject: ["os_list": entries]).write(to: url)
        return TryExportImageSupport.claimsExports(metadataURL: url)
      }
      let target: [String: Any] = ["omarchy_target": "apple-silicon-full-os", "name": "Omarchy"]
      #expect(try claims([target.merging(["native_import": "omarchy-migration/staging/1"]) { $1 }]))
      #expect(try !claims([target]))
      #expect(
        try !claims([target.merging(["native_import": "omarchy-migration/staging/2"]) { $1 }]))
      #expect(try !claims([target.merging(["native_import": true]) { $1 }]))
      #expect(
        try !claims(
          Array(
            repeating: target.merging(["native_import": "omarchy-migration/staging/1"]) { $1 },
            count: 2)))
      #expect(
        !TryExportImageSupport.claimsExports(metadataURL: folder.appendingPathComponent("none")))
    }

    @Test func theRootIsTheLastLinuxPartitionInsideTheExtent() throws {
      let root = try TryExportRootLocator.identify(
        storeIdentifier: "disk0", offsetBytes: 800 * Self.gibibyte, lengthBytes: 46 * Self.gibibyte,
        partitions: Self.partitions())
      #expect(root.identifier == "disk0s7")
      #expect(root.uuid == Self.rootUUID)

      let refused: [(UInt64, UInt64, [TryExportStagingPartition])] = [
        (800 * Self.gibibyte, 45 * Self.gibibyte, Self.partitions()),
        (805 * Self.gibibyte, 41 * Self.gibibyte, Self.partitions()),
        (800 * Self.gibibyte, 46 * Self.gibibyte, Self.partitions(rootLength: 30 * Self.gibibyte)),
        (800 * Self.gibibyte, 46 * Self.gibibyte, Array(Self.partitions().dropLast())),
        (
          800 * Self.gibibyte, 46 * Self.gibibyte,
          Self.partitions() + [
            .init(
              identifier: "disk0s8", storeIdentifier: "disk0", type: "Linux Filesystem",
              uuid: Self.rootUUID, offsetBytes: 845 * Self.gibibyte, lengthBytes: Self.gibibyte)
          ]
        ),
        (
          800 * Self.gibibyte, 46 * Self.gibibyte,
          Self.partitions().map { partition in
            partition.identifier == "disk0s7"
              ? .init(
                identifier: "disk0s7", storeIdentifier: "disk0", type: partition.type, uuid: "",
                offsetBytes: partition.offsetBytes, lengthBytes: partition.lengthBytes)
              : partition
          }
        ),
      ]
      for (offset, length, partitions) in refused {
        #expect(throws: TryExportStagingError(.rootNotFound)) {
          try TryExportRootLocator.identify(
            storeIdentifier: "disk0", offsetBytes: offset, lengthBytes: length,
            partitions: partitions)
        }
      }
    }

    @Test func theMarkerEmbedsTheReceiptAsPublished() throws {
      let receipt = Data(TryOmarchyExportReceiptTests.published.utf8) + Data("\n".utf8)
      let marker = TryExportStagingMarker(
        exportID: "6022f2ec-746c-4636-b786-2240103a7efb",
        partitionUUID: Self.rootUUID.lowercased(), offset: 101_938_364_416, length: 29_365_414,
        sha256: "f39b3d91d695d95d3d3aa3c82e0e2de530727cb355c0e582a02577e2c84f076e",
        receipt: receipt)
      let data = try marker.serializedData()
      let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
      #expect(
        Set(object.keys)
          == ["schema", "export_id", "partition_uuid", "offset", "length", "sha256", "receipt"])
      #expect(object["schema"] as? String == "omarchy-migration/staging/1")
      #expect((object["offset"] as? NSNumber)?.uint64Value == 101_938_364_416)
      #expect((object["receipt"] as? [String: Any])?["export_id"] as? String == marker.exportID)
      #expect(data.last == UInt8(ascii: "\n"))

      let notAnObject = TryExportStagingMarker(
        exportID: marker.exportID, partitionUUID: marker.partitionUUID, offset: 1, length: 1,
        sha256: marker.sha256, receipt: Data("[1]".utf8))
      #expect(throws: TryExportStagingError(.markerFailed)) { try notAnObject.serializedData() }
    }

    @Test func stagingWritesTheBundleThenTheMarker() throws {
      let fixture = try StagingFixture(bundleBytes: 3 * 1024 * 1024 + 123)
      defer { fixture.remove() }
      let marker = try fixture.stage()
      let offset = try #require(
        TryExportStagingRegion.offset(partitionBytes: Self.rootLength, bundleBytes: fixture.length))
      #expect(marker.offset == offset)
      #expect(marker.length == fixture.length)
      #expect(marker.partitionUUID == Self.rootUUID.lowercased())
      #expect(marker.sha256 == fixture.digest)

      let device = try FileHandle(forReadingFrom: fixture.device)
      try device.seek(toOffset: offset)
      let written = try #require(try device.read(upToCount: Int(fixture.length + 8_192)))
      #expect(written.prefix(Int(fixture.length)) == fixture.bundle)
      let padding = written.dropFirst(Int(fixture.length)).prefix(
        Int(TryExportStagingRegion.writtenBytes(fixture.length) - fixture.length))
      #expect(padding.allSatisfy { $0 == 0 })
      #expect(fixture.disks.synchronizations == 1)
      #expect(fixture.disks.markers == [try marker.serializedData()])
      #expect(fixture.disks.mounted.isEmpty)
    }

    @Test func aBundleThatDiffersFromItsReceiptLeavesNoMarker() throws {
      let fixture = try StagingFixture(bundleBytes: 70_000)
      defer { fixture.remove() }
      var changed = fixture.bundle
      changed[10] ^= 0xff
      try changed.write(to: fixture.bundleURL)
      #expect(throws: TryExportStagingError(.bundleChanged)) { try fixture.stage() }
      #expect(fixture.disks.markers.isEmpty)

      try (fixture.bundle + Data([1])).write(to: fixture.bundleURL)
      #expect(throws: TryExportStagingError(.bundleChanged)) { try fixture.stage() }
      #expect(fixture.disks.markers.isEmpty)

      try fixture.bundle.write(to: fixture.bundleURL)
      #expect(throws: TryExportStagingError(.bundleChanged)) {
        try fixture.stage(receipt: Data("{}".utf8))
      }
    }

    @Test func aRootWithoutRoomStagesNothing() throws {
      let fixture = try StagingFixture(bundleBytes: 4_096, rootLength: 37 * Self.gibibyte)
      defer { fixture.remove() }
      #expect(throws: TryExportStagingError(.noRoom)) { try fixture.stage() }
      #expect(fixture.disks.writes == 0)
    }

    @Test func aReadbackThatDiffersLeavesNoMarker() throws {
      let fixture = try StagingFixture(bundleBytes: 9_000)
      defer { fixture.remove() }
      fixture.disks.corruptAt = TryExportStagingRegion.offset(
        partitionBytes: Self.rootLength, bundleBytes: fixture.length)
      #expect(throws: TryExportStagingError(.readbackMismatch)) { try fixture.stage() }
      #expect(fixture.disks.markers.isEmpty)
    }

    @Test func aMarkerThatCannotBeWrittenIsNotStagedButAnUnmountFailureIs() throws {
      let fixture = try StagingFixture(bundleBytes: 9_000)
      defer { fixture.remove() }
      fixture.disks.mountFails = true
      #expect(throws: TryExportStagingError(.markerFailed)) { try fixture.stage() }
      fixture.disks.mountFails = false
      fixture.disks.unmountFails = true
      let marker = try fixture.stage()
      #expect(fixture.disks.markers.last == (try marker.serializedData()))
    }
  }

  private struct StagingFixture {
    let root: URL
    let device: URL
    let bundleURL: URL
    let bundle: Data
    let length: UInt64
    let digest: String
    let receipt: Data
    let disks: MockStagingDisks

    init(bundleBytes: Int, rootLength: UInt64 = TryExportStagingTests.rootLength) throws {
      root = FileManager.default.temporaryDirectory
        .appendingPathComponent("try-export-staging-\(UUID().uuidString)", isDirectory: true)
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
      device = root.appendingPathComponent("disk0s7")
      FileManager.default.createFile(atPath: device.path, contents: nil)
      let handle = try FileHandle(forWritingTo: device)
      try handle.truncate(atOffset: rootLength)
      try handle.close()
      bundleURL = root.appendingPathComponent("bundle.age")
      bundle = Data((0..<bundleBytes).map { UInt8(truncatingIfNeeded: $0 &* 31 &+ 7) })
      try bundle.write(to: bundleURL)
      length = UInt64(bundleBytes)
      digest = SHA256.hash(data: bundle).map { String(format: "%02x", $0) }.joined()
      let (bytes, hexadecimal) = (bundleBytes, digest)
      receipt = try TryOmarchyExportReceiptTests.document(
        TryOmarchyExportReceiptTests.bundle { bundle in
          bundle["bytes"] = bytes
          bundle["sha256"] = hexadecimal
        })
      disks = MockStagingDisks(
        partitions: TryExportStagingTests.partitions(rootLength: rootLength), device: device)
    }

    func stage(receipt override: Data? = nil) throws -> TryExportStagingMarker {
      let handle = try FileHandle(forReadingFrom: bundleURL)
      defer { try? handle.close() }
      return try TryExportStagingWriter(disks: disks, workingDirectory: root).stage(
        bundle: handle, receiptDocument: override ?? receipt, storeIdentifier: "disk0",
        offsetBytes: 800 * TryExportStagingTests.gibibyte,
        lengthBytes: 46 * TryExportStagingTests.gibibyte)
    }

    func remove() {
      try? FileManager.default.removeItem(at: root)
    }
  }

  private final class MockStagingDisks: TryExportStagingDiskOperating, @unchecked Sendable {
    let stagingRows: [TryExportStagingPartition]
    let device: URL
    var mountFails = false
    var unmountFails = false
    var corruptAt: UInt64?
    private(set) var synchronizations = 0
    private(set) var writes = 0
    private(set) var markers: [Data] = []
    private(set) var mounted: [String] = []
    private var mountPoint: URL?

    init(partitions: [TryExportStagingPartition], device: URL) {
      stagingRows = partitions
      self.device = device
    }

    func partitions(on storeIdentifier: String) throws -> [InstallConfESPPartition] {
      stagingRows.map { row in
        InstallConfESPPartition(
          identifier: row.identifier, storeIdentifier: row.storeIdentifier, type: row.type,
          name: row.type == "EFI" ? InstallConfESPLocator.volumeName : "",
          offsetBytes: row.offsetBytes, lengthBytes: row.lengthBytes)
      }
    }

    func stagingPartitions(on storeIdentifier: String) throws -> [TryExportStagingPartition] {
      stagingRows
    }

    func openRaw(_ identifier: String) throws -> Int32 {
      #expect(identifier == "disk0s7")
      writes += 1
      return open(device.path, O_RDWR)
    }

    func synchronize(_ descriptor: Int32) throws {
      synchronizations += 1
      if let corruptAt {
        var byte: UInt8 = 0
        _ = pread(descriptor, &byte, 1, off_t(corruptAt))
        byte ^= 0xff
        _ = pwrite(descriptor, &byte, 1, off_t(corruptAt))
      }
    }

    func mount(_ identifier: String, at mountPoint: URL) throws {
      #expect(identifier == "disk0s5")
      if mountFails { throw InstallConfESPError.mountFailed }
      try FileManager.default.createDirectory(at: mountPoint, withIntermediateDirectories: true)
      self.mountPoint = mountPoint
      mounted.append(identifier)
    }

    func unmount(_ identifier: String) throws {
      if let mountPoint,
        let data = try? Data(
          contentsOf: mountPoint.appendingPathComponent("omarchy/migration.json"))
      {
        markers.append(data)
      }
      mounted.removeAll { $0 == identifier }
      if unmountFails { throw InstallConfESPError.unmountFailed }
    }
  }
#endif
