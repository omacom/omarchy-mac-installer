import Foundation

/// Only explicit ProMotion metadata qualifies a mode. Neither 0 nor 120 Hz
/// alone says whether a mode is adaptive.
struct ProMotionMode: Equatable {
  let id: Int32
  let width: Int
  let height: Int
  let pixelWidth: Int
  let pixelHeight: Int
  let flags: UInt32
  let refreshRate: Double
  let usable: Bool
  let isProMotion: Bool?

  func preservesLayout(of other: Self) -> Bool {
    width == other.width && height == other.height
      && pixelWidth == other.pixelWidth && pixelHeight == other.pixelHeight
      && flags == other.flags
  }
}

struct ProMotionSnapshot {
  let displayID: UInt32
  let builtIn: Bool
  let mirrored: Bool
  let current: ProMotionMode
  let modes: [ProMotionMode]

  var selection: ProMotionSelection {
    guard builtIn else { return .hidden }
    let hasHighRefreshMode = modes.contains {
      $0.isProMotion == true || $0.refreshRate > 60
    }
    guard current.isProMotion == true || hasHighRefreshMode else { return .hidden }
    guard !mirrored else { return .manual }
    if current.isProMotion == true { return .enabled }
    let candidates = modes.filter {
      $0.usable && $0.isProMotion == true && $0.preservesLayout(of: current)
    }
    // Do not choose arbitrarily between distinct mode IDs.
    let unique = Dictionary(grouping: candidates, by: \.id)
    if unique.count == 1, let mode = candidates.first { return .available(mode.id) }
    return .manual
  }
}

enum ProMotionSelection: Equatable {
  case hidden
  case manual
  case enabled
  case available(Int32)
}

@MainActor
protocol ProMotionDisplayAccess {
  func inspect() throws -> ProMotionSnapshot?
  func apply(displayID: UInt32, modeID: Int32) throws
}

enum ProMotionError: Error {
  case unavailable
  case displayChanged
  case configurationFailed
  case verificationFailed
}

/// The simulation gate covers reads as well as writes. No install session or
/// helper is reachable from this service.
@MainActor
struct ProMotionAction {
  let access: any ProMotionDisplayAccess
  let isSimulation: Bool

  func inspect() throws -> ProMotionSelection {
    guard !isSimulation else { return .hidden }
    return try access.inspect()?.selection ?? .hidden
  }

  func enable() throws {
    guard !isSimulation,
      let before = try access.inspect(),
      case .available(let target) = before.selection
    else { throw ProMotionError.unavailable }
    try access.apply(displayID: before.displayID, modeID: target)
    guard let after = try access.inspect(),
      after.displayID == before.displayID,
      after.builtIn, !after.mirrored,
      after.current.id == target,
      after.current.isProMotion == true,
      after.current.preservesLayout(of: before.current)
    else { throw ProMotionError.verificationFailed }
  }
}
