import CoreGraphics
import Foundation

/// The Recovery window is compact. Only the recovery account form needs the
/// full height; the window grows for it and shrinks again, keeping its top edge.
enum RecoveryWindowLayout {
  static let width: CGFloat = 780
  static let compactHeight: CGFloat = 440
  static let accountHeight: CGFloat = 660
  /// The window starts compact; `center()` places it afterwards.
  static let initialFrame = NSRect(x: 0, y: 0, width: width, height: compactHeight)

  static func height(showsAccountForm: Bool) -> CGFloat {
    showsAccountForm ? accountHeight : compactHeight
  }

  /// `frame` at `height` with the same top edge. AppKit frames grow upward
  /// from their origin, so the origin moves instead. If the screen's visible
  /// frame is known, the window moves up only as far as it must to stay on it,
  /// and never above its top.
  static func frame(_ frame: NSRect, height: CGFloat, visible: NSRect?) -> NSRect {
    var result = NSRect(x: frame.minX, y: frame.maxY - height, width: frame.width, height: height)
    guard let visible else { return result }
    if result.minY < visible.minY { result.origin.y = visible.minY }
    if result.maxY > visible.maxY { result.origin.y = visible.maxY - height }
    return result
  }

  /// The details log's scrolling height, so its sheet fits inside the window:
  /// the full 290 points in the tall window, less in the compact one.
  static func detailsLogHeight(windowHeight: CGFloat) -> CGFloat {
    max(120, min(290, windowHeight - 240))
  }
}
