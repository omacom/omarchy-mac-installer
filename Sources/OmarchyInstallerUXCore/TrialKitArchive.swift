#if os(macOS)
  import Compression
  import Darwin
  import Foundation
  import zlib

  /// A downloaded `TryOmarchyKit.zip`, unpacked by this reader alone. The
  /// central directory must list only files and folders under the one kit
  /// folder, with relative names. Each entry's local header must agree with
  /// it, the entries must follow each other with nothing between them, and
  /// no entry may write more bytes than it declares. Files are written 0600,
  /// or 0700 when executable, folders 0700, with no extended attributes, so
  /// AppleDouble `._` entries are skipped.
  public enum TrialKitArchive {
    public static let maximumEntries = 20_000
    public static let maximumUnpackedBytes: UInt64 = 4 << 30
    public static let maximumNameBytes = 1_024
    static let maximumDirectoryBytes: UInt64 = 32 << 20

    public enum Kind: Equatable, Sendable {
      case file
      case directory
    }

    public struct Entry: Equatable, Sendable {
      public let name: String
      public let kind: Kind
      public let unpackedBytes: UInt64
      let packedBytes: UInt64
      let method: UInt16
      let flags: UInt16
      let crc: UInt32
      let executable: Bool
      let headerOffset: UInt64
      let nameBytes: [UInt8]
    }

    public enum Refusal: Error, Equatable, Sendable {
      case unreadable
      case unsupported
      case tooLarge
      case unsafeEntry(String)
      case disagrees(String)
      case extractionFailed
    }

    struct Directory {
      let entries: [Entry]
      let offset: UInt64
    }

    /// The archive's entries, or a refusal for anything unsafe or unknown.
    public static func entries(of archive: URL) throws -> [Entry] {
      try directory(of: archive).entries
    }

    static func directory(of archive: URL) throws -> Directory {
      guard let handle = try? FileHandle(forReadingFrom: archive) else { throw Refusal.unreadable }
      defer { try? handle.close() }
      guard let size = try? handle.seekToEnd(), size >= 22 else { throw Refusal.unreadable }
      let tailSize = min(size, 65_557)
      try handle.seek(toOffset: size - tailSize)
      guard let tail = try handle.read(upToCount: Int(tailSize)), tail.count == Int(tailSize) else {
        throw Refusal.unreadable
      }
      let tailBytes = [UInt8](tail)
      guard let end = endOfCentralDirectory(in: tailBytes) else { throw Refusal.unreadable }
      let record = Reader(bytes: tailBytes, offset: end)
      let entryCount = record.u16(10)
      let directorySize = UInt64(record.u32(12))
      let directoryOffset = UInt64(record.u32(16))
      if end >= 20, Reader(bytes: tailBytes, offset: end - 20).u32(0) == 0x0706_4b50 {
        throw Refusal.unsupported
      }
      guard record.u16(4) == 0, record.u16(6) == 0, record.u16(8) == entryCount,
        entryCount != 0xFFFF, directorySize != 0xFFFF_FFFF, directoryOffset != 0xFFFF_FFFF
      else { throw Refusal.unsupported }
      guard Int(entryCount) <= maximumEntries, directorySize <= maximumDirectoryBytes else {
        throw Refusal.tooLarge
      }
      guard directoryOffset + directorySize == size - tailSize + UInt64(end) else {
        throw Refusal.unreadable
      }
      try handle.seek(toOffset: directoryOffset)
      guard let bytes = try handle.read(upToCount: Int(directorySize)),
        bytes.count == Int(directorySize)
      else { throw Refusal.unreadable }
      return Directory(
        entries: try parse(directory: [UInt8](bytes), count: Int(entryCount)),
        offset: directoryOffset)
    }

    static func parse(directory: [UInt8], count: Int) throws -> [Entry] {
      var entries: [Entry] = []
      var offset = 0
      var total: UInt64 = 0
      for _ in 0..<count {
        guard offset + 46 <= directory.count else { throw Refusal.unreadable }
        let header = Reader(bytes: directory, offset: offset)
        guard header.u32(0) == 0x0201_4b50 else { throw Refusal.unreadable }
        let nameLength = Int(header.u16(28))
        let nameStart = offset + 46
        let next = nameStart + nameLength + Int(header.u16(30)) + Int(header.u16(32))
        guard next <= directory.count else { throw Refusal.unreadable }
        let nameBytes = Array(directory[nameStart..<nameStart + nameLength])
        guard nameLength > 0, nameLength <= maximumNameBytes,
          let name = String(validating: nameBytes, as: UTF8.self)
        else { throw Refusal.unsafeEntry("an entry has no readable name") }
        let flags = header.u16(8)
        let method = header.u16(10)
        let packed = header.u32(20)
        let unpacked = header.u32(24)
        guard flags & 0x1 == 0, method == 0 || method == 8, packed != 0xFFFF_FFFF,
          unpacked != 0xFFFF_FFFF, header.u16(34) == 0
        else { throw Refusal.unsafeEntry(name) }
        let (kind, executable) = try kind(
          of: name, host: header.u16(4) >> 8, external: header.u32(38))
        guard kind == .file || (packed == 0 && unpacked == 0) else {
          throw Refusal.unsafeEntry(name)
        }
        total += UInt64(unpacked)
        guard total <= maximumUnpackedBytes else { throw Refusal.tooLarge }
        entries.append(
          Entry(
            name: name, kind: kind, unpackedBytes: UInt64(unpacked), packedBytes: UInt64(packed),
            method: method, flags: flags, crc: header.u32(16), executable: executable,
            headerOffset: UInt64(header.u32(42)), nameBytes: nameBytes))
        offset = next
      }
      guard offset == directory.count else { throw Refusal.unreadable }
      try checkNames(entries)
      return entries
    }

    private static func kind(of name: String, host: UInt16, external: UInt32) throws -> (Kind, Bool)
    {
      let isFolderName = name.hasSuffix("/")
      guard host == 3 else { return (isFolderName ? .directory : .file, false) }
      let mode = mode_t(external >> 16)
      guard mode & mode_t(S_ISUID | S_ISGID | S_ISVTX) == 0, mode & 0o022 == 0 else {
        throw Refusal.unsafeEntry(name)
      }
      let executable = mode & 0o100 != 0
      switch mode & S_IFMT {
      case S_IFDIR where isFolderName: return (.directory, false)
      case S_IFREG where !isFolderName: return (.file, executable)
      case 0: return (isFolderName ? .directory : .file, executable)
      default: throw Refusal.unsafeEntry(name)
      }
    }

    /// Every name sits under the kit's folder, but for the folder's own
    /// AppleDouble entry, with no empty, `.` or `..` parts and no
    /// backslashes, and none repeats, compared the way APFS compares names.
    static func checkNames(_ entries: [Entry]) throws {
      var seen: Set<String> = []
      let top = TrialKitLayout.archiveFolder
      for entry in entries {
        var name = Substring(entry.name)
        if entry.kind == .directory { name = name.dropLast() }
        let parts = name.split(separator: "/", omittingEmptySubsequences: false)
        let topDouble = parts.count == 1 && name == "._" + top
        guard !name.isEmpty, !name.contains("\\"), !name.contains("\0"), parts.count <= 64,
          parts.first.map(String.init) == top || topDouble,
          parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." })
        else { throw Refusal.unsafeEntry(entry.name) }
        guard seen.insert(folded(String(name))).inserted else {
          throw Refusal.unsafeEntry(entry.name)
        }
      }
    }

    private static func folded(_ name: String) -> String {
      name.precomposedStringWithCanonicalMapping.lowercased()
    }

    /// Unpacks the archive into `destination`, which must not exist. On a
    /// refusal nothing unpacked is left.
    public static func unpack(_ archive: URL, into destination: URL) throws {
      let directory = try directory(of: archive)
      guard mkdir(destination.path, 0o700) == 0 else { throw Refusal.extractionFailed }
      do {
        guard let handle = try? FileHandle(forReadingFrom: archive) else {
          throw Refusal.unreadable
        }
        defer { try? handle.close() }
        var expected: UInt64 = 0
        for entry in directory.entries {
          guard entry.headerOffset == expected else { throw Refusal.disagrees(entry.name) }
          expected = try write(entry, from: handle, under: destination)
        }
        guard expected == directory.offset else { throw Refusal.disagrees("the archive's end") }
        try checkUnpacked(destination)
      } catch {
        try? FileManager.default.removeItem(at: destination)
        throw error
      }
    }

    /// Writes one entry, checked against its local header, and returns where
    /// the next one must start.
    private static func write(_ entry: Entry, from handle: FileHandle, under root: URL) throws
      -> UInt64
    {
      try handle.seek(toOffset: entry.headerOffset)
      guard let raw = try handle.read(upToCount: 30), raw.count == 30 else {
        throw Refusal.disagrees(entry.name)
      }
      let local = Reader(bytes: [UInt8](raw), offset: 0)
      let nameLength = Int(local.u16(26))
      let extraLength = UInt64(local.u16(28))
      let flags = local.u16(6)
      guard local.u32(0) == 0x0403_4b50, nameLength == entry.nameBytes.count,
        local.u16(8) == entry.method, flags & 0x8 == entry.flags & 0x8, flags & 0x1 == 0,
        let name = try handle.read(upToCount: nameLength), [UInt8](name) == entry.nameBytes
      else { throw Refusal.disagrees(entry.name) }
      if flags & 0x8 == 0 {
        guard local.u32(14) == entry.crc, UInt64(local.u32(18)) == entry.packedBytes,
          UInt64(local.u32(22)) == entry.unpackedBytes
        else { throw Refusal.disagrees(entry.name) }
      }
      let dataStart = entry.headerOffset + 30 + UInt64(nameLength) + extraLength
      try handle.seek(toOffset: dataStart)
      let path = root.path + "/" + entry.name
      let skipped = entry.name.split(separator: "/").last?.hasPrefix("._") == true
      if entry.kind == .directory {
        try makeFolders(path, under: root.path)
      } else if !skipped {
        try makeFolders((path as NSString).deletingLastPathComponent, under: root.path)
        try writeFile(entry, from: handle, to: path)
      }
      var end = dataStart + entry.packedBytes
      if flags & 0x8 != 0 {
        try handle.seek(toOffset: end)
        guard let raw = try handle.read(upToCount: 16), raw.count >= 12 else {
          throw Refusal.disagrees(entry.name)
        }
        let descriptor = Reader(bytes: [UInt8](raw), offset: 0)
        let signed = raw.count == 16 && descriptor.u32(0) == 0x0807_4b50
        let fields = signed ? 4 : 0
        guard descriptor.u32(fields) == entry.crc,
          UInt64(descriptor.u32(fields + 4)) == entry.packedBytes,
          UInt64(descriptor.u32(fields + 8)) == entry.unpackedBytes
        else { throw Refusal.disagrees(entry.name) }
        end += UInt64(12 + fields)
      }
      return end
    }

    private static func makeFolders(_ path: String, under root: String) throws {
      guard path.hasPrefix(root + "/") || path == root else { throw Refusal.extractionFailed }
      var current = root
      for part in path.dropFirst(root.count).split(separator: "/") {
        current += "/" + part
        var information = stat()
        if lstat(current, &information) == 0 {
          guard information.st_mode & S_IFMT == S_IFDIR else { throw Refusal.extractionFailed }
        } else if mkdir(current, 0o700) != 0 {
          throw Refusal.extractionFailed
        }
      }
    }

    private static func writeFile(_ entry: Entry, from handle: FileHandle, to path: String) throws {
      let descriptor = open(
        path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, entry.executable ? 0o700 : 0o600)
      guard descriptor >= 0 else { throw Refusal.unsafeEntry(entry.name) }
      defer { close(descriptor) }
      var written: UInt64 = 0
      var crc = crc32(0, nil, 0)
      let sink: (UnsafeRawBufferPointer) throws -> Void = { chunk in
        written += UInt64(chunk.count)
        guard written <= entry.unpackedBytes else { throw Refusal.tooLarge }
        crc = crc32(crc, chunk.bindMemory(to: Bytef.self).baseAddress, uInt(chunk.count))
        var offset = 0
        while offset < chunk.count {
          let count = Darwin.write(descriptor, chunk.baseAddress! + offset, chunk.count - offset)
          guard count > 0 else { throw Refusal.extractionFailed }
          offset += count
        }
      }
      if entry.method == 0 {
        guard entry.packedBytes == entry.unpackedBytes else { throw Refusal.disagrees(entry.name) }
        var remaining = entry.packedBytes
        while remaining > 0 {
          guard let chunk = try handle.read(upToCount: Int(min(remaining, 1 << 20))), !chunk.isEmpty
          else { throw Refusal.disagrees(entry.name) }
          remaining -= UInt64(chunk.count)
          try chunk.withUnsafeBytes(sink)
        }
      } else {
        try inflate(entry, from: handle, into: sink)
      }
      guard written == entry.unpackedBytes, crc == UInt(entry.crc) else {
        throw Refusal.disagrees(entry.name)
      }
    }

    /// Raw DEFLATE through the Compression framework, fed no more than the
    /// entry's packed bytes and stopped as soon as it writes too much.
    private static func inflate(
      _ entry: Entry, from handle: FileHandle, into sink: (UnsafeRawBufferPointer) throws -> Void
    ) throws {
      let capacity = 1 << 16
      let stream = UnsafeMutablePointer<compression_stream>.allocate(capacity: 1)
      let input = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
      let output = UnsafeMutablePointer<UInt8>.allocate(capacity: capacity)
      defer {
        stream.deallocate()
        input.deallocate()
        output.deallocate()
      }
      guard
        compression_stream_init(stream, COMPRESSION_STREAM_DECODE, COMPRESSION_ZLIB)
          != COMPRESSION_STATUS_ERROR
      else { throw Refusal.extractionFailed }
      defer { compression_stream_destroy(stream) }
      var remaining = entry.packedBytes
      stream.pointee.src_size = 0
      while true {
        if stream.pointee.src_size == 0, remaining > 0 {
          guard let chunk = try handle.read(upToCount: Int(min(remaining, UInt64(capacity)))),
            !chunk.isEmpty
          else { throw Refusal.disagrees(entry.name) }
          chunk.copyBytes(to: input, count: chunk.count)
          remaining -= UInt64(chunk.count)
          stream.pointee.src_ptr = UnsafePointer(input)
          stream.pointee.src_size = chunk.count
        }
        stream.pointee.dst_ptr = output
        stream.pointee.dst_size = capacity
        let finish = remaining == 0 ? Int32(COMPRESSION_STREAM_FINALIZE.rawValue) : 0
        let status = compression_stream_process(stream, finish)
        guard status != COMPRESSION_STATUS_ERROR else { throw Refusal.disagrees(entry.name) }
        let produced = capacity - stream.pointee.dst_size
        if produced > 0 { try sink(UnsafeRawBufferPointer(start: output, count: produced)) }
        if status == COMPRESSION_STATUS_END {
          guard remaining == 0, stream.pointee.src_size == 0 else {
            throw Refusal.disagrees(entry.name)
          }
          return
        }
        if produced == 0, stream.pointee.src_size == 0, remaining == 0 {
          throw Refusal.disagrees(entry.name)
        }
      }
    }

    /// The unpacked folder holds only plain files with one link each and
    /// folders, and nothing in it carries an extended attribute this reader
    /// did not write.
    public static func checkUnpacked(_ root: URL) throws {
      var total: UInt64 = 0
      var information = stat()
      guard lstat(root.path, &information) == 0, information.st_mode & S_IFMT == S_IFDIR else {
        throw Refusal.extractionFailed
      }
      var pending = [root.path]
      while let folder = pending.popLast() {
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: folder) else {
          throw Refusal.extractionFailed
        }
        for name in names {
          let path = folder + "/" + name
          let shown = String(path.dropFirst(root.path.count + 1))
          guard lstat(path, &information) == 0 else { throw Refusal.extractionFailed }
          guard information.st_mode & mode_t(S_ISUID | S_ISGID) == 0 else {
            throw Refusal.unsafeEntry(shown)
          }
          switch information.st_mode & S_IFMT {
          case S_IFDIR:
            pending.append(path)
          case S_IFREG:
            guard information.st_nlink == 1 else { throw Refusal.unsafeEntry(shown) }
            total += UInt64(information.st_size)
            guard total <= maximumUnpackedBytes else { throw Refusal.tooLarge }
          default:
            throw Refusal.unsafeEntry(shown)
          }
          if removexattr(path, "com.apple.quarantine", XATTR_NOFOLLOW) != 0, errno != ENOATTR {
            throw Refusal.extractionFailed
          }
        }
      }
    }

    private static func endOfCentralDirectory(in bytes: [UInt8]) -> Int? {
      var index = bytes.count - 22
      while index >= 0 {
        let reader = Reader(bytes: bytes, offset: index)
        if reader.u32(0) == 0x0605_4b50, index + 22 + Int(reader.u16(20)) == bytes.count {
          return index
        }
        index -= 1
      }
      return nil
    }

    private struct Reader {
      let bytes: [UInt8]
      let offset: Int

      func u16(_ at: Int) -> UInt16 {
        UInt16(bytes[offset + at]) | UInt16(bytes[offset + at + 1]) << 8
      }

      func u32(_ at: Int) -> UInt32 {
        UInt32(u16(at)) | UInt32(u16(at + 2)) << 16
      }
    }
  }
#endif
