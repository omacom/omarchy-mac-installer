#if os(macOS)
  import Darwin
  import Foundation

  /// The VM's outbox, `native-exports/<export id>/`, where Omarchy leaves a
  /// finished export that the launcher publishes to Try Omarchy's Exports
  /// folder when the VM stops. Only names are read: the guest writes the
  /// outbox, and exports come from Exports alone.
  public enum TrialExportOutbox {
    public static func hasExport(in vmRoot: URL) -> Bool {
      let outbox = vmRoot.appendingPathComponent("native-exports", isDirectory: true)
      guard let names = try? FileManager.default.contentsOfDirectory(atPath: outbox.path) else {
        return false
      }
      return names.contains { name in
        guard name.range(of: exportIDPattern, options: .regularExpression) != nil else {
          return false
        }
        var information = stat()
        return lstat(outbox.appendingPathComponent(name).path, &information) == 0
          && information.st_mode & S_IFMT == S_IFDIR
      }
    }

    static let exportIDPattern =
      "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
  }
#endif
