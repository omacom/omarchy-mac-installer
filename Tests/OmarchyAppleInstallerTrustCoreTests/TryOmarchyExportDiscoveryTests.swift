import CryptoKit
import Darwin
import Foundation
import Testing

@testable import OmarchyAppleInstallerTrustCore

struct TryOmarchyExportDiscoveryTests {
  static let header = Data(
    """
    age-encryption.org/v1
    -> scrypt a1/WNf81SqYX4le1z4GvhQ 18
    hBeH2yed5pctEHVew0m9pHXtO6wQpwTLzcne3V7yMJo
    --- MagBY95MDdy5Zmp3V14PLI6H73+QR7eDVkOu+0CywB8

    """.utf8
  )

  /// A private temporary Exports folder that is removed afterwards.
  final class Folder {
    let root: URL
    let exports: URL

    init() throws {
      root = FileManager.default.temporaryDirectory
        .appendingPathComponent("try-export-tests-\(UUID().uuidString)", isDirectory: true)
      exports = root.appendingPathComponent("Exports", isDirectory: true)
      try FileManager.default.createDirectory(
        at: exports,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
      )
    }

    deinit {
      try? FileManager.default.removeItem(at: root)
    }

    var discovery: TryOmarchyExportDiscovery {
      TryOmarchyExportDiscovery(directory: exports)
    }

