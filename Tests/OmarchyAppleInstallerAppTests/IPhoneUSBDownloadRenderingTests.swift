#if os(macOS) && DEBUG
  import AppKit
  import OmarchyAppleInstallerTrustCore
  import OmarchyInstallerUXCore
  import SwiftUI
  import XCTest
  @testable import OmarchyAppleInstallerApp

  @MainActor
  final class IPhoneUSBDownloadRenderingTests: XCTestCase {
    @MainActor private final class Hosted {
      var session: InstallerSession?
    }

    func testUSBOptionRendersAndStartsThenPausesTheMockDownload() async throws {
      let hosted = Hosted()
      let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
      let environment = PreviewInstallerEnvironment(
        scenario: .iPhoneUSB,
        journalURL: root.appendingPathComponent(
          "evidence/apple-silicon/2026-08-29-m1-fresh-install-v6/execution-journal.jsonl"))
      let window = NSWindow(
        contentRect: CGRect(x: 0, y: 0, width: 780, height: 680), styleMask: [.borderless],
        backing: .buffered, defer: false)
      window.isReleasedWhenClosed = false
      defer {
        window.close()
        hosted.session?.cancelPrefetchOnQuit()
      }
      let view = NSHostingView(
        rootView: OnePageInstallerView(
          environment: environment, channel: nil,
          onSessionAvailable: { hosted.session = $0 }
        ).frame(width: 780))
      window.contentView = view
      window.orderFront(nil)
      view.layoutSubtreeIfNeeded()
      for _ in 0..<100 where hosted.session == nil {
        try await Task.sleep(for: .milliseconds(10))
      }
      let session = try XCTUnwrap(hosted.session)
      for _ in 0..<100 where session.isBusy {
        try await Task.sleep(for: .milliseconds(10))
      }
      await session.continueToPlan()
      for _ in 0..<100 where session.prefetchState != .waitingForUnmeteredNetwork {
        try await Task.sleep(for: .milliseconds(10))
      }
      XCTAssertEqual(session.prefetchState, .waitingForUnmeteredNetwork)
      XCTAssertTrue(session.showsIPhoneUSBDownloadOption)
      XCTAssertFalse(session.downloadNetwork.allowsIPhoneUSBDownload)
      try await save(view, window: window, name: "waiting")

      let toggle = try XCTUnwrap(findSwitch(in: view), "The native USB switch must be present")
      toggle.performClick(nil)
      for _ in 0..<100 where !session.downloadNetwork.allowsIPhoneUSBDownload {
        try await Task.sleep(for: .milliseconds(10))
      }
      try await Task.sleep(for: .milliseconds(100))
      XCTAssertTrue(session.downloadNetwork.allowsIPhoneUSBDownload)
      guard case .downloading = session.prefetchState else {
        return XCTFail("USB consent must resume the download")
      }
      try await save(view, window: window, name: "downloading")
      let enabledToggle = try XCTUnwrap(findSwitch(in: view))
      enabledToggle.performClick(nil)
      for _ in 0..<100 {
        if case .paused = session.prefetchState { break }
        try await Task.sleep(for: .milliseconds(10))
      }
      XCTAssertFalse(session.downloadNetwork.allowsIPhoneUSBDownload)
      guard case .paused = session.prefetchState else {
        return XCTFail("Revoking USB consent must pause the download")
      }
      try await save(view, window: window, name: "paused")
    }

    private func findSwitch(in view: NSView) -> NSSwitch? {
      if let control = view as? NSSwitch { return control }
      for child in view.subviews {
        if let result = findSwitch(in: child) { return result }
      }
      return nil
    }

    private func save(_ view: NSView, window: NSWindow, name: String) async throws {
      for _ in 0..<4 {
        view.layoutSubtreeIfNeeded()
        window.setContentSize(view.fittingSize)
        try await Task.sleep(for: .milliseconds(20))
      }
      XCTAssertGreaterThan(view.bounds.height, 400)
      XCTAssertLessThanOrEqual(view.bounds.height, OnePageInstallerView.maximumContentHeight)
      guard let directory = ProcessInfo.processInfo.environment["OMARCHY_IPHONE_USB_PREVIEW_DIR"],
        !directory.isEmpty
      else { return }
      let representation = try XCTUnwrap(view.bitmapImageRepForCachingDisplay(in: view.bounds))
      view.cacheDisplay(in: view.bounds, to: representation)
      let data = try XCTUnwrap(representation.representation(using: .png, properties: [:]))
      let url = URL(fileURLWithPath: directory).appendingPathComponent("iphone-usb-\(name).png")
      try FileManager.default.createDirectory(
        at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
      try data.write(to: url)
    }
  }
#endif
