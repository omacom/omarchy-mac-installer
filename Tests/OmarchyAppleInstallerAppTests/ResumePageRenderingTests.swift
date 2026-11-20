#if os(macOS) && DEBUG
  import AppKit
  import SwiftUI
  import XCTest
  import OmarchyAppleInstallerTrustCore
  @testable import OmarchyAppleInstallerApp

  @MainActor
  final class ResumePageRenderingTests: XCTestCase {
    func testResumeAndRecoveryInstructionsFitWithVisibleActions() throws {
      let output = URL(fileURLWithPath: "/tmp/omarchy-resume-ui", isDirectory: true)
      try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
      let ticket = InterruptedInstallResumeTicket(
        id: UUID(), deviceIdentifier: "apple,j414s",
        allocationBytes: 100_000_000_000,
        metadataDigest: "sha256:" + String(repeating: "e", count: 64),
        payloadDigest: "sha256:" + String(repeating: "f", count: 64))
      let scenarios: [(Bool, Bool, String?, String)] = [
        (
          false, false,
          "Saved installation found. Choose Resume Installation to verify its partitions and installed images before rebuilding startup files.",
          "resume"
        ), (false, true, nil, "resuming"),
        (
          false, false,
          "Resumption stopped. The installation record was preserved. Check Installation again before retrying. FileNotFoundError: [Errno 2] No such file or directory: '/Volumes/EFI - OMARC/m1n1/boot-j700.bin'",
          "failed"
        ),
        (true, false, nil, "completed"),
      ]
      for (completed, busy, message, name) in scenarios {
        let window = NSWindow(
          contentRect: CGRect(x: 0, y: 0, width: 780, height: 680),
          styleMask: [.borderless], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.appearance = NSAppearance(named: .darkAqua)
        let view = NSHostingView(
          rootView: ResumePageFrame {
            OmarchyResumeView(
              previewTicket: ticket, completed: completed, busy: busy, message: message)
          }
          .frame(width: 780)
          .omarchyTypography()
          .environment(\.colorScheme, .dark)
          .background(OmarchyTheme.window))
        window.contentView = view
        for _ in 0..<5 {
          view.layoutSubtreeIfNeeded()
          RunLoop.main.run(until: Date().addingTimeInterval(0.02))
          window.setContentSize(view.fittingSize)
        }
        XCTAssertLessThanOrEqual(view.fittingSize.height, 680)
        XCTAssertGreaterThan(view.fittingSize.height, 100)
        let bitmap = try XCTUnwrap(view.bitmapImageRepForCachingDisplay(in: view.bounds))
        view.cacheDisplay(in: view.bounds, to: bitmap)
        let png = try XCTUnwrap(bitmap.representation(using: .png, properties: [:]))
        try png.write(to: output.appendingPathComponent(name + ".png"))
        window.close()
      }
    }
  }
#endif
