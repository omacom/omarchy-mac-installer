#if os(macOS)
  import Darwin
  import Foundation

  /// Deletes the virtual machine in the default folder, and nothing else. The
  /// folder moves to the Trash whole, so Finder's Put Back restores it.
  public enum TrialVMFolder {
    /// The default folder, under Application Support, that Try Omarchy uses too.
    public static let components = ["Try Omarchy", "VM", "v1"]

    public enum Refusal: Error, Equatable, LocalizedError {
      case notTheDefaultFolder
      case unsafeFolder
      case open
      case changed

      public var errorDescription: String? {
        switch self {
        case .notTheDefaultFolder:
          "Only the virtual machine in the default folder can be deleted here."
        case .unsafeFolder:
          "The virtual machine’s folder isn’t a plain folder, so it was left in place."
        case .open:
          "Omarchy is open in Try Omarchy or another copy of this app. Shut it down there first."
        case .changed:
          "The virtual machine’s folder changed while it was being deleted, so it was left in place."
        }
      }
    }

    public static func root(under base: URL) -> URL {
      components.reduce(base) { $0.appendingPathComponent($1, isDirectory: true) }
    }

    /// Opens each folder from `base` down to `shell` without following links,
    /// takes the launcher's lock on `shell/lock`, creating it as the launcher
    /// does, and keeps it until the folder is in the Trash. The path is checked
    /// against the opened folders after the lock and again right before the move.
    public static func trash(
      under base: URL,
      recycle: (URL) throws -> Void = {
        try FileManager.default.trashItem(at: $0, resultingItemURL: nil)
      }
    ) throws {
      guard base.isFileURL, base.path.hasPrefix("/"),
        !base.pathComponents.contains(where: { $0 == "." || $0 == ".." })
      else { throw Refusal.notTheDefaultFolder }
      let names = components + ["shell"]
      var descriptors: [Int32] = []
      defer { for descriptor in descriptors { close(descriptor) } }

      var parent = open(base.path, O_RDONLY | O_DIRECTORY | O_CLOEXEC)
      guard parent >= 0 else { throw Refusal.unsafeFolder }
      descriptors.append(parent)
      for name in names {
        parent = openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else { throw Refusal.unsafeFolder }
        descriptors.append(parent)
        try requireOwnDirectory(parent)
      }
      descriptors.append(try takeLock(in: parent))

      var folder = base
      var expected: [(URL, stat)] = []
      for (index, name) in names.enumerated() {
        folder.appendPathComponent(name, isDirectory: true)
        var information = stat()
        guard fstat(descriptors[index + 1], &information) == 0 else { throw Refusal.unsafeFolder }
        expected.append((folder, information))
      }
      try verify(expected)
      try recycle(expected[components.count - 1].0)
    }

    private static func requireOwnDirectory(_ descriptor: Int32) throws {
      var information = stat()
      guard fstat(descriptor, &information) == 0, information.st_mode & S_IFMT == S_IFDIR,
        information.st_uid == getuid()
      else { throw Refusal.unsafeFolder }
    }

    /// Each path still names the folder that was opened, and none is a link.
    private static func verify(_ expected: [(URL, stat)]) throws {
      for (url, opened) in expected {
        var information = stat()
        guard lstat(url.path, &information) == 0, information.st_mode & S_IFMT == S_IFDIR,
          information.st_dev == opened.st_dev, information.st_ino == opened.st_ino
        else { throw Refusal.changed }
      }
    }

    /// The launcher holds this lock while the virtual machine runs. Creating
    /// the file when it is missing means a launcher starting now conflicts too.
    private static func takeLock(in shell: Int32) throws -> Int32 {
      let descriptor = openat(
        shell, "lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
      guard descriptor >= 0 else { throw Refusal.unsafeFolder }
      var information = stat()
      var lock = flock()
      lock.l_type = Int16(F_WRLCK)
      lock.l_whence = Int16(SEEK_SET)
      guard fstat(descriptor, &information) == 0, information.st_mode & S_IFMT == S_IFREG,
        information.st_uid == getuid(), information.st_nlink == 1
      else {
        close(descriptor)
        throw Refusal.unsafeFolder
      }
      guard fcntl(descriptor, F_SETLK, &lock) == 0 else {
        close(descriptor)
        throw Refusal.open
      }
      return descriptor
    }
  }
#endif
