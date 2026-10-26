import Foundation
import Testing

@testable import OmarchyAppleInstallerTrustCore

struct TryOmarchyExportReceiptTests {
  /// The receipt Try Omarchy published for a real export, byte for byte.
  static let published = """
    {"bundle": {"bytes": 29365414, "format": "age-v1-scrypt", "schema": "omarchy-migration/bundle/2", \
    "sha256": "f39b3d91d695d95d3d3aa3c82e0e2de530727cb355c0e582a02577e2c84f076e"}, \
    "estimates": {"entries": 862, "expanded_bytes": 28680598}, \
    "export_id": "6022f2ec-746c-4636-b786-2240103a7efb", "policy_revision": "try-omarchy/82927e9/4", \
    "request_id": "ae43bd60-ed7a-4492-93dc-05cf85a2124d", "schema": "omarchy-migration/receipt/1"}
    """

  static func document(
    _ change: (inout [String: Any]) -> Void = { _ in }
  ) throws -> Data {
    var object =
      try JSONSerialization.jsonObject(with: Data(published.utf8)) as! [String: Any]
    change(&object)
    return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
  }

  typealias Change = (inout [String: Any]) -> Void

  static func bundle(_ change: @escaping Change) -> Change {
    { object in
      var bundle = object["bundle"] as! [String: Any]
      change(&bundle)
      object["bundle"] = bundle
    }
  }

  @Test func decodesThePublishedReceipt() throws {
    let receipt = try TryOmarchyExportReceipt.decode(Data(Self.published.utf8))
    #expect(receipt.exportID == "6022f2ec-746c-4636-b786-2240103a7efb")
    #expect(receipt.requestID == "ae43bd60-ed7a-4492-93dc-05cf85a2124d")
    #expect(receipt.policyRevision == "try-omarchy/82927e9/4")
    #expect(receipt.bundleBytes == 29_365_414)
    #expect(
      receipt.bundleDigest.hexadecimal
        == "f39b3d91d695d95d3d3aa3c82e0e2de530727cb355c0e582a02577e2c84f076e"
    )
    #expect(receipt.expandedBytes == 28_680_598)
    #expect(receipt.entries == 862)
  }

  @Test func namesAnUnknownSchemaBeforeItsFields() throws {
    for schema in ["omarchy-migration/receipt/2", "omarchy-migration-fixture/1"] {
      let data = try Self.document {
        $0["schema"] = schema
        $0["filename"] = "bundle.age"
      }
      #expect(throws: TryOmarchyExportReceiptError.unsupportedSchema) {
        try TryOmarchyExportReceipt.decode(data)
      }
    }
  }

  @Test func rejectsTheContractsInvalidReceipts() throws {
    let cases: [(name: String, change: Change, path: String)] = [
      (
        "unknown top-level field",
        { $0["filename"] = "bundle.age" },
        "$"
      ),
      (
        "missing export identity",
        { $0["export_id"] = nil },
        "$"
      ),
      (
        "non-canonical uuid",
        { $0["export_id"] = "6022F2EC-746C-4636-B786-2240103A7EFB" },
        "$.export_id"
      ),
      (
        "unbraced uuid",
        { $0["request_id"] = "{ae43bd60-ed7a-4492-93dc-05cf85a2124d}" },
        "$.request_id"
      ),
      (
        "uppercase digest",
        Self.bundle { $0["sha256"] = String(repeating: "A", count: 64) },
        "$.bundle.sha256"
      ),
      (
        "prefixed digest",
        Self.bundle { $0["sha256"] = "sha256:" + String(repeating: "a", count: 64) },
        "$.bundle.sha256"
      ),
      (
        "empty ciphertext",
        Self.bundle { $0["bytes"] = 0 },
        "$.bundle.bytes"
      ),
      (
        "oversized ciphertext",
        Self.bundle { $0["bytes"] = 276_018_757_633 },
        "$.bundle.bytes"
      ),
      (
        "unsupported bundle format",
        Self.bundle { $0["format"] = "age-v1-x25519" },
        "$.bundle.format"
      ),
      (
        "older bundle schema",
        Self.bundle { $0["schema"] = "omarchy-migration/bundle/1" },
        "$.bundle.schema"
      ),
      (
        "unknown bundle field",
        Self.bundle { $0["filename"] = "bundle.age" },
        "$.bundle"
      ),
      (
        "boolean is not an integer",
        { $0["estimates"] = ["entries": true, "expanded_bytes": 1] },
        "$.estimates.entries"
      ),
      (
        "too many entries",
        { $0["estimates"] = ["entries": 200_001, "expanded_bytes": 1] },
        "$.estimates.entries"
      ),
      (
        "negative size",
        { $0["estimates"] = ["entries": 1, "expanded_bytes": -1] },
        "$.estimates.expanded_bytes"
      ),
      (
        "uppercase policy",
        { $0["policy_revision"] = "Try-omarchy/1" },
        "$.policy_revision"
      ),
      (
        "policy with a newline",
        { $0["policy_revision"] = "try-omarchy/1\n" },
        "$.policy_revision"
      ),
      (
        "long policy",
        { $0["policy_revision"] = String(repeating: "a", count: 129) },
        "$.policy_revision"
      ),
    ]
    for (name, change, path) in cases {
      let data = try Self.document(change)
      #expect(throws: TryOmarchyExportReceiptError.invalid(path), "\(name)") {
        try TryOmarchyExportReceipt.decode(data)
      }
    }
  }

  @Test func readsIntegersStrictly() throws {
    let entries = "\"entries\": 862"
    for number in ["862.0", "8.62e2"] {
      let text = Self.published.replacingOccurrences(of: entries, with: "\"entries\": \(number)")
      #expect(throws: TryOmarchyExportReceiptError.invalid("$.estimates.entries")) {
        try TryOmarchyExportReceipt.decode(Data(text.utf8))
      }
    }
  }

  @Test func rejectsDuplicateKeysMalformedAndOversizedDocuments() {
    let duplicate = Self.published.replacingOccurrences(
      of: "\"schema\": \"omarchy-migration/receipt/1\"}",
      with: "\"schema\": \"omarchy-migration/receipt/1\", "
        + "\"export_id\": \"6022f2ec-746c-4636-b786-2240103a7efb\"}"
    )
    #expect(throws: TryOmarchyExportReceiptError.duplicateKey) {
      try TryOmarchyExportReceipt.decode(Data(duplicate.utf8))
    }
    #expect(throws: TryOmarchyExportReceiptError.invalidJSON) {
      try TryOmarchyExportReceipt.decode(Data((Self.published + " {}").utf8))
    }
    #expect(throws: TryOmarchyExportReceiptError.invalidJSON) {
      try TryOmarchyExportReceipt.decode(Data([0x7B, 0xFF, 0x7D]))
    }
    let padded = Self.published + String(repeating: " ", count: 1024 * 1024)
    #expect(throws: TryOmarchyExportReceiptError.oversizedDocument) {
      try TryOmarchyExportReceipt.decode(Data(padded.utf8))
    }
  }
}

