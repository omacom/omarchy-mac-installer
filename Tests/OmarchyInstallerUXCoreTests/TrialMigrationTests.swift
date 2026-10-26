#if os(macOS)
  import Foundation
  import XCTest

  @testable import OmarchyInstallerUXCore

  @MainActor
  final class TrialMigrationTests: XCTestCase {
    private let newer = TryExportSessionTests.newer
    private let older = TryExportSessionTests.older
    private let permissions = TrialPermissions(
      accessibility: .allowed, microphone: .allowed, camera: .allowed)

    private func offer(
      _ exports: TryOmarchyExportsDisplay, chosen: String? = nil,
      check: TryOmarchyExportCheck = .idle, waits: Bool = false, phase: TrialPhase = .hidden
    ) -> TrialMigrationOffer {
      TrialMigrationOffer(
        exports: exports, chosenID: chosen, check: check, exportWaitsInOutbox: waits,
        phase: phase)
    }

    func testOffersTheNewestExportOnceItsCheckPasses() {
      let found = TryOmarchyExportsDisplay.found([newer, older], unusable: 0)
      XCTAssertEqual(offer(.notChecked), .looking)
      XCTAssertNil(offer(.notChecked).primary)
      XCTAssertTrue(offer(.notChecked).isBusy)

      let unchecked = offer(found)
      XCTAssertEqual(unchecked, .export(newer, .idle))
      XCTAssertEqual(unchecked.primary, .installWithExport(enabled: false))
      XCTAssertTrue(unchecked.isBusy)

      let checking = offer(found, chosen: newer.id, check: .checking)
      XCTAssertEqual(checking.checkLine, PlainLanguage.tryExportChecking)
      XCTAssertFalse(checking.primary?.isEnabled ?? true)

      let verified = offer(found, chosen: newer.id, check: .verified)
      XCTAssertEqual(verified.primary, .installWithExport(enabled: true))
      XCTAssertEqual(verified.primary?.title, "Install with These Files")
      XCTAssertEqual(verified.checkLine, PlainLanguage.tryExportVerified)
      XCTAssertFalse(verified.isBusy)
      XCTAssertTrue(verified.message.contains(newer.summary))

      let failed = offer(found, chosen: newer.id, check: .failed("tampered"))
      XCTAssertEqual(failed.checkLine, "tampered")
      XCTAssertTrue(failed.checkFailed)
      XCTAssertEqual(failed.primary, .installWithExport(enabled: false))

      XCTAssertEqual(
        offer(found, chosen: older.id, check: .verified), .export(older, .verified),
        "A chosen export that is still there stays chosen")
      XCTAssertEqual(
        offer(.found([newer], unusable: 0), chosen: older.id, check: .verified),
        .export(newer, .idle), "A chosen export that is gone gives way to the newest")
    }

    func testAnExportInTheRunningVMComesFirst() {
      let found = TryOmarchyExportsDisplay.found([newer], unusable: 0)
      let running = TrialPhase.running(permissions)
      XCTAssertEqual(offer(found, waits: true, phase: running), .waiting(stopping: false))
      XCTAssertEqual(
        offer(found, waits: true, phase: running).primary, .shutDownToPublish(enabled: true))
      XCTAssertEqual(offer(found, waits: true, phase: running).primary?.title, "Shut Down Omarchy")
      XCTAssertEqual(offer(found, waits: true, phase: .stopping), .waiting(stopping: true))
      XCTAssertEqual(
        offer(found, waits: true, phase: .stopping).primary, .shutDownToPublish(enabled: false))
      XCTAssertEqual(
        offer(found, waits: true, phase: .hidden), .export(newer, .idle),
        "Once Omarchy is down the outbox has been published")
    }

    func testWithoutAnExportItSaysHowToMakeOne() {
      XCTAssertEqual(offer(.none), .none(running: false))
      XCTAssertEqual(offer(.unavailable), .none(running: false))
      XCTAssertNil(offer(.none).primary)
      XCTAssertTrue(offer(.none).message.contains("Start Omarchy"))
      let running = offer(.none, phase: .running(permissions))
      XCTAssertEqual(running, .none(running: true))
      XCTAssertTrue(running.message.contains("Setup → Move to Native Install"))
      XCTAssertFalse(running.message.contains("Start Omarchy"))
    }

    func testFindsAFinishedExportInTheVMsOutbox() throws {
      let root = FileManager.default.temporaryDirectory
        .appendingPathComponent("trial-outbox-\(UUID().uuidString)", isDirectory: true)
      defer { try? FileManager.default.removeItem(at: root) }
      let outbox = root.appendingPathComponent("native-exports", isDirectory: true)
      XCTAssertFalse(TrialExportOutbox.hasExport(in: root), "No outbox yet")

      try FileManager.default.createDirectory(at: outbox, withIntermediateDirectories: true)
      try FileManager.default.createDirectory(
        at: outbox.appendingPathComponent(".export.Ab12Cd"), withIntermediateDirectories: false)
      FileManager.default.createFile(
        atPath: outbox.appendingPathComponent(newer.id).path, contents: nil)
      try FileManager.default.createSymbolicLink(
        at: outbox.appendingPathComponent(older.id), withDestinationURL: root)
      try FileManager.default.createDirectory(
        at: outbox.appendingPathComponent(newer.id.uppercased() + "x"),
        withIntermediateDirectories: false)
      XCTAssertFalse(
        TrialExportOutbox.hasExport(in: root),
        "An export in progress, a file, a link and a stray name don't count")

      try FileManager.default.removeItem(at: outbox.appendingPathComponent(newer.id))
      try FileManager.default.createDirectory(
        at: outbox.appendingPathComponent(newer.id), withIntermediateDirectories: false)
      XCTAssertTrue(TrialExportOutbox.hasExport(in: root))

      XCTAssertFalse(
        TrialExportOutbox.hasExport(in: root.appendingPathComponent("missing", isDirectory: true)))
    }
  }
#endif
