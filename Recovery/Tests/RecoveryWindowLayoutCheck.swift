import CoreGraphics
import Foundation

@main struct Check {
  static func main() {
    precondition(RecoveryWindowLayout.height(showsAccountForm: false) == 440)
    precondition(RecoveryWindowLayout.height(showsAccountForm: true) == 660)
    precondition(RecoveryWindowLayout.initialFrame == NSRect(x: 0, y: 0, width: 780, height: 440))

    // Growing keeps the top edge; AppKit's origin is the bottom-left corner.
    let screen = NSRect(x: 0, y: 0, width: 1512, height: 944)
    let compact = NSRect(x: 100, y: 300, width: 780, height: 440)
    let grown = RecoveryWindowLayout.frame(compact, height: 660, visible: screen)
    precondition(grown == NSRect(x: 100, y: 80, width: 780, height: 660), "\(grown)")
    // Back restores the compact frame exactly.
    let back = RecoveryWindowLayout.frame(grown, height: 440, visible: screen)
    precondition(back == compact, "\(back)")
    // Without screen information the top edge stays put.
    precondition(RecoveryWindowLayout.frame(compact, height: 660, visible: nil).maxY == 740)

    // Near the bottom of the screen the window moves up just enough to stay on it.
    let low = NSRect(x: 100, y: 100, width: 780, height: 440)
    let lifted = RecoveryWindowLayout.frame(
      low, height: 660, visible: NSRect(x: 0, y: 40, width: 1512, height: 900))
    precondition(lifted == NSRect(x: 100, y: 40, width: 780, height: 660), "\(lifted)")
    // On a screen shorter than the window, the top edge stays visible.
    let short = RecoveryWindowLayout.frame(
      compact, height: 660, visible: NSRect(x: 0, y: 0, width: 1024, height: 600))
    precondition(short.maxY == 600 && short.height == 660, "\(short)")

    // The details log shortens so its sheet fits the compact window.
    precondition(RecoveryWindowLayout.detailsLogHeight(windowHeight: 660) == 290)
    precondition(RecoveryWindowLayout.detailsLogHeight(windowHeight: 440) == 200)
    precondition(RecoveryWindowLayout.detailsLogHeight(windowHeight: 300) == 120)
    print(
      "PASS window layout: compact and account heights, top edge kept, kept on screen, details log fits"
    )
  }
}
