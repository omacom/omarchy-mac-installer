import CryptoKit
import Darwin
import Foundation

/// An export Try Omarchy published and the installer can offer. Discovery
/// checks its layout, receipt, size and age header; `verified` is set only
/// after the ciphertext's digest matched the receipt.
public struct TryOmarchyExport: Equatable, Sendable {
  public let directory: URL
  public let receipt: TryOmarchyExportReceipt
  public let publishedAt: Date
  public let verified: Bool
  let bundleIdentity: TryOmarchyExportFileIdentity

  public var bundleURL: URL {
    directory.appendingPathComponent(TryOmarchyExportDiscovery.bundleName)
  }
}

struct TryOmarchyExportFileIdentity: Equatable, Sendable {
  let device: Int64
  let inode: UInt64
  let size: Int64
  let modifiedSeconds: Int
  let modifiedNanoseconds: Int

  init(_ status: stat) {
    device = Int64(status.st_dev)
    inode = UInt64(status.st_ino)
    size = Int64(status.st_size)
    modifiedSeconds = status.st_mtimespec.tv_sec
    modifiedNanoseconds = status.st_mtimespec.tv_nsec
  }
}

public enum TryOmarchyExportProblem: Error, Equatable, Sendable {
  case exportsFolderUnsafe
  /// Not a directory named by a canonical lowercase export UUID.
  case unexpectedEntry
  /// Owned by another user, writable by others, or a symbolic link.
  case unsafeOwnership
  /// Anything other than exactly `bundle.age` and `receipt.json`.
  case unexpectedContents
  case unreadable
  case receipt(TryOmarchyExportReceiptError)
  /// The receipt names another export than its folder.
  case identityMismatch
  case sizeMismatch
  case notAnAgeBundle
  case digestMismatch
  case changedDuringVerification
}

public struct TryOmarchyExportDiscoveryResult: Equatable, Sendable {
  /// Usable exports, newest first.
  public let exports: [TryOmarchyExport]
  /// Folder names that look like exports but cannot be used, with why.
  public let rejected: [String: TryOmarchyExportProblem]

  public static let empty = TryOmarchyExportDiscoveryResult(exports: [], rejected: [:])
}

/// Finds Try Omarchy migration exports in
/// `~/Library/Application Support/Try Omarchy/Exports/<export id>/`. It reads
/// only the public receipt and ciphertext; it never needs the passphrase.
public struct TryOmarchyExportDiscovery: Sendable {
  static let bundleName = "bundle.age"
  static let receiptName = "receipt.json"
  /// Finder may leave one in any folder the user opened.
  static let finderMetadataName = ".DS_Store"
  static let headerLimit = 4 * 81

  public let directory: URL
  public let ownerID: uid_t

  public init(directory: URL, ownerID: uid_t = getuid()) {
    self.directory = directory
    self.ownerID = ownerID
  }

  public static func exportsDirectory(home: URL) -> URL {
    home.appendingPathComponent(
      "Library/Application Support/Try Omarchy/Exports",
      isDirectory: true
    )
  }

  public func discover() -> Result<TryOmarchyExportDiscoveryResult, TryOmarchyExportProblem> {
    var status = stat()
    if lstat(directory.path, &status) != 0 {
      return errno == ENOENT ? .success(.empty) : .failure(.exportsFolderUnsafe)
    }
    guard isPrivateDirectory(status) else {
      return .failure(.exportsFolderUnsafe)
    }
    guard let names = try? FileManager.default.contentsOfDirectory(atPath: directory.path) else {
      return .failure(.exportsFolderUnsafe)
    }
    var exports: [TryOmarchyExport] = []
    var rejected: [String: TryOmarchyExportProblem] = [:]
    // Hidden entries are Finder's or an export Try Omarchy is still
    // assembling before renaming it into place; neither is an export yet.
    for name in names.sorted() where !name.hasPrefix(".") {
      switch inspect(name) {
      case .success(let export): exports.append(export)
      case .failure(let problem): rejected[name] = problem
      }
    }
    exports.sort {
      ($0.publishedAt, $0.receipt.exportID) > ($1.publishedAt, $1.receipt.exportID)
    }
    return .success(TryOmarchyExportDiscoveryResult(exports: exports, rejected: rejected))
  }

