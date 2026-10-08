import SwiftUI

/// Design tokens for the approved installer look: Try Omarchy's monospaced
/// type and buttons on omarchy.org's Tokyo Night colours (blue accent). The
/// installer is always dark, whatever the system appearance.
enum OmarchyTheme {
  // MARK: Palette

  // omarchy.org's Tokyo Night tokens (background night/storm, foreground
  // lavender, blue accent, red and yellow from the same scheme). Secondary
  // text is the accent, slightly muted like Try Omarchy's but kept at 4.5:1 or
  // better for its small sizes.
  static let window = rgb(0x1A_1B26)
  static let card = rgb(0x24_283B)
  static let text = rgb(0xC0_CAF5)
  static let secondaryText = rgb(0x7A_A2F7, alpha: 0.9)
  static let separator = rgb(0x41_4868)
  static let track = rgb(0x2F_334D)
  static let accent = rgb(0x7A_A2F7)
  static let accentText = rgb(0x1A_1B26)
  static let success = rgb(0x9E_Cb6B)
  static let danger = rgb(0xF7_768E)
  static let caution = rgb(0xE0_AF68)

  /// The disk divider handle: a firm blue that reads on the pale Omarchy
  /// segment.
  static let handle = rgb(0x3D_74E8)

  // Button feedback stays in the accent's blue: a primary button lightens
  // under the pointer and deepens while pressed. A secondary button takes an
  // accent border and lighter blue text on hover and a tinted surface when
  // pressed; every label stays at 4.5:1 or better.
  static let buttonHover = rgb(0x9A_B8FA)
  static let buttonPressed = rgb(0x66_90EC)
  static let buttonHoverText = rgb(0x9A_B8FA)
  static let buttonPressedSurface = rgb(0x41_4868)

  // MARK: Metrics

  static let cardRadius: CGFloat = 8
  static let buttonRadius: CGFloat = 6
  static let buttonHeight: CGFloat = 32
  static let buttonTracking: CGFloat = 0.35
  /// Extra leading for small help text, which often wraps: about 1.4 times
  /// its size instead of SF Mono's default 1.2.
  static let helpLineSpacing: CGFloat = 2

  // MARK: Type

  // Try Omarchy's scale, all in the system monospaced face. Views pick a role
  // and never set their own point size.
  static let title = mono(22, .bold)
  static let heading = mono(13, .bold)
  /// Try Omarchy's subtitle line ("OMARCHY  ·  APPLE SILICON"): short facts
  /// drawn uppercase in the accent colour.
  static let eyebrow = mono(10, .semibold)
  static let body = mono(11)
  static let detail = mono(10)
  static let control = mono(11, .medium)
  static let button = mono(11, .bold)
  static let badge = mono(10, .bold)
  static let technical = mono(10)
  static let numeral = mono(13, .bold)

  static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
    .system(size: size, weight: weight, design: .monospaced)
  }

  private static func rgb(_ hex: Int, alpha: Double = 1) -> Color {
    Color(
      .sRGB,
      red: Double((hex >> 16) & 0xFF) / 255,
      green: Double((hex >> 8) & 0xFF) / 255,
      blue: Double(hex & 0xFF) / 255,
      opacity: alpha
    )
  }
}

extension View {
  /// Small secondary help text that may wrap onto several lines.
  func omarchyHelpText() -> some View {
    font(OmarchyTheme.detail)
      .foregroundStyle(OmarchyTheme.secondaryText)
      .lineSpacing(OmarchyTheme.helpLineSpacing)
      .fixedSize(horizontal: false, vertical: true)
  }

  /// The default type for a window or sheet, so text and controls without a
  /// role of their own are monospaced too.
  func omarchyTypography() -> some View {
    font(OmarchyTheme.body).fontDesign(.monospaced)
  }
}
