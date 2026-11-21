#if os(macOS) && DEBUG
  import AppKit
  import SwiftUI
  import XCTest

  @testable import OmarchyAppleInstallerApp

  @MainActor
  final class KeyboardBehaviorTests: XCTestCase {
    private final class KeyWindow: NSWindow {
      override var canBecomeKey: Bool { true }
      override var isKeyWindow: Bool { true }
    }

    private func host(_ view: some View) -> NSWindow {
      let window = KeyWindow(
        contentRect: CGRect(x: -10_000, y: -10_000, width: 520, height: 600),
        styleMask: [.borderless], backing: .buffered, defer: false)
      window.isReleasedWhenClosed = false
      window.contentView = NSHostingView(rootView: view)
      window.orderFront(nil)
      addTeardownBlock { @MainActor in window.close() }
      settle(0.2)
      return window
    }

    private func settle(_ seconds: TimeInterval) {
      RunLoop.main.run(until: Date().addingTimeInterval(seconds))
    }

    private func press(_ key: String, modifiers: NSEvent.ModifierFlags, in window: NSWindow) -> Bool
    {
      let event = NSEvent.keyEvent(
        with: .keyDown, location: .zero, modifierFlags: modifiers, timestamp: 0,
        windowNumber: window.windowNumber, context: nil, characters: key,
        charactersIgnoringModifiers: key, isARepeat: false, keyCode: key == "\r" ? 36 : 53)!
      return window.performKeyEquivalent(with: event)
    }

    private func pressTab(in window: NSWindow) {
      for type in [NSEvent.EventType.keyDown, .keyUp] {
        window.sendEvent(
          NSEvent.keyEvent(
            with: type, location: .zero, modifierFlags: [], timestamp: 0,
            windowNumber: window.windowNumber, context: nil, characters: "\t",
            charactersIgnoringModifiers: "\t", isARepeat: false, keyCode: 48)!)
      }
    }

    func testCommandReturnPressesTheMainActionEvenWithoutAReturnShortcut() {
      var presses = 0
      let window = host(
        MainActionButton("Remove", kind: .danger, shortcut: nil) { presses += 1 })
      XCTAssertTrue(press("\r", modifiers: .command, in: window))
      settle(0.1)
      XCTAssertEqual(presses, 1)
      XCTAssertFalse(press("\r", modifiers: [], in: window))
      settle(0.1)
      XCTAssertEqual(presses, 1, "Return must not press a button that left it off")
    }

    func testCommandReturnLeavesADisabledMainActionAlone() {
      var presses = 0
      let window = host(MainActionButton("Remove") { presses += 1 }.disabled(true))
      _ = press("\r", modifiers: .command, in: window)
      settle(0.1)
      XCTAssertEqual(presses, 0)
    }

    func testEscapeRunsTheHiddenShortcutOnlyWhileEnabled() {
      var presses = 0
      let enabled = host(HiddenShortcutButton(shortcut: .cancelAction) { presses += 1 })
      XCTAssertTrue(press("\u{1b}", modifiers: [], in: enabled))
      settle(0.1)
      XCTAssertEqual(presses, 1)
      let disabled = host(
        HiddenShortcutButton(shortcut: .cancelAction) { presses += 1 }.disabled(true))
      _ = press("\u{1b}", modifiers: [], in: disabled)
      settle(0.1)
      XCTAssertEqual(presses, 1)
    }

    func testRemovalAccountStepFocusesThePasswordField() {
      let window = host(
        OmarchyRemovalSheet(
          isSimulation: true, onBusyChanged: { _ in }, onClose: {}, onRequiresReview: {},
          simulationScenario: RemovalPreviewScenario.accountFirst.rawValue))
      settle(1.0)
      let editor = window.firstResponder as? NSTextView
      XCTAssertTrue(
        editor?.delegate is NSSecureTextField, "\(String(describing: window.firstResponder))")
    }

    func testRemovalFlowRunsFromTheKeyboard() {
      var closes = 0
      let window = host(
        OmarchyRemovalSheet(
          isSimulation: true, onBusyChanged: { _ in }, onClose: { closes += 1 },
          onRequiresReview: {},
          simulationScenario: RemovalPreviewScenario.accountFirst.rawValue))
      settle(1.0)
      func focusedField() -> String? {
        ((window.firstResponder as? NSTextView)?.delegate as? NSTextField)?.placeholderString
      }
      func type(_ text: String) {
        (window.firstResponder as? NSTextView)?.insertText(
          text, replacementRange: NSRange(location: NSNotFound, length: 0))
        settle(0.15)
      }

      // The first setup fails and clears the password, as a real failure does.
      XCTAssertEqual(focusedField(), "macOS password")
      type("test")
      XCTAssertTrue(press("\r", modifiers: .command, in: window))
      settle(1.0)
      XCTAssertEqual(focusedField(), "macOS password")

      type("test")
      XCTAssertTrue(press("\r", modifiers: .command, in: window))
      settle(1.4)
      // The account just typed also approves the removal, so nothing is empty.
      window.makeFirstResponder(window.contentView)
      window.selectKeyView(following: window.contentView!)
      settle(0.2)
      XCTAssertEqual(focusedField(), "Account name")
      pressTab(in: window)
      settle(0.2)
      XCTAssertEqual(focusedField(), "macOS password")

      XCTAssertFalse(press("\r", modifiers: [], in: window), "Return must not remove")
      XCTAssertTrue(press("\r", modifiers: .command, in: window))
      settle(2.6)
      XCTAssertNil(focusedField())

      XCTAssertTrue(press("\u{1b}", modifiers: [], in: window))
      settle(0.1)
      XCTAssertEqual(closes, 1)
    }

    func testRemovalReviewFocusesThePasswordField() {
      let window = host(
        OmarchyRemovalSheet(previewScenario: .success, heightCap: 756, showsAccountFields: true))
      settle(0.5)
      let editor = window.firstResponder as? NSTextView
      XCTAssertTrue(
        editor?.delegate is NSSecureTextField, "\(String(describing: window.firstResponder))")
      XCTAssertFalse(
        press("\r", modifiers: .command, in: window), "Remove stays off until the password is typed"
      )
    }
  }
#endif
