import Foundation

/// How Recovery enrolls this installation's Stage 1. The engine chose the mode
/// by chip (Engine/overlay/src/omarchy_chip.py) and recorded it in
/// install-config.json; Recovery never picks it from a board list.
enum AuraBootMode: String, Codable {
  /// The raw Stage 1 image, on chips without an execute window (M1 to M3) and
  /// on boards that keep a raw Stage 1 they were qualified with (the Neo).
  case raw
  /// Aura's Mach-O boot object, padded for the execute window (M4, A18 Pro, M5
  /// and later) and enrolled with `--compress`. It loads the confined Stage 2.
  /// After aurora-installer 7be5339's AuraBootMode (MIT).
  case macho

  var stage1Path: String {
    self == .raw ? "raw/aura-raw.bin" : "macho/aura-boot.macho"
  }

  /// The Recovery payload keeps the Stage 2 this installation chainloads under
  /// one name in both modes; the manifest's espPath says where it goes.
  var stage2Path: String { "raw/boot.bin" }

  /// The ESP file the enrolled Stage 1 loads.
  var espStage2Path: String {
    self == .raw ? "m1n1/boot.bin" : AuraStage1Format.confinedStage2Path
  }

  /// The code window the object is padded for. A raw image has none.
  var executeWindowSize: UInt64? { self == .macho ? ExecuteWindow.size : nil }

  func enrollmentArguments(stage1: String, volume: String) -> [String] {
    var arguments = ["configure-boot", "-c", stage1, "-v", volume]
    switch self {
    case .raw:
      arguments += ["--raw", "--entry-point", "2048", "--lowest-virtual-address", "0"]
    case .macho:
      // LZFSE in Preboot, as the stock kernelcache is stored, so the padding
      // never reaches it.
      arguments.append("--compress")
    }
    return arguments
  }
}

struct BootSelection: Codable {
  let mode: AuraBootMode
  let nestedVirtualization: Bool

  init(mode: AuraBootMode = .raw) {
    self.mode = mode
    nestedVirtualization = false
  }
}

enum RecoveryBootPolicy {
  static func authorizationArguments(
    groupUUID: String, allowCustomBootArguments: Bool = false
  ) -> [String] {
    [allowCustomBootArguments ? "-nca" : "-nc", "-v", groupUUID]
  }
}
