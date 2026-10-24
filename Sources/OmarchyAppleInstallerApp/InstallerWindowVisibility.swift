import AppKit
import SwiftUI

/// Orders the installer's window out and back as Try asks, with the app out
/// of the Dock while it is out, and says when the person brings it back while
/// it is out.
struct InstallerWindowVisibility: NSViewRepresentable {
  let shows: Bool
  let onShown: () -> Void

  func makeNSView(context: Context) -> InstallerWindowTracker { InstallerWindowTracker() }

  func updateNSView(_ view: InstallerWindowTracker, context: Context) {
    view.onShown = onShown
    view.apply(shows)
  }
}

final class InstallerWindowTracker: NSView {
  var onShown: () -> Void = {}
  private var shows = true

  override func viewDidMoveToWindow() {
    super.viewDidMoveToWindow()
    NotificationCenter.default.removeObserver(self)
    guard let window else { return }
    NotificationCenter.default.addObserver(
      self, selector: #selector(windowDidBecomeKey), name: NSWindow.didBecomeKeyNotification,
      object: window)
    if !shows { window.orderOut(nil) }
  }

  func apply(_ shows: Bool) {
    guard shows != self.shows else { return }
    self.shows = shows
    guard let window else { return }
    if shows {
      NSApp.setActivationPolicy(.regular)
      window.makeKeyAndOrderFront(nil)
      NSApp.activate()
    } else {
      window.orderOut(nil)
      NSApp.setActivationPolicy(.accessory)
    }
  }

  @objc private func windowDidBecomeKey(_ notification: Notification) {
    guard !shows else { return }
    onShown()
  }
}
