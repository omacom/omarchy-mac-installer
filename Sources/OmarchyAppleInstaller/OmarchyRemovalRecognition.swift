#if os(macOS)
  import Foundation

  /// What removal found between macOS and Apple Recovery. Recognition is by
  /// structure; names are only reported. Files on the installation then have
  /// to confirm it (RemovalEvidence) before a plan exists.
  enum RemovalLayout {
    case freeSpace(macOS: RemovalPartition, target: UInt64)
    case installation(RemovalInstallation, macOS: RemovalPartition, target: UInt64)

    static let minimumFreeSpace: UInt64 = 1 << 30
    static let memberGap: UInt64 = 16 << 20
    // asahi-installer's STUB_SIZE is 2,499,805,184 bytes; a stub on macOS 26
    // firmware (the MacBook Neo) is 5,999,951,872.
    static let stubSizes: [ClosedRange<UInt64>] = [
      2_300_000_000...2_700_000_000, 5_800_000_000...6_200_000_000,
    ]
    static let maximumESP: UInt64 = 1 << 30

    static func recognize(_ snapshot: RemovalSnapshot) throws -> RemovalLayout {
      let parts = snapshot.partitions.sorted { $0.offset < $1.offset }
      guard !parts.isEmpty, parts.count <= 32,
        Set(parts.map(\.uuid)).count == parts.count,
        parts.allSatisfy({ $0.size > 0 && !$0.offset.addingReportingOverflow($0.size).overflow }),
        zip(parts, parts.dropFirst()).allSatisfy({ $0.end <= $1.offset }),
        parts[parts.count - 1].end <= snapshot.diskSize
      else {
        throw RemovalFailure(message: RemovalText.inconsistent(parts, snapshot))
      }
      guard parts.count >= 3, parts[0].type == "Apple_APFS_ISC",
        parts[parts.count - 1].type == "Apple_APFS_Recovery",
        parts[1].type == "Apple_APFS", parts[1].uuid == snapshot.macOSStoreUUID,
        snapshot.containers.filter({
          $0.storeUUID == parts[1].uuid && $0.uuid == snapshot.macOSContainerUUID
        }).count == 1
      else {
        throw RemovalFailure(message: RemovalText.unexpectedFrame(parts, snapshot))
      }
      let macOS = parts[1]
      let recovery = parts[parts.count - 1]
      let target = recovery.offset - macOS.offset
      let middle = Array(parts[2..<(parts.count - 1)])
      if middle.isEmpty {
        let gap = recovery.offset - macOS.end
        guard gap >= minimumFreeSpace else {
          throw RemovalFailure(message: RemovalText.nothingFound(gap: gap))
        }
        return .freeSpace(macOS: macOS, target: target)
      }

      let stranded = strandedFreeSpace(middle: middle, recovery: recovery)
      func refuse(_ finding: String) -> RemovalFailure {
        RemovalFailure(message: RemovalText.refusal(finding, stranded: stranded, snapshot))
      }
      let kinds = middle.map { classify($0, snapshot) }
      let others = zip(middle, kinds).filter { $0.1 == .otherSystem }.map(\.0)
      if !others.isEmpty { throw refuse(RemovalText.otherSystem(others, snapshot)) }
      let foreign = zip(middle, kinds).filter { $0.1 == .foreign }.map(\.0)
      if !foreign.isEmpty { throw refuse(RemovalText.foreign(foreign, snapshot)) }
      let stubs = zip(middle, kinds).filter { $0.1 == .stub }.map(\.0)
      if stubs.count > 1 { throw refuse(RemovalText.several(stubs, snapshot)) }
      if stubs.isEmpty { throw refuse(RemovalText.partial(middle, snapshot)) }
      guard middle.count >= 2, middle.count <= 4, kinds[0] == .stub, kinds[1] == .esp,
        kinds.dropFirst(2).allSatisfy({ $0 == .linux }),
        middle[1].size <= maximumESP,
        zip(middle, middle.dropFirst()).allSatisfy({ $1.offset - $0.end < memberGap })
      else { throw refuse(RemovalText.shape(middle, snapshot)) }
      let stub = middle[0]
      let container = snapshot.containers.first { $0.storeUUID == stub.uuid }!
      let system = container.volumes.first { $0.roles == ["System"] }!
      return .installation(
        RemovalInstallation(
          name: system.name, stub: stub, system: system, esp: middle[1],
          linux: Array(middle.dropFirst(2))),
        macOS: macOS, target: target)
    }

    enum Kind: Equatable { case stub, esp, linux, otherSystem, foreign }

    static func classify(_ part: RemovalPartition, _ snapshot: RemovalSnapshot) -> Kind {
      switch part.type {
      case "EFI": return .esp
      case "Linux Filesystem": return .linux
      case "Apple_APFS":
        let containers = snapshot.containers.filter { $0.storeUUID == part.uuid }
        guard containers.count == 1 else { return .foreign }
        let volumes = containers[0].volumes
        let systems = volumes.filter { $0.roles == ["System"] }
        let data = volumes.filter { $0.roles == ["Data"] }
        guard !systems.isEmpty, !data.isEmpty else { return .foreign }
        guard stubSizes.contains(where: { $0.contains(part.size) }), volumes.count == 4,
          systems.count == 1,
          data.count == 1, let group = systems[0].group, data[0].group == group,
          volumes.filter({ $0.roles == ["Preboot"] }).count == 1,
          volumes.filter({ $0.roles == ["Recovery"] }).count == 1
        else { return .otherSystem }
        return .stub
      default: return .foreign
      }
    }

    /// Unallocated space ≥ 1 GiB that sits behind a partition, so macOS can't grow into it.
    static func strandedFreeSpace(middle: [RemovalPartition], recovery: RemovalPartition)
      -> (bytes: UInt64, behind: [RemovalPartition])?
    {
      var total: UInt64 = 0
      let edges = middle + [recovery]
      for (index, part) in middle.enumerated() {
        total += edges[index + 1].offset - part.end
      }
      return total >= minimumFreeSpace ? (total, middle) : nil
    }
  }

  struct RemovalCreatedPartition: Codable, Equatable, Hashable, Sendable {
    let uuid: String
    let type: String
  }

  enum RemovalInstallerRecord: Codable, Equatable, Sendable {
    case missing
    case unreadable
    case partitions([RemovalCreatedPartition])
  }

  /// Raw files read from the installation's EFI partition and startup container.
  struct RemovalInstallFiles: Sendable {
    var espBootObject: Data?
    var stubInfo: Data?
    var installerLog: Data?
    var stubHasLibrary: Bool
    var stubBootObject: Data?
    /// The helper's own records of installations that stopped while installing
    /// the startup container and ESP, before asahi/ on the ESP was written.
    var interruptedInstalls: [RemovalInterruptedInstall] = []
  }

  /// One partition as the engine recorded it right after creating it.
  struct RemovalRecordedPartition: Codable, Equatable, Hashable, Sendable {
    let uuid: String
    let offset: UInt64
    let size: UInt64
  }

  /// One root-owned install journal that created a startup container and then
  /// stopped: `stub_and_esp_started` without its checkpoint, and no completion.
  /// It names the startup container exactly and the extent the plan approved;
  /// engines from 2026-11-06 also leave a receipt naming every partition.
  struct RemovalInterruptedInstall: Codable, Equatable, Hashable, Sendable {
    let journal: SHA256Digest
    let targetEvidence: SHA256Digest
    let stubUUID: String
    let stubOffset: UInt64
    let stubSize: UInt64
    let planOffset: UInt64
    let planLength: UInt64
    let engineVersion: String
    /// The startup container, EFI, boot and root, in that order, from the
    /// journal's partitions receipt; nil when the engine wrote none.
    let partitions: [RemovalRecordedPartition]?
    let receipt: SHA256Digest?

    /// Engines named `…omarchy.YYYYMMDD.<hash>` from this date record every
    /// partition; only journals of earlier engines may fall back to the extent.
    static let firstReceiptEngineDate = "20261106"

    var predatesPartitionReceipts: Bool {
      guard
        let match = engineVersion.range(
          of: #"-omarchy\.[0-9]{8}\.[0-9a-f]{12}\z"#, options: .regularExpression)
      else { return true }
      let date = String(engineVersion[match].dropFirst("-omarchy.".count).prefix(8))
      return date < Self.firstReceiptEngineDate
    }

    /// nil unless `transcript` is a well-formed journal in exactly that state,
    /// `target` is its `apfs-target-prepared` checkpoint evidence and
    /// `receipt`, when there is one, names its partitions consistently.
    static func parse(transcript: Data, target: Data, receipt: Data? = nil)
      -> RemovalInterruptedInstall?
    {
      guard transcript.last == 10 else { return nil }
      var plan: (digest: String, offset: UInt64, length: UInt64, engine: String)?
      var events = Set<String>()
      var checkpoints = [String: String]()
      let lines = transcript.split(separator: 10, omittingEmptySubsequences: false).dropLast()
      for (index, line) in lines.enumerated() {
        guard
          let record = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any],
          Set(record.keys) == ["schema_version", "sequence", "type", "payload"],
          uint64(record["schema_version"]) == 1,
          uint64(record["sequence"]) == UInt64(index + 1),
          let type = record["type"] as? String,
          let payload = record["payload"] as? [String: Any]
        else { return nil }
        switch (index, type) {
        case (0, "inspection"), (1, "inventory"):
          continue
        case (2, "plan"):
          // The engine writes the plan digest as bare hex, unlike evidence digests.
          guard let digest = payload["plan_digest"] as? String,
            SHA256Digest(hexadecimal: digest) != nil,
            let offset = uint64(payload["offset_bytes"]),
            let length = uint64(payload["length_bytes"]), length > 0,
            !offset.addingReportingOverflow(length).overflow,
            let engine = payload["engine_version"] as? String, !engine.isEmpty
          else { return nil }
          plan = (digest, offset, length, engine)
        case (3..., "event"):
          guard let plan, Set(payload.keys) == ["plan_digest", "name"],
            payload["plan_digest"] as? String == plan.digest,
            let name = payload["name"] as? String, events.insert(name).inserted
          else { return nil }
        case (3..., "checkpoint"):
          guard let plan,
            Set(payload.keys) == ["plan_digest", "identifier", "phase", "evidence_digest"],
            payload["plan_digest"] as? String == plan.digest,
            let identifier = payload["identifier"] as? String,
            let digest = payload["evidence_digest"] as? String,
            SHA256Digest(rawValue: digest) != nil,
            checkpoints.updateValue(digest, forKey: identifier) == nil
          else { return nil }
        default:
          // Anything else, a completion included, is not an interrupted install.
          return nil
        }
      }
      guard let plan, events.contains("stub_and_esp_started"),
        let targetDigest = checkpoints["apfs-target-prepared"],
        Set(checkpoints.keys).isSubset(of: ["existing-install-removed", "apfs-target-prepared"]),
        SHA256Digest(hashing: target).rawValue == targetDigest,
        let evidence = try? JSONSerialization.jsonObject(with: target) as? [String: Any],
        Set(evidence.keys)
          == ["plan_digest", "partition_identifier", "offset_bytes", "size_bytes", "uuid"],
        evidence["plan_digest"] as? String == plan.digest,
        let raw = evidence["uuid"] as? String, let uuid = UUID(uuidString: raw),
        let offset = uint64(evidence["offset_bytes"]), offset == plan.offset,
        let size = uint64(evidence["size_bytes"]), size > 0, size <= plan.length,
        let targetEvidence = SHA256Digest(rawValue: targetDigest)
      else { return nil }
      var partitions: [RemovalRecordedPartition]?
      if let receipt {
        // A receipt that is there but doesn't parse proves nothing.
        guard
          let object = try? JSONSerialization.jsonObject(with: receipt) as? [String: Any],
          Set(object.keys) == ["plan_digest", "partitions"],
          object["plan_digest"] as? String == plan.digest,
          let entries = object["partitions"] as? [[String: Any]], entries.count == 4
        else { return nil }
        var recorded = [RemovalRecordedPartition]()
        for entry in entries {
          guard Set(entry.keys) == ["uuid", "offset_bytes", "size_bytes"],
            let raw = entry["uuid"] as? String, let id = UUID(uuidString: raw),
            let start = uint64(entry["offset_bytes"]), let length = uint64(entry["size_bytes"]),
            length > 0, start >= plan.offset,
            !start.addingReportingOverflow(length).overflow,
            start + length <= plan.offset + plan.length,
            !recorded.contains(where: { $0.uuid == id.uuidString })
          else { return nil }
          recorded.append(
            RemovalRecordedPartition(uuid: id.uuidString, offset: start, size: length))
        }
        guard
          recorded[0] == RemovalRecordedPartition(uuid: uuid.uuidString, offset: offset, size: size)
        else { return nil }
        partitions = recorded
      }
      return RemovalInterruptedInstall(
        journal: SHA256Digest(hashing: transcript), targetEvidence: targetEvidence,
        stubUUID: uuid.uuidString,
        stubOffset: offset, stubSize: size, planOffset: plan.offset, planLength: plan.length,
        engineVersion: plan.engine, partitions: partitions,
        receipt: receipt.map(SHA256Digest.init(hashing:)))
    }

    private static func uint64(_ value: Any?) -> UInt64? {
      guard let number = value as? NSNumber,
        CFGetTypeID(number as CFTypeRef) != CFBooleanGetTypeID(),
        let exact = Int64(exactly: number.doubleValue), exact >= 0,
        number.int64Value == exact
      else { return nil }
      return UInt64(exact)
    }
  }

  /// What the files prove, without keeping their contents (stub_info.json names
  /// the Mac's administrators). Digests let execution recheck the exact files.
  struct RemovalEvidence: Codable, Equatable, Sendable {
    let espBootObject: SHA256Digest?
    let stubInfo: SHA256Digest?
    let stubVolumeGroup: String?
    let installerLog: SHA256Digest?
    let installerRecord: RemovalInstallerRecord
    let stubHasLibrary: Bool
    let stubBootObject: SHA256Digest?
    let stubHasM1n1: Bool
    let stubEFIPartitions: [String]?
    /// Optional so removal records saved before it existed still decode.
    let interruptedInstalls: [RemovalInterruptedInstall]?

    init(files: RemovalInstallFiles) {
      espBootObject = files.espBootObject.map(SHA256Digest.init(hashing:))
      stubInfo = files.stubInfo.map(SHA256Digest.init(hashing:))
      stubVolumeGroup = files.stubInfo.flatMap(Self.volumeGroup(stubInfo:))
      installerLog = files.installerLog.map(SHA256Digest.init(hashing:))
      installerRecord =
        files.installerLog.map {
          Self.createdPartitions(log: $0).map { .partitions($0) } ?? .unreadable
        }
        ?? .missing
      stubHasLibrary = files.stubHasLibrary
      stubBootObject = files.stubBootObject.map(SHA256Digest.init(hashing:))
      stubHasM1n1 =
        files.stubBootObject.map { $0.range(of: Data("##m1n1_ver##".utf8)) != nil } ?? false
      stubEFIPartitions = files.stubBootObject.flatMap(Self.efiPartitions(bootObject:))
      // Sorted: the same journals must compare equal however a directory lists them.
      let interrupted = files.interruptedInstalls.sorted {
        $0.journal.rawValue < $1.journal.rawValue
      }
      interruptedInstalls = interrupted.isEmpty ? nil : interrupted
    }

    /// nil when every check passes; otherwise the first check that failed, as a phrase.
    func problem(for installation: RemovalInstallation) -> String? {
      if let proof = stoppedInstallProof(for: installation) { return proof }
      guard espBootObject != nil else { return "its EFI partition has no m1n1/boot.bin" }
      if let reason = installerRecordProblem(for: installation) { return reason }
      return startupContainerProblem(for: installation)
    }

    /// Whether only the helper's journal of an install that stopped partway
    /// proves this installation (for the removal sheet's wording).
    func provenByStoppedInstall(_ installation: RemovalInstallation) -> Bool {
      stoppedInstallProof(for: installation) == .some(nil)
    }

    /// nil when no journal applies and the ESP records decide; .some(nil) when
    /// the journal proves the installation; .some(reason) when it applies but
    /// doesn't match the disk.
    private func stoppedInstallProof(for installation: RemovalInstallation) -> String?? {
      guard stubInfo == nil || installerRecord == .missing else { return nil }
      let matches = (interruptedInstalls ?? []).filter {
        $0.stubUUID == UUID(uuidString: installation.stub.uuid)?.uuidString
      }
      guard matches.count == 1, let record = matches.first else { return nil }
      let mismatch =
        "the installer’s record of the installation that stopped doesn’t match the disk"
      let members = installation.members
      if let recorded = record.partitions {
        // Every partition by the exact identity it was created with: the ESP
        // and the startup container's m1n1 may not have been written yet.
        let found = members.map {
          RemovalRecordedPartition(
            uuid: UUID(uuidString: $0.uuid)?.uuidString ?? $0.uuid, offset: $0.offset,
            size: $0.size)
        }
        guard found == recorded else { return .some(mismatch) }
        guard !stubHasLibrary else { return .some(Self.fullSystem) }
        return .some(nil)
      }
      // Older engines left no receipt: only when both ESP records are missing,
      // the Linux partitions are proven by their place in the approved extent.
      guard stubInfo == nil, installerRecord == .missing, record.predatesPartitionReceipts
      else { return nil }
      guard espBootObject != nil else { return .some("its EFI partition has no m1n1/boot.bin") }
      let limit = record.planOffset + record.planLength
      guard members.count == 4, installation.stub.offset == record.stubOffset,
        installation.stub.size == record.stubSize,
        members.allSatisfy({
          $0.offset >= record.planOffset && !$0.offset.addingReportingOverflow($0.size).overflow
            && $0.end <= limit
        })
      else { return .some(mismatch) }
      return .some(startupContainerProblem(for: installation))
    }

    static let fullSystem =
      "its startup container holds a full system (a Library folder), unlike a startup container made by the Asahi installer"

    private func installerRecordProblem(for installation: RemovalInstallation) -> String? {
      guard stubInfo != nil, let stubVolumeGroup else {
        return "its EFI partition has no readable asahi/stub_info.json"
      }
      guard stubVolumeGroup == installation.system.group else {
        return
          "the asahi/stub_info.json on its EFI partition names a different startup container"
      }
      switch installerRecord {
      case .missing:
        return "its EFI partition has no installer record (asahi/installer.log)"
      case .unreadable:
        return
          "the installer record on its EFI partition (asahi/installer.log) has entries that couldn’t be read"
      case .partitions(let created):
        let found = installation.members.map {
          RemovalCreatedPartition(uuid: $0.uuid, type: $0.type)
        }
        let extra = installation.members.filter {
          !created.contains(RemovalCreatedPartition(uuid: $0.uuid, type: $0.type))
        }
        let missing = created.filter { !found.contains($0) }
        if !extra.isEmpty || !missing.isEmpty {
          var parts = [String]()
          if !extra.isEmpty {
            parts.append(
              "\(extra.map(\.identifier).joined(separator: ", ")) \(extra.count == 1 ? "wasn’t" : "weren’t") created by that installer"
            )
          }
          if !missing.isEmpty {
            parts.append(
              "\(missing.count) partition\(missing.count == 1 ? "" : "s") it created \(missing.count == 1 ? "is" : "are") gone"
            )
          }
          return
            "the installer record on its EFI partition doesn’t match the disk: \(parts.joined(separator: "; "))"
        }
      }
      return nil
    }

    private func startupContainerProblem(for installation: RemovalInstallation) -> String? {
      guard !stubHasLibrary else { return Self.fullSystem }
      guard stubBootObject != nil else {
        return "its startup container has no m1n1 boot object (Finish Installation.app)"
      }
      guard stubHasM1n1 else { return "the boot object in its startup container isn’t m1n1" }
      guard let links = stubEFIPartitions, links.count == 1,
        let link = UUID(uuidString: links[0]), link.uuidString == installation.esp.uuid
      else {
        return
          "the m1n1 boot object in its startup container doesn’t point to this EFI partition"
      }
      return nil
    }

    static func volumeGroup(stubInfo: Data) -> String? {
      guard let object = try? JSONSerialization.jsonObject(with: stubInfo) as? [String: Any],
        let raw = object["vgid"] as? String, let uuid = UUID(uuidString: raw)
      else { return nil }
      return uuid.uuidString
    }

    /// asahi-installer logs `New partition: Partition(name=..., type=..., uuid=...)`
    /// for every partition it adds, and copies the log to the ESP's asahi/.
    /// Any record that doesn't parse, or a repeated UUID, makes the whole log unreadable.
    static func createdPartitions(log: Data) -> [RemovalCreatedPartition]? {
      let marker = "New partition: Partition("
      guard
        let pattern = try? NSRegularExpression(
          pattern:
            #"^name='[^']*', offset=[0-9]+, size=[0-9]+, free=False, type='([^']+)', uuid='([0-9A-Fa-f-]{36})'"#
        )
      else { return nil }
      var created = [RemovalCreatedPartition]()
      var resumed: [RemovalCreatedPartition]?
      for line in String(decoding: log, as: UTF8.self).split(whereSeparator: \.isNewline) {
        if let range = line.range(of: "Verified existing partitions: ") {
          guard resumed == nil,
            let bytes = String(line[range.upperBound...]).data(using: .utf8),
            let object = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any],
            Set(object.keys) == ["format", "partitions"], object["format"] as? Int == 1,
            let entries = object["partitions"] as? [[String: String]], entries.count == 4
          else { return nil }
          var verified = [RemovalCreatedPartition]()
          for entry in entries {
            guard Set(entry.keys) == ["uuid", "type"], let raw = entry["uuid"],
              let uuid = UUID(uuidString: raw), let type = entry["type"],
              !verified.contains(where: { $0.uuid == uuid.uuidString })
            else { return nil }
            verified.append(RemovalCreatedPartition(uuid: uuid.uuidString, type: type))
          }
          guard
            verified.map(\.type) == ["Apple_APFS", "EFI", "Linux Filesystem", "Linux Filesystem"]
          else { return nil }
          resumed = verified
        }
        guard let range = line.range(of: marker) else { continue }
        let rest = String(line[range.upperBound...])
        let whole = NSRange(rest.startIndex..., in: rest)
        guard let match = pattern.firstMatch(in: rest, range: whole),
          let typeRange = Range(match.range(at: 1), in: rest),
          let uuidRange = Range(match.range(at: 2), in: rest),
          let uuid = UUID(uuidString: String(rest[uuidRange]))
        else { return nil }
        let entry = RemovalCreatedPartition(uuid: uuid.uuidString, type: String(rest[typeRange]))
        guard !created.contains(where: { $0.uuid == entry.uuid }) else { return nil }
        created.append(entry)
      }
      guard resumed == nil || created.isEmpty else { return nil }
      return resumed ?? created
    }

    /// The EFI partitions a startup container's m1n1 chainloads. Aurora's J700
    /// Stage 1 (the MacBook Neo) names its one ESP in a versioned, CRC-checked
    /// config block; asahi's m1n1 keeps variables after the first "STACKBOT" up
    /// to the first NUL, one per line (asahi-installer m1n1.py extract_vars).
    /// nil when neither is there.
    static func efiPartitions(bootObject: Data) -> [String]? {
      if let aurora = auroraStage1Partition(bootObject: bootObject) { return [aurora] }
      guard let marker = bootObject.range(of: Data("STACKBOT".utf8)) else { return nil }
      let tail = bootObject[marker.upperBound...]
      let region = tail.prefix { $0 != 0 }
      guard region.count < tail.count, region.allSatisfy({ $0 < 0x80 }) else { return nil }
      let key = "chosen.asahi,efi-system-partition="
      return String(decoding: region, as: UTF8.self).split(separator: "\n")
        .filter { $0.hasPrefix(key) }.map { String($0.dropFirst(key.count)) }
    }

    /// Aurora's J700 Stage 1 config block (aurora-silicon/m1n1
    /// tools/fill_stage1_config.py): magic, then version, proxy window, a
    /// NUL-padded 40-byte ESP PARTUUID and a 192-byte Stage 2 path, then the
    /// CRC-32 of those fields. nil unless there is exactly one valid version-1
    /// block naming an ESP.
    static func auroraStage1Partition(bootObject: Data) -> String? {
      let magic = Data("AURORA-S1-CFG01\0".utf8)
      let bodySize = 4 + 4 + 40 + 192
      let bytes = [UInt8](bootObject)
      guard let first = bootObject.range(of: magic),
        bootObject.range(of: magic, in: first.upperBound..<bootObject.endIndex) == nil
      else { return nil }
      let start = first.upperBound - bootObject.startIndex
      guard start + bodySize + 4 <= bytes.count else { return nil }
      let body = Array(bytes[start..<(start + bodySize)])
      func word(_ offset: Int) -> UInt32 {
        (0..<4).reduce(UInt32(0)) { $0 | UInt32(bytes[offset + $1]) << (8 * $1) }
      }
      guard word(start) == 1, word(start + bodySize) == crc32(body) else { return nil }
      let field = body[8..<48].prefix { $0 != 0 }
      guard body[(8 + field.count)..<48].allSatisfy({ $0 == 0 }),
        let uuid = UUID(uuidString: String(decoding: field, as: UTF8.self))
      else { return nil }
      return uuid.uuidString
    }

    static func crc32(_ bytes: [UInt8]) -> UInt32 {
      var crc: UInt32 = 0xFFFF_FFFF
      for byte in bytes {
        crc ^= UInt32(byte)
        for _ in 0..<8 { crc = crc & 1 == 1 ? (crc >> 1) ^ 0xEDB8_8320 : crc >> 1 }
      }
      return ~crc
    }
  }

  /// Every message says what was found and ends by saying nothing changed.
  enum RemovalText {
    static func size(_ bytes: UInt64) -> String {
      let value = Double(bytes)
      if value >= 1e12 { return String(format: "%.1f TB", value / 1e12) }
      if value >= 1e9 { return String(format: "%.1f GB", value / 1e9) }
      if value >= 1e6 { return String(format: "%.1f MB", value / 1e6) }
      return "\(bytes) bytes"
    }

    static func describe(_ part: RemovalPartition, _ snapshot: RemovalSnapshot) -> String {
      var facts = [String]()
      switch part.type {
      case "Apple_APFS": facts.append("APFS container")
      case "Apple_APFS_ISC": facts.append("Apple system container")
      case "Apple_APFS_Recovery": facts.append("Apple Recovery")
      case "EFI": facts.append("EFI partition")
      case "Linux Filesystem": facts.append("Linux partition")
      default: facts.append(part.type)
      }
      if !part.name.isEmpty { facts.append("“\(part.name)”") }
      facts.append(size(part.size))
      if part.type == "Apple_APFS",
        let container = snapshot.containers.first(where: { $0.storeUUID == part.uuid })
      {
        let order = ["System", "Data", "Preboot", "Recovery"]
        let volumes = container.volumes.sorted {
          let left = order.firstIndex(of: $0.roles.first ?? "") ?? order.count
          let right = order.firstIndex(of: $1.roles.first ?? "") ?? order.count
          return left == right ? $0.name < $1.name : left < right
        }
        facts.append(
          volumes.isEmpty
            ? "no volumes" : "volumes " + volumes.map { "“\($0.name)”" }.joined(separator: ", "))
      }
      return "\(part.identifier) (\(facts.joined(separator: ", ")))"
    }

    static func list(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot) -> String {
      parts.map { describe($0, snapshot) }.joined(separator: "; ")
    }

    static func refusal(
      _ finding: String, stranded: (bytes: UInt64, behind: [RemovalPartition])?,
      _ snapshot: RemovalSnapshot
    ) -> String {
      var message = finding
      if let stranded {
        let ids = stranded.behind.map(\.identifier).joined(separator: ", ")
        message +=
          " \(size(stranded.bytes)) of unallocated space isn’t directly after macOS: \(ids) \(stranded.behind.count == 1 ? "sits" : "sit") between them, so macOS can’t grow into it."
      }
      return message + " Nothing was changed."
    }

    static func inconsistent(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot) -> String {
      "The internal disk’s partition map couldn’t be read consistently. Found: \(list(parts, snapshot)). Nothing was changed."
    }

    static func unexpectedFrame(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot)
      -> String
    {
      "The internal disk doesn’t have the layout removal expects: Apple’s system container first, the running macOS next and Apple Recovery last. Found: \(list(parts, snapshot)). Nothing was changed."
    }

    static func nothingFound(gap: UInt64) -> String {
      if gap == 0 {
        return
          "No Omarchy installation, or other installation made with the Asahi installer, was found, and there’s no unallocated space after macOS. Nothing was changed."
      }
      return
        "No Omarchy installation, or other installation made with the Asahi installer, was found. \(size(gap)) directly after macOS is unallocated; that’s too little to return, so it was left as it is. Nothing was changed."
    }

    static func otherSystem(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot) -> String {
      "Found another system: \(list(parts, snapshot)). It looks like another macOS installation, and removal never deletes one."
    }

    static func foreign(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot) -> String {
      "Found \(parts.count == 1 ? "a partition that isn’t" : "partitions that aren’t") part of an installation made with the Asahi installer: \(list(parts, snapshot)). Removal only deletes partitions it can prove belong to one installation, so it stopped."
    }

    static func several(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot) -> String {
      "Found more than one installation: \(list(parts, snapshot)). Removal handles a single installation, so it stopped."
    }

    static func partial(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot) -> String {
      "Found \(list(parts, snapshot)) without the startup container every installation made with the Asahi installer has. This looks like a partly removed installation, which needs a manual review."
    }

    static func shape(_ parts: [RemovalPartition], _ snapshot: RemovalSnapshot) -> String {
      "Found a startup container, but the partitions with it don’t match one installation (startup container, EFI partition, then up to two Linux partitions, side by side): \(list(parts, snapshot))."
    }

    static func members(_ installation: RemovalInstallation) -> String {
      installation.members.map(\.identifier).joined(separator: ", ")
    }

    static func unconfirmed(_ installation: RemovalInstallation, reason: String) -> String {
      "Found the installation “\(installation.name)” (\(members(installation))), but \(reason). It can’t be confirmed as one installation, so nothing was changed."
    }

    static func unreadable(_ installation: RemovalInstallation, detail: String) -> String {
      "Found the installation “\(installation.name)” (\(members(installation))), but its files couldn’t be checked: \(detail) Nothing was changed."
    }

    static func growthLimited(free: UInt64, limit: UInt64) -> String {
      "\(size(free)) directly after macOS is unallocated, but macOS reports it can only grow to \(size(limit)). Nothing was changed."
    }

    /// The plan line's detail: what the Mac starts up from before removal.
    static func startupNow(_ startup: RemovalStartup) -> String {
      switch startup {
      case .macOS: return "It already starts up from macOS"
      case .other(let name): return "Your Mac starts up from “\(name)” now"
      case .nextStartupOverride: return "Replaces a one-time startup choice for the next restart"
      case .unknown: return "macOS couldn’t report which system your Mac starts up from"
      }
    }

    static let startupChanged =
      "Your Mac’s startup disk changed since you reviewed removal. Close this window and review removal again."

    static func startupRefused(_ name: String, reason: String?) -> String {
      "macOS didn’t set “\(name)” as the startup disk\(reported(reason)). Nothing was deleted."
    }

    /// After macOS accepted the change, so the startup disk may now be macOS.
    static func startupUnconfirmed(_ name: String, _ now: RemovalStartup, reason: String?)
      -> String
    {
      let state: String
      switch now {
      case .macOS: state = "your Mac starts up from macOS"
      case .other(let other): state = "your Mac starts up from “\(other)”"
      case .nextStartupOverride:
        state = "a one-time startup choice for the next restart is still set"
      case .unknown: state = "it can’t tell which system your Mac starts up from"
      }
      let failed = reason == nil ? "" : ", and replacing it didn’t work\(reported(reason))"
      return
        "macOS set “\(name)” as the startup disk, but afterwards it reports that \(state)\(failed). Nothing was deleted."
    }

    private static func reported(_ reason: String?) -> String {
      guard let reason, !reason.isEmpty else { return "" }
      return ". It reported: “\(reason)”"
    }
  }
#endif
