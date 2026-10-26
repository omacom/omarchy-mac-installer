import Foundation

/// The public `omarchy-migration/receipt/1` document Try Omarchy publishes
/// beside an encrypted migration bundle. It names the ciphertext's identity
/// and size; it never carries a passphrase or a path.
public struct TryOmarchyExportReceipt: Equatable, Sendable {
  public static let schema = "omarchy-migration/receipt/1"
  public static let bundleSchema = "omarchy-migration/bundle/2"
  public static let bundleFormat = "age-v1-scrypt"
  public static let maximumDocumentBytes = 1024 * 1024
  public static let maximumEntries: Int64 = 200_000
  public static let maximumExpandedBytes: Int64 = 256 * 1024 * 1024 * 1024
  /// Expanded content plus a 64 MiB manifest plus 1 GiB of format overhead.
  public static let maximumBundleBytes: Int64 =
    maximumExpandedBytes + 64 * 1024 * 1024 + 1024 * 1024 * 1024

  public let requestID: String
  public let exportID: String
  public let policyRevision: String
  public let bundleBytes: Int64
  public let bundleDigest: SHA256Digest
  public let expandedBytes: Int64
  public let entries: Int64

  public static func decode(_ data: Data) throws -> TryOmarchyExportReceipt {
    guard data.count <= maximumDocumentBytes else {
      throw TryOmarchyExportReceiptError.oversizedDocument
    }
    let value: StrictJSONValue
    do {
      value = try StrictJSON.parse(data)
    } catch StrictJSONError.duplicateKey {
      throw TryOmarchyExportReceiptError.duplicateKey
    } catch {
      throw TryOmarchyExportReceiptError.invalidJSON
    }
    // Name an unknown schema before judging its fields.
    guard case .object(let members) = value, case .string(schema) = members["schema"] else {
      throw TryOmarchyExportReceiptError.unsupportedSchema
    }
    let document = try object(
      value,
      "$",
      ["schema", "request_id", "export_id", "policy_revision", "bundle", "estimates"]
    )
    let bundle = try object(
      document["bundle"],
      "$.bundle",
      ["format", "schema", "bytes", "sha256"]
    )
    guard case .string(bundleFormat) = bundle["format"] else {
      throw TryOmarchyExportReceiptError.invalid("$.bundle.format")
    }
    guard case .string(bundleSchema) = bundle["schema"] else {
      throw TryOmarchyExportReceiptError.invalid("$.bundle.schema")
    }
    guard case .string(let hexadecimal) = bundle["sha256"],
      let digest = SHA256Digest(hexadecimal: hexadecimal)
    else {
      throw TryOmarchyExportReceiptError.invalid("$.bundle.sha256")
    }
    let estimates = try object(document["estimates"], "$.estimates", ["expanded_bytes", "entries"])
    return TryOmarchyExportReceipt(
      requestID: try uuid(document["request_id"], "$.request_id"),
      exportID: try uuid(document["export_id"], "$.export_id"),
      policyRevision: try label(document["policy_revision"], "$.policy_revision"),
      bundleBytes: try integer(bundle["bytes"], "$.bundle.bytes", 1...maximumBundleBytes),
      bundleDigest: digest,
      expandedBytes: try integer(
        estimates["expanded_bytes"],
        "$.estimates.expanded_bytes",
        0...maximumExpandedBytes
      ),
      entries: try integer(estimates["entries"], "$.estimates.entries", 0...maximumEntries)
    )
  }

  private static func object(
    _ value: StrictJSONValue?,
    _ path: String,
    _ keys: Set<String>
  ) throws -> [String: StrictJSONValue] {
    guard case .object(let members) = value, Set(members.keys) == keys else {
      throw TryOmarchyExportReceiptError.invalid(path)
    }
    return members
  }

  private static func integer(
    _ value: StrictJSONValue?,
    _ path: String,
    _ range: ClosedRange<Int64>
  ) throws -> Int64 {
    guard case .integer(let integer) = value, range.contains(integer) else {
      throw TryOmarchyExportReceiptError.invalid(path)
    }
    return integer
  }

  /// A canonical lowercase UUID, as the contract requires for identifiers.
  static func isCanonicalUUID(_ text: String) -> Bool {
    UUID(uuidString: text)?.uuidString.lowercased() == text
  }

  private static func uuid(_ value: StrictJSONValue?, _ path: String) throws -> String {
    guard case .string(let text) = value, isCanonicalUUID(text) else {
      throw TryOmarchyExportReceiptError.invalid(path)
    }
    return text
  }

  private static func label(_ value: StrictJSONValue?, _ path: String) throws -> String {
    guard case .string(let text) = value,
      let first = text.utf8.first,
      text.utf8.count <= 128,
      isLowercaseAlphanumeric(first),
      text.utf8.allSatisfy({ isLowercaseAlphanumeric($0) || Array("._/-".utf8).contains($0) })
    else {
      throw TryOmarchyExportReceiptError.invalid(path)
    }
    return text
  }

  private static func isLowercaseAlphanumeric(_ byte: UInt8) -> Bool {
    (byte >= 0x30 && byte <= 0x39) || (byte >= 0x61 && byte <= 0x7A)
  }
}

public enum TryOmarchyExportReceiptError: Error, Equatable, Sendable {
  case oversizedDocument
  case invalidJSON
  case duplicateKey
  case unsupportedSchema
  /// The field at this JSON path is missing, unexpected or out of range.
  case invalid(String)
}
