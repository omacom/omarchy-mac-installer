import Foundation

// Turns Engine/source-lock.json into the engine pin compiled into the app:
// the inspection engine's version, archive name, size and digest. The lock is
// the only place the engine is pinned (docs/engine-versions.md), so nothing
// generated from it is committed.

func fail(_ message: String) -> Never {
  FileHandle.standardError.write(Data("engine pin generator: \(message)\n".utf8))
  exit(1)
}

func object(_ value: Any?, _ name: String) -> [String: Any] {
  guard let object = value as? [String: Any] else {
    fail("\(name) is not an object")
  }
  return object
}

func string(_ object: [String: Any], _ key: String, _ name: String) -> String {
  guard let value = object[key] as? String, !value.isEmpty else {
    fail("\(name).\(key) is not a non-empty string")
  }
  return value
}

func isHex(_ value: String, count: Int) -> Bool {
  value.count == count
    && value.allSatisfy { ("0"..."9").contains($0) || ("a"..."f").contains($0) }
}

let versionCharacters = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.+-")

let arguments = CommandLine.arguments
guard arguments.count == 3 else {
  fail("usage: OmarchyEnginePinGenerator SOURCE_LOCK OUTPUT")
}
let lockURL = URL(fileURLWithPath: arguments[1])
let output = URL(fileURLWithPath: arguments[2])
let lock: [String: Any]
do {
  lock = object(
    try JSONSerialization.jsonObject(with: Data(contentsOf: lockURL)), "the source lock")
} catch {
  fail("cannot read \(lockURL.path): \(error)")
}
let overlay = object(lock["downstream_overlay"], "downstream_overlay")
let artifact = object(lock["validation_artifact"], "validation_artifact")
let version = string(overlay, "version", "downstream_overlay")
let fileName = string(artifact, "filename", "validation_artifact")
let digest = string(artifact, "sha256", "validation_artifact")
guard version.hasPrefix("v"), version.allSatisfy(versionCharacters.contains) else {
  fail("downstream_overlay.version is not an engine version: \(version)")
}
guard fileName == "installer-\(version).tar.gz" else {
  fail("validation_artifact.filename is \(fileName), expected installer-\(version).tar.gz")
}
guard isHex(digest, count: 64) else {
  fail("validation_artifact.sha256 is not a lowercase SHA-256")
}
guard let size = artifact["size_bytes"] as? NSNumber,
  CFGetTypeID(size) != CFBooleanGetTypeID(), size.int64Value > 0,
  size.stringValue == String(size.uint64Value)
else {
  fail("validation_artifact.size_bytes is not a positive integer")
}

let rendered = """
  // Generated from Engine/source-lock.json. Edit that file, not this one.
  #if os(macOS)
    extension ValidationEngineArtifactLocator {
      public static let version = "\(version)"
      public static let fileName = "\(fileName)"
      public static let expectedDigest = "sha256:\(digest)"
      public static let expectedSizeBytes: UInt64 = \(size.uint64Value)
    }
  #endif

  """
let data = Data(rendered.utf8)
if (try? Data(contentsOf: output)) != data {
  do {
    try data.write(to: output, options: .atomic)
  } catch {
    fail("cannot write \(output.path): \(error)")
  }
}
