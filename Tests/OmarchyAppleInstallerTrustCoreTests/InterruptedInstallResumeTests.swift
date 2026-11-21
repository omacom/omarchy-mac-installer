#if os(macOS)
  import Foundation
  import XCTest
  @testable import OmarchyAppleInstallerTrustCore

  final class InterruptedInstallResumeTests: XCTestCase {
    private func fixture(_ name: String, extension ext: String) throws -> Data {
      try Data(
        contentsOf: XCTUnwrap(
          Bundle.module.url(
            forResource: name, withExtension: ext, subdirectory: "Fixtures")))
    }

    private func record() throws -> (Data, Data) {
      (
        try fixture("interrupted-native-recovery", extension: "jsonl"),
        try fixture("interrupted-native-recovery-target", extension: "json")
      )
    }

    private func appended(_ data: Data, type: String, payload: [String: Any]) throws -> Data {
      let value: [String: Any] = [
        "schema_version": 1, "sequence": data.split(separator: 10).count + 1,
        "type": type, "payload": payload,
      ]
      return data + (try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]))
        + Data([10])
    }

    func testReleasedInterruptedJournalIsDiscoverableAndEvidenceBound() throws {
      let (data, evidence) = try record()
      let saved = try XCTUnwrap(
        SavedInterruptedInstall.parse(
          data,
          journal: URL(fileURLWithPath: "/unused/record.jsonl"), readEvidence: { _ in evidence }))
      XCTAssertEqual(saved.ticket.deviceIdentifier, "apple,j414s")
      XCTAssertEqual(saved.ticket.allocationBytes, 100_000_000_000)
      XCTAssertEqual(saved.plan.engineDigest, SavedInterruptedInstall.originalEngineDigest)
      XCTAssertFalse(saved.engineCompleted)
      XCTAssertThrowsError(
        try SavedInterruptedInstall.parse(
          data,
          journal: saved.journal, readEvidence: { _ in Data("changed target".utf8) }))
    }

    func testUnrelatedMutationAndUnboundCompletionAreNotResumable() throws {
      let (data, evidence) = try record()
      let journal = URL(fileURLWithPath: "/unused/record.jsonl")
      let plan = try XCTUnwrap(
        SavedInterruptedInstall.parse(
          data, journal: journal,
          readEvidence: { _ in evidence })
      ).plan
      let unrelated = try appended(
        data, type: "event",
        payload: [
          "plan_digest": plan.planDigest, "name": "unknown_mutation_started",
        ])
      XCTAssertNil(
        try SavedInterruptedInstall.parse(
          unrelated, journal: journal, readEvidence: { _ in evidence }))
      let completed = try appended(
        data, type: "completion",
        payload: [
          "plan_digest": plan.planDigest, "outcome": "awaiting_recovery",
        ])
      XCTAssertNil(
        try SavedInterruptedInstall.parse(
          completed, journal: journal, readEvidence: { _ in evidence }))
    }

    func testTruncatedAndChangedPlanRecordsFailClosed() throws {
      let (data, evidence) = try record()
      let journal = URL(fileURLWithPath: "/unused/record.jsonl")
      XCTAssertThrowsError(
        try SavedInterruptedInstall.parse(
          data.dropLast(), journal: journal,
          readEvidence: { _ in evidence }))
      let changed = Data(
        String(decoding: data, as: UTF8.self)
          .replacingOccurrences(
            of: SavedInterruptedInstall.originalEngineVersion, with: "unknown-engine"
          ).utf8)
      XCTAssertThrowsError(
        try SavedInterruptedInstall.parse(
          changed, journal: journal, readEvidence: { _ in evidence }))
    }

    func testPrivateSnapshotsRejectWrongBytesAndNeverExecuteTheInput() throws {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
      defer { try? FileManager.default.removeItem(at: root) }
      let source = root.appendingPathComponent("input")
      try Data("#!/bin/sh\nexit 99\n".utf8).write(to: source)
      let handle = try FileHandle(forReadingFrom: source)
      defer { try? handle.close() }
      XCTAssertThrowsError(
        try SavedInterruptedInstall.snapshot(
          handle,
          to: root.appendingPathComponent("snapshot"), maximum: 1024,
          expectedDigest: "sha256:" + String(repeating: "0", count: 64)))
      let bytes = try Data(contentsOf: source)
      let good = root.appendingPathComponent("verified")
      try SavedInterruptedInstall.snapshot(
        handle, to: good, maximum: 1024,
        expectedDigest: SavedInterruptedInstall.digest(bytes))
      XCTAssertEqual(try SavedInterruptedInstall.privateData(good, maximum: 1024), bytes)
      try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: good.path)
      XCTAssertThrowsError(try SavedInterruptedInstall.privateData(good, maximum: 1024))
    }

    func testPrivatePayloadKeepsTheAuthenticatedMetadataFilename() throws {
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
      defer { try? FileManager.default.removeItem(at: root) }
      let metadata = root.appendingPathComponent("installer_data.json")
      func write(_ names: [String]) throws {
        let entries = names.map { ["omarchy_target": "apple-silicon-full-os", "package": $0] }
        try JSONSerialization.data(withJSONObject: ["os_list": entries]).write(to: metadata)
        try FileManager.default.setAttributes(
          [.posixPermissions: 0o600], ofItemAtPath: metadata.path)
      }
      let originalName = "omarchy-2026.11.05-aarch64-apple-silicon-mac-stable-os-package.zip"
      try write([originalName])
      let destination = try SavedInterruptedInstall.payloadSnapshotURL(in: root)
      XCTAssertEqual(destination.lastPathComponent, originalName)
      XCTAssertEqual(destination.deletingLastPathComponent().path, root.path)
      for names in [
        ["../outside.zip"], ["/tmp/outside.zip"], ["installer_data.json"],
        [originalName, originalName], [],
      ] {
        try write(names)
        XCTAssertThrowsError(try SavedInterruptedInstall.payloadSnapshotURL(in: root))
      }
    }

    func testDiscoveryRefusesMultipleTargetsAndLinkedJournal() throws {
      let (data, evidence) = try record()
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      let journals = root.appendingPathComponent("execution-journals")
      try FileManager.default.createDirectory(
        at: root, withIntermediateDirectories: false,
        attributes: [.posixPermissions: 0o700])
      defer { try? FileManager.default.removeItem(at: root) }
      try FileManager.default.createDirectory(
        at: journals, withIntermediateDirectories: false,
        attributes: [.posixPermissions: 0o700])
      func add(_ name: String) throws -> URL {
        let journal = journals.appendingPathComponent(String(repeating: name, count: 64) + ".jsonl")
        try data.write(to: journal)
        let target = URL(fileURLWithPath: journal.path + ".apfs-target-prepared.evidence")
        try evidence.write(to: target)
        for file in [journal, target] {
          try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        }
        return journal
      }
      let first = try add("a")
      XCTAssertNotNil(try SavedInterruptedInstall.discover(in: root))
      _ = try add("b")
      XCTAssertThrowsError(try SavedInterruptedInstall.discover(in: root))
      try FileManager.default.removeItem(at: first)
      try FileManager.default.createSymbolicLink(
        at: first, withDestinationURL: journals.appendingPathComponent("other"))
      XCTAssertThrowsError(try SavedInterruptedInstall.discover(in: root))
    }

    func testRemovalAcceptsTruthfulResumeReceiptAndRejectsMixedOrDuplicateProof() throws {
      let entries = ["Apple_APFS", "EFI", "Linux Filesystem", "Linux Filesystem"]
        .enumerated().map { index, type in
          [
            "uuid": String(format: "%08d-1111-4111-8111-111111111111", index + 1), "type": type,
          ]
        }
      func log(_ entries: [[String: String]]) throws -> Data {
        let bytes = try JSONSerialization.data(
          withJSONObject: ["format": 1, "partitions": entries], options: [.sortedKeys])
        return Data("root INFO Verified existing partitions: ".utf8) + bytes + Data([10])
      }
      XCTAssertEqual(RemovalEvidence.createdPartitions(log: try log(entries))?.count, 4)
      XCTAssertNil(
        RemovalEvidence.createdPartitions(
          log: try log([entries[0], entries[0], entries[2], entries[3]])))
      let created =
        "New partition: Partition(name='disk0s5', offset=1, size=1, free=False, type='EFI', uuid='11111111-1111-4111-8111-111111111111')\n"
      XCTAssertNil(RemovalEvidence.createdPartitions(log: try log(entries) + Data(created.utf8)))
    }

    func testRecoveredCompletionRemainsDiscoverableAfterLostReply() throws {
      let (original, evidence) = try record()
      let journal = URL(fileURLWithPath: "/unused/record.jsonl")
      let saved = try XCTUnwrap(
        SavedInterruptedInstall.parse(
          original, journal: journal,
          readEvidence: { _ in evidence }))
      var data = original
      let completedEvidence = Data("verified resumed state".utf8)
      let rebound =
        "interrupted-stub-and-esp-rebound-"
        + String(SavedInterruptedInstall.digest(completedEvidence).dropFirst(7))
      for (identifier, phase) in [
        (SavedInterruptedInstall.boundCheckpoint, "stub_and_esp"),
        ("interrupted-stub-and-esp-rebound", "stub_and_esp"),
        ("interrupted-stub-and-esp-rebound-2", "stub_and_esp"),
        (rebound, "stub_and_esp"),
        ("stub-and-esp-installed", "stub_and_esp"),
      ] {
        data = try appended(
          data, type: "checkpoint",
          payload: [
            "plan_digest": saved.plan.planDigest, "identifier": identifier, "phase": phase,
            "evidence_digest": SavedInterruptedInstall.digest(completedEvidence),
          ])
      }
      data = try appended(
        data, type: "event",
        payload: [
          "plan_digest": saved.plan.planDigest, "name": "recovery_handoff_started",
        ])
      data = try appended(
        data, type: "checkpoint",
        payload: [
          "plan_digest": saved.plan.planDigest, "identifier": "recovery-handoff-prepared",
          "phase": "awaiting_recovery",
          "evidence_digest": SavedInterruptedInstall.digest(completedEvidence),
        ])
      data = try appended(
        data, type: "completion",
        payload: [
          "plan_digest": saved.plan.planDigest, "outcome": "awaiting_recovery",
        ])
      let completed = try XCTUnwrap(
        SavedInterruptedInstall.parse(
          data, journal: journal,
          readEvidence: {
            $0.lastPathComponent.contains("apfs-target-prepared") ? evidence : completedEvidence
          }))
      XCTAssertTrue(completed.engineCompleted)
      let changed = Data(
        String(decoding: data, as: UTF8.self).replacingOccurrences(
          of: rebound, with: "interrupted-stub-and-esp-rebound-" + String(repeating: "a", count: 64)
        ).utf8)
      XCTAssertNil(
        try SavedInterruptedInstall.parse(
          changed, journal: journal,
          readEvidence: {
            $0.lastPathComponent.contains("apfs-target-prepared") ? evidence : completedEvidence
          }))
    }

    func testExistingHelperWorkOrReplacementBlocksResumeDiscovery() async throws {
      let work = HelperWorkState()
      let server = ClosedEngineHelperServer(
        workingDirectory: FileManager.default.temporaryDirectory,
        executor: NeverResumeExecutor(), credentialValidator: AcceptResumeCredentials(),
        removalDisks: FakeRemovalDisk(), removalAdminValidator: { _ in }, work: work)
      try work.beginJob()
      do {
        _ = try await server.prepareInterruptedResume()
        XCTFail("Concurrent job admitted")
      } catch { XCTAssertEqual(error as? ClosedEngineHelperError, .busy) }
      work.endJob()
      XCTAssertTrue(work.beginReplacement(token: "test"))
      do {
        _ = try await server.prepareInterruptedResume()
        XCTFail("Resume admitted while replacing helper")
      } catch { XCTAssertEqual(error as? ClosedEngineHelperError, .beingReplaced) }
      work.cancelReplacement(token: "test")
    }

    func testChangedJournalCannotUseAnEarlierResumeApproval() async throws {
      let (data, evidence) = try record()
      let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
      let journals = root.appendingPathComponent("execution-journals")
      try FileManager.default.createDirectory(
        at: root, withIntermediateDirectories: false,
        attributes: [.posixPermissions: 0o700])
      defer { try? FileManager.default.removeItem(at: root) }
      try FileManager.default.createDirectory(
        at: journals, withIntermediateDirectories: false,
        attributes: [.posixPermissions: 0o700])
      let journal = journals.appendingPathComponent(String(repeating: "a", count: 64) + ".jsonl")
      let target = URL(fileURLWithPath: journal.path + ".apfs-target-prepared.evidence")
      try data.write(to: journal)
      try evidence.write(to: target)
      for file in [journal, target] {
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
      }
      let server = ClosedEngineHelperServer(
        workingDirectory: root,
        executor: NeverResumeExecutor(), credentialValidator: AcceptResumeCredentials(),
        removalDisks: FakeRemovalDisk(), removalAdminValidator: { _ in })
      let approval = try await server.prepareInterruptedResume()
      XCTAssertTrue(approval.automaticResumeAvailable)
      let ticket = try XCTUnwrap(approval.ticket)
      try (data + Data([10])).write(to: journal)
      do {
        _ = try await server.resumeInterruptedInstall(
          ticket: ticket.id,
          metadata: .nullDevice, payload: .nullDevice,
          document: InstallConf(encrypt: true, lane: "stable").serializedData,
          authorization: MachineOwnerAuthorization(
            username: "owner", password: Data("password".utf8)),
          clientBundle: Bundle.module.bundleURL)
        XCTFail("Changed journal admitted")
      } catch let error as InterruptedResumeFailure {
        XCTAssertTrue(error.message.contains("changed"))
      }
    }
  }

  private struct AcceptResumeCredentials: MachineOwnerCredentialValidating {
    func validate(_ authorization: MachineOwnerAuthorization) throws {}
  }

  private struct NeverResumeExecutor: ImportedEngineHandoffExecuting {
    func execute(
      _ package: ImportedEngineHandoffPackage, authorization: MachineOwnerAuthorization,
      operation: EngineHandoffOperation
    ) async throws -> Data {
      XCTFail("No engine should run in admission failure tests")
      throw PinnedAsahiEngineExecutionError.launchFailed
    }
  }
#endif
