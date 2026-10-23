import AppKit

/// The omarchy.org pixel wordmark, drawn from its cell grid so it stays crisp
/// at any size, coloured with the cyan-to-purple gradient of the site's hero
/// art. No image file is involved.
final class OmarchyWordmark: NSView {

  /// 81 × 19 cells, sampled from the site's OpenGraph image.
  static let grid: [String] = [
    ".................###.............................................................",
    "..#####......###########......#######....#######....#######....#...#......#...#..",
    ".#######....#############....########...########...########...##...##....##...##.",
    "###...###..###...###...###..###...###..###...###..###...###..###...###..###...###",
    "###...###..###...###...###..###...###..###...###..###...###..###...###..###...###",
    "###...###..###...###...###..###...###..###...###..###...##...###...###..###...###",
    "###...###..###...###...###..###...###..###...###..###...#....###...###..###...###",
    "###...###..###...###...###..###...###..###...###..###........###...###..###...###",
    "###...###..###...###...###.##########.#########...###.......###########.#########",
    "###...###..###...###...###.##########.########....###......###########..#########",
    "###...###..###...###...###..###...###..###........###........###...###........###",
    "###...###..###...###...###..###...###.##########..###...#....###...###...##...###",
    "###...###..###...###...###..###...###.##########..###...##...###...###..###...###",
    "###...###..###...###...###..###...###..###...###..###...###..###...###..###...###",
    "###...###..###...###...###..###...###..###...###..###...###..###...###..###...###",
    ".#######....##...###...##...###...##...###...###..########...###...##....#######.",
    "..#####......#...###...#....###...#....###...###..#######....###...#......#####..",
    ".......................................###...##..................................",
    ".......................................###...#...................................",
  ]

  /// One colour per cell row, top to bottom: white, turquoise, cyan, blue,
  /// purple, magenta.
  static let rowColors: [NSColor] = [
    0xFF_FFFF, 0xEA_FDFF, 0xC8_FAFB, 0xB4_F9F8, 0x9D_EFF9, 0x86_DFFA, 0x7D_CFFF,
    0x67_C4F9, 0x55_B5F2, 0x47_A4EA, 0x3F_93E3, 0x4A_80D8, 0x56_70CB, 0x64_62BE,
    0x71_56B3, 0x7C_4CAB, 0x8A_45A8, 0x9C_3EA6, 0xA8_3AA3,
  ].map(rgb)

  /// The same ramp for light mode without its white and pale cyan top, which
  /// vanish on the light window: light blue down to the same purple.
  static let lightRowColors: [NSColor] = [
    0x55_B5F2, 0x4D_ACEE, 0x46_A2E9, 0x42_99E5, 0x41_8FE1, 0x48_84DA, 0x4E_7BD4,
    0x55_72CC, 0x5C_6AC5, 0x64_62BE, 0x6B_5BB8, 0x72_55B2, 0x78_4FAE, 0x7F_4AAA,
    0x87_47A9, 0x90_43A7, 0x9A_3FA6, 0xA1_3CA5, 0xA8_3AA3,
  ].map(rgb)

  static let columns = grid[0].count
  static let rows = grid.count

  override var isFlipped: Bool { true }

  override func draw(_ dirtyRect: NSRect) {
    super.draw(dirtyRect)
    let isDark = effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
    let colors = isDark ? Self.rowColors : Self.lightRowColors
    let cell = min(bounds.width / CGFloat(Self.columns), bounds.height / CGFloat(Self.rows))
    let originX = (bounds.width - cell * CGFloat(Self.columns)) / 2
    let originY = (bounds.height - cell * CGFloat(Self.rows)) / 2
    for (row, line) in Self.grid.enumerated() {
      colors[row].setFill()
      for (column, character) in line.enumerated() where character == "#" {
        NSBezierPath(
          rect: NSRect(
            x: originX + CGFloat(column) * cell,
            y: originY + CGFloat(row) * cell,
            width: cell + 0.4,
            height: cell + 0.4
          )
        ).fill()
      }
    }
  }

  override func viewDidChangeEffectiveAppearance() {
    super.viewDidChangeEffectiveAppearance()
    needsDisplay = true
  }

  override func accessibilityLabel() -> String? { "Omarchy" }

  private static func rgb(_ hex: Int) -> NSColor {
    NSColor(
      srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
      green: CGFloat((hex >> 8) & 0xFF) / 255,
      blue: CGFloat(hex & 0xFF) / 255,
      alpha: 1
    )
  }
}
