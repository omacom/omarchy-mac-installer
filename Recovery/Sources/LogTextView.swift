import AppKit

/// A bounded, selectable native log view that preserves the user's scroll position.
final class LogTextView: NSScrollView {
  private let editor: NSTextView

  init(text: String) {
    editor = NSTextView(frame: NSRect(x: 0, y: 0, width: 474, height: 200))
    super.init(frame: .zero)
    hasVerticalScroller = true
    autohidesScrollers = true
    drawsBackground = true
    backgroundColor = OmarchyTheme.window
    wantsLayer = true
    layer?.cornerRadius = OmarchyTheme.buttonRadius
    layer?.borderColor = OmarchyTheme.darkCGColor(OmarchyTheme.separator)
    layer?.borderWidth = 1
    editor.isEditable = false
    editor.isSelectable = true
    editor.isRichText = false
    editor.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
    editor.textColor = .textColor
    editor.drawsBackground = false
    editor.textContainerInset = NSSize(width: 12, height: 12)
    editor.isVerticallyResizable = true
    editor.isHorizontallyResizable = false
    // tile() sets the width; autoresizing would add the growth from .zero.
    editor.autoresizingMask = []
    editor.textContainer?.widthTracksTextView = true
    editor.textContainer?.containerSize = NSSize(
      width: 474, height: CGFloat.greatestFiniteMagnitude)
    documentView = editor
    update(text: text)
  }

  required init?(coder: NSCoder) { fatalError("Use the programmatic initializer") }

  /// This view starts at .zero and takes its size from constraints. With a
  /// width autoresizing mask the text view would gain that whole growth on top
  /// of its initial width and end up twice as wide as the visible area: lines
  /// wrapped past the right edge, and the clip view kept the sideways offset
  /// left by scrolling to the end before layout. Keep the text view exactly as
  /// wide as the clip view, whichever scroller style is in use.
  override func tile() {
    super.tile()
    let width = contentView.bounds.width
    guard width > 0, editor.frame.width != width else { return }
    editor.setFrameSize(NSSize(width: width, height: editor.frame.height))
  }

  var displayedText: String { editor.string }
  var textView: NSTextView { editor }

  func update(text: String) {
    guard editor.string != text else { return }
    let atEnd = editor.string.isEmpty || contentView.bounds.maxY >= editor.bounds.maxY - 24
    let selection = editor.selectedRange()
    editor.string = text
    if atEnd {
      editor.scrollRangeToVisible(NSRange(location: editor.string.utf16.count, length: 0))
    } else {
      let count = editor.string.utf16.count
      let start = min(selection.location, count)
      editor.setSelectedRange(
        NSRange(location: start, length: min(selection.length, count - start)))
    }
  }
}