    /// Publishes an export the way Try does; returns its folder.
    @discardableResult
    func publish(
      id: String = UUID().uuidString.lowercased(),
      payload: Data = Data((0..<70_000).map { UInt8($0 % 251) }),
      published: Date = Date(timeIntervalSince1970: 1_800_000_000),
      receipt change: (inout [String: Any]) -> Void = { _ in }
    ) throws -> URL {
      let folder = exports.appendingPathComponent(id, isDirectory: true)
      try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false)
      chmod(folder.path, 0o700)
      let bundle = TryOmarchyExportDiscoveryTests.header + payload
      var receipt: [String: Any] = [
        "schema": "omarchy-migration/receipt/1",
        "request_id": "ae43bd60-ed7a-4492-93dc-05cf85a2124d",
        "export_id": id,
        "policy_revision": "try-omarchy/82927e9/4",
        "bundle": [
          "format": "age-v1-scrypt",
          "schema": "omarchy-migration/bundle/2",
          "bytes": bundle.count,
          "sha256": SHA256.hash(data: bundle).map { String(format: "%02x", $0) }.joined(),
        ],
        "estimates": ["expanded_bytes": payload.count, "entries": 3],
      ]
      change(&receipt)
      try bundle.write(to: folder.appendingPathComponent("bundle.age"))
      try JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys])
        .write(to: folder.appendingPathComponent("receipt.json"))
      for name in ["bundle.age", "receipt.json"] {
        let path = folder.appendingPathComponent(name).path
        chmod(path, 0o600)
        try FileManager.default.setAttributes([.modificationDate: published], ofItemAtPath: path)
      }
      return folder
    }

    func found() throws -> TryOmarchyExportDiscoveryResult {
      try discovery.discover().get()
    }
  }

  @Test func findsNothingWhenTryHasNeverExported() throws {
    let folder = try Folder()
    let missing = TryOmarchyExportDiscovery(
      directory: folder.root.appendingPathComponent("Nope", isDirectory: true)
    )
    #expect(try missing.discover().get() == .empty)
    #expect(try folder.found() == .empty)
  }

  @Test func findsAndVerifiesAPublishedExport() throws {
    let folder = try Folder()
    let id = "6022f2ec-746c-4636-b786-2240103a7efb"
    try folder.publish(id: id)
    try Data().write(to: folder.exports.appendingPathComponent(".DS_Store"))
    let assembling = folder.exports.appendingPathComponent(".\(id).Ab12Cd", isDirectory: true)
    try FileManager.default.createDirectory(at: assembling, withIntermediateDirectories: false)
    try Data("partial".utf8).write(to: assembling.appendingPathComponent("bundle.age"))
    try Data().write(to: folder.exports.appendingPathComponent("\(id)/.DS_Store"))

    let result = try folder.found()
    #expect(result.rejected.isEmpty)
    let export = try #require(result.exports.first)
    #expect(result.exports.count == 1)
    #expect(export.receipt.exportID == id)
    #expect(export.verified == false)
    #expect(export.publishedAt == Date(timeIntervalSince1970: 1_800_000_000))

    let verified = try folder.discovery.verify(export)
    #expect(verified.verified)
    #expect(verified.receipt == export.receipt)
  }

  @Test func listsNewestFirst() throws {
    let folder = try Folder()
    let older = try folder.publish(published: Date(timeIntervalSince1970: 1_700_000_000))
    let newer = try folder.publish(published: Date(timeIntervalSince1970: 1_800_000_000))
    let ids = try folder.found().exports.map(\.directory.lastPathComponent)
    #expect(ids == [newer.lastPathComponent, older.lastPathComponent])
  }

  @Test func rejectsFoldersThatAreNotExactlyAnExport() throws {
    let folder = try Folder()
    let manager = FileManager.default
    try manager.createDirectory(
      at: folder.exports.appendingPathComponent("not-an-export"),
      withIntermediateDirectories: false
    )
    let stray = UUID().uuidString.lowercased()
    try Data().write(to: folder.exports.appendingPathComponent(stray))
    let upper = UUID().uuidString
    try manager.createDirectory(
      at: folder.exports.appendingPathComponent(upper),
      withIntermediateDirectories: false
    )
    let extra = try folder.publish()
    try Data().write(to: extra.appendingPathComponent("request.json"))
    let partial = try folder.publish()
    try manager.removeItem(at: partial.appendingPathComponent("bundle.age"))
    let renamed = try folder.publish(receipt: { $0["export_id"] = UUID().uuidString.lowercased() })
    let resized = try folder.publish(receipt: {
      var bundle = $0["bundle"] as! [String: Any]
      bundle["bytes"] = (bundle["bytes"] as! Int) + 1
      $0["bundle"] = bundle
    })
    let plaintext = try folder.publish()
    var tar = Data("ustar".utf8)
    tar.append(Data(repeating: 0, count: TryOmarchyExportDiscoveryTests.header.count + 70_000 - 5))
    try tar.write(to: plaintext.appendingPathComponent("bundle.age"))
    let invalid = try folder.publish(receipt: { $0["filename"] = "bundle.age" })

    let result = try folder.found()
    #expect(result.exports.isEmpty)
    #expect(
      result.rejected == [
        "not-an-export": .unexpectedEntry,
        stray: .unexpectedEntry,
        upper: .unexpectedEntry,
        extra.lastPathComponent: .unexpectedContents,
        partial.lastPathComponent: .unexpectedContents,
        renamed.lastPathComponent: .identityMismatch,
        resized.lastPathComponent: .sizeMismatch,
        plaintext.lastPathComponent: .notAnAgeBundle,
        invalid.lastPathComponent: .receipt(.invalid("$")),
      ]
    )
  }

  @Test func refusesLinksAndFilesOthersCouldChange() throws {
    let folder = try Folder()
    let linked = try folder.publish()
    let elsewhere = folder.root.appendingPathComponent("elsewhere.age")
    try FileManager.default.moveItem(at: linked.appendingPathComponent("bundle.age"), to: elsewhere)
    try FileManager.default.createSymbolicLink(
      at: linked.appendingPathComponent("bundle.age"),
      withDestinationURL: elsewhere
    )
    let shared = try folder.publish()
    chmod(shared.appendingPathComponent("receipt.json").path, 0o666)
    let open = try folder.publish()
    chmod(open.path, 0o777)
    let real = try folder.publish()
    let alias = folder.exports.appendingPathComponent(UUID().uuidString.lowercased())
    try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: real)

    let result = try folder.found()
    #expect(result.exports.map(\.directory) == [real])
    #expect(result.rejected[linked.lastPathComponent] == .unsafeOwnership)
    #expect(result.rejected[shared.lastPathComponent] == .unsafeOwnership)
    #expect(result.rejected[open.lastPathComponent] == .unsafeOwnership)
    #expect(result.rejected[alias.lastPathComponent] == .unexpectedEntry)

    let otherOwner = TryOmarchyExportDiscovery(directory: folder.exports, ownerID: getuid() + 1)
    #expect(otherOwner.discover() == .failure(.exportsFolderUnsafe))

    let aliasRoot = folder.root.appendingPathComponent("AliasExports")
    try FileManager.default.createSymbolicLink(at: aliasRoot, withDestinationURL: folder.exports)
    let throughAlias = TryOmarchyExportDiscovery(directory: aliasRoot)
    #expect(throughAlias.discover() == .failure(.exportsFolderUnsafe))
  }

  @Test func refusesNamedPipesWithoutWaitingOnThem() throws {
    let folder = try Folder()
    let pipedReceipt = try folder.publish()
    let pipedBundle = try folder.publish()
    let real = try folder.publish()
    for file in [
      pipedReceipt.appendingPathComponent("receipt.json"),
      pipedBundle.appendingPathComponent("bundle.age"),
    ] {
      try FileManager.default.removeItem(at: file)
      #expect(mkfifo(file.path, 0o600) == 0)
    }

    let result = try folder.found()
    #expect(result.exports.map(\.directory) == [real])
    #expect(result.rejected[pipedReceipt.lastPathComponent] == .unsafeOwnership)
    #expect(result.rejected[pipedBundle.lastPathComponent] == .unsafeOwnership)
  }

  @Test func verificationCatchesTamperingAndChanges() throws {
    let folder = try Folder()
    try folder.publish(receipt: {
      var bundle = $0["bundle"] as! [String: Any]
      bundle["sha256"] = String(repeating: "0", count: 64)
      $0["bundle"] = bundle
    })
    let tampered = try #require(try folder.found().exports.first)
    #expect(throws: TryOmarchyExportProblem.digestMismatch) {
      try folder.discovery.verify(tampered)
    }

    let changing = try Folder()
    let changed = try changing.publish()
    let export = try #require(try changing.found().exports.first)
    let bundle = changed.appendingPathComponent("bundle.age")
    var bytes = try Data(contentsOf: bundle)
    bytes[bytes.count - 1] ^= 0xFF
    try bytes.write(to: bundle)
    #expect(throws: TryOmarchyExportProblem.changedDuringVerification) {
      try changing.discovery.verify(export)
    }
  }

  @Test func verificationStopsWhenCancelled() throws {
    let folder = try Folder()
    try folder.publish()
    let export = try #require(try folder.found().exports.first)
    #expect(throws: CancellationError.self) {
      try folder.discovery.verify(export, isCancelled: { true })
    }
  }

  @Test func recognizesOnlyAOneRecipientScryptHeader() {
    let header = TryOmarchyExportDiscoveryTests.header
    #expect(TryOmarchyExportDiscovery.isScryptAgeHeader(ArraySlice(header)))
    for (target, replacement) in [
      ("-> scrypt", "-> X25519"),
      (" 18\n", " 22\n"),
      ("age-encryption.org/v1", "age-encryption.org/v2"),
      ("--- ", "-> "),
    ] {
      let changed = String(decoding: header, as: UTF8.self)
        .replacingOccurrences(of: target, with: replacement)
      let bytes = ArraySlice(Data(changed.utf8))
      #expect(!TryOmarchyExportDiscovery.isScryptAgeHeader(bytes), "\(target)")
    }
    #expect(!TryOmarchyExportDiscovery.isScryptAgeHeader(ArraySlice(header.prefix(60))))
  }

  /// Checks a real Exports folder when one is named, for example
  /// `TRY_OMARCHY_EXPORTS="$HOME/Library/Application Support/Try Omarchy/Exports"`.
  @Test(.enabled(if: ProcessInfo.processInfo.environment["TRY_OMARCHY_EXPORTS"] != nil))
  func verifiesARealExportsFolder() throws {
    let directory = URL(
      fileURLWithPath: try #require(ProcessInfo.processInfo.environment["TRY_OMARCHY_EXPORTS"]),
      isDirectory: true
    )
    let discovery = TryOmarchyExportDiscovery(directory: directory)
    let result = try discovery.discover().get()
    #expect(result.rejected.isEmpty, "\(result.rejected)")
    #expect(!result.exports.isEmpty)
    for export in result.exports {
      #expect(try discovery.verify(export).verified)
    }
  }
}