  /// Streams the ciphertext and compares its SHA-256 with the receipt. The
  /// file must be the one discovery saw and must not change while it is read.
  public func verify(
    _ export: TryOmarchyExport,
    isCancelled: () -> Bool = { false }
  ) throws -> TryOmarchyExport {
    let descriptor = open(export.bundleURL.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
    guard descriptor >= 0 else {
      throw TryOmarchyExportProblem.unreadable
    }
    defer { close(descriptor) }
    var before = stat()
    guard fstat(descriptor, &before) == 0 else {
      throw TryOmarchyExportProblem.unreadable
    }
    guard TryOmarchyExportFileIdentity(before) == export.bundleIdentity else {
      throw TryOmarchyExportProblem.changedDuringVerification
    }
    var hasher = SHA256()
    var buffer = [UInt8](repeating: 0, count: 1024 * 1024)
    var total: Int64 = 0
    while true {
      if isCancelled() {
        throw CancellationError()
      }
      let count = buffer.withUnsafeMutableBytes { read(descriptor, $0.baseAddress, $0.count) }
      if count < 0 {
        if errno == EINTR { continue }
        throw TryOmarchyExportProblem.unreadable
      }
      if count == 0 { break }
      total += Int64(count)
      guard total <= export.receipt.bundleBytes else {
        throw TryOmarchyExportProblem.changedDuringVerification
      }
      buffer.withUnsafeBytes {
        hasher.update(bufferPointer: UnsafeRawBufferPointer(rebasing: $0[..<count]))
      }
    }
    var after = stat()
    guard fstat(descriptor, &after) == 0,
      TryOmarchyExportFileIdentity(after) == export.bundleIdentity,
      total == export.receipt.bundleBytes
    else {
      throw TryOmarchyExportProblem.changedDuringVerification
    }
    guard SHA256Digest.prefixedHex(hasher.finalize()) == export.receipt.bundleDigest.rawValue else {
      throw TryOmarchyExportProblem.digestMismatch
    }
    return TryOmarchyExport(
      directory: export.directory,
      receipt: export.receipt,
      publishedAt: export.publishedAt,
      verified: true,
      bundleIdentity: export.bundleIdentity
    )
  }

  private func inspect(_ name: String) -> Result<TryOmarchyExport, TryOmarchyExportProblem> {
    let folder = directory.appendingPathComponent(name, isDirectory: true)
    var status = stat()
    guard TryOmarchyExportReceipt.isCanonicalUUID(name),
      lstat(folder.path, &status) == 0,
      status.st_mode & S_IFMT == S_IFDIR
    else {
      return .failure(.unexpectedEntry)
    }
    guard isPrivateDirectory(status) else {
      return .failure(.unsafeOwnership)
    }
    guard let contents = try? FileManager.default.contentsOfDirectory(atPath: folder.path) else {
      return .failure(.unreadable)
    }
    let expected: Set = [Self.bundleName, Self.receiptName]
    guard Set(contents).subtracting([Self.finderMetadataName]) == expected else {
      return .failure(.unexpectedContents)
    }

    let receiptData: Data
    let receiptStatus: stat
    switch readSmallFile(folder.appendingPathComponent(Self.receiptName)) {
    case .success(let read): (receiptData, receiptStatus) = read
    case .failure(let problem): return .failure(problem)
    }
    let receipt: TryOmarchyExportReceipt
    do {
      receipt = try TryOmarchyExportReceipt.decode(receiptData)
    } catch let error as TryOmarchyExportReceiptError {
      return .failure(.receipt(error))
    } catch {
      return .failure(.receipt(.invalidJSON))
    }
    guard receipt.exportID == name else {
      return .failure(.identityMismatch)
    }

    let bundlePath = folder.appendingPathComponent(Self.bundleName).path
    let descriptor = open(bundlePath, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
    guard descriptor >= 0 else {
      return .failure(errno == ELOOP ? .unsafeOwnership : .unreadable)
    }
    defer { close(descriptor) }
    var bundleStatus = stat()
    guard fstat(descriptor, &bundleStatus) == 0 else {
      return .failure(.unreadable)
    }
    guard isPrivateFile(bundleStatus) else {
      return .failure(.unsafeOwnership)
    }
    guard Int64(bundleStatus.st_size) == receipt.bundleBytes else {
      return .failure(.sizeMismatch)
    }
    var header = [UInt8](repeating: 0, count: Self.headerLimit)
    let count = header.withUnsafeMutableBytes { pread(descriptor, $0.baseAddress, $0.count, 0) }
    guard count > 0, Self.isScryptAgeHeader(header[..<count]) else {
      return .failure(.notAnAgeBundle)
    }
    return .success(
      TryOmarchyExport(
        directory: folder,
        receipt: receipt,
        publishedAt: Date(
          timeIntervalSince1970: TimeInterval(receiptStatus.st_mtimespec.tv_sec)
            + TimeInterval(receiptStatus.st_mtimespec.tv_nsec) / 1_000_000_000
        ),
        verified: false,
        bundleIdentity: TryOmarchyExportFileIdentity(bundleStatus)
      )
    )
  }

  /// Every open is non-blocking, so a named pipe planted in an export can't
  /// stall discovery before its type is checked; regular files read as usual.
  private func readSmallFile(_ url: URL) -> Result<(Data, stat), TryOmarchyExportProblem> {
    let descriptor = open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK)
    guard descriptor >= 0 else {
      return .failure(errno == ELOOP ? .unsafeOwnership : .unreadable)
    }
    defer { close(descriptor) }
    var status = stat()
    guard fstat(descriptor, &status) == 0 else {
      return .failure(.unreadable)
    }
    guard isPrivateFile(status) else {
      return .failure(.unsafeOwnership)
    }
    guard status.st_size <= TryOmarchyExportReceipt.maximumDocumentBytes else {
      return .failure(.receipt(.oversizedDocument))
    }
    let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
    guard let data = try? handle.readToEnd() ?? Data(),
      data.count <= TryOmarchyExportReceipt.maximumDocumentBytes
    else {
      return .failure(.unreadable)
    }
    return .success((data, status))
  }

  private func isPrivateDirectory(_ status: stat) -> Bool {
    status.st_mode & S_IFMT == S_IFDIR && status.st_uid == ownerID && status.st_mode & 0o022 == 0
  }

  private func isPrivateFile(_ status: stat) -> Bool {
    status.st_mode & S_IFMT == S_IFREG && status.st_uid == ownerID && status.st_mode & 0o022 == 0
  }

  /// An age v1 file with exactly one scrypt recipient at work factor 18:
  /// version line, `-> scrypt <salt> 18`, the wrapped key, then the MAC line.
  static func isScryptAgeHeader(_ bytes: ArraySlice<UInt8>) -> Bool {
    let lines = bytes.split(separator: 0x0A, maxSplits: 4, omittingEmptySubsequences: false)
    guard lines.count == 5 else {
      return false
    }
    func text(_ index: Int) -> String? {
      String(bytes: lines[index], encoding: .ascii)
    }
    func isBase64(_ value: Substring, count: Int) -> Bool {
      value.utf8.count == count
        && value.utf8.allSatisfy { byte in
          (0x41...0x5A).contains(byte) || (0x61...0x7A).contains(byte)
            || (0x30...0x39).contains(byte) || byte == 0x2B || byte == 0x2F
        }
    }
    guard text(0) == "age-encryption.org/v1",
      let stanza = text(1), stanza.hasPrefix("-> scrypt "), stanza.hasSuffix(" 18"),
      let body = text(2),
      let mac = text(3), mac.hasPrefix("--- ")
    else {
      return false
    }
    let salt = stanza.dropFirst("-> scrypt ".count).dropLast(" 18".count)
    return isBase64(salt, count: 22) && isBase64(Substring(body), count: 43)
      && isBase64(mac.dropFirst(4), count: 43)
  }
}