struct StrictJSONTests {
  @Test func readsEscapesAndSurrogatePairs() throws {
    let value = try StrictJSON.parse(Data(#"{"a":"é😀\n\"/","b":[1,-2,true,null]}"#.utf8))
    #expect(
      value
        == .object([
          "a": .string("é😀\n\"/"),
          "b": .array([.integer(1), .integer(-2), .boolean(true), .null]),
        ])
    )
  }

  @Test func rejectsWhatJSONSerializationWouldAccept() {
    for text in [
      #"{"a":1,"a":2}"#, "[01]", "[1.]", "[.5]", "[-]", #"["\ud83d"]"#, "[1,]", "{\"a\":1,}",
      "[\"tab\there\"]", "[1] [2]", "", "nan", "[Infinity]",
    ] {
      #expect(throws: (any Error).self, "\(text)") {
        try StrictJSON.parse(Data(text.utf8))
      }
    }
    #expect(throws: StrictJSONError.duplicateKey("a")) {
      try StrictJSON.parse(Data(#"{"a":1,"a":2}"#.utf8))
    }
  }

  @Test func keepsOutOfRangeAndFractionalNumbersApart() throws {
    #expect(try StrictJSON.parse(Data("9223372036854775807".utf8)) == .integer(.max))
    #expect(try StrictJSON.parse(Data("9223372036854775808".utf8)) == .otherNumber)
    #expect(try StrictJSON.parse(Data("1.5e3".utf8)) == .otherNumber)
  }

  @Test func boundsNesting() {
    let deep = String(repeating: "[", count: 65) + String(repeating: "]", count: 65)
    #expect(throws: StrictJSONError.tooDeep) {
      try StrictJSON.parse(Data(deep.utf8))
    }
    let allowed = String(repeating: "[", count: 64) + String(repeating: "]", count: 64)
    #expect((try? StrictJSON.parse(Data(allowed.utf8))) != nil)
  }
}
