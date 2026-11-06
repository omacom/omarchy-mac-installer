import CoreGraphics
import Darwin
import Foundation

@MainActor
struct MacProMotionDisplay: ProMotionDisplayAccess {
  func inspect() throws -> ProMotionSnapshot? {
    var count: UInt32 = 0
    guard CGGetOnlineDisplayList(0, nil, &count) == .success, count <= 128 else {
      throw ProMotionError.unavailable
    }
    var displays = [CGDirectDisplayID](repeating: 0, count: Int(count))
    guard CGGetOnlineDisplayList(count, &displays, &count) == .success else {
      throw ProMotionError.unavailable
    }
    let builtIn = displays.prefix(Int(count)).filter {
      CGDisplayIsBuiltin($0) != 0 && CGDisplayIsActive($0) != 0
    }
    guard builtIn.count == 1, let display = builtIn.first else { return nil }
    guard let current = CGDisplayCopyDisplayMode(display),
      let modes = CGDisplayCopyAllDisplayModes(
        display, [kCGDisplayShowDuplicateLowResolutionModes: true] as CFDictionary
      ) as? [CGDisplayMode]
    else { throw ProMotionError.unavailable }
    let metadata = ProMotionMetadata.read(display: display, modes: modes + [current])
    return ProMotionSnapshot(
      displayID: display, builtIn: true,
      mirrored: CGDisplayIsInMirrorSet(display) != 0,
      current: describe(current, metadata: metadata),
      modes: modes.map { describe($0, metadata: metadata) }
    )
  }

  func apply(displayID: UInt32, modeID: Int32) throws {
    // Refresh the topology and mode choice immediately before the transaction.
    guard let snapshot = try inspect(), snapshot.displayID == displayID,
      snapshot.selection == .available(modeID),
      let modes = CGDisplayCopyAllDisplayModes(
        displayID, [kCGDisplayShowDuplicateLowResolutionModes: true] as CFDictionary
      ) as? [CGDisplayMode],
      let mode = modes.first(where: { $0.ioDisplayModeID == modeID })
    else { throw ProMotionError.displayChanged }
    var configuration: CGDisplayConfigRef?
    guard CGBeginDisplayConfiguration(&configuration) == .success,
      let configuration
    else { throw ProMotionError.configurationFailed }
    guard CGConfigureDisplayWithDisplayMode(configuration, displayID, mode, nil) == .success
    else {
      CGCancelDisplayConfiguration(configuration)
      throw ProMotionError.configurationFailed
    }
    // Complete consumes the transaction even on failure; do not cancel it again.
    guard CGCompleteDisplayConfiguration(configuration, .permanently) == .success else {
      throw ProMotionError.configurationFailed
    }
  }

  private func describe(_ mode: CGDisplayMode, metadata: [Int32: Bool]) -> ProMotionMode {
    ProMotionMode(
      id: mode.ioDisplayModeID, width: mode.width, height: mode.height,
      pixelWidth: mode.pixelWidth, pixelHeight: mode.pixelHeight,
      flags: mode.ioFlags, refreshRate: mode.refreshRate,
      usable: mode.isUsableForDesktopGUI(), isProMotion: metadata[mode.ioDisplayModeID]
    )
  }
}

/// Optional, read-only SkyLight queries. Public Quartz APIs expose nominal Hz,
/// but not the ProMotion designation. Failure leaves the manual Settings path.
/// ABI reference: joevt/AllRez, AllRez/AppleMisc.h (433f3513).
/// See docs/promotion.md for the private API and hardware qualification boundary.
@MainActor
private enum ProMotionMetadata {
  typealias Count = @convention(c) (UInt32, UnsafeMutablePointer<UInt32>) -> Int32
  typealias Description = @convention(c) (UInt32, Int32, UnsafeMutableRawPointer, Int32) -> Int32
  typealias Flag = @convention(c) (UInt32, UInt32) -> Bool

  static func read(display: UInt32, modes: [CGDisplayMode]) -> [Int32: Bool] {
    guard let library = dlopen(
      "/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY
    ) else { return [:] }
    defer { dlclose(library) }
    guard let countAddress = dlsym(library, "SLSGetNumberOfDisplayModes"),
      let descriptionAddress = dlsym(library, "SLSGetDisplayModeDescriptionOfLength"),
      let flagAddress = dlsym(library, "SLSIsDisplayModeProMotion")
    else { return [:] }
    let getCount = unsafeBitCast(countAddress, to: Count.self)
    let getDescription = unsafeBitCast(descriptionAddress, to: Description.self)
    let isProMotion = unsafeBitCast(flagAddress, to: Flag.self)
    var count: UInt32 = 0
    guard getCount(display, &count) == 0, count > 0, count <= 4096 else { return [:] }
    var result: [Int32: Bool] = [:]
    for index in 0..<count {
      // The known description is 212 bytes. Allocate extra space defensively;
      // request only that layout and cross-check it against public Quartz data.
      var words = [UInt32](repeating: 0, count: 128)
      let status = words.withUnsafeMutableBytes {
        getDescription(display, Int32(index), $0.baseAddress!, 212)
      }
      guard status == 0 else { return [:] }
      let id = Int32(bitPattern: words[49]) // IODisplayModeID at 0xc4
      guard let mode = modes.first(where: { $0.ioDisplayModeID == id }) else { continue }
      guard words[2] == UInt32(mode.width), words[3] == UInt32(mode.height),
        words[50] == UInt32(mode.pixelWidth), words[51] == UInt32(mode.pixelHeight),
        words[48] == mode.ioFlags
      else { return [:] }
      // The query takes the table INDEX, not IODisplayModeID.
      let flag = isProMotion(display, index)
      if let existing = result[id], existing != flag { return [:] }
      result[id] = flag
    }
    return result
  }
}
