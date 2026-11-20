#if os(macOS)
  import CryptoKit
  import Darwin
  import Foundation

  public struct InterruptedInstallResumeTicket: Codable, Equatable, Sendable {
    public let id: UUID
    public let deviceIdentifier: String
    public let allocationBytes: UInt64
    public let metadataDigest: String
    public let payloadDigest: String

    public init(
      id: UUID, deviceIdentifier: String, allocationBytes: UInt64,
      metadataDigest: String, payloadDigest: String
    ) {
      self.id = id
      self.deviceIdentifier = deviceIdentifier
      self.allocationBytes = allocationBytes
      self.metadataDigest = metadataDigest
      self.payloadDigest = payloadDigest
    }
  }

  public struct InterruptedInstallResumeReply: Codable, Equatable, Sendable {
    public let ticket: InterruptedInstallResumeTicket?
    public let completed: Bool
    public let automaticResumeAvailable: Bool
    public let message: String

    init(
      ticket: InterruptedInstallResumeTicket? = nil, completed: Bool = false,
      automaticResumeAvailable: Bool = false, message: String
    ) {
      self.ticket = ticket
      self.completed = completed
      self.automaticResumeAvailable = automaticResumeAvailable
      self.message = message
    }
  }

  struct InterruptedResumeFailure: Error, LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
  }

  struct SavedInterruptedInstall: Sendable {
    static let originalEngineVersion = "v0.9.2-omarchy.20261022.31425f17fb9b"
    static let originalEngineDigest =
      "sha256:5fea6b561e6fb82260f573bae099c70ba2e9998df56d38cf285987432320d4b5"
    static let boundCheckpoint = "interrupted-stub-and-esp-bound"
    let journal: URL
    let journalDigest: String
    let plan: EnginePlanMessage
    let engineCompleted: Bool
    let ticket: InterruptedInstallResumeTicket

    static func discover(in directory: URL) throws -> SavedInterruptedInstall? {
      let journals = directory.appendingPathComponent("execution-journals", isDirectory: true)
      var details = stat()
      if lstat(journals.path, &details) != 0 && errno == ENOENT { return nil }
      try requirePrivateDirectory(directory)
      try requirePrivateDirectory(journals)
      let entries = try FileManager.default.contentsOfDirectory(
        at: journals, includingPropertiesForKeys: nil)
      guard entries.count <= 512 else {
        throw InterruptedResumeFailure("Too many installation records. The records need review.")
      }
      let names = entries.filter {
        $0.lastPathComponent.range(of: "\\A[0-9a-f]{64}\\.jsonl\\z", options: .regularExpression)
          != nil
      }
      guard names.count <= 128 else {
        throw InterruptedResumeFailure("Too many installation records. The records need review.")
      }
      var candidates = [SavedInterruptedInstall]()
      for name in names {
        let data = try privateData(
          name, maximum: Int(PinnedAsahiEngineExecutor.maximumTranscriptBytes))
        if let record = try parse(data, journal: name) { candidates.append(record) }
      }
      guard candidates.count <= 1 else {
        throw InterruptedResumeFailure(
          "More than one interrupted installation was found. Nothing was changed.")
      }
      return candidates.first
    }

    static func parse(
      _ data: Data, journal: URL,
      readEvidence: (URL) throws -> Data = {
        try privateData($0, maximum: 1 << 20)
      }
    ) throws -> SavedInterruptedInstall? {
      let records = try EngineTranscriptDecoder().decode(data)
      guard records.count >= 6, case .inspection(let inspection) = records[0].message,
        inspection.support == "supported", inspection.deviceIdentifier != "apple,j614s",
        case .plan(let plan) = records[2].message,
        plan.engineVersion == originalEngineVersion, plan.engineDigest == originalEngineDigest,
        ["resize", "free"].contains(plan.candidateKind)
      else { return nil }
      var events = Set<String>()
      var checkpoints = Set<String>()
      var reboundCheckpoints = Set<String>()
      var completed = false
      for record in records.dropFirst(3) {
        switch record.message {
        case .event(let event):
          guard events.insert(event.name).inserted else { return nil }
        case .checkpoint(let checkpoint):
          let evidence = try readEvidence(
            URL(fileURLWithPath: journal.path + "." + checkpoint.identifier + ".evidence"))
          guard digest(evidence) == checkpoint.evidenceDigest else {
            throw InterruptedResumeFailure(
              "An installation checkpoint changed. Nothing was changed.")
          }
          if checkpoint.identifier.range(
            of: "\\Ainterrupted-stub-and-esp-rebound-[0-9a-f]{64}\\z", options: .regularExpression)
            != nil
          {
            let suffix = String(checkpoint.identifier.suffix(64))
            guard checkpoint.evidenceDigest == "sha256:" + suffix else { return nil }
            reboundCheckpoints.insert(checkpoint.identifier)
          }
          checkpoints.insert(checkpoint.identifier)
        case .completion(let completion):
          guard completion.outcome == "awaiting_recovery" else { return nil }
          completed = true
        default: return nil
        }
      }
      let allowedEvents: Set<String> = [
        "apfs_preparation_started", "stub_and_esp_started", "recovery_handoff_started",
      ]
      let allowedCheckpoints: Set<String> = [
        "apfs-target-prepared", boundCheckpoint, "stub-and-esp-installed",
        "interrupted-stub-and-esp-rebound", "interrupted-stub-and-esp-rebound-2",
        "recovery-handoff-prepared",
      ]
      guard events.isSubset(of: allowedEvents),
        Set(["apfs_preparation_started", "stub_and_esp_started"]).isSubset(of: events),
        checkpoints.isSubset(of: allowedCheckpoints.union(reboundCheckpoints)),
        checkpoints.contains("apfs-target-prepared"),
        reboundCheckpoints.isEmpty || checkpoints.contains(boundCheckpoint),
        !checkpoints.contains("interrupted-stub-and-esp-rebound")
          || checkpoints.contains(boundCheckpoint),
        !checkpoints.contains("interrupted-stub-and-esp-rebound-2")
          || checkpoints.contains(boundCheckpoint),
        !checkpoints.contains("stub-and-esp-installed") || checkpoints.contains(boundCheckpoint),
        !events.contains("recovery_handoff_started")
          || checkpoints.contains("stub-and-esp-installed"),
        !completed
          || (checkpoints.contains(boundCheckpoint)
            && checkpoints.contains("recovery-handoff-prepared"))
      else { return nil }
      return SavedInterruptedInstall(
        journal: journal, journalDigest: digest(data), plan: plan,
        engineCompleted: completed,
        ticket: InterruptedInstallResumeTicket(
          id: UUID(), deviceIdentifier: plan.deviceIdentifier, allocationBytes: plan.lengthBytes,
          metadataDigest: plan.metadataDigest, payloadDigest: plan.payloadDigest))
    }

    static func digest(_ data: Data) -> String {
      "sha256:" + SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Called after the original metadata has been privately snapshotted and
    /// matched to the journal. The engine checks this basename against it.
    static func payloadSnapshotURL(in inputs: URL) throws -> URL {
      let data = try privateData(
        inputs.appendingPathComponent("installer_data.json"), maximum: 1 << 20)
      guard let metadata = try JSONSerialization.jsonObject(with: data) as? [String: Any],
        let entries = metadata["os_list"] as? [[String: Any]]
      else { throw InterruptedResumeFailure("The original installation metadata is invalid.") }
      let targets = entries.filter { $0["omarchy_target"] as? String == "apple-silicon-full-os" }
      guard targets.count == 1, let name = targets[0]["package"] as? String,
        name.range(of: "\\A[A-Za-z0-9][A-Za-z0-9._-]{0,250}\\.zip\\z", options: .regularExpression)
          != nil
      else { throw InterruptedResumeFailure("The original package filename is invalid.") }
      return inputs.appendingPathComponent(name, isDirectory: false)
    }

    static func requirePrivateDirectory(_ path: URL) throws {
      var details = stat()
      guard lstat(path.path, &details) == 0, (details.st_mode & S_IFMT) == S_IFDIR,
        details.st_uid == geteuid(), details.st_mode & 0o077 == 0
      else { throw InterruptedResumeFailure("The installation record directory is unsafe.") }
    }

    static func privateData(_ path: URL, maximum: Int) throws -> Data {
      let descriptor = Darwin.open(path.path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
      guard descriptor >= 0 else {
        throw InterruptedResumeFailure("An installation record could not be read.")
      }
      defer { Darwin.close(descriptor) }
      var details = stat()
      guard fstat(descriptor, &details) == 0, (details.st_mode & S_IFMT) == S_IFREG,
        details.st_uid == geteuid(), details.st_mode & 0o077 == 0,
        details.st_size > 0, details.st_size <= maximum
      else { throw InterruptedResumeFailure("An installation record is unsafe.") }
      let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
      let bytes = try handle.read(upToCount: maximum + 1) ?? Data()
      guard bytes.count == details.st_size else {
        throw InterruptedResumeFailure("An installation record changed while reading it.")
      }
      return bytes
    }

    static func snapshot(
      _ source: FileHandle, to target: URL, maximum: Int64, expectedDigest: String? = nil
    ) throws {
      var details = stat()
      guard fstat(source.fileDescriptor, &details) == 0, (details.st_mode & S_IFMT) == S_IFREG,
        details.st_size > 0, details.st_size <= maximum
      else { throw InterruptedResumeFailure("The selected installation file is invalid.") }
      let descriptor = Darwin.open(
        target.path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0o600)
      guard descriptor >= 0 else {
        throw InterruptedResumeFailure("Could not snapshot installation files.")
      }
      let output = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
      defer { try? output.close() }
      try source.seek(toOffset: 0)
      var count: Int64 = 0
      var hash = SHA256()
      while let block = try source.read(upToCount: 4 << 20), !block.isEmpty {
        count += Int64(block.count)
        guard count <= details.st_size else {
          throw InterruptedResumeFailure("The selected file grew while reading it.")
        }
        hash.update(data: block)
        try output.write(contentsOf: block)
      }
      let actual = "sha256:" + hash.finalize().map { String(format: "%02x", $0) }.joined()
      guard count == details.st_size, expectedDigest == nil || actual == expectedDigest else {
        throw InterruptedResumeFailure(
          "The selected files do not match the original installation download.")
      }
      try output.synchronize()
    }
  }
#endif
