import AppKit

/// Draws keyboard focus around the full themed button, not AppKit's default bezel.
class RecoveryButton: NSButton {
  override var focusRingMaskBounds: NSRect { bounds }

  override func drawFocusRingMask() {
    NSBezierPath(
      roundedRect: bounds, xRadius: OmarchyTheme.buttonRadius, yRadius: OmarchyTheme.buttonRadius
    ).fill()
  }
}

final class RecoveryPrimaryButton: RecoveryButton {
  override var intrinsicContentSize: NSSize {
    let size = super.intrinsicContentSize
    return NSSize(
      width: max(96, size.width + 40), height: max(OmarchyTheme.buttonHeight, size.height))
  }

  init(title: String, target: AnyObject?, action: Selector?) {
    super.init(frame: .zero)
    self.title = title.uppercased()
    self.target = target
    self.action = action
    bezelStyle = .rounded
    font = OmarchyTheme.button
    contentTintColor = OmarchyTheme.accentText
    wantsLayer = true
    layer?.backgroundColor = OmarchyTheme.darkCGColor(OmarchyTheme.accent)
    layer?.cornerRadius = OmarchyTheme.buttonRadius
    isBordered = false
    translatesAutoresizingMaskIntoConstraints = false
    heightAnchor.constraint(equalToConstant: OmarchyTheme.buttonHeight).isActive = true
  }

  required init?(coder: NSCoder) { fatalError("Use the programmatic initializer") }
}

final class RecoverySecondaryButton: RecoveryButton {
  override var intrinsicContentSize: NSSize {
    let size = super.intrinsicContentSize
    return NSSize(width: size.width + 32, height: max(OmarchyTheme.buttonHeight, size.height))
  }

  init(title: String, target: AnyObject?, action: Selector?) {
    super.init(frame: .zero)
    self.title = title
    self.target = target
    self.action = action
    font = OmarchyTheme.control
    contentTintColor = OmarchyTheme.secondaryText
    isBordered = false
    wantsLayer = true
    layer?.backgroundColor = OmarchyTheme.darkCGColor(OmarchyTheme.card)
    layer?.borderColor = OmarchyTheme.darkCGColor(OmarchyTheme.separator)
    layer?.borderWidth = 1
    layer?.cornerRadius = OmarchyTheme.buttonRadius
    translatesAutoresizingMaskIntoConstraints = false
    heightAnchor.constraint(equalToConstant: OmarchyTheme.buttonHeight).isActive = true
  }

  required init?(coder: NSCoder) { fatalError("Use the programmatic initializer") }
}

final class RecoveryTextButton: RecoveryButton {
  override var intrinsicContentSize: NSSize {
    let size = super.intrinsicContentSize
    return NSSize(width: size.width + 24, height: max(OmarchyTheme.buttonHeight, size.height))
  }

  init(title: String, target: AnyObject?, action: Selector?) {
    super.init(frame: .zero)
    self.title = title
    self.target = target
    self.action = action
    font = OmarchyTheme.control
    contentTintColor = OmarchyTheme.secondaryText
    isBordered = false
    translatesAutoresizingMaskIntoConstraints = false
    heightAnchor.constraint(equalToConstant: OmarchyTheme.buttonHeight).isActive = true
  }

  required init?(coder: NSCoder) { fatalError("Use the programmatic initializer") }
}
